"""Dedup + Weekly State diff 모듈.

기준선(baseline)은 data/state/sent/last.json — 발송이 확정된 시점에만 갱신된다.
생성(/digest/run) 단계에서는 기준선을 건드리지 않는다.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from app.models import ScreenedItem

logger = logging.getLogger(__name__)


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


def diff_against_sent(
    items: list[ScreenedItem],
    state_dir: Path,
) -> tuple[list[ScreenedItem], dict]:
    """발송 기준선(sent/last.json)과 비교 → 신규 항목만 반환.

    기준선을 수정하지 않는다. 기준선 갱신은 commit_sent_state()에서만 수행.
    sent 기록이 없으면 전부 신규로 처리.
    """
    last_path = state_dir / "sent" / "last.json"

    prev_urls: set[str] = set()
    if last_path.exists():
        try:
            prev_state = json.loads(last_path.read_text(encoding="utf-8"))
            prev_urls = {_norm_url(it["url"]) for it in prev_state.get("items", [])}
            logger.info("diff: loaded %d URLs from sent baseline", len(prev_urls))
        except Exception as exc:
            logger.warning("diff: could not load sent baseline (%s) — treating all as new", exc)

    if not prev_urls:
        new_items = items
    else:
        new_items = [it for it in items if _norm_url(it.url) not in prev_urls]

    logger.info("diff: %d new / %d total (baseline unchanged)", len(new_items), len(items))

    stats = {
        "total_after_dedup": len(items),
        "prev_state_urls": len(prev_urls),
        "new_items": len(new_items),
    }
    return new_items, stats


def commit_sent_state(
    items: list[ScreenedItem],
    digest_id: str,
    state_dir: Path,
) -> None:
    """발송 확정 시 기준선을 갱신하고 발송 이력을 저장.

    sent/last.json — 다음 실행의 diff 기준선
    sent/{digest_id}.json — 발송 이력 (감사 로그)
    """
    sent_dir = state_dir / "sent"
    sent_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "digest_id": digest_id,
        "committed_at": datetime.now().isoformat(),
        "items": [
            {"url": it.url, "title": it.title, "source_id": it.source_id}
            for it in items
        ],
    }

    last_path = sent_dir / "last.json"
    last_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    history_path = sent_dir / f"{digest_id}.json"
    history_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "commit_sent_state: baseline updated → %s (%d items)", last_path, len(items)
    )
