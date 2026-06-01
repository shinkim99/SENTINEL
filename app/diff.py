"""Dedup + Weekly State diff 모듈."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from app.models import ScreenedItem

logger = logging.getLogger(__name__)

_STATE_FILE = "last_digest.json"


def _norm_url(url: str) -> str:
    """Strip query string and trailing slash for dedup comparison."""
    url = re.sub(r"\?.*$", "", url)
    return url.rstrip("/").lower()


def dedup(items: list[ScreenedItem]) -> list[ScreenedItem]:
    """URL 기준 중복 제거 (같은 URL = 같은 문서)."""
    seen: set[str] = set()
    out: list[ScreenedItem] = []
    for item in items:
        key = _norm_url(item.url)
        if key not in seen:
            seen.add(key)
            out.append(item)
    removed = len(items) - len(out)
    if removed:
        logger.info("dedup: removed %d duplicates", removed)
    return out


def diff_and_save(
    items: list[ScreenedItem],
    state_dir: Path,
) -> tuple[list[ScreenedItem], dict]:
    """Weekly State와 비교 → 신규만 반환, 현재 결과를 state에 저장.

    Returns (new_items, diff_stats).
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / _STATE_FILE

    prev_urls: set[str] = set()
    if state_path.exists():
        try:
            prev_state = json.loads(state_path.read_text(encoding="utf-8"))
            prev_urls = {_norm_url(it["url"]) for it in prev_state.get("items", [])}
            logger.info("diff: loaded %d URLs from previous state", len(prev_urls))
        except Exception as exc:
            logger.warning("diff: could not load state (%s) — treating all as new", exc)

    if not prev_urls:
        new_items = items
    else:
        new_items = [it for it in items if _norm_url(it.url) not in prev_urls]

    logger.info("diff: %d new / %d total", len(new_items), len(items))

    # Save current full result as next week's baseline
    _write_state(items, state_path)

    stats = {
        "total_after_dedup": len(items),
        "prev_state_urls": len(prev_urls),
        "new_items": len(new_items),
    }
    return new_items, stats


def _write_state(items: list[ScreenedItem], state_path: Path) -> None:
    payload = {
        "generated_at": datetime.now().isoformat(),
        "items": [
            {"url": it.url, "title": it.title, "source_id": it.source_id}
            for it in items
        ],
    }
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("diff: state saved → %s (%d items)", state_path, len(items))
