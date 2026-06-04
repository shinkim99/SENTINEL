"""SENTINEL 서버리스 진입점 — FastAPI 없이 파이프라인을 직접 호출한다.

GitHub Actions(또는 로컬)에서 1회 실행되어:
  collect → 1차/2차 screen → registry diff → build_email + build_dashboard
  → registry.json 커밋(다음 주 diff 기준) → Resend API 발송 → 대시보드 public/index.html 출력.

기존 app/main.py(FastAPI) / n8n 경로는 그대로 보존된다. 이 스크립트는 별도 경로.

환경변수(.env 또는 GitHub Secrets):
  ANTHROPIC_API_KEY, LAW_GO_KR_API_KEY, DIGEST_RECIPIENTS, RESEND_API_KEY,
  DASHBOARD_URL(Pages URL).

사용:
  python -m scripts.run_digest --mode draft   # 본인(첫 수신자)에게만 — 검토용
  python -m scripts.run_digest --mode send    # DIGEST_RECIPIENTS 전체 — 본부 발송
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from app.collect.runner import collect_all
from app.config import Settings, get_settings
from app.deliver import send_via_resend
from app.models import ProfileSpec
from app.registry import (
    apply_screened_items,
    commit_registry,
    dedup_screened,
    get_changed_items,
    load_registry,
)
from app.screen import screen_pass1, screen_pass2
from app.synthesize import build_dashboard, build_email

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("run_digest")

_PUBLIC_DIR = Path("public")


def _current_digest_id() -> str:
    iso = datetime.now().isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _load_profiles(profiles_dir: Path) -> list[ProfileSpec]:
    profiles: list[ProfileSpec] = []
    for path in sorted(profiles_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        try:
            profiles.append(ProfileSpec.model_validate(raw))
        except Exception as exc:
            raise ValueError(f"Profile validation failed [{path.name}]: {exc}") from exc
    return profiles


async def run(mode: str) -> int:
    cfg: Settings = get_settings()

    if not cfg.anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY 미설정 — 중단")
        return 2

    digest_id = _current_digest_id()
    checked_at = datetime.now().strftime("%Y-%m-%d")
    logger.info("[%s] mode=%s 시작", digest_id, mode)

    profiles = _load_profiles(cfg.profiles_dir)
    logger.info("[%s] 프로필 %d개 로드", digest_id, len(profiles))

    # 1. Collect
    raw_items, collect_stats = await collect_all(profiles, cfg)
    logger.info("[%s] 수집 %d건 (실패: %s)",
                digest_id, collect_stats["total_collected"], collect_stats["collection_failures"])

    # 2. 1차 스크리닝
    pass1_items, pass1_stats = await screen_pass1(raw_items, profiles, cfg)

    # 3. 2차 스크리닝
    screened_items, pass2_stats = await screen_pass2(pass1_items, profiles, cfg)

    # 4. Dedup + registry diff
    deduped = dedup_screened(screened_items)
    registry = load_registry(cfg.state_dir)
    updated_registry, changed_ids = apply_screened_items(deduped, registry, checked_at)
    changed_items = get_changed_items(updated_registry)

    # 5. stats 집계
    stats = {
        "digest_id": digest_id,
        "total_collected": collect_stats["total_collected"],
        "passed_screen1": pass1_stats["passed_screen1"],
        "passed_screen2": pass2_stats["passed_screen2"],
        "changed_this_week": len(changed_ids),
        "collection_failures": collect_stats["collection_failures"],
        "by_source": collect_stats.get("by_source", {}),
        "dropped_citation_mismatch": pass2_stats.get("dropped_citation_mismatch", 0),
    }
    logger.info(
        "[%s] 수집 %s → 1차 %s → 2차 %s → 변경 %s",
        digest_id, stats["total_collected"], stats["passed_screen1"],
        stats["passed_screen2"], stats["changed_this_week"],
    )

    # 6. HTML 생성 (이메일=변경분, 대시보드=전체)
    email_html = build_email(changed_items, profiles, cfg.dashboard_url, stats, digest_id)
    dashboard_html = build_dashboard(
        list(updated_registry.values()), stats, digest_id,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    # 7. 산출물 파일 출력
    _PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    (_PUBLIC_DIR / "index.html").write_text(dashboard_html, encoding="utf-8")
    (_PUBLIC_DIR / "email_preview.html").write_text(email_html, encoding="utf-8")
    logger.info("[%s] 대시보드 → public/index.html, 이메일 미리보기 → public/email_preview.html", digest_id)

    # 8. registry.json 커밋 (다음 주 diff 기준선 — Actions가 레포에 push)
    commit_registry(updated_registry, digest_id, cfg.state_dir)
    logger.info("[%s] registry.json 커밋 (%d개 규제)", digest_id, len(updated_registry))

    # 9. 발송 (Resend)
    recipients = cfg.recipients_list
    if not recipients:
        logger.warning("[%s] DIGEST_RECIPIENTS 미설정 — 발송 건너뜀 (대시보드/레지스트리는 갱신됨)", digest_id)
        return 0

    if mode == "draft":
        to = recipients[:1]  # 첫 수신자(운영자 본인)에게만
        subject = f"[검토] SENTINEL 주간 규제 다이제스트 {digest_id}"
        from_email = cfg.resend_from_email  # 테스트 도메인 — 본인 인증 주소로만 발송 가능
    else:  # send
        to = recipients      # 본부 전체
        subject = f"SENTINEL 주간 규제 다이제스트 {digest_id}"
        from_email = cfg.resend_from_email

    if not cfg.resend_api_key:
        logger.error("[%s] RESEND_API_KEY 미설정 — 발송 불가 (대시보드/레지스트리는 갱신됨)", digest_id)
        return 3

    try:
        result = send_via_resend(email_html, to, cfg, subject=subject, from_email=from_email)
        logger.info("[%s] 발송 완료 mode=%s → %d명 (id=%s)",
                    digest_id, mode, len(to), result.get("id"))
    except Exception as exc:
        logger.error("[%s] 발송 실패: %s", digest_id, exc)
        return 4

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="SENTINEL 주간 다이제스트 (서버리스)")
    parser.add_argument(
        "--mode",
        choices=["draft", "send"],
        default="draft",
        help="draft=본인(첫 수신자)에게만 검토용 | send=DIGEST_RECIPIENTS 전체 발송. 기본 draft.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.mode)))


if __name__ == "__main__":
    main()
