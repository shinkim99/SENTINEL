"""HTML 다이제스트 합성 모듈."""
from __future__ import annotations

from app.models import DigestResult, ProfileSpec


def synthesize(
    screened_items: list[dict],
    profiles: list[ProfileSpec],
) -> DigestResult:
    """도메인 → 국가 → 영향도 순으로 HTML 다이제스트 생성.

    출력 형식:
    - markdown 금지, HTML 컴포넌트 직접 생성.
    - metric card, lifecycle 배지, 국가 비교 테이블 포함.

    Args:
        screened_items: diff 통과한 최종 아이템.
        profiles: 전체 도메인 프로파일 목록.

    Returns:
        html·summary·stats가 채워진 DigestResult.
    """
    raise NotImplementedError
