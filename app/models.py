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


def _parse_lifecycle_stage(v: Any) -> LifecycleStage:
    """JSON 문자열 → LifecycleStage 변환 (strict 모드 BeforeValidator용)."""
    if isinstance(v, LifecycleStage):
        return v
    return LifecycleStage(str(v))


# strict 모드에서 JSON 문자열을 enum으로 변환하는 타입 alias
_LifecycleStageField = Annotated[LifecycleStage, BeforeValidator(_parse_lifecycle_stage)]


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
    model_config = ConfigDict(strict=True)

    id: str
    domain: str
    country: str
    type: str
    tier: int
    url: str
    health: str


class DigestResult(BaseModel):
    html: str
    summary: str
    stats: dict[str, Any]
