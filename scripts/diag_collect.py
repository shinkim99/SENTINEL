"""수집 진단 스크립트 — 소스별 첫 요청의 HTTP 상태와 수집 건수만 출력.

API 키 값은 절대 출력하지 않는다. 존재 여부·길이만 기록.

사용:
  python -m scripts.diag_collect            # 전체 소스 진단
  python -m scripts.diag_collect --source us # federal_register만
  python -m scripts.diag_collect --source eu # eurlex만
  python -m scripts.diag_collect --source kr # law_go_kr만
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta

from app.collect.eurlex import EurLexCollector
from app.collect.federal_register import FederalRegisterCollector
from app.collect.law_go_kr import LawGoKrCollector
from app.config import get_settings

# INFO 로그만 출력 (WARNING 이상은 항상 출력)
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s — %(message)s")

_DIAG_KEYWORDS = ["battery", "hydrogen", "climate", "regulation", "recycled"]


async def diag_source(source: str | None) -> None:
    cfg = get_settings()
    from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    verify = cfg.http_verify

    print(f"\n{'='*60}")
    print(f"SENTINEL 수집 진단 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  기간  : {from_date} ~ 오늘")
    print(f"  키워드: {_DIAG_KEYWORDS}")
    print(f"{'='*60}\n")

    # ── Federal Register ──────────────────────────────────────────
    if source in (None, "us"):
        print("[1] us-federal-register")
        print(f"    URL  : https://www.federalregister.gov/api/v1/documents.json")
        try:
            c = FederalRegisterCollector(verify=verify)
            items = await c.collect(_DIAG_KEYWORDS, from_date)
            if items:
                print(f"    결과 : ✓ {len(items)} 건")
                print(f"    샘플 : {items[0].title[:70]!r}")
            else:
                print("    결과 : ⚠ 0건 (키워드 매칭 없음 또는 차단)")
        except Exception as exc:
            print(f"    결과 : ✗ {type(exc).__name__}: {exc}")
        print()

    # ── EUR-Lex Cellar SPARQL ─────────────────────────────────────
    if source in (None, "eu"):
        print("[2] eu-eurlex (Cellar SPARQL)")
        print(f"    URL  : https://publications.europa.eu/webapi/rdf/sparql")
        try:
            c = EurLexCollector(verify=verify)
            items = await c.collect(_DIAG_KEYWORDS, from_date)
            if items:
                print(f"    결과 : ✓ {len(items)} 건")
                print(f"    샘플 : {items[0].title[:70]!r} ({items[0].url[:60]})")
            else:
                print("    결과 : ⚠ 0건 (키워드 매칭 없음, 차단, 또는 SPARQL 오류)")
        except Exception as exc:
            print(f"    결과 : ✗ {type(exc).__name__}: {exc}")
        print()

    # ── law.go.kr ─────────────────────────────────────────────────
    if source in (None, "kr"):
        print("[3] kr-law-go-kr")
        print(f"    URL  : https://www.law.go.kr/DRF/lawSearch.do")
        if not cfg.law_go_kr_api_key:
            print("    결과 : ⚠ LAW_GO_KR_API_KEY 미설정 — 건너뜀")
        else:
            print(f"    키   : 설정됨 (길이={len(cfg.law_go_kr_api_key)}자, 값 출력 금지)")
            print("    ⚠ 주의: OC 키는 IP 바인딩. GitHub Actions 러너 IP는 매 실행 변경됨.")
            try:
                c = LawGoKrCollector(api_key=cfg.law_go_kr_api_key, verify=verify)
                # 키워드 2개만 — IP 오류 여부를 빠르게 확인
                items = await c.collect(_DIAG_KEYWORDS[:2], from_date)
                if items:
                    print(f"    결과 : ✓ {len(items)} 건")
                    print(f"    샘플 : {items[0].title[:70]!r}")
                else:
                    print("    결과 : ⚠ 0건 (IP 바인딩 거부 또는 키워드 매칭 없음 — 상단 WARNING 로그 확인)")
            except Exception as exc:
                print(f"    결과 : ✗ {type(exc).__name__}: {exc}")
        print()

    print("진단 완료.")
    print("  403  → IP/UA 차단 의심 (상단 WARNING 로그에 status/body 확인)")
    print("  0건  → 차단 또는 키워드 매칭 없음")
    print("  timeout → 네트워크 접근 불가 (방화벽/DNS)")


def main() -> None:
    parser = argparse.ArgumentParser(description="SENTINEL 수집기 진단")
    parser.add_argument(
        "--source",
        choices=["us", "eu", "kr"],
        default=None,
        help="진단할 소스 (생략 시 전체)",
    )
    args = parser.parse_args()
    asyncio.run(diag_source(args.source))


if __name__ == "__main__":
    main()
