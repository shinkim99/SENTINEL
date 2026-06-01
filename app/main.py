from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException

from app.collect.runner import collect_all
from app.config import Settings, get_settings
from app.diff import dedup, diff_and_save
from app.models import DigestResult, ProfileSpec, SourceItem
from app.screen import screen_pass1, screen_pass2
from app.synthesize import build_html

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="SENTINEL reg-watch", version="0.2.0")


def _load_profiles(profiles_dir: Path) -> list[ProfileSpec]:
    profiles: list[ProfileSpec] = []
    for path in sorted(profiles_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        try:
            profiles.append(ProfileSpec.model_validate(raw))
        except Exception as exc:
            raise ValueError(f"Profile validation failed [{path.name}]: {exc}") from exc
    return profiles


def _load_sources(sources_path: Path) -> list[SourceItem]:
    raw = json.loads(sources_path.read_text(encoding="utf-8"))
    return [SourceItem.model_validate(s) for s in raw.get("sources", [])]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/digest/run", response_model=DigestResult)
async def digest_run() -> DigestResult:
    cfg: Settings = get_settings()

    if not cfg.anthropic_api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    # 1. Load profiles
    try:
        profiles = _load_profiles(cfg.profiles_dir)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    logger.info("Loaded %d profiles", len(profiles))

    # 2. Collect
    raw_items, collect_stats = await collect_all(profiles, cfg)
    logger.info("Collected %d items total", collect_stats["total_collected"])

    # 3. Pass 1 — domain/country match (high recall, cheap model)
    pass1_items, pass1_stats = await screen_pass1(raw_items, profiles, cfg)

    # 4. Pass 2 — impact analysis + citation validation (expensive model)
    screened_items, pass2_stats = await screen_pass2(pass1_items, profiles, cfg)

    # 5. Dedup + diff against last state
    deduped = dedup(screened_items)
    new_items, diff_stats = diff_and_save(deduped, cfg.state_dir)

    # 6. Synthesize HTML
    html = build_html(new_items, profiles)

    stats = {**collect_stats, **pass1_stats, **pass2_stats, **diff_stats}
    summary = (
        f"신규 {len(new_items)}건 | "
        f"수집 {collect_stats['total_collected']}건 → "
        f"1차 {pass1_stats['passed_screen1']}건 → "
        f"2차 {pass2_stats['passed_screen2']}건 → "
        f"신규 {diff_stats['new_items']}건"
    )

    logger.info("digest_run complete: %s", summary)
    return DigestResult(html=html, summary=summary, stats=stats)
