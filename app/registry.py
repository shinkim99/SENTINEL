"""규제 레지스트리 — regulation_id 기반 영속 상태 관리 (diff.py 승격).

기준선은 data/state/registry.json — 발송 승인 시점에만 갱신된다.
/digest/run 에서는 pending/ 에 proposed 상태를 저장하고,
/digest/{id}/approve 에서 commit_registry() 를 호출한다.
"""
from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from app.models import HistoryEntry, Regulation, ScreenedItem

logger = logging.getLogger(__name__)

_REGISTRY_FILE = "registry.json"
_PENDING_SUFFIX = ".registry.json"


# ── slug helper ───────────────────────────────────────────────────────────────

def _canonical_key(text: str) -> str:
    """title → normalized slug (max 60 chars). Used as fallback when LLM omits canonical_key."""
    key = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    key = re.sub(r"\s+", "-", key.strip())
    key = re.sub(r"-+", "-", key)
    return key[:60].rstrip("-")


# ── load / save ───────────────────────────────────────────────────────────────

def load_registry(state_dir: Path) -> dict[str, Regulation]:
    """Load registry.json → {regulation_id: Regulation}. Empty dict if absent."""
    path = state_dir / _REGISTRY_FILE
    if not path.exists():
        logger.info("registry: no existing %s — starting fresh", path)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        registry: dict[str, Regulation] = {}
        for item in data.get("regulations", []):
            try:
                reg = Regulation.model_validate(item)
                registry[reg.regulation_id] = reg
            except Exception as exc:
                logger.warning("registry: skipping invalid entry %r: %s", item.get("regulation_id"), exc)
        logger.info("registry: loaded %d regulations", len(registry))
        return registry
    except Exception as exc:
        logger.error("registry: load failed (%s) — starting fresh", exc)
        return {}


