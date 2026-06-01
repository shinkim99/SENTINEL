"""로컬 발송 테스트용 모듈 — 운영 SMTP는 n8n 담당, 이 모듈은 개발/검증 전용."""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import Settings

logger = logging.getLogger(__name__)


def send_via_smtp(html: str, recipients: list[str], cfg: Settings) -> None:
    """HTML 다이제스트를 SMTP로 직접 발송.

    .env의 SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / DIGEST_RECIPIENTS 사용.
    운영 환경에서는 n8n이 발송을 담당하므로 이 함수는 로컬 테스트 전용이다.
    """
    if not recipients:
        raise ValueError("수신자 목록이 비어 있습니다 (.env DIGEST_RECIPIENTS 확인)")
    if not cfg.smtp_host:
        raise ValueError("SMTP_HOST가 설정되지 않았습니다 (.env 확인)")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "SENTINEL 주간 규제 인텔리전스 다이제스트"
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
