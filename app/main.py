from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from app.collect.runner import collect_all
from app.config import Settings, get_settings
from app.deliver import send_via_smtp
from app.models import ApproveResult, DigestRunResult, DigestStatus, ProfileSpec, SourceItem
from app.registry import (
    apply_screened_items,
    commit_registry,
    dedup_screened,
    get_changed_items,
    load_pending_registry,
    load_registry,
    save_pending_registry,
)
from app.screen import screen_pass1, screen_pass2
from app.synthesize import build_dashboard, build_email

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="SENTINEL reg-watch", version="0.4.0")


def _current_digest_id() -> str:
    iso = datetime.now().isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _load_profiles(profiles_dir: Path) -> list[ProfileSpec]:
    profiles: list[ProfileSpec] = []
    for path in sorted(profiles_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        try:
            profiles.append(ProfileSpec.model_validate(raw))
        except Exception as exc:
            raise ValueError(f"Profile validation failed [{path.name}]: {exc}") from exc
    return profiles


def _load_sources(sources_path: Path) -> list[SourceItem]:
    raw = json.loads(sources_path.read_text(encoding="utf-8"))
    return [SourceItem.model_validate(s) for s in raw.get("sources", [])]


def _save_pending(
    digest_id: str,
    html: str,
    summary: str,
    stats: dict,
    state_dir: Path,
) -> None:
    pending_dir = state_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)

    (pending_dir / f"{digest_id}.html").write_text(html, encoding="utf-8")

    meta = {
        "digest_id": digest_id,
        "generated_at": datetime.now().isoformat(),
        "summary": summary,
        "stats": stats,
    }
    (pending_dir / f"{digest_id}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("pending saved: %s", digest_id)


def _load_pending(digest_id: str, state_dir: Path) -> tuple[str, dict]:
    pending_dir = state_dir / "pending"
    html_path = pending_dir / f"{digest_id}.html"
    meta_path = pending_dir / f"{digest_id}.meta.json"

    if not html_path.exists() or not meta_path.exists():
        raise KeyError(digest_id)

    html = html_path.read_text(encoding="utf-8")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return html, meta


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> str:
    """전체 규제 레지스트리 대시보드.

    registry.json 전체를 로드하여 카드/테이블/필터/이력 뷰로 렌더링.
    """
    cfg: Settings = get_settings()
    registry = load_registry(cfg.state_dir)
    return build_dashboard(list(registry.values()))


@app.post("/digest/run", response_model=DigestRunResult)
async def digest_run() -> DigestRunResult:
    """다이제스트 생성 엔드포인트.

    collect → screen → registry apply → build_email → pending 저장.
    Weekly State(registry.json) 기준선은 이 단계에서 갱신하지 않는다.

    - review_first: status=pending_review, /approve 후 registry 커밋.
    - auto_send: status=ready_to_send, 즉시 registry 커밋.
    """
    cfg: Settings = get_settings()

    if not cfg.anthropic_api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    digest_id = _current_digest_id()
    checked_at = datetime.now().strftime("%Y-%m-%d")

    try:
        profiles = _load_profiles(cfg.profiles_dir)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    logger.info("[%s] Loaded %d profiles", digest_id, len(profiles))

    # 1. Collect
    raw_items, collect_stats = await collect_all(profiles, cfg)
    logger.info("[%s] Collected %d items", digest_id, collect_stats["total_collected"])

    # 2. Pass 1 — domain/country match (high recall, cheap model)
    pass1_items, pass1_stats = await screen_pass1(raw_items, profiles, cfg)

    # 3. Pass 2 — impact analysis + citation + registry fields (expensive model)
    screened_items, pass2_stats = await screen_pass2(pass1_items, profiles, cfg)

    # 4. Dedup + registry diff
    deduped = dedup_screened(screened_items)
    registry = load_registry(cfg.state_dir)
    updated_registry, changed_ids = apply_screened_items(deduped, registry, checked_at)
    changed_items = get_changed_items(updated_registry)

    # 5. Synthesize email HTML (changed items only)
    html = build_email(changed_items, profiles, cfg.dashboard_url)

    registry_stats = {
        "total_after_dedup": len(deduped),
        "registry_total": len(updated_registry),
        "changed_this_week": len(changed_ids),
    }
    stats = {**collect_stats, **pass1_stats, **pass2_stats, **registry_stats}
    summary = (
        f"신규/변경 {len(changed_items)}건 | "
        f"수집 {collect_stats['total_collected']}건 → "
        f"1차 {pass1_stats['passed_screen1']}건 → "
        f"2차 {pass2_stats['passed_screen2']}건 → "
        f"레지스트리 변경 {len(changed_ids)}건"
    )

    # 6. Save pending (registry 커밋 아직 안 함)
    _save_pending(digest_id, html, summary, stats, cfg.state_dir)
    save_pending_registry(updated_registry, digest_id, cfg.state_dir)

    # 7. auto_send: 즉시 레지스트리 커밋
    if cfg.send_mode == "auto_send":
        commit_registry(updated_registry, digest_id, cfg.state_dir)
        status = DigestStatus.ready_to_send
        logger.info("[%s] auto_send: registry committed", digest_id)
    else:
        status = DigestStatus.pending_review
        logger.info("[%s] review_first: awaiting approval", digest_id)

    logger.info("[%s] digest_run complete: %s", digest_id, summary)
    return DigestRunResult(
        digest_id=digest_id,
        html=html,
        summary=summary,
        stats=stats,
        status=status,
    )


@app.post("/digest/{digest_id}/approve", response_model=ApproveResult)
async def digest_approve(digest_id: str) -> ApproveResult:
    """발송 승인 엔드포인트.

    pending 레지스트리를 커밋하여 registry.json(diff 기준선)을 갱신한다.
    최종 HTML을 반환하면 n8n이 SMTP로 발송한다.
    """
    cfg: Settings = get_settings()

    try:
        html, meta = _load_pending(digest_id, cfg.state_dir)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"pending digest not found: {digest_id}",
        )

    try:
        pending_registry = load_pending_registry(digest_id, cfg.state_dir)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"pending registry not found: {digest_id}",
        )

    commit_registry(pending_registry, digest_id, cfg.state_dir)

    changed_count = sum(1 for r in pending_registry.values() if r.changed_this_week)
    logger.info(
        "[%s] approved — registry committed, %d total, %d changed",
        digest_id, len(pending_registry), changed_count,
    )

    return ApproveResult(
        digest_id=digest_id,
        html=html,
        summary=meta.get("summary", ""),
        status="approved",
    )


@app.post("/digest/{digest_id}/send-local")
async def digest_send_local(digest_id: str) -> dict:
    """로컬 SMTP 테스트 발송 엔드포인트 — 운영 발송은 n8n 담당.

    pending HTML을 .env DIGEST_RECIPIENTS로 직접 발송한다.
    """
    cfg: Settings = get_settings()

    try:
        html, _ = _load_pending(digest_id, cfg.state_dir)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"pending digest not found: {digest_id}",
        )

    recipients = cfg.recipients_list
    if not recipients:
        raise HTTPException(
            status_code=422,
            detail="DIGEST_RECIPIENTS not configured in .env",
        )

    try:
        send_via_smtp(html, recipients, cfg)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SMTP error: {exc}") from exc

    logger.info("[%s] send-local: sent to %s", digest_id, recipients)
    return {"digest_id": digest_id, "sent_to": recipients, "status": "sent"}
