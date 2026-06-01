from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict


class LifecycleStage(str, Enum):
    proposed = "proposed"
    enacted = "enacted"
    in_force = "in_force"
    amended = "amended"
    repealed = "repealed"


LIFECYCLE_LABELS_KO: dict[LifecycleStage, str] = {
    LifecycleStage.proposed: "입법예고/제안",
    LifecycleStage.enacted: "공포",
    LifecycleStage.in_force: "시행",
    LifecycleStage.amended: "개정",
    LifecycleStage.repealed: "폐지",
}

LIFECYCLE_COLORS: dict[str, tuple[str, str]] = {
    "proposed":  ("#DBEAFE", "#1E40AF"),
    "enacted":   ("#FEF3C7", "#92400E"),
    "in_force":  ("#D1FAE5", "#065F46"),
    "amended":   ("#EDE9FE", "#5B21B6"),
    "repealed":  ("#FEE2E2", "#991B1B"),
    "unclear":   ("#F3F4F6", "#374151"),
}

DOMAIN_LABELS_KO: dict[str, str] = {
    "secondary_battery": "2차전지",
    "green_eco":         "친환경",
    "hydrogen":          "수소",
    "space_environment": "우주환경",
}

COUNTRY_LABELS_KO: dict[str, str] = {
    "EU": "EU",
    "US": "미국",
    "KR": "한국",
    "CN": "중국",
    "JP": "일본",
    "INTL": "국제",
}


def _parse_lifecycle_stage(v: Any) -> LifecycleStage:
    if isinstance(v, LifecycleStage):
        return v
    return LifecycleStage(str(v))


_LifecycleStageField = Annotated[LifecycleStage, BeforeValidator(_parse_lifecycle_stage)]


# ── Profile / Source registry ─────────────────────────────────────────────────

class ProfileSpec(BaseModel):
    model_config = ConfigDict(strict=True)

    project_id: str
    domain: str
    status: str
    watch_countries: list[str]
    keywords: list[str]
    concerns: list[str]
    lifecycle_interest: list[_LifecycleStageField]
    open_questions: Optional[list[str]] = None
    last_reviewed_by: Optional[str] = None


class SourceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    domain: str
    country: str
    type: str
    tier: int
    url: str
    health: str
    name: Optional[str] = None
    note: Optional[str] = None


# ── Collect layer ─────────────────────────────────────────────────────────────

class RawItem(BaseModel):
    source_id: str
    title: str
    url: str
    published_at: str          # ISO-8601 date string
    snippet: str
    country: str
    raw: dict[str, Any] = {}   # original payload for citation quote extraction


# ── Screen layer ──────────────────────────────────────────────────────────────

class Citation(BaseModel):
    source_id: str
    quote: str


class ScreenedItem(BaseModel):
    # RawItem fields (flattened for JSON serialization)
    source_id: str
    title: str
    url: str
    published_at: str
    snippet: str
    country: str
    # Screening output
    domain: str
    lifecycle_stage: str       # LifecycleStage value or "unclear"
    impact_summary: str
    citation: Citation


# ── Pipeline output ───────────────────────────────────────────────────────────

class DigestStatus(str, Enum):
    pending_review = "pending_review"
    ready_to_send = "ready_to_send"


class DigestRunResult(BaseModel):
    """POST /digest/run 응답. 생성 완료 + pending 저장 후 반환."""
    digest_id: str
    html: str
    summary: str
    stats: dict[str, Any]
    status: DigestStatus


class ApproveResult(BaseModel):
    """POST /digest/{digest_id}/approve 응답. 승인=발송 확정 시 반환."""
    digest_id: str
    html: str
    summary: str
    status: str  # "approved"


class DigestResult(BaseModel):
    """레거시 호환용 — 내부 테스트에서 참조 가능."""
    html: str
    summary: str
    stats: dict[str, Any]
