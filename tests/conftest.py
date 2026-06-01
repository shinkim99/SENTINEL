"""Shared fixtures for SENTINEL tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.models import Citation, ScreenedItem

# Absolute path to repo root (tests/ is one level below)
REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture
def sample_items() -> list[ScreenedItem]:
    """3 screened items covering 2 domains — used as fixture pipeline output."""
    return [
        ScreenedItem(
            source_id="eu-eurlex",
            title="EU Battery Regulation 2023/1542",
            url="https://eurlex.eu/battery-reg",
            published_at="2026-05-28",
            snippet="Recycled content requirements for EV batteries...",
            country="EU",
            domain="secondary_battery",
            lifecycle_stage="proposed",
            impact_summary="12% recycled content mandatory from 2027",
            citation=Citation(source_id="eu-eurlex", quote="12% recycled cobalt"),
            canonical_key="eu-battery-regulation-2023-1542",
            name="EU Battery Regulation (2023/1542)",
            date_text="2026-05-28",
            impact_type="direct",
            alert="urgent",
            confidence="A",
        ),
        ScreenedItem(
            source_id="federal-register",
            title="IRA Section 45X Manufacturing Credit",
            url="https://fr.gov/ira-45x",
            published_at="2025-10-01",
            snippet="Battery cell and module manufacturing credit details...",
            country="US",
            domain="secondary_battery",
            lifecycle_stage="in_force",
            impact_summary="Battery manufacturing tax credit — FEOC restrictions apply",
            citation=Citation(source_id="federal-register", quote="Section 45X credit"),
            canonical_key="ira-section-45x-manufacturing-credit",
            name="IRA Section 45X Manufacturing Credit",
            date_text="2025-10-01",
            impact_type="direct",
            alert="urgent",
            confidence="A",
        ),
        ScreenedItem(
            source_id="law-go-kr",
            title="수소안전관리법 시행령 개정",
            url="https://law.go.kr/hydrogen-safety",
            published_at="2026-04-30",
            snippet="수소 저장 운반 장치 안전 검사 주기 단축...",
            country="KR",
            domain="hydrogen",
            lifecycle_stage="enacted",
            impact_summary="수소 저장 운반 장치 안전 검사 주기 단축 및 기준 강화",
            citation=Citation(source_id="law-go-kr", quote="안전 검사 주기 단축"),
            canonical_key="hydrogen-safety-act-amendment",
            name="수소안전관리법 시행령 개정",
            date_text="2026-04-30",
            impact_type="direct",
            alert="watch",
            confidence="A",
        ),
    ]
