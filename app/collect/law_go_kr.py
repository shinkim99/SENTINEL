"""KR 법제처 Open API collector — API key required.

⚠ IP 바인딩 제약: 법제처 OC 키는 발급 시 등록한 IP에서만 유효.
  GitHub Actions 러너는 실행마다 IP가 바뀌므로 접근이 거부될 수 있음.
  증상: ConnectTimeout — TCP handshake 자체가 성립되지 않음 (해외 IP 차단).
  해결: law.go.kr 관리자에게 IP 제한 해제 또는 화이트리스트 요청.

빠른 실패 전략: 키워드 루프 진입 전 단회 probe 요청으로 접근 가능성 판정.
  - probe 실패(Timeout/ConnectError) → 전체 키워드 즉시 스킵, ~8초 내 종료.
  - probe 성공(HTTP 응답 수신) → 정상 전체 수집 진행.
  circuit_breaker(순차 카운터) 방식은 get_with_retry가 base class TimeoutException으로
  재전파할 때 타입 미매칭으로 카운터가 리셋되는 버그가 있어 probe로 교체.

진단 로그: 키 값은 절대 출력하지 않으며 존재 여부·길이만 기록.
"""
from __future__ import annotations

import logging
from typing import Union

import httpx

from app.collect.http_util import get_with_retry, make_client
from app.models import RawItem

logger = logging.getLogger(__name__)

_BASE = "https://www.law.go.kr/DRF/lawSearch.do"
_HOST = "www.law.go.kr"

_TIMEOUT = 8.0  # probe + per-keyword timeout (해외 IP 차단 시 빠른 실패)
_RETRIES = 2    # per-keyword retries (probe는 재시도 없이 1회)


class LawGoKrCollector:
    source_id = "kr-law-go-kr"

    def __init__(self, api_key: str, verify: Union[bool, str] = True) -> None:
        self._api_key = api_key
        self._verify = verify

    async def _probe(self, client: httpx.AsyncClient, sample_kw: str) -> bool:
        """단회 probe — 네트워크 연결 가능 여부만 판정.

        HTTP 응답 수신(상태코드 무관) → True (연결됨).
        Timeout / ConnectError 등 응답 미수신 → False (IP 차단 의심).
        재시도 없음: 차단 환경에서 8초 이내 판정.
        """
        try:
            await client.get(
                _BASE,
                params={
                    "OC": self._api_key,
                    "target": "law",
                    "type": "JSON",
                    "query": sample_kw,
                    "display": 1,
                },
                follow_redirects=True,
            )
            return True  # 어떤 HTTP 응답이든 수신 → 연결 성공
        except httpx.TimeoutException as exc:
            # ConnectTimeout / ReadTimeout / WriteTimeout / PoolTimeout 모두 포함
            timeout_kind = type(exc).__name__
            logger.warning(
                "law_go_kr probe: %s — host=%s, 8s 이내 응답 없음 (해외 IP 차단 의심)",
                timeout_kind, _HOST,
            )
            return False
        except httpx.ConnectError as exc:
            logger.warning(
                "law_go_kr probe: ConnectError — host=%s, 연결 거부/리셋 (IP 차단 의심): %s",
                _HOST, type(exc).__name__,
            )
            return False
        except Exception as exc:
            # RemoteProtocolError, JSONDecodeError 등 — HTTP 레벨까지는 도달
            logger.warning(
                "law_go_kr probe: 예외 %s — 연결은 성립된 것으로 간주, 수집 진행",
                type(exc).__name__,
            )
            return True

    async def collect(self, keywords: list[str], from_date: str) -> list[RawItem]:
        # 키 값 절대 출력 금지 — 존재 여부·길이만 로깅
        logger.info(
            "law_go_kr: 수집 시작 — host=%s, key_set=%s, key_len=%d, "
            "keywords=%d, timeout=%.0fs, retries=%d",
            _HOST, bool(self._api_key), len(self._api_key),
            len(keywords), _TIMEOUT, _RETRIES,
        )

        if not keywords:
            return []

        async with make_client(verify=self._verify, timeout=_TIMEOUT) as client:

            # ── Probe: 단회 연결 판정 ──────────────────────────────────────
            logger.info(
                "law_go_kr: probe 시작 — host=%s, timeout=%.0fs, retries=1 (단회)",
                _HOST, _TIMEOUT,
            )
            if not await self._probe(client, keywords[0]):
                logger.error(
                    "law_go_kr: probe 실패 — 해외 IP 차단 의심 (host=%s). "
                    "전체 키워드 %d개 스킵. federal_register/eurlex 수집은 영향 없음.",
                    _HOST, len(keywords),
                )
                return []

            logger.info(
                "law_go_kr: probe 성공 → 전체 키워드 %d개 수집 시작",
                len(keywords),
            )

            # ── 전체 키워드 수집 ───────────────────────────────────────────
            items: list[RawItem] = []
            seen_urls: set[str] = set()

            for kw in keywords:
                resp = None
                try:
                    resp = await get_with_retry(
                        client,
                        _BASE,
                        params={
                            "OC": self._api_key,
                            "target": "law",
                            "type": "JSON",
                            "query": kw,
                            "display": 20,
                        },
                        retries=_RETRIES,
                        tag=f"law_go_kr kw={kw!r}",
                    )

                    # 403 = IP 차단 또는 인증 거부
                    if resp.status_code == 403:
                        logger.error(
                            "law_go_kr keyword=%r: 403 — OC 키 IP 바인딩 거부 의심. "
                            "body_prefix=%r (키 값 미포함)",
                            kw, resp.text[:200],
                        )
                        continue

                    if resp.status_code != 200:
                        logger.warning(
                            "law_go_kr keyword=%r: HTTP %d body_prefix=%r",
                            kw, resp.status_code, resp.text[:300],
                        )
                        resp.raise_for_status()

                    content_type = resp.headers.get("content-type", "")
                    if "json" not in content_type and "text" not in content_type:
                        logger.warning(
                            "law_go_kr keyword=%r: 예상 외 content-type=%r body=%r",
                            kw, content_type, resp.text[:200],
                        )

                    data = resp.json()

                except Exception as exc:
                    status = resp.status_code if resp is not None else "N/A"
                    body_prefix = resp.text[:300] if resp is not None else "(no response)"
                    logger.warning(
                        "law_go_kr keyword=%r: type=%s msg=%r status=%s body=%r",
                        kw,
                        type(exc).__name__,
                        str(exc) or "(empty)",
                        status,
                        body_prefix,
                    )
                    continue

                for law in data.get("LawSearch", {}).get("law", []):
                    law_id = law.get("법령ID") or law.get("법령id", "")
                    name_kr = law.get("법령명한글", "")
                    url = f"https://www.law.go.kr/lsInfoP.do?lsId={law_id}&ancYnChk=0"
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

        logger.info("law_go_kr: 수집 완료 — %d items (keywords=%d)", len(items), len(keywords))
        return items
