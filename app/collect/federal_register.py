"""US Federal Register collector — REST API, no auth required.

빠른 실패 전략: 키워드 루프 진입 전 단회 probe 요청으로 접근 가능성 판정.
  - probe 실패(403 / Timeout / ConnectError) → 전체 키워드 즉시 스킵, ~8초 내 종료.
  - probe 성공(HTTP 응답 수신) → 정상 전체 수집 진행.
  eurlex / KR inbox 로드 / 스크리닝 / 발송은 영향 없이 계속 진행.
"""
from __future__ import annotations

import logging
from typing import Union

import httpx

from app.collect.http_util import get_with_retry, make_client
from app.models import RawItem

logger = logging.getLogger(__name__)

_BASE = "https://www.federalregister.gov/api/v1/documents.json"
_HOST = "www.federalregister.gov"
_FIELDS = [
    "title", "html_url", "publication_date", "abstract",
    "agencies", "document_number", "type",
]

_PROBE_TIMEOUT = 8.0  # probe 전용 — 8s 이내 응답 없으면 차단으로 판정


class FederalRegisterCollector:
    source_id = "us-federal-register"

    def __init__(self, verify: Union[bool, str] = True) -> None:
        self._verify = verify

    async def _probe(self, client: httpx.AsyncClient, sample_kw: str) -> bool:
        """단회 probe — 네트워크 연결 가능 여부만 판정.

        HTTP 응답 수신(상태코드 무관, 단 403 제외) → True (연결됨).
        403 / Timeout / ConnectError 등 정상 응답 미수신 → False (IP 차단 의심).
        재시도 없음: 차단 환경에서 8초 이내 판정.
        """
        try:
            resp = await client.get(
                _BASE,
                params={
                    "conditions[term]": sample_kw,
                    "per_page": 1,
                    "fields[]": ["title"],
                },
                follow_redirects=True,
                timeout=_PROBE_TIMEOUT,
            )
            if resp.status_code == 403:
                logger.warning(
                    "federal_register probe: 403 Forbidden — 러너 IP 차단 의심 (host=%s)",
                    _HOST,
                )
                return False
            return True  # 200/4xx/5xx 모두 "연결됨"으로 판정
        except httpx.TimeoutException as exc:
            logger.warning(
                "federal_register probe: %s — host=%s, %ds 이내 응답 없음 (IP 차단 의심)",
                type(exc).__name__, _HOST, int(_PROBE_TIMEOUT),
            )
            return False
        except httpx.ConnectError as exc:
            logger.warning(
                "federal_register probe: ConnectError — host=%s, 연결 거부/리셋: %s",
                _HOST, type(exc).__name__,
            )
            return False
        except Exception as exc:
            # RemoteProtocolError 등 — HTTP 레벨까지는 도달한 것으로 간주
            logger.warning(
                "federal_register probe: 예외 %s — 연결 성립으로 간주, 수집 진행",
                type(exc).__name__,
            )
            return True

    async def collect(self, keywords: list[str], from_date: str) -> list[RawItem]:
        if not keywords:
            return []

        async with make_client(verify=self._verify) as client:

            # ── Probe: 단회 연결 판정 ─────────────────────────────────────
            logger.info(
                "federal_register: probe — host=%s, timeout=%.0fs, retries=1 (단회)",
                _HOST, _PROBE_TIMEOUT,
            )
            if not await self._probe(client, keywords[0]):
                logger.error(
                    "federal_register: probe 실패 — IP 차단 또는 접근 불가 (host=%s). "
                    "전체 키워드 %d개 스킵. eurlex/KR inbox 는 영향 없음.",
                    _HOST, len(keywords),
                )
                return []

            logger.info(
                "federal_register: probe 성공 → 전체 키워드 %d개 수집 시작", len(keywords),
            )

            # ── 전체 키워드 수집 ──────────────────────────────────────────
            items: list[RawItem] = []
            seen_urls: set[str] = set()

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
