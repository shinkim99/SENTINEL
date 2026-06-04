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


LIFECYCLE_LABELS_KO: dict[str, str] = {
    "proposed": "입법예고/제안",
    "enacted":  "공포",
    "in_force": "시행",
    "amended":  "개정",
    "repealed": "폐지",
    "unclear":  "불명확",
}

LIFECYCLE_COLORS: dict[str, tuple[str, str]] = {
    "proposed": ("#DBEAFE", "#1E40AF"),
    "enacted":  ("#FEF3C7", "#92400E"),
    "in_force": ("#D1FAE5", "#065F46"),
    "amended":  ("#EDE9FE", "#5B21B6"),
    "repealed": ("#FEE2E2", "#991B1B"),
    "unclear":  ("#F3F4F6", "#374151"),
}

ALERT_LABELS_KO: dict[str, str] = {
    "urgent": "긴급",
    "watch":  "주시",
    "opp":    "기회",
    "mon":    "모니터링",
}

ALERT_COLORS: dict[str, tuple[str, str]] = {
    "urgent": ("#FEE2E2", "#991B1B"),
    "watch":  ("#FEF9C3", "#854D0E"),
    "opp":    ("#D1FAE5", "#065F46"),
    "mon":    ("#F3F4F6", "#374151"),
}

IMPACT_TYPE_LABELS_KO: dict[str, str] = {
    "direct":   "직접",
    "indirect": "간접",
}

DOMAIN_LABELS_KO: dict[str, str] = {
    "secondary_battery": "2차전지",
    "green_eco":         "친환경",
    "hydrogen":          "수소",
    "space_environment": "우주환경",
}

DOMAIN_ICONS: dict[str, str] = {
    "secondary_battery": "🔋",
    "green_eco":         "🌿",
    "hydrogen":          "⚡",
    "space_environment": "🛸",
}

COUNTRY_LABELS_KO: dict[str, str] = {
    "EU":   "EU",
    "US":   "미국",
    "KR":   "한국",
    "CN":   "중국",
    "JP":   "일본",
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
    status: str = ""          # 프로필 JSON에 없는 경우 빈 문자열로 기본 처리
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
    published_at: str
    snippet: str
    country: str
    raw: dict[str, Any] = {}


# ── Screen layer ──────────────────────────────────────────────────────────────

class Citation(BaseModel):
    source_id: str
    quote: str


class ScreenedItem(BaseModel):
    # RawItem fields (flattened)
    source_id: str
    title: str
    url: str
    published_at: str
    snippet: str
    country: str
    # Screening output
    domain: str
    lifecycle_stage: str
    impact_summary: str
    citation: Citation
    # Registry fields (from pass2 — have defaults for backward compat)
    canonical_key: str = ""       # normalized slug for registry matching
    name: str = ""                # official regulation name
    date_text: str = ""           # effective/publication date as text
    impact_type: str = "direct"   # direct | indirect
    alert: str = "mon"            # urgent | watch | opp | mon
    confidence: str = "B"         # A | B | C


# ── Registry layer ────────────────────────────────────────────────────────────

class HistoryEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: str
    stage: str
    note: str
    source: str


class Regulation(BaseModel):
    """Canonical registry entry for a single regulation."""
    model_config = ConfigDict(extra="ignore")

    regulation_id: str            # canonical_key + "_" + country
    domain: str
    country: str
    name: str
    summary: str
    lifecycle_stage: str
    date_text: str
    rd_impact: str
    impact_type: str = "direct"
    alert: str = "mon"
    source: str
    source_url: str
    confidence: str = "B"
    checked_at: str
    changed_this_week: bool = False
    citation_quote: str = ""
    history: list[HistoryEntry] = []


# ── Pipeline output ───────────────────────────────────────────────────────────

class DigestStatus(str, Enum):
    pending_review = "pending_review"
    ready_to_send = "ready_to_send"


class DigestRunResult(BaseModel):
    digest_id: str
    html: str
    summary: str
    stats: dict[str, Any]
    status: DigestStatus
    recipients: str = ""        # 본부 수신(쉼표 구분) — n8n Gmail sendTo 용
    operator_email: str = ""    # 검토 메일 수신(운영자)


class ApproveResult(BaseModel):
    digest_id: str
    html: str
    summary: str
    status: str
    recipients: str = ""        # 본부 수신(쉼표 구분) — n8n Gmail sendTo 용
