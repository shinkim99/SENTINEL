"""HTML 합성 모듈.

build_email     : 변경 항목만 담은 이메일 HTML (email-safe, inline CSS, table 레이아웃, JS 없음).
                  디자인 기준: templates/sentinel_digest_email_v2.html
                  (prism 헤더, 메트릭+funnel, 수집실패 알림 조건부, 국가 비교표,
                   domain→country 그룹, lifecycle/alert/impact_type 배지, citation,
                   history trail, 대시보드 CTA).
build_dashboard : 전체 레지스트리 대시보드 HTML.
                  디자인 기준: templates/radar_reference_v3.html (v4 — 5개 메트릭 카드,
                  prism 그라데이션 헤더, impact_type 배지, citation 모달).
                  템플릿 파일을 읽어 /* SENTINEL_REG_DATA */ 블록만 교체(마커·ID 보존).
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Optional

from app.models import (
    DOMAIN_LABELS_KO,
    LIFECYCLE_LABELS_KO,
    ProfileSpec,
    Regulation,
)

logger = logging.getLogger(__name__)

_FONT = "'Inter','Noto Sans KR','Malgun Gothic',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"

_COUNTRY_ORDER = ["US", "KR", "EU", "CN", "JP", "INTL"]
_LIFECYCLE_SORT = ["in_force", "enacted", "amended", "proposed", "repealed", "unclear"]

# 수집기 source_id → 국가 (수집 실패 시 국가 매핑용)
_SOURCE_COUNTRY = {
    "us-federal-register": "US",
    "eu-eurlex": "EU",
    "kr-law-go-kr": "KR",
}

# 도메인별 강조색 (prism 팔레트)
_DOMAIN_COLORS = {
    "secondary_battery": "#5b8def",
    "green_eco": "#22b07d",
    "hydrogen": "#a78bfa",
    "space_environment": "#ec5a9f",
}
_DOMAIN_ICONS = {
    "secondary_battery": "🔋",
    "green_eco": "🌿",
    "hydrogen": "⚡",
    "space_environment": "🛸",
}

# 국가 배지 (플래그 + 라벨 + 색)
_COUNTRY_BADGE = {
    "US": ("🇺🇸 미국", "#9a3412", "#ffedd5"),
    "KR": ("🇰🇷 한국", "#0e7490", "#cffafe"),
    "EU": ("🇪🇺 EU", "#b45309", "#fef3c7"),
    "CN": ("🇨🇳 중국", "#9d174d", "#fce7f3"),
    "JP": ("🇯🇵 일본", "#5b21b6", "#ede9fe"),
    "INTL": ("🌐 국제", "#4b5563", "#eef0f3"),
}

# lifecycle 배지 색 (bg, fg)
_LC_COLORS = {
    "proposed": ("#fef3c7", "#b45309"),
    "enacted":  ("#dbeafe", "#1e40af"),
    "in_force": ("#dcfce7", "#16a34a"),
    "amended":  ("#ede9fe", "#5b21b6"),
    "repealed": ("#fee2e2", "#991b1b"),
    "unclear":  ("#eef0f3", "#4b5563"),
}

# alert 배지 (라벨, bg, fg)
_AL_BADGE = {
    "urgent": ("● 긴급", "#fee2e2", "#991b1b"),
    "watch":  ("⚠ 주시", "#fef3c7", "#b45309"),
    "opp":    ("◎ 기회", "#d1fae5", "#047857"),
    "mon":    ("모니터링", "#eef0f3", "#6b7280"),
}

# impact_type 배지 (라벨, bg, fg)
_IT_BADGE = {
    "direct":   ("직접", "#dbeafe", "#3b6fd4"),
    "indirect": ("간접", "#eef0f3", "#6b7280"),
}

# 이메일 상단 일회성 안내 블록. 끄려면 False로 변경.
INTRO_NOTICE = False

# ══════════════════════════════════════════════════════════════════════════════
# EMAIL  (디자인: templates/sentinel_digest_email_v2.html)
# ══════════════════════════════════════════════════════════════════════════════

def build_email(
    changed_items: list[Regulation],
    profiles: list[ProfileSpec],
    dashboard_url: str = "",
    stats: Optional[dict] = None,
    digest_id: str = "",
) -> str:
    """Email-safe HTML 다이제스트. changed_this_week 항목만 포함.

    stats 가 주어지면 funnel/수집실패 알림/국가표를 채운다. (FastAPI 경로는 stats 생략 가능)
    """
    stats = stats or {}
    run_date = datetime.now().strftime("%Y-%m-%d")
    week = digest_id or stats.get("digest_id") or _iso_week()
    btn_url = dashboard_url or "#"

    by_domain: dict[str, dict[str, list[Regulation]]] = defaultdict(lambda: defaultdict(list))
    for item in changed_items:
        by_domain[item.domain][item.country].append(item)

    profile_domains = [p.domain for p in profiles]
    sorted_domains = sorted(
        by_domain.keys(),
        key=lambda d: profile_domains.index(d) if d in profile_domains else 999,
    )

    sections = [
        _email_header(week),
        _email_intro_notice(),
        _email_metrics(len(changed_items), stats),
        _email_top_cta(btn_url),
        _email_failure_alert(stats),
        _email_country_table(changed_items, profiles, stats),
    ]
    for domain in sorted_domains:
        sections.append(_email_domain_section(domain, by_domain[domain]))
    sections.append(_email_cta(btn_url))
    sections.append(_email_footer(week))

    body = "\n".join(s for s in sections if s)
    return (
        "<!DOCTYPE html><html lang='ko'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1.0'>"
        f"<title>SENTINEL 주간 규제 다이제스트 {escape(week)}</title>"
        "</head>"
        f"<body style=\"margin:0;padding:0;background:#f4f5f7;font-family:{_FONT}-webkit-font-smoothing:antialiased;\">"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='background:#f4f5f7;'>"
        "<tr><td align='center' style='padding:24px 12px;'>"
        "<table role='presentation' width='640' cellpadding='0' cellspacing='0' "
        "style='width:640px;max-width:640px;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e6e8ed;'>"
        f"{body}"
        "</table></td></tr></table>"
        "</body></html>"
    )


def _iso_week() -> str:
    iso = datetime.now().isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _email_header(week: str) -> str:
    return (
        "<tr><td bgcolor='#5b8def' style=\"background:#5b8def;"
        "background:linear-gradient(135deg,#5b8def 0%,#a78bfa 50%,#f472b6 100%);padding:26px 30px;\">"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>"
        "<td style='vertical-align:middle;'>"
        "<div style='font-size:11px;letter-spacing:2px;color:rgba(255,255,255,.82);font-weight:600;text-transform:uppercase;'>SENTINEL · Regulatory Watch</div>"
        "<div style='font-size:23px;font-weight:800;color:#ffffff;margin-top:4px;'>주간 규제 다이제스트</div>"
        "<div style='font-size:12px;color:rgba(255,255,255,.85);margin-top:3px;'>2차전지 · 친환경 · 수소 · 우주환경 — 사내 R&amp;D 본부</div>"
        "</td>"
        "<td align='right' style='vertical-align:middle;white-space:nowrap;'>"
        f"<div style='display:inline-block;background:rgba(255,255,255,.18);color:#ffffff;font-size:12px;font-weight:700;padding:6px 12px;border-radius:999px;'>{escape(week)}</div>"
        "</td></tr></table></td></tr>"
    )


def _email_intro_notice() -> str:
    """본부 발송 다이제스트 상단 일회성 안내 블록. INTRO_NOTICE=False로 비활성화."""
    if not INTRO_NOTICE:
        return ""
    return (
        "<tr><td style='padding:20px 22px 0 22px;'>"
        "<section style=\"margin:0 0 24px;padding:18px 20px;border:1px solid #e2e5e9;border-radius:8px;background:#fafbfc;color:#374151;font-size:13.5px;line-height:1.65;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;\">"
        "<div style=\"font-size:14.5px;font-weight:700;color:#111827;margin-bottom:4px;\">안내 &mdash; 규제 인텔리전스 수집 체계</div>"
        "<div style=\"color:#6b7280;font-size:12.5px;margin-bottom:16px;\">본 다이제스트가 어떤 출처를, 어떻게 검증해 전달하는지 공유드립니다.</div>"
        "<div style=\"text-transform:uppercase;letter-spacing:.04em;font-size:10.5px;font-weight:700;color:#0f766e;margin:0 0 8px;\">1. 수집 범위</div>"
        "<table width='100%' style='border-collapse:separate;border-spacing:8px 0;margin:0 0 12px;'>"
        "<tr>"
        "<td style='text-align:center;padding:12px 4px;background:#f0faf6;border-radius:8px;width:33%;'><div style='font-size:23px;font-weight:700;color:#0f766e;line-height:1;'>4</div><div style='font-size:11px;color:#5f6b66;margin-top:5px;'>추적 도메인</div></td>"
        "<td style='text-align:center;padding:12px 4px;background:#f0faf6;border-radius:8px;width:33%;'><div style='font-size:23px;font-weight:700;color:#0f766e;line-height:1;'>3</div><div style='font-size:11px;color:#5f6b66;margin-top:5px;'>규제 권역 KR&middot;US&middot;EU</div></td>"
        "<td style='text-align:center;padding:12px 4px;background:#f0faf6;border-radius:8px;width:33%;'><div style='font-size:23px;font-weight:700;color:#0f766e;line-height:1;'>9</div><div style='font-size:11px;color:#5f6b66;margin-top:5px;'>1차 출처 (관보&middot;규제기관)</div></td>"
        "</tr>"
        "</table>"
        "<div style='font-size:11px;color:#6b7280;margin:0 0 6px;'>공통 (전 도메인)</div>"
        "<div style='margin:0 0 10px;'>"
        "<span style=\"display:inline-block;padding:4px 10px;margin:0 6px 6px 0;border:1px solid #cfd8dc;border-radius:6px;background:#fff;font-size:12px;color:#37474f;\">국가법령정보센터<span style='color:#90a4ae;'> &middot; KR</span></span>"
        "<span style=\"display:inline-block;padding:4px 10px;margin:0 6px 6px 0;border:1px solid #cfd8dc;border-radius:6px;background:#fff;font-size:12px;color:#37474f;\">Federal Register<span style='color:#90a4ae;'> &middot; US</span></span>"
        "<span style=\"display:inline-block;padding:4px 10px;margin:0 6px 6px 0;border:1px solid #cfd8dc;border-radius:6px;background:#fff;font-size:12px;color:#37474f;\">EUR-Lex<span style='color:#90a4ae;'> &middot; EU</span></span>"
        "</div>"
        "<div style='font-size:11px;color:#6b7280;margin:0 0 6px;'>우주환경 전문기관</div>"
        "<div style='margin:0 0 10px;'>"
        "<span style=\"display:inline-block;padding:4px 10px;margin:0 6px 6px 0;border:1px solid #cfd8dc;border-radius:6px;background:#fff;font-size:12px;color:#37474f;\">FCC</span>"
        "<span style=\"display:inline-block;padding:4px 10px;margin:0 6px 6px 0;border:1px solid #cfd8dc;border-radius:6px;background:#fff;font-size:12px;color:#37474f;\">FAA/AST</span>"
        "<span style=\"display:inline-block;padding:4px 10px;margin:0 6px 6px 0;border:1px solid #cfd8dc;border-radius:6px;background:#fff;font-size:12px;color:#37474f;\">ITU-BR</span>"
        "<span style=\"display:inline-block;padding:4px 10px;margin:0 6px 6px 0;border:1px solid #cfd8dc;border-radius:6px;background:#fff;font-size:12px;color:#37474f;\">ESA</span>"
        "<span style=\"display:inline-block;padding:4px 10px;margin:0 6px 6px 0;border:1px solid #cfd8dc;border-radius:6px;background:#fff;font-size:12px;color:#37474f;\">EUSPA</span>"
        "<span style=\"display:inline-block;padding:4px 10px;margin:0 6px 6px 0;border:1px solid #cfd8dc;border-radius:6px;background:#fff;font-size:12px;color:#37474f;\">NOAA</span>"
        "</div>"
        "<div style='font-size:12px;color:#6b7280;margin:0 0 16px;'>모두 관보&middot;규제기관 등 1차 출처입니다. 산업 매체는 발견 보조용으로만 쓰고, 1차 출처로 검증된 항목만 보고에 포함합니다.</div>"
        "<div style=\"text-transform:uppercase;letter-spacing:.04em;font-size:10.5px;font-weight:700;color:#0f766e;margin:0 0 6px;\">2. 검증 방식</div>"
        "<div style='margin:0 0 16px;'>수집 &rarr; 도메인&middot;국가 선별 &rarr; 영향도&middot;규제단계 분석 + <strong>원문 인용 검증</strong> &rarr; 지난주 대비 변경분 추출. 각 항목의 규제 단계(<strong>입법예고 / 공포 / 시행 / 개정 / 폐지</strong>)를 명시하며, 원문 인용으로 검증되지 않는 항목은 제외합니다(추정&middot;환각 차단). 특정 출처의 수집 0건은 '변화 없음'이 아니라 '<strong>수집 실패</strong>'로 구분&middot;표시해 조용한 누락을 막습니다. <span style='color:#6b7280;'>(운영: 국내 출처는 접근 제약으로 사내에서 주 1회 동기화합니다.)</span></div>"
        "<div style=\"text-transform:uppercase;letter-spacing:.04em;font-size:10.5px;font-weight:700;color:#0f766e;margin:0 0 6px;\">3. 확장 예정</div>"
        "<div style='margin:0 0 16px;'>국민참여입법센터 Open API 연계 &rarr; 우주항공청(KASA) 등 <strong>입법예고&middot;행정예고(공포 이전 단계)</strong> 수집 추가 (현재 연동 검증 중).</div>"
        "<div style='padding:14px 16px;background:#f0faf6;border-radius:8px;'>"
        "<div style=\"text-transform:uppercase;letter-spacing:.04em;font-size:10.5px;font-weight:700;color:#0f766e;margin:0 0 8px;\">커버리지 &mdash; 공백은 투명하게, 보완은 계획적으로</div>"
        "<div style='margin:0 0 5px;'>입법예고(국내&middot;공포 이전) <span style='color:#0f766e;font-weight:600;'>&rarr; 국민참여입법센터 연계로 보완 중</span></div>"
        "<div style='margin:0 0 5px;'>관심국 확장(중국 등) <span style='color:#0f766e;font-weight:600;'>&rarr; 출처 검증되는 대로 순차 추가</span></div>"
        "<div style='margin:0 0 10px;'>포털 외 고시&middot;공고 <span style='color:#0f766e;font-weight:600;'>&rarr; 누락 의심 회신 시 출처 즉시 보강</span></div>"
        "<div style=\"font-size:13px;color:#1f2937;border-top:1px solid #d4ebe2;padding-top:10px;\">확보된 출처 범위는 빠짐없이 추적하도록 설계했으며, <strong>보고된 항목은 모두 1차 출처로 검증된 사실</strong>입니다. 공백은 위처럼 공유하고 순차 보완하니 신뢰하셔도 됩니다.</div>"
        "</div>"
        "</section>"
        "</td></tr>"
    )


def _email_metrics(changed_count: int, stats: dict) -> str:
    collected = stats.get("total_collected", "—")
    s1 = stats.get("passed_screen1", "—")
    s2 = stats.get("passed_screen2", "—")
    changed = stats.get("changed_this_week", changed_count)
    dropped = stats.get("dropped_citation_mismatch", 0)
    noise = ""
    if isinstance(collected, int) and isinstance(s1, int):
        noise = f"노이즈 {max(collected - s1, 0)}건 필터 · 인용 미확인 {dropped}건 drop"

    return (
        "<tr><td style='padding:20px 22px 6px 22px;'>"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr>"
        "<td width='40%' style='padding:6px;'>"
        "<div style='background:#f0f5ff;border:1px solid #d8e4fb;border-radius:12px;padding:16px;text-align:center;'>"
        f"<div style='font-size:30px;font-weight:800;color:#3b6fd4;line-height:1;'>{changed_count}</div>"
        "<div style='font-size:12px;color:#6b7280;margin-top:5px;'>이번 주 신규 · 변경</div>"
        "</div></td>"
        "<td width='60%' style='padding:6px;'>"
        "<div style='background:#f7f8fa;border:1px solid #eceef2;border-radius:12px;padding:14px 16px;'>"
        "<div style='font-size:11px;color:#9ca3af;margin-bottom:6px;'>수집 → 스크리닝 파이프라인</div>"
        "<div style='font-size:13px;color:#374151;font-weight:600;'>"
        f"수집 <span style='color:#111827;'>{collected}</span> "
        f"<span style='color:#d1d5db;'>→</span> 1차 <span style='color:#111827;'>{s1}</span> "
        f"<span style='color:#d1d5db;'>→</span> 2차 <span style='color:#111827;'>{s2}</span> "
        f"<span style='color:#d1d5db;'>→</span> 변경 <span style='color:#3b6fd4;'>{changed}</span>"
        "</div>"
        + (f"<div style='font-size:11px;color:#9ca3af;margin-top:6px;'>{escape(noise)}</div>" if noise else "")
        + "</div></td>"
        "</tr></table></td></tr>"
    )


def _email_top_cta(btn_url: str) -> str:
    """요약 지표 바로 아래 · 첫 콘텐츠 섹션 위에 배치하는 대시보드 바로가기 버튼."""
    if not btn_url or btn_url == "#":
        return ""
    return (
        "<tr><td style='padding:0 22px;'>"
        "<table width='100%' cellpadding='0' cellspacing='0' border='0' style='margin-top:16px;'>"
        "<tr><td align='center' style='background:#FFFFFF;border:1px solid #E2E8F0;border-radius:8px;padding:18px;'>"
        f"<a href='{escape(btn_url)}' style='display:inline-block;background:#0F2944;color:#FFFFFF;font-family:Arial,Helvetica,sans-serif;font-size:14px;font-weight:bold;text-decoration:none;padding:13px 30px;border-radius:24px;'>&#x1F4CA;&nbsp; 전체 레지스트리 대시보드 보기 &nbsp;&rarr;</a>"
        "</td></tr></table>"
        "</td></tr>"
    )


def _email_failure_alert(stats: dict) -> str:
    failures: list[str] = stats.get("collection_failures") or []
    if not failures:
        return ""
    labels = []
    for sid in failures:
        country = _SOURCE_COUNTRY.get(sid, "")
        ctl = _COUNTRY_BADGE.get(country, (sid, "", ""))[0] if country else sid
        labels.append(f"{escape(str(ctl))}({escape(sid)})")
    joined = ", ".join(labels)
    return (
        "<tr><td style='padding:4px 28px 0 28px;'>"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        "style='background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;'><tr><td style='padding:11px 14px;'>"
        "<span style='font-size:12px;font-weight:700;color:#b45309;'>⚠ 수집 실패</span>"
        f"<span style='font-size:12px;color:#92400e;'> · {joined} 0건 수집 — 네트워크/접근 문제로 추정. "
        "<b>\"변화 없음\"이 아니라 미수집</b>이므로 해당 출처 항목은 이번 주 누락. 운영자 확인 필요.</span>"
        "</td></tr></table></td></tr>"
    )


def _email_country_table(
    changed_items: list[Regulation],
    profiles: list[ProfileSpec],
    stats: dict,
) -> str:
    by_country: dict[str, list[Regulation]] = defaultdict(list)
    for item in changed_items:
        by_country[item.country].append(item)

    failures: list[str] = stats.get("collection_failures") or []
    failed_countries = {_SOURCE_COUNTRY.get(sid) for sid in failures if _SOURCE_COUNTRY.get(sid)}

    rows: list[str] = []
    countries = sorted(
        by_country.keys(),
        key=lambda c: _COUNTRY_ORDER.index(c) if c in _COUNTRY_ORDER else 99,
    )
    for c in countries:
        regs = by_country[c]
        label = _COUNTRY_BADGE.get(c, (c, "#374151", "#eef0f3"))[0]
        domains = " · ".join(
            dict.fromkeys(DOMAIN_LABELS_KO.get(r.domain, r.domain) for r in regs)
        )
        rows.append(
            f"<tr><td style='padding:9px 12px;color:#374151;font-weight:600;border-bottom:1px solid #f3f4f6;'>{escape(label)}</td>"
            f"<td style='padding:9px 12px;color:#3b6fd4;font-weight:700;border-bottom:1px solid #f3f4f6;'>{len(regs)}</td>"
            f"<td style='padding:9px 12px;color:#6b7280;border-bottom:1px solid #f3f4f6;'>{escape(domains)}</td></tr>"
        )

    for c in sorted(failed_countries - set(by_country.keys())):
        label = _COUNTRY_BADGE.get(c, (c, "", ""))[0]
        rows.append(
            f"<tr><td style='padding:9px 12px;color:#b45309;font-weight:600;'>{escape(label)}</td>"
            "<td style='padding:9px 12px;color:#9ca3af;'>—</td>"
            "<td style='padding:9px 12px;color:#b45309;font-size:11px;'>수집 실패 (위 알림 참조)</td></tr>"
        )

    if not rows:
        rows.append(
            "<tr><td colspan='3' style='padding:12px;color:#9ca3af;text-align:center;'>이번 주 변경 항목 없음</td></tr>"
        )

    # 변경 없는 도메인 (추적 지속)
    changed_domains = {r.domain for r in changed_items}
    quiet = [
        f"{_DOMAIN_ICONS.get(p.domain,'')} {DOMAIN_LABELS_KO.get(p.domain, p.domain)}"
        for p in profiles
        if p.domain not in changed_domains
    ]
    quiet_line = (
        f"<div style='font-size:11px;color:#9ca3af;margin-top:7px;'>변경 없는 도메인 (추적 지속): {escape(', '.join(quiet))} — 이번 주 변화 없음</div>"
        if quiet else ""
    )

    return (
        "<tr><td style='padding:16px 28px 4px 28px;'>"
        "<div style='font-size:13px;font-weight:700;color:#111827;margin-bottom:8px;'>국가별 변경 분포</div>"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        "style='border:1px solid #eceef2;border-radius:10px;overflow:hidden;font-size:12.5px;'>"
        "<tr style='background:#f7f8fa;'>"
        "<td style='padding:8px 12px;color:#9ca3af;font-size:11px;font-weight:600;border-bottom:1px solid #eceef2;'>국가</td>"
        "<td style='padding:8px 12px;color:#9ca3af;font-size:11px;font-weight:600;border-bottom:1px solid #eceef2;'>변경</td>"
        "<td style='padding:8px 12px;color:#9ca3af;font-size:11px;font-weight:600;border-bottom:1px solid #eceef2;'>도메인</td>"
        "</tr>"
        f"{''.join(rows)}"
        "</table>"
        f"{quiet_line}"
        "</td></tr>"
    )


def _email_domain_section(domain: str, by_country: dict[str, list[Regulation]]) -> str:
    label = DOMAIN_LABELS_KO.get(domain, domain)
    icon = _DOMAIN_ICONS.get(domain, "")
    color = _DOMAIN_COLORS.get(domain, "#5b8def")
    total = sum(len(v) for v in by_country.values())

    header = (
        "<tr><td style='padding:18px 28px 0 28px;'>"
        f"<div style='font-size:15px;font-weight:700;color:#111827;border-left:3px solid {color};padding-left:10px;'>"
        f"{escape(icon)} {escape(label)} <span style='color:#9ca3af;font-weight:500;font-size:12px;'>· {total}건</span></div>"
        "</td></tr>"
    )

    sorted_countries = sorted(
        by_country.keys(),
        key=lambda c: _COUNTRY_ORDER.index(c) if c in _COUNTRY_ORDER else 99,
    )
    cards: list[str] = []
    for country in sorted_countries:
        items_sorted = sorted(
            by_country[country],
            key=lambda r: _LIFECYCLE_SORT.index(r.lifecycle_stage)
            if r.lifecycle_stage in _LIFECYCLE_SORT else 99,
        )
        for reg in items_sorted:
            cards.append(_email_reg_card(reg, color))

    return header + "".join(cards)


def _badge(label: str, bg: str, fg: str, *, white: bool = False) -> str:
    fg_final = "#ffffff" if white else fg
    return (
        f"<span style='display:inline-block;font-size:10px;font-weight:700;color:{fg_final};"
        f"background:{bg};padding:2px 8px;border-radius:6px;margin-left:3px;'>{escape(label)}</span>"
    )


def _email_reg_card(reg: Regulation, domain_color: str) -> str:
    ct_label, ct_fg, ct_bg = _COUNTRY_BADGE.get(reg.country, (reg.country, "#4b5563", "#eef0f3"))
    lc_bg, lc_fg = _LC_COLORS.get(reg.lifecycle_stage, ("#eef0f3", "#4b5563"))
    lc_label = LIFECYCLE_LABELS_KO.get(reg.lifecycle_stage, reg.lifecycle_stage)
    al_label, al_bg, al_fg = _AL_BADGE.get(reg.alert, (reg.alert, "#eef0f3", "#6b7280"))
    it_label, it_bg, it_fg = _IT_BADGE.get(reg.impact_type, (reg.impact_type, "#eef0f3", "#6b7280"))

    badges = (
        f"<span style='display:inline-block;font-size:10px;font-weight:700;color:{ct_fg};"
        f"background:{ct_bg};padding:2px 8px;border-radius:6px;'>{escape(ct_label)}</span>"
        + _badge(lc_label, lc_bg, lc_fg)
        + _badge(al_label, al_bg, al_fg)
        + _badge(it_label, it_bg, it_fg)
        + (_badge("● 이번 주", "#5b8def", "", white=True) if reg.changed_this_week else "")
    )

    # R&D 영향 박스: direct → 핑크 강조, indirect/기타 → 그레이
    if reg.impact_type == "direct":
        impact_box = (
            "<div style='font-size:12px;color:#374151;line-height:1.5;margin-top:8px;"
            "background:#fff5f7;border:1px solid #fbd5e3;border-radius:8px;padding:8px 10px;'>"
            f"<b style='color:#be3a6e;'>R&amp;D 영향</b> · {escape(reg.rd_impact)}</div>"
        )
    else:
        impact_box = (
            "<div style='font-size:12px;color:#374151;line-height:1.5;margin-top:8px;"
            "background:#f7f8fa;border-radius:8px;padding:8px 10px;'>"
            f"<b style='color:#3b6fd4;'>R&amp;D 영향</b> · {escape(reg.rd_impact)}</div>"
        )

    citation = (
        "<div style='font-size:11.5px;color:#6b7280;font-style:italic;line-height:1.5;margin-top:8px;"
        f"border-left:2px solid #e6e8ed;padding-left:10px;'>&ldquo;{escape(reg.citation_quote[:300])}&rdquo;</div>"
        if reg.citation_quote else ""
    )

    # 이력 trail (최근 2건 인라인)
    trail = ""
    if reg.history:
        recent = sorted(reg.history, key=lambda h: h.date, reverse=True)[:2]
        parts = [
            f"{escape(h.date)} {escape(LIFECYCLE_LABELS_KO.get(h.stage, h.stage))} {escape(h.note)}".strip()
            for h in recent
        ]
        trail = (
            "<div style='font-size:11px;color:#9ca3af;margin-top:8px;'>이력: "
            + " → ".join(parts) + "</div>"
        )

    source_line = (
        "<div style='font-size:11px;color:#9ca3af;margin-top:4px;'>출처 "
        f"<a href='{escape(reg.source_url)}' target='_blank' style='color:#3b6fd4;text-decoration:none;'>{escape(reg.source)} ↗</a>"
        f" · 신뢰도 {escape(reg.confidence)} · 확인 {escape(reg.checked_at)}</div>"
    )

    return (
        "<tr><td style='padding:10px 28px 0 28px;'>"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        "style='border:1px solid #eceef2;border-radius:12px;'><tr>"
        f"<td style='padding:14px 16px;border-left:3px solid {domain_color};border-radius:12px;'>"
        f"<div>{badges}</div>"
        f"<div style='font-size:14px;font-weight:700;color:#111827;margin-top:8px;'>{escape(reg.name)}</div>"
        f"<div style='font-size:12.5px;color:#4b5563;line-height:1.55;margin-top:4px;'>{escape(reg.summary)}</div>"
        f"{impact_box}{citation}{trail}{source_line}"
        "</td></tr></table></td></tr>"
    )


def _email_cta(btn_url: str) -> str:
    if not btn_url or btn_url == "#":
        return ""
    return (
        "<tr><td align='center' style='padding:26px 28px 8px 28px;'>"
        "<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
        "<td bgcolor='#3b6fd4' style='border-radius:10px;'>"
        f"<a href='{escape(btn_url)}' target='_blank' style='display:inline-block;padding:13px 28px;color:#ffffff;font-size:14px;font-weight:700;text-decoration:none;'>전체 레지스트리 대시보드 보기 →</a>"
        "</td></tr></table>"
        "<div style='font-size:11px;color:#9ca3af;margin-top:10px;'>변경 없이 추적 중인 전체 규제 · 항목별 변경 이력 타임라인</div>"
        "</td></tr>"
    )


def _email_footer(week: str) -> str:
    return (
        "<tr><td style='padding:18px 28px 26px 28px;'>"
        "<div style='border-top:1px solid #eceef2;padding-top:14px;'>"
        "<div style='font-size:11px;color:#9ca3af;line-height:1.6;'>"
        "1차 출처 기반 자동 수집·요약. lifecycle 단계는 명시 출처 기준이며 불명확 시 "
        "<b style='color:#6b7280;'>불명확</b>으로 표기. 인용 출처 미확인 항목은 자동 제외. "
        "수집 0건은 \"변화 없음\"과 구분하여 실패로 표기."
        "</div>"
        f"<div style='font-size:11px;color:#c4c9d1;margin-top:9px;'>SENTINEL · 사내 R&amp;D 규제 인텔리전스 · 주간 발송 · {escape(week)}</div>"
        "</div></td></tr>"
    )


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD  (디자인: templates/radar_reference_v3.html — v4)
# ══════════════════════════════════════════════════════════════════════════════

_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "radar_reference_v3.html"
_REG_DATA_RE = re.compile(
    r"/\* SENTINEL_REG_DATA \*/.*?/\* END SENTINEL_REG_DATA \*/",
    re.DOTALL,
)
_CHANGELOG_RE = re.compile(
    r"/\* SENTINEL_CHANGELOG \*/.*?/\* END SENTINEL_CHANGELOG \*/",
    re.DOTALL,
)


def _iso_weeks_window(anchor: str, count: int = 8) -> list[str]:
    """anchor("YYYY-Www")를 포함한 직전 count주의 ISO 주차 라벨 리스트 (오래된순)."""
    from datetime import date, timedelta
    try:
        year, wnum = int(anchor[:4]), int(anchor[6:])
        end_mon = date.fromisocalendar(year, wnum, 1)
    except (ValueError, AttributeError, TypeError):
        d = date.today()
        iso = d.isocalendar()
        end_mon = date.fromisocalendar(iso[0], iso[1], 1)
    return [
        f"{(end_mon - timedelta(weeks=i)).isocalendar()[0]}-"
        f"W{(end_mon - timedelta(weeks=i)).isocalendar()[1]:02d}"
        for i in range(count - 1, -1, -1)
    ]


def _inject_changelog(html: str, state_dir: Path, digest_id: str = "") -> str:
    """changelog.json 최근 8주를 /* SENTINEL_CHANGELOG */ 블록에 주입.

    - digest_id 기준 8주 윈도우 생성 (연도 포함 라벨 — "2026-W25" 형식).
    - 결측 주차는 {new:0, ...}으로 채워 비연속 주차 라벨링 오류 방지.
    - 데이터 수집 이전 구간(선행 all-zero 주차)은 제거해 차트를 간결하게.
    """
    if not _CHANGELOG_RE.search(html):
        logger.warning("changelog: SENTINEL_CHANGELOG marker not found — skipping")
        return html

    week_index: dict[str, dict] = {}
    changelog_path = state_dir / "changelog.json"
    if changelog_path.exists():
        try:
            raw = json.loads(changelog_path.read_text(encoding="utf-8"))
            week_index = {w.get("week", ""): w for w in raw if w.get("week")}
        except Exception as exc:
            logger.warning("changelog: load failed (%s) — empty chart", exc)

    anchor = digest_id or _iso_week()
    window = _iso_weeks_window(anchor, count=8)

    chart_data: list[dict] = []
    for w in window:
        entry = week_index.get(w)
        chart_data.append({
            "week": w,
            "new": entry.get("new", 0) if entry else 0,
            "updated": entry.get("updated", 0) if entry else 0,
            "stage_changed": entry.get("stage_changed", 0) if entry else 0,
            "deleted": entry.get("removed", 0) if entry else 0,
        })

    # 선행 all-zero 주차 제거 (데이터 수집 전 구간)
    while chart_data and not any(
        chart_data[0][k] for k in ("new", "updated", "stage_changed", "deleted")
    ):
        chart_data.pop(0)

    replacement = (
        "/* SENTINEL_CHANGELOG */\n"
        f"var CHANGELOG = {_safe_json(chart_data)};\n"
        "/* END SENTINEL_CHANGELOG */"
    )
    return _CHANGELOG_RE.sub(lambda _m: replacement, html, count=1)


def build_dashboard(
    all_regs: list[Regulation],
    stats: Optional[dict] = None,
    digest_id: str = "",
    generated_at: str = "",
    logo_url: str = "",
    state_dir: Optional[Path] = None,
) -> str:
    """전체 레지스트리 대시보드 HTML.

    v4 템플릿(radar_reference_v3.html)을 읽어 /* SENTINEL_REG_DATA */ 블록의
    REG 배열만 교체한다. CSS/마크업/JS/element ID는 불변.
    stats 가 주어지면 수집 상태 스트립(#collect-strip)을 채워 노출한다.
    logo_url 이 주어지면 헤더 좌측에 로고 이미지를 삽입한다 (이메일 불변).
    """
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    reg_json = _safe_json([r.model_dump() for r in all_regs])

    replacement = (
        "/* SENTINEL_REG_DATA */\n"
        f"var REG = {reg_json};\n"
        "/* END SENTINEL_REG_DATA */"
    )
    html = _REG_DATA_RE.sub(lambda _m: replacement, template, count=1)

    if stats:
        html = _inject_collect_strip(html, stats, digest_id, generated_at)

    if logo_url:
        html = _inject_logo(html, logo_url)

    _state_dir = state_dir if state_dir is not None else Path("data/state")
    html = _inject_changelog(html, _state_dir, digest_id)

    return html


def _inject_logo(html: str, logo_url: str) -> str:
    """헤더 텍스트 블록 좌측에 로고 <img> 삽입 (대시보드 전용)."""
    logo_img = (
        f'<img src="{escape(logo_url)}" width="36" height="36" alt="SENTINEL" '
        'style="border-radius:8px;flex-shrink:0;align-self:center;">'
    )
    # <div class="header-row"> 안의 첫 번째 <div style="flex:1;"> 직전에 로고 삽입
    patched = re.sub(
        r'(<div class="header-row">(\s|\r?\n)*\s*)(<div style="flex:1;">)',
        lambda m: m.group(1) + logo_img + "\n    " + m.group(3),
        html,
        count=1,
    )
    if patched == html:
        logger.warning("dashboard logo: header-row 패턴 미발견 — 로고 삽입 건너뜀")
    return patched


def _inject_collect_strip(html: str, stats: dict, digest_id: str, generated_at: str) -> str:
    week = digest_id or stats.get("digest_id") or _iso_week()
    gen = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")

    by_source: dict = stats.get("by_source") or {}
    ok = sum(1 for v in by_source.values() if v.get("status") == "ok")
    failed = [sid for sid, v in by_source.items() if v.get("status") != "ok"]

    week_html = (
        f"<b>{escape(week)}</b> · 수집 {stats.get('total_collected', '—')} "
        f"→ 1차 {stats.get('passed_screen1', '—')} "
        f"→ 2차 {stats.get('passed_screen2', '—')} "
        f"→ 변경 {stats.get('changed_this_week', '—')} · 생성 {escape(gen)}"
    )
    src_parts = [f"<span class='src-ok'>정상 {ok}</span>"]
    if failed:
        src_parts.append(
            "<span class='src-fail'>실패 " + str(len(failed)) + "</span> ("
            + escape(", ".join(failed)) + ")"
        )
    src_html = " · ".join(src_parts)

    html = html.replace(
        '<div class="collect-strip" id="collect-strip" style="display:none;">',
        '<div class="collect-strip" id="collect-strip">',
    )
    html = html.replace(
        '<span id="collect-week"></span>',
        f'<span id="collect-week">{week_html}</span>',
    )
    html = html.replace(
        '<span id="collect-sources"></span>',
        f'<span id="collect-sources">{src_html}</span>',
    )
    return html


def _safe_json(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False)
    return raw.replace("</", "<\\/").replace("<!--", "<\\!--")
