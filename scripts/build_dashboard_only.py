"""대시보드 전용 빌드 — collect/screen/dedup/이메일 발송 없음.

이미 커밋된 data/state/registry.json을 읽어 build_dashboard()로
public/index.html만 생성한다. .github/workflows/deploy_dashboard.yml에서 호출.

LLM/Resend API 키 불필요. 환경변수: LOGO_URL(선택, 기본값 있음).

사용:
  python -m scripts.build_dashboard_only
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.registry import load_registry
from app.synthesize import _iso_week, build_dashboard

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

_PUBLIC_DIR = Path("public")


def main() -> None:
    cfg = get_settings()

    registry = load_registry(cfg.state_dir)
    digest_id = _iso_week()

    dashboard_html = build_dashboard(
        list(registry.values()),
        stats=None,
        digest_id=digest_id,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        logo_url=cfg.logo_url,
        state_dir=cfg.state_dir,
    )

    _PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    (_PUBLIC_DIR / "index.html").write_text(dashboard_html, encoding="utf-8")
    logger.info(
        "[%s] 대시보드 → public/index.html (%d개 규제, 발송 없음)",
        digest_id, len(registry),
    )


if __name__ == "__main__":
    main()
