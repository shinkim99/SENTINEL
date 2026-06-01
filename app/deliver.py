"""로컬 발송 테스트용 모듈 — 실제 발송은 n8n SMTP가 담당."""
from __future__ import annotations

from app.models import DigestResult


async def send_review_request(digest: DigestResult, recipient: str) -> None:
    """운영자에게 검토 요청 메일 발송 (review_first 모드).

    Args:
        digest: 생성된 다이제스트.
        recipient: 검토 요청 수신자 이메일.
    """
    raise NotImplementedError


async def send_digest(digest: DigestResult, recipients: list[str]) -> None:
    """최종 다이제스트 발송 (auto_send 모드 또는 검토 승인 후).

    Args:
        digest: 생성된 다이제스트.
        recipients: 수신자 이메일 목록 (.env DIGEST_RECIPIENTS).
    """
    raise NotImplementedError
