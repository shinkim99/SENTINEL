"""두 단계 스크리닝 파이프라인 (Anthropic API + Pydantic strict + single-retry)."""
from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import AsyncAnthropic

from app.config import Settings
from app.models import Citation, ProfileSpec, RawItem, ScreenedItem

logger = logging.getLogger(__name__)

_BATCH_SIZE = 15

# ── Tool schemas ──────────────────────────────────────────────────────────────

_PASS1_TOOL: dict[str, Any] = {
    "name": "report_relevant_items",
    "description": "Report indices of items relevant to the watch domains.",
    "input_schema": {
        "type": "object",
        "properties": {
            "relevant_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "0-based indices of relevant items from the batch",
            }
        },
        "required": ["relevant_indices"],
    },
}

_PASS2_TOOL: dict[str, Any] = {
    "name": "analyze_regulation",
    "description": "Structured analysis of a single regulatory document.",
    "input_schema": {
        "type": "object",
        "properties": {
            "relevant": {"type": "boolean"},
            "domain": {
                "type": "string",
                "description": "Most relevant domain from watch list",
            },
            "lifecycle_stage": {
                "type": "string",
                "enum": ["proposed", "enacted", "in_force", "amended", "repealed", "unclear"],
            },
            "impact_summary": {
                "type": "string",
                "description": "2-3 sentences: how this affects the R&D domain concerns",
            },
            "citation": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "quote": {"type": "string", "description": "Direct quote from provided content"},
                },
                "required": ["source_id", "quote"],
            },
        },
        "required": ["relevant", "domain", "lifecycle_stage", "impact_summary", "citation"],
    },
}


# ── Pass 1 — batch, low-cost ──────────────────────────────────────────────────

async def screen_pass1(
    items: list[RawItem],
    profiles: list[ProfileSpec],
    cfg: Settings,
) -> tuple[list[RawItem], dict]:
    """1차 스크리닝: 저비용 모델로 도메인·국가 매칭 (high recall).

    Returns (passed_items, stats).
    """
    if not items:
        return [], {"passed_screen1": 0, "total_screen1": 0}

    domains_ctx = "\n".join(
        f"- {p.domain} (countries: {', '.join(p.watch_countries)}): {', '.join(p.keywords[:8])}"
        for p in profiles
    )

    client = AsyncAnthropic(api_key=cfg.anthropic_api_key)
    relevant_urls: set[str] = set()

    for batch_start in range(0, len(items), _BATCH_SIZE):
        batch = items[batch_start : batch_start + _BATCH_SIZE]
        items_text = "\n".join(
            f"[{i}] {item.title} | {item.country} | {item.snippet[:200]}"
            for i, item in enumerate(batch)
        )
        prompt = (
            "You are a regulatory intelligence pre-filter.\n\n"
            "Watch domains:\n" + domains_ctx + "\n\n"
            "Review these items and return indices of those POTENTIALLY relevant "
            "(be inclusive — exclude only clearly unrelated items):\n\n"
            + items_text
        )

        indices = await _call_pass1(client, cfg.anthropic_model_screen, prompt)
        for idx in indices:
            if 0 <= idx < len(batch):
                relevant_urls.add(batch[idx].url)

    passed = [it for it in items if it.url in relevant_urls]
    stats = {"total_screen1": len(items), "passed_screen1": len(passed)}
    logger.info("screen_pass1: %d/%d passed", len(passed), len(items))
    return passed, stats


async def _call_pass1(
    client: AsyncAnthropic,
    model: str,
    prompt: str,
) -> list[int]:
    """Single-retry wrapper for pass1 tool call. Returns empty list on hard failure."""
    for attempt in range(2):
        try:
            resp = await client.messages.create(
                model=model,
                max_tokens=512,
                tools=[_PASS1_TOOL],
                tool_choice={"type": "tool", "name": "report_relevant_items"},
                messages=[{"role": "user", "content": prompt}],
            )
            for block in resp.content:
                if block.type == "tool_use" and block.name == "report_relevant_items":
                    return block.input.get("relevant_indices", [])
        except Exception as exc:
            if attempt == 0:
                logger.warning("pass1 attempt 0 failed (%s), retrying", exc)
                continue
            logger.error("pass1 both attempts failed: %s", exc)
    return []


# ── Pass 2 — per-item, high-cost ──────────────────────────────────────────────

