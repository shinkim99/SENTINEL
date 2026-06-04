"""US Federal Register collector — REST API, no auth required.

403 Forbidden 대응:
- 명시적 User-Agent 헤더 (http_util.make_client)
- 지수 백오프 3회 재시도 (http_util.get_with_retry)
- 최종 403 시 "IP 차단 의심" 경고 로그
"""
from __future__ import annotations

import logging
from typing import Union

from app.collect.http_util import get_with_retry, make_client
from app.models import RawItem

logger = logging.getLogger(__name__)

_BASE = "https://www.federalregister.gov/api/v1/documents.json"
_FIELDS = [
    "title", "html_url", "publication_date", "abstract",
    "agencies", "document_number", "type",
]


class FederalRegisterCollector:
    source_id = "us-federal-register"

    def __init__(self, verify: Union[bool, str] = True) -> None:
        self._verify = verify

    async def collect(self, keywords: list[str], from_date: str) -> list[RawItem]:
        items: list[RawItem] = []
        seen_urls: set[str] = set()

        async with make_client(verify=self._verify) as client:
            for kw in keywords:
                resp = None
                try:
                    resp = await get_with_retry(
                        client,
                        _BASE,
                        params={
                            "conditions[term]": kw,
                            "conditions[publication_date][gte]": from_date,
                            "per_page": 20,
                            "order": "newest",
                            "fields[]": _FIELDS,
                        },
                        tag=f"federal_register kw={kw!r}",
                    )
                    if resp.status_code == 403:
                        logger.error(
                            "federal_register: 최종 403 — 러너 IP 차단 의심. "
                            "모든 재시도 소진. keyword=%r", kw,
                        )
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    status = resp.status_code if resp is not None else "N/A"
                    body = resp.text[:200] if resp is not None else ""
                    logger.warning(
                        "federal_register keyword=%r error: type=%s msg=%r "
                        "status=%s body=%r",
                        kw, type(exc).__name__, str(exc) or "(empty)",
                        status, body,
                    )
                    continue

                for doc in data.get("results", []):
                    url = doc.get("html_url", "")
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)

                    agencies = ", ".join(
                        a.get("name", "") for a in doc.get("agencies", [])
                    )
                    snippet = doc.get("abstract") or agencies or ""

                    items.append(
                        RawItem(
                            source_id=self.source_id,
                            title=doc.get("title", "(no title)"),
                            url=url,
                            published_at=doc.get("publication_date", ""),
                            snippet=snippet[:500],
                            country="US",
                            raw=doc,
                        )
                    )

        logger.info("federal_register: %d items (keywords=%d)", len(items), len(keywords))
        return items
