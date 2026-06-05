"""baseline_battery.json → registry.json AI 매칭 머지 (최초 1회, 멱등).

매칭 기준 (엄격):
  - 동일 법령번호·공식 명칭(예: EU 2023/1542, §45X, CRMA, CSRD)일 때만 MATCH.
  - 같은 모법(IRA 등)의 다른 조항·다른 행정명령은 별개 규제 → NEW.
  - 주제·국가가 같아도 공식 명칭이 다르면 NEW.
  - 애매하면 NEW — false-positive 보다 중복이 안전.

멱등 가드: registry.json 의 "baseline_merged":["baseline_battery"] 플래그.
           재실행하려면 --force 옵션 사용.

실행:
  python -m scripts.merge_baseline              # 최초 머지
  python -m scripts.merge_baseline --force      # 재머지 (이전 baseline 항목 제거 후 재실행)

완료 후:
  git add data/state/registry.json
  git commit -m "chore: merge baseline_battery into registry"
  git push
"""
from __future__ import annotations

import argparse
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
_BASELINE_KEY  = "baseline_battery"
_BASELINE_SRC  = "battery-intel-portal (baseline)"  # --force 롤백 식별자
_MODEL         = "claude-haiku-4-5-20251001"


# ── 엄격한 LLM 매칭 ───────────────────────────────────────────────────────────

_MATCH_PROMPT = """\
You are a regulatory database curator. Determine if the BASELINE is the EXACT SAME \
legal instrument as one of the CANDIDATES.

STRICT match criteria — ALL must be true:
  1. Same official law number, regulation number, or unique short title
     (e.g. "EU 2023/1542", "IRA §45X", "CRMA", "CSRD", "JCMA").
  2. Same jurisdiction/country.
  3. Same scope — a different article, section, or provision of the same parent law
     is a DIFFERENT instrument, not a match.

DO NOT match any of these:
  • IRA §45X  ≠  IRA FEOC  ≠  IRA amendments  ≠  IRA §48C
    (different sections of IRA = different instruments)
  • CSRD  ≠  EU Battery Regulation  (different EU instruments)
  • Different executive orders, even if from the same administration
  • Two regulations that share the same general policy topic but have different names

BASELINE:
  Name   : {name}
  Summary: {summary}
  Country: {country}

CANDIDATES (same country):
{lines}

Reply with ONE integer:
  • The candidate NUMBER (1, 2, …) if it is the exact same instrument — no doubt.
  • 0 if no candidate matches, or if you are even slightly uncertain.
"""


async def _find_match(
    baseline: Regulation,
    candidates: list[Regulation],
    client: AsyncAnthropic,
) -> Regulation | None:
    """baseline 과 명백히 동일한 법령인 기존 항목을 반환. 없거나 애매하면 None."""
    if not candidates:
        return None

    lines = "\n".join(
        f"  [{i+1}] {c.regulation_id}  |  {c.name}  |  {c.summary[:120]}"
        for i, c in enumerate(candidates)
    )
    prompt = _MATCH_PROMPT.format(
        name=baseline.name,
        summary=baseline.summary[:200],
        country=baseline.country,
        lines=lines,
    )

    try:
        msg = await client.messages.create(
            model=_MODEL,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # 첫 토큰만 파싱 (LLM 이 "1\n" 또는 "0" 등 출력)
        token = raw.split()[0].rstrip(".")
        idx = int(token) - 1  # 1-based → 0-based
        if idx < 0:  # 0 응답
            return None
        if 0 <= idx < len(candidates):
            match = candidates[idx]
            logger.info(
                "  MATCH: '%s' → '%s'",
                baseline.name[:40], match.regulation_id,
            )
            return match
    except (ValueError, IndexError):
        # "0" 응답이거나 숫자 외 텍스트 — 미매칭 처리
        pass
    except Exception as exc:
        logger.warning("  LLM 오류 (%s) — 미매칭으로 처리", type(exc).__name__)

    return None


# ── 기존 항목 보강 ─────────────────────────────────────────────────────────────

def _enrich(existing: Regulation, baseline: Regulation) -> bool:
    """빈 필드를 baseline 값으로 채움. lifecycle/name 은 기존 우선.
    변경이 있으면 True 반환."""
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
        existing.history.append(HistoryEntry(
            date=baseline.checked_at,
            stage=existing.lifecycle_stage,
            note=f"baseline 정보 보강: {baseline.name[:50]}",
            source="battery-intel-portal",
        ))
    return changed


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="baseline_battery → registry 머지")
    p.add_argument(
        "--force",
        action="store_true",
        help=(
            "이미 머지된 baseline 도 재실행. "
            "registry 에서 이전에 추가된 baseline 전용 항목(source=battery-intel-portal (baseline)) 을 "
            "제거하고 플래그를 초기화한 뒤 재머지."
        ),
    )
    return p.parse_args()


# ── main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    args = _parse_args()
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
    print(f"baseline 로드: {len(baselines)}건  ({_BASELINE_PATH})")

    # 2. registry 파일 로드
    if _REGISTRY_PATH.exists():
        registry_file: dict = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    else:
        registry_file = {"committed_at": "", "digest_id": "", "regulations": []}

    already_merged: list[str] = registry_file.get("baseline_merged", [])

    # 3. 멱등 가드 / --force 롤백
    if _BASELINE_KEY in already_merged:
        if not args.force:
            print(f"[SKIP] '{_BASELINE_KEY}' 이미 머지됨 (멱등).")
            print("  재머지하려면: python -m scripts.merge_baseline --force")
            return

        # --force: 이전에 순수 baseline 으로 추가된 항목만 제거
        print("[FORCE] 이전 baseline 머지 롤백 중...")
        before = len(registry_file.get("regulations", []))
        registry_file["regulations"] = [
            r for r in registry_file.get("regulations", [])
            if r.get("source") != _BASELINE_SRC
        ]
        after = len(registry_file["regulations"])
        already_merged.remove(_BASELINE_KEY)
        registry_file["baseline_merged"] = already_merged
        print(f"  제거된 항목: {before - after}건 (source='{_BASELINE_SRC}')")
        print(f"  남은 항목  : {after}건")

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

    print(f"\n--- 매칭 시작 (model={_MODEL}, 엄격 모드) ---")
    for baseline_item in baselines:
        candidates = by_country.get(baseline_item.country, [])
        match = await _find_match(baseline_item, candidates, client)

        if match is not None:
            matched_count += 1
            if _enrich(match, baseline_item):
                enriched_count += 1
                print(f"  ENRICHED  {match.regulation_id}")
            else:
                print(f"  MATCHED   {match.regulation_id}  (보강 불필요)")
        else:
            new_item = deepcopy(baseline_item)
            new_item.changed_this_week = False
            registry[new_item.regulation_id] = new_item
            by_country[new_item.country].append(new_item)
            new_count += 1
            print(f"  NEW       {new_item.regulation_id}  — {new_item.name[:45]}")

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
