"""공용 HTTP 클라이언트 유틸리티.

모든 collector가 make_client() + get_with_retry()를 사용하여
- 명시적 User-Agent (python-httpx 기본 UA는 다수 서버가 차단)
- 지수 백오프 재시도 (403/429/5xx)
- 명시적 15s timeout
을 보장한다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Union

import httpx

logger = logging.getLogger(__name__)

# 봇 필터를 우회하기 위한 식별 가능한 User-Agent.
# 서버 관리자가 오용이 아님을 확인할 수 있도록 설명 포함.
USER_AGENT = (
    "SENTINEL-RegWatch/1.0 "
    "(regulatory-data-collection; public-sources-only; "
    "contact: regulatory-watch-bot)"
)

_COMMON_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
}


def make_client(
    verify: Union[bool, str] = True,
    timeout: float = 15.0,
    extra_headers: dict | None = None,
) -> httpx.AsyncClient:
    """UA + timeout이 기본 적용된 AsyncClient를 반환한다."""
    headers = {**_COMMON_HEADERS, **(extra_headers or {})}
    return httpx.AsyncClient(headers=headers, timeout=timeout, verify=verify)


async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    extra_headers: dict | None = None,
    follow_redirects: bool = True,
    retries: int = 3,
    backoff_base: float = 2.0,
    tag: str = "",
) -> httpx.Response:
    """GET 요청을 최대 retries 회 재시도.

    - 403: "IP/UA 차단 의심" 경고 후 재시도. 최종 시도에서도 403이면 응답 반환.
    - 429/5xx: 재시도.
    - TimeoutException/ConnectError: 재시도.
    - 최종적으로 예외만 남으면 마지막 예외를 전파.
    """
    label = tag or url[:70]
    last_exc: Exception | None = None

    for attempt in range(1, retries + 1):
        is_last = attempt == retries
        try:
            resp = await client.get(
                url,
                params=params,
                headers=extra_headers or {},
                follow_redirects=follow_redirects,
            )

            if resp.status_code == 403:
                logger.warning(
                    "[%s] 403 Forbidden (시도 %d/%d) — IP/UA 차단 의심. "
                    "UA='%s'",
                    label, attempt, retries,
                    (extra_headers or {}).get("User-Agent") or client.headers.get("user-agent", "?"),
                )
                if not is_last:
                    await asyncio.sleep(backoff_base ** (attempt - 1))
                    continue
                return resp

            if resp.status_code in (429, 500, 502, 503, 504):
                logger.warning(
                    "[%s] HTTP %d (시도 %d/%d), %.0fs 후 재시도",
                    label, resp.status_code, attempt, retries,
                    backoff_base ** (attempt - 1),
                )
                if not is_last:
                    await asyncio.sleep(backoff_base ** (attempt - 1))
                    continue
                return resp

            return resp

        except (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
        ) as exc:
            last_exc = exc
            logger.warning(
                "[%s] %s (시도 %d/%d)%s",
                label, type(exc).__name__, attempt, retries,
                "" if is_last else f" → {backoff_base ** (attempt - 1):.0f}s 후 재시도",
            )
            if not is_last:
                await asyncio.sleep(backoff_base ** (attempt - 1))

    if last_exc:
        raise last_exc
    raise RuntimeError(f"get_with_retry: unexpected exit [{label}]")
