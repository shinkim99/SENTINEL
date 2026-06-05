"""SENTINEL 서버리스 진입점 — FastAPI 없이 파이프라인을 직접 호출한다.

GitHub Actions(또는 로컬)에서 1회 실행되어:
  collect(US/EU) + KR inbox 로드 → 1차/2차 screen → registry diff
  → build_email + build_dashboard → registry.json 커밋 → Resend API 발송
  → 대시보드 public/index.html 출력.

KR 수집 분리: law.go.kr 은 GitHub 러너(해외 IP)에서 차단됨.
  → 회사 PC 에서 scripts/collect_kr.py 실행 후 data/inbox/kr_latest.json push.
  → 이 스크립트가 그 파일을 읽어 US/EU 결과에 합산.
  → 파일 없거나 8일 초과 시 KR collection_failure (이메일 배너), US/EU 는 정상 진행.

기존 app/main.py(FastAPI) / n8n 경로는 그대로 보존된다. 이 스크립트는 별도 경로.

환경변수(.env 또는 GitHub Secrets):
  ANTHROPIC_API_KEY, DIGEST_RECIPIENTS, RESEND_API_KEY, DASHBOARD_URL.
  (LAW_GO_KR_API_KEY 는 회사 PC collect_kr.py 전용 — 여기선 불필요)

사용:
  python -m scripts.run_digest --mode draft   # 본인(첫 수신자)에게만 — 검토용
  python -m scripts.run_digest --mode send    # DIGEST_RECIPIENTS 전체 — 본부 발송
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.collect.runner import collect_all
from app.config import Settings, get_settings
from app.deliver import send_via_resend
from app.models import ProfileSpec, RawItem
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
_KR_INBOX_PATH = Path("data/inbox/kr_latest.json")
_KR_STALE_DAYS = 8  # 이 일수 초과 시 KR collection_failure


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


def _load_kr_inbox() -> tuple[list[RawItem], dict]:
    """data/inbox/kr_latest.json 로드 (회사 PC 수집분).

    Returns (items, meta).  meta["status"]: "ok" | "missing" | "stale" | "error".
    missing/stale/error → 빈 리스트 반환, 호출자가 collection_failure 처리.
    """
    if not _KR_INBOX_PATH.exists():
        logger.warning(
            "kr_latest.json 없음 (%s) — KR collection_failure. "
            "회사 PC 에서 scripts/collect_kr.bat 실행 후 push 필요.",
            _KR_INBOX_PATH,
        )
        return [], {"status": "missing"}

    try:
        data = json.loads(_KR_INBOX_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("kr_latest.json 파싱 오류: %s", exc)
        return [], {"status": "error"}

    week = data.get("week", "unknown")
    collected_at_str = data.get("collected_at", "")

    # 신선도 확인
    if collected_at_str:
        try:
            collected_at = datetime.fromisoformat(collected_at_str)
            if collected_at.tzinfo is None:
                collected_at = collected_at.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - collected_at).total_seconds() / 86400
            if age_days > _KR_STALE_DAYS:
                logger.warning(
                    "kr_latest.json 오래됨 (%.1f일 경과, week=%s, path=%s) — KR collection_failure",
                    age_days, week, _KR_INBOX_PATH,
                )
                return [], {"status": "stale", "age_days": age_days, "week": week}
        except (ValueError, TypeError):
            logger.warning("kr_latest.json collected_at 파싱 실패: %r — 신선도 확인 건너뜀", collected_at_str)

    raw_list = data.get("items", [])
    try:
        items = [RawItem.model_validate(r) for r in raw_list]
    except Exception as exc:
        logger.error("kr_latest.json items 역직렬화 오류: %s", exc)
        return [], {"status": "error"}

    logger.info(
        "kr_latest.json 로드 — %d건, week=%s, collected_at=%s",
        len(items), week, collected_at_str,
    )
    return items, {
        "status": "ok",
        "count": len(items),
        "week": week,
        "collected_at": collected_at_str,
    }


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

    # 1. Collect (US/EU)
    raw_items, collect_stats = await collect_all(profiles, cfg)

    # 2. KR inbox 로드 및 합산
    kr_items, kr_meta = _load_kr_inbox()
    if kr_meta["status"] == "ok":
        raw_items = raw_items + kr_items  # KR URL 은 US/EU 와 중복 없음
        collect_stats["by_source"]["kr-law-go-kr"] = {
            "count": len(kr_items),
            "status": "ok",
            "week": kr_meta.get("week"),
            "collected_at": kr_meta.get("collected_at"),
        }
        collect_stats["total_collected"] = len(raw_items)
        logger.info(
            "[%s] KR inbox 합산: %d건 (week=%s)",
            digest_id, len(kr_items), kr_meta.get("week"),
        )
    else:
        collect_stats["collection_failures"].append("kr-law-go-kr")
        collect_stats["by_source"]["kr-law-go-kr"] = {
            "count": 0,
            "status": kr_meta["status"],
        }
        logger.warning(
            "[%s] KR inbox %s — KR collection_failure (US/EU 는 정상 진행)",
            digest_id, kr_meta["status"],
        )

    logger.info(
        "[%s] 수집 합계 %d건 (실패: %s)",
        digest_id, collect_stats["total_collected"], collect_stats["collection_failures"],
    )

    # 3. 1차 스크리닝
    pass1_items, pass1_stats = await screen_pass1(raw_items, profiles, cfg)

    # 4. 2차 스크리닝
    screened_items, pass2_stats = await screen_pass2(pass1_items, profiles, cfg)

    # 5. Dedup + registry diff
    deduped = dedup_screened(screened_items)

    # ── registry 로드 진단 로그 ──────────────────────────────────────────────
    registry_path = (cfg.state_dir / "registry.json").resolve()
    registry_exists = registry_path.exists()
    logger.info(
        "[%s] registry 경로: %s | 파일 존재: %s",
        digest_id, registry_path, registry_exists,
    )
    registry = load_registry(cfg.state_dir)
    logger.info(
        "[%s] 기존 레지스트리 %d건 로드%s",
        digest_id, len(registry),
        " (첫 실행 — 전부 신규 처리)" if not registry_exists else "",
    )
    # ────────────────────────────────────────────────────────────────────────

    updated_registry, changed_ids = apply_screened_items(deduped, registry, checked_at)
    changed_items = get_changed_items(updated_registry)

    # 6. stats 집계
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

    # 7. HTML 생성 (이메일=변경분, 대시보드=전체)
    email_html = build_email(changed_items, profiles, cfg.dashboard_url, stats, digest_id)
    dashboard_html = build_dashboard(
        list(updated_registry.values()), stats, digest_id,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        logo_url=cfg.logo_url,
    )

    # 8. 산출물 파일 출력
    _PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    (_PUBLIC_DIR / "index.html").write_text(dashboard_html, encoding="utf-8")
    (_PUBLIC_DIR / "email_preview.html").write_text(email_html, encoding="utf-8")
    logger.info(
        "[%s] 대시보드 → public/index.html, 이메일 미리보기 → public/email_preview.html",
        digest_id,
    )

    # 9. registry.json 커밋 (다음 주 diff 기준선 — Actions 가 레포에 push)
    commit_registry(updated_registry, digest_id, cfg.state_dir)
    logger.info("[%s] registry.json 커밋 (%d개 규제)", digest_id, len(updated_registry))

    # 10. 발송 (Resend)
    recipients = cfg.recipients_list
    if not recipients:
        logger.warning(
            "[%s] DIGEST_RECIPIENTS 미설정 — 발송 건너뜀 (대시보드/레지스트리는 갱신됨)",
            digest_id,
        )
        return 0

    if mode == "draft":
        to = recipients[:1]
        subject = f"[검토] SENTINEL 주간 규제 다이제스트 {digest_id}"
        from_email = cfg.resend_from_email
    else:  # send
        to = recipients
        subject = f"SENTINEL 주간 규제 다이제스트 {digest_id}"
        from_email = cfg.resend_from_email

    if not cfg.resend_api_key:
        logger.error(
            "[%s] RESEND_API_KEY 미설정 — 발송 불가 (대시보드/레지스트리는 갱신됨)",
            digest_id,
        )
        return 3

    try:
        result = send_via_resend(email_html, to, cfg, subject=subject, from_email=from_email)
        logger.info(
            "[%s] 발송 완료 mode=%s → %d명 (id=%s)",
            digest_id, mode, len(to), result.get("id"),
        )
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
