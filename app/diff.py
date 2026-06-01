"""Dedup + Weekly State diff 모듈."""
from __future__ import annotations

from pathlib import Path


def dedup(items: list[dict]) -> list[dict]:
    """동일 변화 클러스터링 후 중복 제거.

    Args:
        items: 2차 스크리닝 통과 아이템.

    Returns:
        클러스터 대표 아이템만 남긴 목록.
    """
    raise NotImplementedError


def diff_against_state(items: list[dict], state_path: Path) -> list[dict]:
    """Weekly State와 비교하여 신규 변화만 추출.

    Args:
        items: dedup 완료 아이템.
        state_path: 이전 주 스냅샷 JSON 경로.

    Returns:
        이번 주 신규 아이템만.
    """
    raise NotImplementedError


def save_state(items: list[dict], state_path: Path) -> None:
    """발송 완료된 결과를 Weekly State로 저장.

    Args:
        items: 이번 주 최종 아이템.
        state_path: 저장할 스냅샷 경로.
    """
    raise NotImplementedError