async def screen_pass2(
    items: list[RawItem],
    profiles: list[ProfileSpec],
    cfg: Settings,
) -> tuple[list[ScreenedItem], dict]:
    """2차 스크리닝: 고비용 모델로 영향도 분석.

    강제 규칙:
    - citation.source_id 불일치 → DROP (hallucination 차단).
    - lifecycle_stage 단정 불가 → "unclear" (추측 금지).

    Returns (screened_items, stats).
    """
    if not items:
        return [], {"passed_screen2": 0, "dropped_citation_mismatch": 0, "dropped_not_relevant": 0}

    client = AsyncAnthropic(api_key=cfg.anthropic_api_key)
    concerns_ctx = "\n".join(
        f"- {p.domain}: {', '.join(p.concerns)}"
        for p in profiles
    )
    domain_list = [p.domain for p in profiles]

    screened: list[ScreenedItem] = []
    dropped_citation = 0
    dropped_irrelevant = 0

    for item in items:
        result = await _call_pass2(client, cfg.anthropic_model_impact, item, concerns_ctx, domain_list)
        if result is None:
            dropped_citation += 1
            continue
        if not result.get("relevant", False):
            dropped_irrelevant += 1
            continue

        # Hallucination guard: citation.source_id must match item.source_id
        cit = result.get("citation", {})
        if cit.get("source_id") != item.source_id:
            logger.warning(
                "citation source_id mismatch: expected %r got %r — DROP [%s]",
                item.source_id, cit.get("source_id"), item.title[:60],
            )
            dropped_citation += 1
            continue

        screened.append(
            ScreenedItem(
                source_id=item.source_id,
                title=item.title,
                url=item.url,
                published_at=item.published_at,
                snippet=item.snippet,
                country=item.country,
                domain=result.get("domain") or domain_list[0],
                lifecycle_stage=result.get("lifecycle_stage", "unclear"),
                impact_summary=result.get("impact_summary", ""),
                citation=Citation(
                    source_id=cit["source_id"],
                    quote=cit.get("quote", ""),
                ),
            )
        )

    stats = {
        "total_screen2": len(items),
        "passed_screen2": len(screened),
        "dropped_citation_mismatch": dropped_citation,
        "dropped_not_relevant": dropped_irrelevant,
    }
    logger.info(
        "screen_pass2: %d passed, %d dropped(citation), %d dropped(irrelevant)",
        len(screened), dropped_citation, dropped_irrelevant,
    )
    return screened, stats


async def _call_pass2(
    client: AsyncAnthropic,
    model: str,
    item: RawItem,
    concerns_ctx: str,
    domain_list: list[str],
) -> dict | None:
    """Single-retry wrapper for pass2. Returns None on hard failure."""
    prompt = (
        f"Analyze this regulatory document.\n\n"
        f"Title: {item.title}\n"
        f"Source: {item.source_id} | Country: {item.country}\n"
        f"Published: {item.published_at}\n"
        f"Content: {item.snippet[:800]}\n\n"
        f"Watch domains: {', '.join(domain_list)}\n"
        f"Domain concerns:\n{concerns_ctx}\n\n"
        f"RULES:\n"
        f"- citation.source_id MUST be exactly: \"{item.source_id}\"\n"
        f"- citation.quote must be a direct excerpt from the provided content\n"
        f"- lifecycle_stage: use 'unclear' if you cannot determine it\n"
        f"- If you cannot quote directly, set relevant=false"
    )

    messages: list[dict] = [{"role": "user", "content": prompt}]

    for attempt in range(2):
        try:
            resp = await client.messages.create(
                model=model,
                max_tokens=600,
                tools=[_PASS2_TOOL],
                tool_choice={"type": "tool", "name": "analyze_regulation"},
                messages=messages,
            )
            for block in resp.content:
                if block.type == "tool_use" and block.name == "analyze_regulation":
                    return block.input
        except Exception as exc:
            if attempt == 0:
                logger.warning("pass2 item=%r attempt 0 failed (%s), retrying", item.url[:60], exc)
                # Add clarification on retry
                messages.append({"role": "assistant", "content": resp.content if 'resp' in dir() else ""})
                messages.append({"role": "user", "content": "Please try again with valid JSON following the schema exactly."})
                continue
            logger.error("pass2 item=%r both attempts failed: %s", item.url[:60], exc)

    return None
