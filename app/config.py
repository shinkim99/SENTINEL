from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Union

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = ""
    anthropic_model_screen: str = "claude-haiku-4-5-20251001"
    anthropic_model_impact: str = "claude-sonnet-4-6"

    law_go_kr_api_key: str = ""

    # 발송 설정
    send_mode: str = "review_first"  # review_first | auto_send
    digest_recipients: str = ""       # 본부 수신(쉼표 구분) — OPERATOR_EMAIL 와 별개
    operator_email: str = ""          # 검토 메일 수신(운영자 본인)
    digest_from_email: str = ""       # SMTP 발송 시 From (Gmail 노드는 불필요)

    # 대시보드 URL (이메일 CTA 버튼 링크 — 배포 후 실제 URL로 교체)
    # GitHub Actions(서버리스) 경로에서는 DASHBOARD_URL 환경변수로 Pages URL 주입.
    dashboard_url: str = "http://localhost:8010/dashboard"

    # ── Resend API (서버리스/GitHub Actions 발송 경로) ──
    # https://resend.com — API 키 1개로 SMTP 없이 HTTPS 발송.
    resend_api_key: str = ""
    # draft 모드 기본 From (Resend 테스트 도메인 — 본인 인증 주소로만 발송 가능).
    # 도메인 인증 후 운영에서는 본인 도메인 주소로 교체.
    resend_from_email: str = "SENTINEL <onboarding@resend.dev>"

    # SMTP (로컬 테스트용 — 운영 SMTP는 n8n 담당)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""

    sources_path: Path = Path("data/sources.json")
    profiles_dir: Path = Path("data/profiles")
    state_dir: Path = Path("data/state")

    # 대시보드 헤더 로고 URL (build_dashboard 전용 — 이메일엔 미사용)
    logo_url: str = "https://shinkim99.github.io/SENTINEL/ico/icon-1.png"

    # SSL — REQUESTS_CA_BUNDLE=false (bypass) | /path/to/ca.crt (custom bundle)
    requests_ca_bundle: str = ""

    @property
    def http_verify(self) -> Union[bool, str]:
        ca = self.requests_ca_bundle or os.environ.get("SSL_CERT_FILE", "")
        if not ca:
            return True
        if ca.lower() == "false":
            return False
        return ca

    @property
    def recipients_list(self) -> list[str]:
        return [r.strip() for r in self.digest_recipients.split(",") if r.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