def save_pending_registry(
    registry: dict[str, Regulation],
    digest_id: str,
    state_dir: Path,
) -> None:
    """Save proposed registry snapshot to pending/ — NOT the committed baseline."""
    pending_dir = state_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    path = pending_dir / f"{digest_id}{_PENDING_SUFFIX}"
    payload = {
        "digest_id": digest_id,
        "saved_at": datetime.now().isoformat(),
        "regulations": [r.model_dump() for r in registry.values()],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("registry: pending saved → %s (%d regs)", path, len(registry))


def load_pending_registry(digest_id: str, state_dir: Path) -> dict[str, Regulation]:
    """Load pending registry snapshot for the given digest_id. Raises KeyError if absent."""
    path = state_dir / "pending" / f"{digest_id}{_PENDING_SUFFIX}"
    if not path.exists():
        raise KeyError(f"pending registry not found: {digest_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    registry: dict[str, Regulation] = {}
    for item in data.get("regulations", []):
        reg = Regulation.model_validate(item)
        registry[reg.regulation_id] = reg
    logger.info("registry: loaded pending %s (%d regs)", digest_id, len(registry))
    return registry


def commit_registry(
    registry: dict[str, Regulation],
    digest_id: str,
    state_dir: Path,
) -> None:
    """Commit registry to disk. ONLY called on approve or auto_send.

    Writes:
    - state/registry.json  — new diff baseline
    - state/sent/{digest_id}.json — audit log
    """
    state_dir.mkdir(parents=True, exist_ok=True)

    reg_path = state_dir / _REGISTRY_FILE
    payload = {
        "committed_at": datetime.now().isoformat(),
        "digest_id": digest_id,
        "regulations": [r.model_dump() for r in registry.values()],
    }
    reg_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    sent_dir = state_dir / "sent"
    sent_dir.mkdir(parents=True, exist_ok=True)
    changed = [r.model_dump() for r in registry.values() if r.changed_this_week]
    audit = {
        "digest_id": digest_id,
        "committed_at": datetime.now().isoformat(),
        "total_regulations": len(registry),
        "changed_this_week": len(changed),
        "changed_items": changed,
    }
    (sent_dir / f"{digest_id}.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(
        "registry: committed → %s (%d total, %d changed)",
        reg_path, len(registry), len(changed),
    )


# ── core logic ────────────────────────────────────────────────────────────────

def apply_screened_items(
    items: list[ScreenedItem],
    registry: dict[str, Regulation],
    checked_at: str,
) -> tuple[dict[str, Regulation], list[str]]:
    """Apply screened items onto a deep copy of the registry.

    Matching key: canonical_key + country (= regulation_id).

    Rules:
    - All existing entries → changed_this_week = False (reset).
    - New entry → changed_this_week = True, history entry "신규 등록".
    - Existing, state changed (lifecycle/summary/date_text) → changed_this_week = True,
      history entry with diff description.
    - Existing, no change → changed_this_week = False, only checked_at updated.

    Returns (updated_registry_copy, changed_regulation_ids).
    Does NOT write to disk.
    """
    reg_copy: dict[str, Regulation] = deepcopy(registry)

    for reg in reg_copy.values():
        reg.changed_this_week = False

    changed_ids: list[str] = []

    for item in items:
        ck = item.canonical_key or _canonical_key(item.title)
        reg_id = f"{ck}_{item.country}"
        existing = reg_copy.get(reg_id)

        if existing is None:
            new_reg = Regulation(
                regulation_id=reg_id,
                domain=item.domain,
                country=item.country,
                name=item.name or item.title,
                summary=item.impact_summary,
                lifecycle_stage=item.lifecycle_stage,
                date_text=item.date_text or item.published_at,
                rd_impact=item.impact_summary,
                impact_type=item.impact_type,
                alert=item.alert,
                source=item.source_id,
                source_url=item.url,
                confidence=item.confidence,
                checked_at=checked_at,
                changed_this_week=True,
                citation_quote=item.citation.quote if item.citation else "",
                history=[
                    HistoryEntry(
                        date=item.published_at,
                        stage=item.lifecycle_stage,
                        note="신규 등록",
                        source=item.source_id,
                    )
                ],
            )
            reg_copy[reg_id] = new_reg
            changed_ids.append(reg_id)
            logger.info("registry: NEW %s [%s/%s]", reg_id, item.country, item.lifecycle_stage)

        else:
            changed = False
            note_parts: list[str] = []

            if existing.lifecycle_stage != item.lifecycle_stage:
                note_parts.append(f"{existing.lifecycle_stage} → {item.lifecycle_stage}")
                existing.lifecycle_stage = item.lifecycle_stage
                changed = True

            if item.impact_summary and existing.summary != item.impact_summary:
                existing.summary = item.impact_summary
                existing.rd_impact = item.impact_summary
                if not note_parts:
                    note_parts.append("내용 업데이트")
                changed = True

            if item.date_text and existing.date_text != item.date_text:
                existing.date_text = item.date_text
                if not note_parts:
                    note_parts.append("날짜 업데이트")
                changed = True

            existing.checked_at = checked_at

            if changed:
                existing.history.append(
                    HistoryEntry(
                        date=item.published_at,
                        stage=item.lifecycle_stage,
                        note=", ".join(note_parts),
                        source=item.source_id,
                    )
                )
                existing.changed_this_week = True
                existing.citation_quote = item.citation.quote if item.citation else ""
                changed_ids.append(reg_id)
                logger.info("registry: CHANGED %s: %s", reg_id, ", ".join(note_parts))

    logger.info(
        "registry: applied %d items → %d changed / %d total (not yet committed)",
        len(items), len(changed_ids), len(reg_copy),
    )
    return reg_copy, changed_ids


def get_changed_items(registry: dict[str, Regulation]) -> list[Regulation]:
    """Return all regulations with changed_this_week=True, sorted by domain then country."""
    items = [r for r in registry.values() if r.changed_this_week]
    items.sort(key=lambda r: (r.domain, r.country))
    return items


def dedup_screened(items: list[ScreenedItem]) -> list[ScreenedItem]:
    """URL-based dedup before passing to apply_screened_items."""
    seen: set[str] = set()
    out: list[ScreenedItem] = []
    for item in items:
        key = item.url.rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    removed = len(items) - len(out)
    if removed:
        logger.info("dedup: removed %d duplicate URLs", removed)
    return out
