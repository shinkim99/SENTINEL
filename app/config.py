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

    digest_recipients: str = ""
    send_mode: str = "review_first"

    sources_path: Path = Path("data/sources.json")
    profiles_dir: Path = Path("data/profiles")
    state_dir: Path = Path("data/state")

    # SSL — REQUESTS_CA_BUNDLE=false (bypass) | /path/to/ca.crt (custom bundle)
    requests_ca_bundle: str = ""   # maps to REQUESTS_CA_BUNDLE in .env

    @property
    def http_verify(self) -> Union[bool, str]:
        """Returns httpx verify argument.
        'false' → skip verification (unsafe, for testing only)
        '<path>' → custom CA bundle
        '' → True (default system certs)
        """
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
