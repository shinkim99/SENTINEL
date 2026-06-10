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
    reply_to: str = "",
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
    rt = reply_to or cfg.digest_reply_to
    if rt:
        payload["reply_to"] = rt

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


# ══════════════════════════════════════════════════════════════════════════════
# CLI — python -m app.deliver --test
# ══════════════════════════════════════════════════════════════════════════════

def _build_test_digest(cfg: Settings) -> tuple[str, str, str]:
    """이번 주 변경분으로 (digest_id, subject, html) 구성. registry.json 기준."""
    import json as _json

    from app.models import ProfileSpec
    from app.registry import get_changed_items, load_registry
    from app.synthesize import _iso_week, build_email

    digest_id = _iso_week()
    registry = load_registry(cfg.state_dir)
    changed_items = get_changed_items(registry)

    profiles: list[ProfileSpec] = []
    for path in sorted(cfg.profiles_dir.glob("*.json")):
        raw = _json.loads(path.read_text(encoding="utf-8"))
        profiles.append(ProfileSpec.model_validate(raw))

    html = build_email(changed_items, profiles, cfg.dashboard_url, {}, digest_id)
    subject = f"[SENTINEL] 주간 규제 다이제스트 — {digest_id}"
    return digest_id, subject, html


def main() -> None:
    """python -m app.deliver --test → DIGEST_RECIPIENTS로 이번 주 다이제스트 1통 발송.

    send_mode(review_first/auto_send)와 무관한 수동 1회성 테스트 발송이며,
    주간 자동 발송 흐름(scripts/run_digest.py)에는 영향을 주지 않는다.
    """
    import argparse
    import sys

    from app.config import get_settings

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="SENTINEL 발송 테스트")
    parser.add_argument(
        "--test", action="store_true",
        help="DIGEST_RECIPIENTS로 이번 주 다이제스트 1통을 즉시 발송",
    )
    args = parser.parse_args()

    if not args.test:
        parser.print_help()
        return

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
    cfg = get_settings()

    recipients = cfg.recipients_list
    if not recipients:
        raise SystemExit("DIGEST_RECIPIENTS가 설정되지 않았습니다 (.env 확인)")

    digest_id, subject, html = _build_test_digest(cfg)
    result = send_via_resend(html, recipients, cfg, subject=subject)
    print(f"발송 완료: {len(recipients)}명 → {recipients}")
    print(f"제목: {subject}")
    print(f"Resend id: {result.get('id')}")


if __name__ == "__main__":
    main()
