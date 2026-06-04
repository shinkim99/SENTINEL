"""KR 법제처 Open API collector — API key required.

⚠ IP 바인딩 제약: 법제처 OC 키는 발급 시 등록한 IP에서만 유효.
  GitHub Actions 러너는 실행마다 IP가 바뀌므로 접근이 거부될 수 있음.
  증상: HTTP 401 / "등록되지 않은 인증키" 메시지.
  해결: law.go.kr 관리자에게 IP 제한 해제 또는 화이트리스트 요청.

진단 로그: 키 값은 절대 출력하지 않으며 존재 여부·길이만 기록.
"""
from __future__ import annotations

import logging
from typing import Union

from app.collect.http_util import get_with_retry, make_client
from app.models import RawItem

logger = logging.getLogger(__name__)

_BASE = "https://www.law.go.kr/DRF/lawSearch.do"


class LawGoKrCollector:
    source_id = "kr-law-go-kr"

    def __init__(self, api_key: str, verify: Union[bool, str] = True) -> None:
        self._api_key = api_key
        self._verify = verify

    async def collect(self, keywords: list[str], from_date: str) -> list[RawItem]:
        # 키 값 절대 출력 금지 — 존재 여부·길이만 로깅
        logger.info(
            "law_go_kr: 수집 시작 (key_set=%s, key_len=%d, keywords=%d)",
            bool(self._api_key), len(self._api_key), len(keywords),
        )

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
                            "OC": self._api_key,
                            "target": "law",
                            "type": "JSON",
                            "query": kw,
                            "display": 20,
                        },
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

                    # 401 / 비-200: 인증 실패
                    if resp.status_code != 200:
                        logger.warning(
                            "law_go_kr keyword=%r: HTTP %d body_prefix=%r",
                            kw, resp.status_code, resp.text[:300],
                        )
                        resp.raise_for_status()

                    # JSON 파싱
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
                        str(exc) or "(empty — JSON decode 오류 또는 IP 바인딩 거부 의심)",
                        status,
                        body_prefix,
                    )
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

        logger.info("law_go_kr: %d items (keywords=%d)", len(items), len(keywords))
        return items
