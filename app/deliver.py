"""발송 모듈.

- send_via_resend : 서버리스(GitHub Actions) 경로 — Resend HTTPS API. SMTP/n8n 불필요.
- send_via_smtp   : 로컬 발송 테스트 — 운영 SMTP는 n8n 담당, 개발/검증 전용.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"
_DEFAULT_SUBJECT = "SENTINEL 주간 규제 인텔리전스 다이제스트"


def send_via_resend(
    html: str,
    recipients: list[str],
    cfg: Settings,
    *,
    subject: str = _DEFAULT_SUBJECT,
    from_email: str = "",
) -> dict:
    """HTML 다이제스트를 Resend API로 발송.

    GitHub Actions 등 서버 없는 환경에서 SMTP 대신 사용한다.
    키는 .env / GitHub Secrets 의 RESEND_API_KEY 에서만 읽는다(로그 노출 금지).

    Returns: Resend API 응답 JSON (성공 시 {"id": "..."}).
    Raises:  ValueError(키/수신자 누락), RuntimeError(4xx/5xx).
    """
    if not cfg.resend_api_key:
        raise ValueError("RESEND_API_KEY가 설정되지 않았습니다 (.env / GitHub Secrets 확인)")
    if not recipients:
        raise ValueError("수신자 목록이 비어 있습니다 (DIGEST_RECIPIENTS 확인)")

    sender = from_email or cfg.resend_from_email
    payload = {
        "from": sender,
        "to": recipients,
        "subject": subject,
        "html": html,
    }

    try:
        resp = httpx.post(
            _RESEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {cfg.resend_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30.0,
            verify=cfg.http_verify,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Resend 요청 실패(네트워크): {exc}") from exc

    if resp.status_code >= 400:
        # 키 값은 절대 로그에 남기지 않는다.
        raise RuntimeError(f"Resend API {resp.status_code}: {resp.text}")

    data = resp.json()
    logger.info("send_via_resend: sent to %d recipient(s), id=%s", len(recipients), data.get("id"))
    return data


def send_via_smtp(html: str, recipients: list[str], cfg: Settings) -> None:
    """HTML 다이제스트를 SMTP로 직접 발송 (로컬 테스트 전용).

    .env의 SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / DIGEST_RECIPIENTS 사용.
    운영 환경에서는 n8n(또는 Resend)이 발송을 담당하므로 이 함수는 로컬 테스트 전용이다.
    """
    if not recipients:
        raise ValueError("수신자 목록이 비어 있습니다 (.env DIGEST_RECIPIENTS 확인)")
    if not cfg.smtp_host:
        raise ValueError("SMTP_HOST가 설정되지 않았습니다 (.env 확인)")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = _DEFAULT_SUBJECT
    msg["From"] = cfg.smtp_user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
        server.ehlo()
        if cfg.smtp_port != 25:
            server.starttls()
        if cfg.smtp_user and cfg.smtp_password:
            server.login(cfg.smtp_user, cfg.smtp_password)
        server.sendmail(cfg.smtp_user, recipients, msg.as_string())

    logger.info("send_via_smtp: sent to %s via %s:%d", recipients, cfg.smtp_host, cfg.smtp_port)
