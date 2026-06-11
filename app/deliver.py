"""발송 모듈.

- send_via_resend : 서버리스(GitHub Actions) 경로 — Resend HTTPS API. SMTP/n8n 불필요.
- send_via_smtp   : 로컬 발송 테스트 — 운영 SMTP는 n8n 담당, 개발/검증 전용.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

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
    cc: Optional[list[str]] = None,
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
    if cc:
        payload["cc"] = cc

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


def main(argv: list[str] | None = None) -> None:
    """python -m app.deliver — 수동 발송 진입점.

    --test : DIGEST_RECIPIENTS 첫 번째 주소 1명에게만 이번 주 다이제스트 발송 (검토용).
             2명 이상으로는 어떤 경우에도 보내지 않는다.
    --send : DIGEST_RECIPIENTS 전체 발송. 수신자 전체 목록·인원수를 먼저 출력하고,
             표준입력으로 정확히 'yes'를 받아야 진행한다 (--yes로 생략 가능).
    인자 없으면 도움말만 출력하고 아무것도 보내지 않는다.

    send_mode(review_first/auto_send)와 무관한 수동 발송이며,
    주간 자동 발송 흐름(scripts/run_digest.py, GitHub Actions)에는 영향을 주지 않는다.
    """
    import argparse
    import sys

    from app.config import get_settings

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="SENTINEL 발송 테스트/수동 발송")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--test", action="store_true",
        help="DIGEST_RECIPIENTS 첫 번째 주소 1명에게만 이번 주 다이제스트 발송",
    )
    group.add_argument(
        "--send", action="store_true",
        help="DIGEST_RECIPIENTS 전체 발송 (확인 필요)",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="--send 시 'yes' 확인 프롬프트 생략 (비대화형 환경용)",
    )
    args = parser.parse_args(argv)

    if not args.test and not args.send:
        parser.print_help()
        return

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
    cfg = get_settings()

    recipients = cfg.recipients_list
    if not recipients:
        raise SystemExit("DIGEST_RECIPIENTS가 설정되지 않았습니다 (.env 확인)")

    digest_id, subject, html = _build_test_digest(cfg)

    if args.test:
        to = recipients[:1]
        print(f"TEST 발송 → {to[0]}")
    else:
        print(f"전체 발송 대상 ({len(recipients)}명): {recipients}")
        if not args.yes:
            answer = input("정말 전체 발송하려면 'yes'를 입력하세요: ")
            if answer != "yes":
                print("발송 취소됨 — 아무것도 보내지 않았습니다.")
                return
        to = recipients

    result = send_via_resend(html, to, cfg, subject=subject)
    print(f"발송 완료: {len(to)}명 → {to}")
    print(f"제목: {subject}")
    print(f"Resend id: {result.get('id')}")


if __name__ == "__main__":
    main()
