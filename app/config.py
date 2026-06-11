from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Union

import truststore
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 회사망 SSL inspection 대응: OS(Windows/macOS/Linux) 신뢰저장소를 ssl 모듈에 주입.
# 모든 엔트리포인트(app.main, app.deliver, scripts/run_digest)가 app.config를 거치므로
# 다른 HTTPS 호출(httpx 등)보다 먼저 실행되도록 여기서 가장 먼저 수행한다.
truststore.inject_into_ssl()


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
    digest_cc: str = ""               # 참조(CC, 쉼표 구분) — send 모드에서만 사용
    digest_reply_to: str = "shinkim99@gmail.com"  # 회신 수신 (운영자 본인)
    operator_email: str = ""          # 검토 메일 수신(운영자 본인)
    digest_from_email: str = ""       # SMTP 발송 시 From (Gmail 노드는 불필요)

    # 대시보드 URL (이메일 CTA 버튼 링크 — 배포 후 실제 URL로 교체)
    # GitHub Actions(서버리스) 경로에서는 DASHBOARD_URL 환경변수로 Pages URL 주입.
    dashboard_url: str = "http://localhost:8010/dashboard"

    # ── Resend API (서버리스/GitHub Actions 발송 경로) ──
    # https://resend.com — API 키 1개로 SMTP 없이 HTTPS 발송.
    resend_api_key: str = ""
    # 발송 From — 인증된 자체 도메인 주소. RESEND_FROM_EMAIL 환경변수로 교체 가능.
    resend_from_email: str = "SENTINEL <sentinel@shinkim99.com>"

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

    # SSL — REQUESTS_CA_BUNDLE=false (bypass) | /path/to/ca.crt (custom bundle, legacy)
    requests_ca_bundle: str = ""

    # httpx verify= 에 그대로 전달. true(기본) | false(검증 생략) | CA 번들 경로.
    # 회사망 SSL inspection 환경: HTTP_VERIFY=C:\path\to\ca-bundle.crt
    http_verify: Union[bool, str] = True

    @field_validator("http_verify", mode="before")
    @classmethod
    def _parse_http_verify_bool(cls, v):
        if isinstance(v, str) and v.strip().lower() in ("true", "false"):
            return v.strip().lower() == "true"
        return v

    @model_validator(mode="after")
    def _resolve_http_verify(self) -> "Settings":
        # HTTP_VERIFY 미지정(기본 True)이면 레거시 REQUESTS_CA_BUNDLE / SSL_CERT_FILE로 폴백.
        if self.http_verify is True:
            ca = self.requests_ca_bundle or os.environ.get("SSL_CERT_FILE", "")
            if ca:
                self.http_verify = False if ca.lower() == "false" else ca
        return self

    @property
    def recipients_list(self) -> list[str]:
        return [r.strip() for r in self.digest_recipients.split(",") if r.strip()]

    @property
    def cc_list(self) -> list[str]:
        return [r.strip() for r in self.digest_cc.split(",") if r.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
