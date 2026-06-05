"""회사 PC(한국 IP) 전용 — law.go.kr KR 규제 수집 후 data/inbox/kr_latest.json 저장.

LLM·발송 없음. LAW_GO_KR_API_KEY 만 필요.
실행 후 collect_kr.bat 이 git add/commit/push → GitHub Actions 자동 트리거.

사용:
  python scripts\\collect_kr.py          ← 직접 실행
  python -m scripts.collect_kr           ← 모듈 실행 (.bat 사용 방식)

환경변수:
  LAW_GO_KR_API_KEY  — 법제처 OC 키 (.env 또는 시스템 환경변수)
  REQUESTS_CA_BUNDLE — 사내 SSL 프록시 CA 경로 (선택)
"""
from __future__ import annotations

import sys
from pathlib import Path

# 직접 실행(python scripts\collect_kr.py) 시 프로젝트 루트를 sys.path에 추가.
# python -m scripts.collect_kr 실행 시에는 이미 루트가 path에 있어 무해.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv 없어도 시스템 환경변수에서 읽음

from app.collect.law_go_kr import LawGoKrCollector
from app.config import get_settings
from app.models import ProfileSpec, RawItem

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("collect_kr")

_INBOX_PATH = Path("data/inbox/kr_latest.json")


def _current_week() -> str:
    iso = datetime.now().isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _load_profiles(profiles_dir: Path) -> list[ProfileSpec]:
    profiles: list[ProfileSpec] = []
    for path in sorted(profiles_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        profiles.append(ProfileSpec.model_validate(raw))
    return profiles


async def main() -> int:
    cfg = get_settings()

    if not cfg.law_go_kr_api_key:
        logger.error(
            "LAW_GO_KR_API_KEY 미설정 — .env 또는 환경변수를 확인하세요. "
            "(키 값은 로그에 출력하지 않습니다)"
        )
        return 1

    # 키 값 절대 출력 금지 — 존재 여부·길이만 로깅
    logger.info("LAW_GO_KR_API_KEY: set=True, len=%d", len(cfg.law_go_kr_api_key))

    profiles = _load_profiles(cfg.profiles_dir)
    if not profiles:
        logger.error("data/profiles/ 에 프로필 JSON 없음")
        return 1

    all_keywords: list[str] = list({kw for p in profiles for kw in p.keywords})
    logger.info("프로필 %d개, 키워드 %d개", len(profiles), len(all_keywords))

    from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    logger.info("수집 기간: %s ~ 오늘", from_date)

    collector = LawGoKrCollector(api_key=cfg.law_go_kr_api_key, verify=cfg.http_verify)
    items: list[RawItem] = await collector.collect(all_keywords, from_date)

    if not items:
        logger.warning(
            "수집 결과 0건 — 키 유효 여부·IP 접근 권한 확인 필요. "
            "0건도 저장하여 Actions 가 'KR 수집 시도했으나 0건' 으로 처리하도록 합니다."
        )

    week = _current_week()
    collected_at = datetime.now(timezone.utc).isoformat()

    payload = {
        "collected_at": collected_at,
        "week": week,
        "items": [item.model_dump() for item in items],
    }

    _INBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    _INBOX_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "저장 완료: %s  (%d건, week=%s, collected_at=%s)",
        _INBOX_PATH, len(items), week, collected_at,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
