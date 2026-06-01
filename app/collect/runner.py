"""Parallel collection runner — tier1 sources, asyncio gather."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.collect.eurlex import EurLexCollector
from app.collect.federal_register import FederalRegisterCollector
from app.collect.law_go_kr import LawGoKrCollector
from app.config import Settings
from app.models import ProfileSpec, RawItem

logger = logging.getLogger(__name__)


async def collect_all(
    profiles: list[ProfileSpec],
    cfg: Settings,
) -> tuple[list[RawItem], dict]:
    """Run all enabled tier-1 collectors in parallel.

    Returns (deduplicated_items, stats_dict).
    """
    from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    verify = cfg.http_verify

    all_keywords: list[str] = list({kw for p in profiles for kw in p.keywords})

    collectors: list = [
        FederalRegisterCollector(verify=verify),
        EurLexCollector(verify=verify),
    ]

    if cfg.law_go_kr_api_key:
        collectors.append(LawGoKrCollector(api_key=cfg.law_go_kr_api_key, verify=verify))
    else:
        logger.warning("LAW_GO_KR_API_KEY not set — KR source skipped")

    results = await asyncio.gather(
        *[c.collect(all_keywords, from_date) for c in collectors],
        return_exceptions=True,
    )

    all_items: list[RawItem] = []
    stats: dict = {"by_source": {}, "collection_failures": []}

    for collector, result in zip(collectors, results):
        sid = collector.source_id
        if isinstance(result, Exception):
            logger.error("collector %s failed: %s", sid, result)
            stats["by_source"][sid] = {"count": 0, "status": "failure", "error": str(result)}
            stats["collection_failures"].append(sid)
        elif len(result) == 0:
            logger.warning("collector %s returned 0 items — collection failure", sid)
            stats["by_source"][sid] = {"count": 0, "status": "empty"}
            stats["collection_failures"].append(sid)
        else:
            stats["by_source"][sid] = {"count": len(result), "status": "ok"}
            all_items.extend(result)

    seen: set[str] = set()
    deduped: list[RawItem] = []
    for item in all_items:
        if item.url not in seen:
            seen.add(item.url)
            deduped.append(item)

    stats["total_collected"] = len(deduped)
    return deduped, stats
