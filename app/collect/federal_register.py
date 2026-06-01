"""US Federal Register collector — REST API, no auth required."""
from __future__ import annotations

import logging
from typing import Union

import httpx

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

        async with httpx.AsyncClient(timeout=30, verify=self._verify) as client:
            for kw in keywords:
                try:
                    resp = await client.get(
                        _BASE,
                        params={
                            "conditions[term]": kw,
                            "conditions[publication_date][gte]": from_date,
                            "per_page": 20,
                            "order": "newest",
                            "fields[]": _FIELDS,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    logger.warning("federal_register keyword=%r error: %s", kw, exc)
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
