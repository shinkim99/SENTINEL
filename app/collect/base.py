from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.models import RawItem


@runtime_checkable
class Collector(Protocol):
    source_id: str

    async def collect(self, keywords: list[str], from_date: str) -> list[RawItem]:
        """수집 실행. 실패 시 빈 리스트 반환(예외 전파 금지 — runner가 처리)."""
        ...
