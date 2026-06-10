"""규제 레지스트리 — regulation_id 기반 영속 상태 관리.

기준선: data/state/registry.json — 실행 완료 시점에 갱신된다.

regulation_id 결정 우선순위:
  1. 기존 레지스트리에서 URL로 역조회한 ID (stable — LLM canonical_key 불일치 방지)
  2. LLM이 제공한 canonical_key + "_" + country
  3. 제목에서 파생한 slug + "_" + country (fallback)
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from app.models import HistoryEntry, Regulation, ScreenedItem

logger = logging.getLogger(__name__)

_REGISTRY_FILE = "registry.json"
_PENDING_SUFFIX = ".registry.json"


# ── slug helper ───────────────────────────────────────────────────────────────

def _canonical_key(text: str) -> str:
    """title → normalized slug (max 60 chars). Used as fallback when LLM omits canonical_key."""
    key = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    key = re.sub(r"\s+", "-", key.strip())
    key = re.sub(r"-+", "-", key)
    return key[:60].rstrip("-")


# ── name normalization (명칭 변경 비교용) ────────────────────────────────────────

_PAREN_RE = re.compile(r"[\(（][^\)）]*[\)）]")


def _normalize_name(name: str) -> str:
    """비교용 이름 정규화: NFC + 괄호(반각/전각) 안 약칭 제거 + 공백/대소문자 정리.

    kr-law-go-kr 등 일부 출처는 괄호 안 약칭이 매주 EN↔KR로 흔들리는데(LLM 비결정성),
    base name이 그대로면 '명칭 변경'으로 보지 않는다 (phantom 이력 누적 방지).
    """
    s = unicodedata.normalize("NFC", name or "")
    s = _PAREN_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.casefold()


# ── load / save ───────────────────────────────────────────────────────────────

def load_registry(state_dir: Path) -> dict[str, Regulation]:
    """Load registry.json → {regulation_id: Regulation}. Empty dict if absent."""
    path = state_dir / _REGISTRY_FILE
    if not path.exists():
        logger.info("registry: no existing %s — starting fresh", path)
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        registry: dict[str, Regulation] = {}
        for item in data.get("regulations", []):
            try:
                reg = Regulation.model_validate(item)
                registry[reg.regulation_id] = reg
            except Exception as exc:
                logger.warning("registry: skipping invalid entry %r: %s", item.get("regulation_id"), exc)
        logger.info("registry: loaded %d regulations from %s", len(registry), path)
        return registry
    except Exception as exc:
        logger.error("registry: load failed (%s) — starting fresh", exc)
        return {}


def save_pending_registry(
    registry: dict[str, Regulation],
    digest_id: str,
    state_dir: Path,
) -> None:
    """Save proposed registry snapshot to pending/ — NOT the committed baseline."""
    pending_dir = state_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    path = pending_dir / f"{digest_id}{_PENDING_SUFFIX}"
    payload = {
        "digest_id": digest_id,
        "saved_at": datetime.now().isoformat(),
        "regulations": [r.model_dump() for r in registry.values()],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("registry: pending saved → %s (%d regs)", path, len(registry))


def load_pending_registry(digest_id: str, state_dir: Path) -> dict[str, Regulation]:
    """Load pending registry snapshot for the given digest_id. Raises KeyError if absent."""
    path = state_dir / "pending" / f"{digest_id}{_PENDING_SUFFIX}"
    if not path.exists():
        raise KeyError(f"pending registry not found: {digest_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    registry: dict[str, Regulation] = {}
    for item in data.get("regulations", []):
        reg = Regulation.model_validate(item)
        registry[reg.regulation_id] = reg
    logger.info("registry: loaded pending %s (%d regs)", digest_id, len(registry))
    return registry


def commit_registry(
    registry: dict[str, Regulation],
    digest_id: str,
    state_dir: Path,
) -> None:
    """Commit registry to disk. Called on approve or run completion.

    Writes:
    - state/registry.json  — new diff baseline
    - state/sent/{digest_id}.json — audit log
    """
    state_dir.mkdir(parents=True, exist_ok=True)

    reg_path = state_dir / _REGISTRY_FILE
    payload = {
        "committed_at": datetime.now().isoformat(),
        "digest_id": digest_id,
        "regulations": [r.model_dump() for r in registry.values()],
    }
    reg_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    sent_dir = state_dir / "sent"
    sent_dir.mkdir(parents=True, exist_ok=True)
    changed = [r.model_dump() for r in registry.values() if r.changed_this_week]
    audit = {
        "digest_id": digest_id,
        "committed_at": datetime.now().isoformat(),
        "total_regulations": len(registry),
        "changed_this_week": len(changed),
        "changed_items": changed,
    }
    (sent_dir / f"{digest_id}.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 주간 스냅샷 저장 — _upsert_changelog가 snapshot[W-1] vs snapshot[W] diff에 사용
    snap_dir = state_dir / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_payload = {
        "digest_id": digest_id,
        "snapshot_at": datetime.now().isoformat(),
        "items": build_url_snapshot(registry),
    }
    (snap_dir / f"{digest_id}.json").write_text(
        json.dumps(snap_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(
        "registry: committed → %s (%d total, %d changed) | snapshot → %s",
        reg_path, len(registry), len(changed), snap_dir / f"{digest_id}.json",
    )


# ── core logic ────────────────────────────────────────────────────────────────

def apply_screened_items(
    items: list[ScreenedItem],
    registry: dict[str, Regulation],
    checked_at: str,
) -> tuple[dict[str, Regulation], list[str], dict]:
    """Apply screened items onto a deep copy of the registry.

    Matching strategy (순서대로):
    1. regulation_id(canonical_key + "_" + country) 직접 조회 — 가장 빠름.
    2. source_url로 역조회 — LLM이 매 실행마다 다른 canonical_key를 반환해도(비결정적)
       URL이 같으면 기존 항목으로 인식. 기존 ID를 유지하여 registry 안정성 보장.

    changed_this_week 판정 기준 (의미있는 변화만):
    - lifecycle_stage 변경 → changed=True + history append.
    - name 변경 → changed=True + history append.
    - date_text(시행일/예정일) 변경 → changed=True + history append.
    - summary/rd_impact/confidence/alert/impact_type: 최신값으로 조용히 갱신.
      LLM 자유생성 텍스트의 미세 차이만으로는 changed=False (과민 판정 방지).

    반환값: (updated_registry, changed_ids, change_summary)
      change_summary = {
        "new": [regulation_id, ...],          # 신규 등록
        "stage_changed": [regulation_id, ...], # lifecycle_stage 변경 (우선순위 높음)
        "updated": [regulation_id, ...],       # name/date_text 변경 (단계변경 아닌 것)
        "removed": [regulation_id, ...],       # 이전 registry에 있었으나 이번 수집에 없는 키
      }
      changed_ids = new + stage_changed + updated (합산, 하위 호환 유지)
    """
    reg_copy: dict[str, Regulation] = deepcopy(registry)
    prev_keys: set[str] = set(registry.keys())

    for reg in reg_copy.values():
        reg.changed_this_week = False

    # URL → regulation_id 역인덱스: canonical_key가 바뀌어도 URL로 기존 항목 찾기.
    # source_url이 같은 항목이 여러 개일 경우 마지막 것이 우선 (중복은 dedup_screened로 예방).
    url_index: dict[str, str] = {
        r.source_url.rstrip("/").lower(): rid
        for rid, r in reg_copy.items()
    }

    changed_ids: list[str] = []

    for item in items:
        ck = item.canonical_key or _canonical_key(item.title)
        reg_id = f"{ck}_{item.country}"

        # 1차: regulation_id 직접 조회
        existing = reg_copy.get(reg_id)

        # 2차: URL 역조회 (canonical_key 불일치 시 fallback)
        if existing is None:
            url_key = item.url.rstrip("/").lower()
            stable_id = url_index.get(url_key)
            if stable_id is not None:
                existing = reg_copy.get(stable_id)
                if existing is not None:
                    # 기존 ID를 유지 (re-key 하지 않음 — LLM이 다음 주에 또 바뀔 수 있음)
                    reg_id = stable_id
                    logger.info(
                        "registry: URL match — canonical_key drift 감지. "
                        "기존 ID 유지: %r (url=%s)",
                        reg_id, item.url[:70],
                    )

        if existing is None:
            new_reg = Regulation(
                regulation_id=reg_id,
                domain=item.domain,
                country=item.country,
                name=item.name or item.title,
                summary=item.impact_summary,
                lifecycle_stage=item.lifecycle_stage,
                date_text=item.date_text or item.published_at,
                rd_impact=item.impact_summary,
                impact_type=item.impact_type,
                alert=item.alert,
                source=item.source_id,
                source_url=item.url,
                confidence=item.confidence,
                checked_at=checked_at,
                changed_this_week=True,
                citation_quote=item.citation.quote if item.citation else "",
                history=[
                    HistoryEntry(
                        date=item.published_at,
                        stage=item.lifecycle_stage,
                        note="신규 등록",
                        source=item.source_id,
                    )
                ],
            )
            reg_copy[reg_id] = new_reg
            # URL 인덱스 갱신 (이후 동일 URL 중복 처리용)
            url_index[item.url.rstrip("/").lower()] = reg_id
            changed_ids.append(reg_id)
            logger.info("registry: NEW %s [%s/%s]", reg_id, item.country, item.lifecycle_stage)

        else:
            changed = False
            note_parts: list[str] = []

            # ── 의미있는 변화 (changed 판정) ─────────────────────────────────
            if existing.lifecycle_stage != item.lifecycle_stage:
                note_parts.append(f"{existing.lifecycle_stage} → {item.lifecycle_stage}")
                existing.lifecycle_stage = item.lifecycle_stage
                changed = True

            if item.name and existing.name != item.name:
                if _normalize_name(existing.name) != _normalize_name(item.name):
                    note_parts.append(f"명칭 변경: {item.name[:40]}")
                    changed = True
                # base name이 같으면(괄호 안 약칭만 EN↔KR로 흔들림) 조용히 갱신 — 이력 미기록
                existing.name = item.name

            if item.date_text and existing.date_text != item.date_text:
                note_parts.append(f"시행일: {existing.date_text} → {item.date_text}")
                existing.date_text = item.date_text
                changed = True

            # ── LLM 텍스트 필드 — 조용히 갱신, changed 판정 제외 ───────────
            if item.impact_summary:
                existing.summary = item.impact_summary
                existing.rd_impact = item.impact_summary
            existing.impact_type = item.impact_type
            existing.alert = item.alert
            existing.confidence = item.confidence
            existing.checked_at = checked_at

            if changed:
                existing.history.append(
                    HistoryEntry(
                        date=item.published_at,
                        stage=item.lifecycle_stage,
                        note=", ".join(note_parts),
                        source=item.source_id,
                    )
                )
                existing.changed_this_week = True
                existing.citation_quote = item.citation.quote if item.citation else ""
                changed_ids.append(reg_id)
                logger.info("registry: CHANGED %s: %s", reg_id, ", ".join(note_parts))

    # change_summary: classify_changes(이전 스냅샷, 이후 스냅샷)
    # 동일 함수를 _upsert_changelog도 호출 — 단일 진실원본 유지.
    # 재실행 시 delta≈0이 되는 문제는 _upsert_changelog에서 커밋된 스냅샷 파일로 해결.
    change_summary = classify_changes(build_url_snapshot(registry), build_url_snapshot(reg_copy))

    logger.info(
        "registry: applied %d items → new=%d stage_changed=%d updated=%d removed=%d / total=%d",
        len(items), len(change_summary["new"]), len(change_summary["stage_changed"]),
        len(change_summary["updated"]), len(change_summary["removed"]), len(reg_copy),
    )
    return reg_copy, changed_ids, change_summary


def get_changed_items(registry: dict[str, Regulation]) -> list[Regulation]:
    """Return all regulations with changed_this_week=True, sorted by domain then country."""
    items = [r for r in registry.values() if r.changed_this_week]
    items.sort(key=lambda r: (r.domain, r.country))
    return items


def dedup_screened(items: list[ScreenedItem]) -> list[ScreenedItem]:
    """URL-based dedup before passing to apply_screened_items."""
    seen: set[str] = set()
    out: list[ScreenedItem] = []
    for item in items:
        key = item.url.rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    removed = len(items) - len(out)
    if removed:
        logger.info("dedup: removed %d duplicate URLs", removed)
    return out


# ── snapshot helpers (changelog diff용) ──────────────────────────────────────

def build_url_snapshot(registry: dict[str, Regulation]) -> dict:
    """Registry를 {identity_key: {regulation_id, lifecycle_stage, date_text, name}} 로 변환.

    identity_key 우선순위 (source_url 비어 있거나 중복이면 fallback):
    1. normalized(source_url)     — URL이 있고 충돌 없는 경우
    2. canonical_key              — regulation_id에서 국가 suffix 제거
    3. f"{name}|{country}"       — canonical_key도 없는 경우
    충돌(동일 source_url 복수 항목)은 regulation_id를 key로 사용해 각 항목을 유지.
    """
    def _identity(rid: str, r: Regulation) -> str:
        if r.source_url:
            return r.source_url.rstrip("/").lower()
        ck = rid.rsplit("_", 1)[0] if "_" in rid else rid
        return ck if ck else f"{r.name}|{r.country}".lower()

    # 충돌 감지 (1-pass)
    key_counts: dict[str, int] = {}
    for rid, r in registry.items():
        k = _identity(rid, r)
        key_counts[k] = key_counts.get(k, 0) + 1

    collision_keys = {k for k, cnt in key_counts.items() if cnt > 1}
    if collision_keys:
        logger.warning(
            "build_url_snapshot: %d 충돌 source_url → regulation_id로 fallback",
            len(collision_keys),
        )

    snapshot: dict[str, dict] = {}
    for rid, r in registry.items():
        k = _identity(rid, r)
        key = rid if k in collision_keys else k
        snapshot[key] = {
            "regulation_id": r.regulation_id,
            "lifecycle_stage": r.lifecycle_stage,
            "date_text": r.date_text or "",
            "name": r.name,
        }

    return snapshot


def classify_changes(prev_snap: dict, curr_snap: dict) -> dict:
    """URL-keyed 스냅샷 두 개를 비교해 {new, stage_changed, updated, removed} URL 리스트 반환.

    단일 진실원본: apply_screened_items와 _upsert_changelog 모두 이 함수를 호출.
    - stage_changed 우선: lifecycle_stage 변경 시 단계변경으로 분류, name/date_text 변경은 updated.
    - 둘 다 변경이면 단계변경으로 분류 (한 번만 카운트).
    """
    prev_keys = set(prev_snap.keys())
    curr_keys = set(curr_snap.keys())

    stage_changed: list[str] = []
    updated: list[str] = []
    for url in sorted(prev_keys & curr_keys):
        p, c = prev_snap[url], curr_snap[url]
        if p["lifecycle_stage"] != c["lifecycle_stage"]:
            stage_changed.append(url)
        elif p.get("date_text") != c.get("date_text") or _normalize_name(
            p.get("name", "")
        ) != _normalize_name(c.get("name", "")):
            updated.append(url)

    return {
        "new": sorted(curr_keys - prev_keys),
        "stage_changed": stage_changed,
        "updated": updated,
        "removed": sorted(prev_keys - curr_keys),
    }
