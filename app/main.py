from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException

from app.collect.runner import collect_all
from app.config import Settings, get_settings
from app.deliver import send_via_smtp
from app.diff import commit_sent_state, dedup, diff_against_sent
from app.models import ApproveResult, DigestRunResult, DigestStatus, ProfileSpec, SourceItem
from app.screen import screen_pass1, screen_pass2
from app.synthesize import build_html

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="SENTINEL reg-watch", version="0.3.0")


def _current_digest_id() -> str:
    """ISO 주차 기준 다이제스트 ID (예: 2026-W23). 같은 주 재실행은 같은 ID."""
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
    items_for_state: list[dict],
    state_dir: Path,
) -> None:
    """생성된 다이제스트를 pending 디렉터리에 저장."""
    pending_dir = state_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)

    (pending_dir / f"{digest_id}.html").write_text(html, encoding="utf-8")

    meta = {
        "digest_id": digest_id,
        "generated_at": datetime.now().isoformat(),
        "summary": summary,
        "stats": stats,
        "items_for_state": items_for_state,
    }
    (pending_dir / f"{digest_id}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("pending saved: %s", digest_id)


def _load_pending(digest_id: str, state_dir: Path) -> tuple[str, dict]:
    """pending 다이제스트의 (html, meta) 반환. 없으면 KeyError."""
    pending_dir = state_dir / "pending"
    html_path = pending_dir / f"{digest_id}.html"
    meta_path = pending_dir / f"{digest_id}.meta.json"

    if not html_path.exists() or not meta_path.exists():
        raise KeyError(digest_id)

    html = html_path.read_text(encoding="utf-8")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return html, meta


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/digest/run", response_model=DigestRunResult)
async def digest_run() -> DigestRunResult:
    """다이제스트 생성 전용 엔드포인트.

    collect → screen → diff → synthesize 후 pending에 저장한다.
    Weekly State 기준선은 이 단계에서 갱신하지 않는다.

    - review_first: status=pending_review, 승인(/approve) 후 기준선 갱신.
    - auto_send: status=ready_to_send, 즉시 기준선 커밋.
    """
    cfg: Settings = get_settings()

    if not cfg.anthropic_api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    digest_id = _current_digest_id()

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

    # 3. Pass 2 — impact analysis + citation validation (expensive model)
    screened_items, pass2_stats = await screen_pass2(pass1_items, profiles, cfg)

    # 4. Dedup + diff against sent baseline (no write)
    deduped = dedup(screened_items)
    new_items, diff_stats = diff_against_sent(deduped, cfg.state_dir)

    # 5. Synthesize HTML
    html = build_html(new_items, profiles)

    stats = {**collect_stats, **pass1_stats, **pass2_stats, **diff_stats}
    summary = (
        f"신규 {len(new_items)}건 | "
        f"수집 {collect_stats['total_collected']}건 → "
        f"1차 {pass1_stats['passed_screen1']}건 → "
        f"2차 {pass2_stats['passed_screen2']}건 → "
        f"신규 {diff_stats['new_items']}건"
    )

    # 6. Save pending (항상 저장 — auto_send도 감사 목적으로 pending 보관)
    items_for_state = [
        {"url": it.url, "title": it.title, "source_id": it.source_id}
        for it in deduped
    ]
    _save_pending(digest_id, html, summary, stats, items_for_state, cfg.state_dir)

    # 7. auto_send면 즉시 기준선 커밋
    if cfg.send_mode == "auto_send":
        commit_sent_state(deduped, digest_id, cfg.state_dir)
        status = DigestStatus.ready_to_send
        logger.info("[%s] auto_send: baseline committed", digest_id)
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

    pending 다이제스트를 확정하고 Weekly State 기준선을 갱신한다.
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

    # pending에 저장된 items로 기준선 갱신
    from app.models import ScreenedItem, Citation

    # items_for_state는 url/title/source_id 미니 형태 — commit에 필요한 최소 필드만 담긴다.
    # ScreenedItem 전체 복원 없이 commit_sent_state가 받는 형식에 맞는 간이 객체 사용.
    class _MinItem:
        def __init__(self, url: str, title: str, source_id: str) -> None:
            self.url = url
            self.title = title
            self.source_id = source_id

    min_items = [
        _MinItem(it["url"], it["title"], it["source_id"])
        for it in meta.get("items_for_state", [])
    ]

    commit_sent_state(min_items, digest_id, cfg.state_dir)  # type: ignore[arg-type]

    logger.info("[%s] approved — baseline committed, %d items", digest_id, len(min_items))

    return ApproveResult(
        digest_id=digest_id,
        html=html,
        summary=meta.get("summary", ""),
        status="approved",
    )


@app.post("/digest/{digest_id}/send-local")
async def digest_send_local(digest_id: str) -> dict:
    """로컬 SMTP 테스트 발송 엔드포인트 — 운영 발송은 n8n 담당.

    pending 다이제스트를 .env DIGEST_RECIPIENTS로 직접 발송한다.
    승인 여부와 무관하게 pending HTML을 사용한다.
    """
    cfg: Settings = get_settings()

    try:
        html, meta = _load_pending(digest_id, cfg.state_dir)
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
