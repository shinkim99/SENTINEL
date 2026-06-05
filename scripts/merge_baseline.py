"""baseline_battery.json → registry.json AI 매칭 머지 (최초 1회, 멱등).

같은 규제인지 LLM(haiku)으로 의미 비교:
  - 매칭 → 기존 regulation_id 유지, 빈 필드 baseline 값으로 보강.
  - 미매칭 → baseline 항목 신규 추가 (changed_this_week=False).

멱등 가드: registry.json 에 "baseline_merged":["baseline_battery"] 기록.
         재실행 시 이 플래그가 있으면 즉시 종료.

비용 최소화: 국가(country)가 같은 기존 항목끼리만 비교.
             LLM 호출은 baseline 항목당 1회 (max_tokens=5).

실행:
  python -m scripts.merge_baseline
  python scripts\\merge_baseline.py   (직접 실행도 가능)

완료 후 registry.json 변경분을 git commit 하세요:
  git add data/state/registry.json
  git commit -m "chore: merge baseline_battery into registry"
  git push
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import asyncio
import json
import logging
from collections import defaultdict
from copy import deepcopy
from datetime import datetime

from anthropic import AsyncAnthropic

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app.config import get_settings
from app.models import HistoryEntry, Regulation

logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
logger = logging.getLogger("merge_baseline")

_BASELINE_PATH = Path("data/seed/baseline_battery.json")
_REGISTRY_PATH = Path("data/state/registry.json")
_BASELINE_KEY = "baseline_battery"
_MODEL = "claude-haiku-4-5-20251001"


# ── LLM 매칭 ─────────────────────────────────────────────────────────────────

async def _find_match(
    baseline: Regulation,
    candidates: list[Regulation],
    client: AsyncAnthropic,
) -> Regulation | None:
    """baseline 과 같은 규제인 기존 항목을 반환. 없으면 None."""
    if not candidates:
        return None

    lines = "\n".join(
        f"[{i+1}] {c.regulation_id}: {c.name} | {c.summary[:100]}"
        for i, c in enumerate(candidates)
    )
    prompt = (
        f"Baseline regulation (country={baseline.country}):\n"
        f"  Name: {baseline.name}\n"
        f"  Summary: {baseline.summary[:150]}\n\n"
        f"Existing registry entries (same country):\n{lines}\n\n"
        "Which entry number is the SAME regulation? "
        "Korean/English name differences and abbreviations are expected. "
        "Reply with ONLY a number (1, 2, ...) or 0 for no match."
    )

    try:
        msg = await client.messages.create(
            model=_MODEL,
            max_tokens=5,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip().split()[0]
        idx = int(text) - 1  # 1-based → 0-based
        if 0 <= idx < len(candidates):
            logger.info(
                "  MATCH: baseline '%s' → existing '%s'",
                baseline.name[:40], candidates[idx].regulation_id,
            )
            return candidates[idx]
    except (ValueError, IndexError):
        pass
    except Exception as exc:
        logger.warning("  LLM 오류 (%s) — 미매칭으로 처리", type(exc).__name__)

    return None


# ── 기존 항목 보강 ─────────────────────────────────────────────────────────────

def _enrich(existing: Regulation, baseline: Regulation) -> bool:
    """빈 필드를 baseline 값으로 채움. lifecycle/name 은 기존 우선.
    보강이 일어났으면 True 반환."""
    changed = False
    if not existing.summary or len(existing.summary) < 20:
        existing.summary = baseline.summary
        changed = True
    if not existing.rd_impact or len(existing.rd_impact) < 20:
        existing.rd_impact = baseline.rd_impact
        changed = True
    if not existing.date_text:
        existing.date_text = baseline.date_text
        changed = True
    if changed:
        existing.history.append(
            HistoryEntry(
                date=baseline.checked_at,
                stage=existing.lifecycle_stage,
                note=f"baseline 정보 보강: {baseline.name[:50]}",
                source="battery-intel-portal",
            )
        )
    return changed


# ── main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    cfg = get_settings()
    if not cfg.anthropic_api_key:
        print("[ERROR] ANTHROPIC_API_KEY 미설정 — .env 를 확인하세요.")
        raise SystemExit(1)

    # 1. baseline 로드
    if not _BASELINE_PATH.exists():
        print(f"[ERROR] baseline 파일 없음: {_BASELINE_PATH}")
        raise SystemExit(1)

    baseline_raw: dict = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    baselines: list[Regulation] = [
        Regulation.model_validate(v) for v in baseline_raw.values()
    ]
    print(f"baseline 로드: {len(baselines)}건 ({_BASELINE_PATH})")

    # 2. registry 로드
    if _REGISTRY_PATH.exists():
        registry_file: dict = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    else:
        registry_file = {"committed_at": "", "digest_id": "", "regulations": []}

    # 3. 멱등 가드
    already_merged: list[str] = registry_file.get("baseline_merged", [])
    if _BASELINE_KEY in already_merged:
        print(f"[SKIP] '{_BASELINE_KEY}' 이미 머지됨 — 재실행 무시 (멱등).")
        print("  재머지가 필요하면 registry.json 의 baseline_merged 항목을 수동 제거하세요.")
        return

    # 4. registry dict 구성
    registry: dict[str, Regulation] = {}
    for item in registry_file.get("regulations", []):
        try:
            r = Regulation.model_validate(item)
            registry[r.regulation_id] = r
        except Exception as exc:
            logger.warning("registry 파싱 스킵: %s", exc)

    print(f"기존 registry: {len(registry)}건")

    # 5. 국가별 후보 그룹
    by_country: dict[str, list[Regulation]] = defaultdict(list)
    for r in registry.values():
        by_country[r.country].append(r)

    # 6. LLM 매칭 + 머지
    client = AsyncAnthropic(api_key=cfg.anthropic_api_key)
    matched_count = 0
    enriched_count = 0
    new_count = 0

    print(f"\n--- 매칭 시작 (LLM={_MODEL}) ---")
    for baseline_item in baselines:
        candidates = by_country.get(baseline_item.country, [])
        match = await _find_match(baseline_item, candidates, client)

        if match is not None:
            matched_count += 1
            if _enrich(match, baseline_item):
                enriched_count += 1
                print(f"  ENRICHED  {match.regulation_id}")
            else:
                print(f"  MATCHED   {match.regulation_id} (보강 불필요)")
        else:
            # 신규 추가
            new_item = deepcopy(baseline_item)
            new_item.changed_this_week = False
            registry[new_item.regulation_id] = new_item
            by_country[new_item.country].append(new_item)
            new_count += 1
            print(f"  NEW       {new_item.regulation_id} — {new_item.name[:40]}")

    # 7. 저장
    already_merged.append(_BASELINE_KEY)
    registry_file["baseline_merged"] = already_merged
    registry_file["committed_at"] = datetime.now().isoformat()
    registry_file["regulations"] = [r.model_dump() for r in registry.values()]

    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(
        json.dumps(registry_file, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n=== 완료 ===")
    print(f"  기존 매칭(통합): {matched_count}건  (보강 있음: {enriched_count}건)")
    print(f"  신규 추가       : {new_count}건")
    print(f"  총 레지스트리   : {len(registry)}건")
    print(f"  저장: {_REGISTRY_PATH}")
    print()
    print("다음 단계:")
    print("  git add data/state/registry.json")
    print('  git commit -m "chore: merge baseline_battery into registry"')
    print("  git push")


if __name__ == "__main__":
    asyncio.run(main())
