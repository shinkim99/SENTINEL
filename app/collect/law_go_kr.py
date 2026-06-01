"""KR 법제처 Open API collector — API key required."""
from __future__ import annotations

import logging
from typing import Union

import httpx

from app.models import RawItem

logger = logging.getLogger(__name__)

_BASE = "https://www.law.go.kr/DRF/lawSearch.do"


class LawGoKrCollector:
    source_id = "kr-law-go-kr"

    def __init__(self, api_key: str, verify: Union[bool, str] = True) -> None:
        self._api_key = api_key
        self._verify = verify

    async def collect(self, keywords: list[str], from_date: str) -> list[RawItem]:
        items: list[RawItem] = []
        seen_urls: set[str] = set()

        async with httpx.AsyncClient(timeout=30, verify=self._verify) as client:
            for kw in keywords:
                try:
                    resp = await client.get(
                        _BASE,
                        params={
                            "OC": self._api_key,
                            "target": "law",
                            "type": "JSON",
                            "query": kw,
                            "display": 20,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    logger.warning("law_go_kr keyword=%r error: %s", kw, exc)
                    continue

                for law in data.get("LawSearch", {}).get("law", []):
                    law_id = law.get("법령ID") or law.get("법령id", "")
                    name_kr = law.get("법령명한글", "")
                    url = f"https://www.law.go.kr/법령/{name_kr}({law_id})"
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    items.append(
                        RawItem(
                            source_id=self.source_id,
                            title=name_kr or "(제목없음)",
                            url=url,
                            published_at=law.get("공포일자", ""),
                            snippet=law.get("법령약칭명", "") or name_kr,
                            country="KR",
                            raw=law,
                        )
                    )

        logger.info("law_go_kr: %d items", len(items))
        return items
