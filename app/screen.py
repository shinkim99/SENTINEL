"""스크리닝 파이프라인 — 1차(저비용) + 2차(고비용) 단계."""
from __future__ import annotations

from app.models import ProfileSpec, SourceItem


async def screen_stage1(
    items: list[SourceItem],
    profile: ProfileSpec,
) -> list[SourceItem]:
    """1차 스크리닝: 저비용 모델로 도메인·국가 매칭만 (high recall).

    Args:
        items: 수집된 원시 아이템 목록.
        profile: 해당 도메인 프로파일.

    Returns:
        도메인·국가 조건을 통과한 아이템.
    """
    raise NotImplementedError


async def screen_stage2(
    items: list[SourceItem],
    profile: ProfileSpec,
) -> list[dict]:
    """2차 스크리닝: 고비용 모델로 영향도 분석.

    강제 규칙:
    - lifecycle_stage 필수 분류.
    - 원문 citation 없으면 drop (hallucination 차단).

    Args:
        items: 1차 통과 아이템.
        profile: 해당 도메인 프로파일.

    Returns:
        lifecycle_stage·impact_score가 부여된 분석 결과 목록.
    """
    raise NotImplementedError
