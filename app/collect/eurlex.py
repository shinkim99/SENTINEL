"""EU EUR-Lex collector — Cellar SPARQL 엔드포인트 사용.

이전 방식(daily-view HTML 스크래핑)은 GitHub Actions 러너에서 403 차단.
→ Publications Office의 공식 RDF/SPARQL API(Cellar)로 교체.

Cellar SPARQL 엔드포인트:
  https://publications.europa.eu/webapi/rdf/sparql
  Accept: application/sparql-results+json

전략:
- 키워드별로 SPARQL CONTAINS 쿼리 전송(영문 제목 기준).
- 결과 work URI에서 CELEX 번호를 추출하여 EUR-Lex 표준 URL 생성.
- 병렬 처리: asyncio.Semaphore(3)으로 동시 요청 수 제한.
- 403/timeout: "IP 차단 의심" 또는 "접근 불가" 명시 로깅.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Union

from app.collect.http_util import get_with_retry, make_client
from app.models import RawItem

logger = logging.getLogger(__name__)

_SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
_SPARQL_ACCEPT = "application/sparql-results+json"

# SPARQL: 최근 EU 법령 중 영문 제목에 키워드가 포함된 항목
# cdm:expression_title 은 언어 태그 없는 literal — LCASE(str()) 로 안전하게 비교
_SPARQL_TEMPLATE = """\
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT DISTINCT ?work ?title ?date WHERE {{
  ?expr cdm:expression_belongs_to_work ?work ;
        cdm:expression_uses_language
          <http://publications.europa.eu/resource/authority/language/ENG> ;
        cdm:expression_title ?title .
  ?work cdm:work_date_document ?date .
  FILTER(str(?date) >= "{from_date}")
  FILTER(CONTAINS(LCASE(str(?title)), "{keyword}"))
}}
ORDER BY DESC(?date)
LIMIT 20
"""

# Cellar work URI 패턴: http://publications.europa.eu/resource/celex/32023R1542
_CELEX_RE = re.compile(r"/celex/([A-Z0-9]+)$")


def _build_url(work_uri: str) -> str:
    """Cellar work URI → EUR-Lex TXT URL."""
    m = _CELEX_RE.search(work_uri)
    if m:
        return f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{m.group(1)}"
    # CELEX가 아닌 URI(ELI 등)는 work URI 그대로 사용
    return work_uri


def _safe_keyword(kw: str) -> str:
    """SPARQL CONTAINS에 삽입할 안전한 소문자 키워드. 따옴표/백슬래시 제거."""
    return kw.lower().replace('"', "").replace("\\", "").strip()


async def _sparql_query(
    client,
    keyword: str,
    from_date: str,
    sem: asyncio.Semaphore,
) -> list[dict]:
    """단일 키워드 SPARQL 쿼리 → binding 목록 반환."""
    kw_safe = _safe_keyword(keyword)
    if not kw_safe:
        return []

    query = _SPARQL_TEMPLATE.format(from_date=from_date, keyword=kw_safe)
    async with sem:
        try:
            resp = await get_with_retry(
                client,
                _SPARQL_ENDPOINT,
                params={"query": query, "format": "application/sparql-results+json"},
                extra_headers={"Accept": _SPARQL_ACCEPT},
                tag=f"cellar kw={keyword!r}",
            )
        except Exception as exc:
            logger.warning("eurlex SPARQL kw=%r network error: %s", keyword, exc)
            return []

        if resp.status_code == 403:
            logger.error(
                "eurlex SPARQL: 최종 403 — Cellar 엔드포인트 IP 차단 의심. kw=%r", keyword,
            )
            return []
        if resp.status_code != 200:
            logger.warning(
                "eurlex SPARQL kw=%r HTTP %d: %s",
                keyword, resp.status_code, resp.text[:200],
            )
            return []

        try:
            data = resp.json()
        except Exception as exc:
            logger.warning("eurlex SPARQL kw=%r JSON 파싱 오류: %s body=%r",
                           keyword, exc, resp.text[:200])
            return []

        return data.get("results", {}).get("bindings", [])


class EurLexCollector:
    source_id = "eu-eurlex"

    def __init__(self, verify: Union[bool, str] = True) -> None:
        self._verify = verify

    async def collect(self, keywords: list[str], from_date: str) -> list[RawItem]:
        """Cellar SPARQL로 키워드별 최근 EU 법령을 수집한다."""
        sem = asyncio.Semaphore(3)

        async with make_client(
            verify=self._verify,
            extra_headers={"Accept": _SPARQL_ACCEPT},
        ) as client:
            tasks = [
                _sparql_query(client, kw, from_date, sem)
                for kw in keywords
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        seen_uris: set[str] = set()
        items: list[RawItem] = []

        for kw, result in zip(keywords, results):
            if isinstance(result, Exception):
                logger.warning("eurlex kw=%r gather error: %s", kw, result)
                continue

            for binding in result:
                work_uri: str = binding.get("work", {}).get("value", "")
                if not work_uri or work_uri in seen_uris:
                    continue
                seen_uris.add(work_uri)

                title: str = binding.get("title", {}).get("value", "(no title)")
                date_val: str = binding.get("date", {}).get("value", "")
                # date_val 예: "2026-05-28" 또는 "2026-05-28T00:00:00"
                published_at = date_val[:10] if date_val else ""
                url = _build_url(work_uri)

                items.append(
                    RawItem(
                        source_id=self.source_id,
                        title=title,
                        url=url,
                        published_at=published_at,
                        snippet=title[:500],   # 전문 fetch 불필요 — 제목으로 1차 스크리닝
                        country="EU",
                        raw={"work_uri": work_uri, "date": date_val, "kw": kw},
                    )
                )

        logger.info(
            "eurlex: %d items (keywords=%d, unique works=%d)",
            len(items), len(keywords), len(seen_uris),
        )
        return items
