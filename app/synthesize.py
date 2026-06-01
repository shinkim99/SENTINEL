"""HTML 다이제스트 합성 — 이메일 안전 inline CSS, table 레이아웃."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from html import escape

from app.models import (
    COUNTRY_LABELS_KO,
    DOMAIN_LABELS_KO,
    LIFECYCLE_COLORS,
    LIFECYCLE_LABELS_KO,
    LifecycleStage,
    ProfileSpec,
    ScreenedItem,
)

# ── constants ────────────────────────────────────────────────────────────────

_FONT = "font-family:Arial,Helvetica,sans-serif;"
_BASE_STYLE = f"{_FONT}color:#1F2937;font-size:14px;line-height:1.6;"


# ── entry point ───────────────────────────────────────────────────────────────

def build_html(items: list[ScreenedItem], profiles: list[ProfileSpec]) -> str:
    """도메인 → 국가 → 영향도 순으로 HTML 다이제스트 생성.

    Returns email-safe HTML string.
    """
    run_date = datetime.now().strftime("%Y-%m-%d")

    # Group: domain → country → items
    by_domain: dict[str, dict[str, list[ScreenedItem]]] = defaultdict(lambda: defaultdict(list))
    for item in items:
        by_domain[item.domain][item.country].append(item)

    # Domain order: follow profile list order
    profile_domains = [p.domain for p in profiles]
    sorted_domains = sorted(
        by_domain.keys(),
        key=lambda d: profile_domains.index(d) if d in profile_domains else 999,
    )

    # Per-domain counts for metric cards
    domain_counts = {d: sum(len(v) for v in by_domain[d].values()) for d in sorted_domains}

    sections = [
        _header(run_date),
        _metric_cards(len(items), domain_counts),
    ]

    for domain in sorted_domains:
        sections.append(_domain_section(domain, by_domain[domain]))

    sections.append(_footer(run_date))

    body = "\n".join(sections)
    return (
        "<!DOCTYPE html><html lang='ko'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>SENTINEL 주간 규제 다이제스트 {run_date}</title>"
        "</head>"
        f"<body style='margin:0;padding:0;background:#F9FAFB;{_BASE_STYLE}'>"
        f"<table width='100%' cellpadding='0' cellspacing='0' border='0' style='background:#F9FAFB;'>"
        "<tr><td align='center' style='padding:24px 16px;'>"
        f"<table width='680' cellpadding='0' cellspacing='0' border='0' style='max-width:680px;width:100%;'>"
        f"<tr><td>{body}</td></tr>"
        "</table></td></tr></table>"
        "</body></html>"
    )


# ── building blocks ───────────────────────────────────────────────────────────

def _header(run_date: str) -> str:
    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' border='0' "
        f"style='background:#1E3A5F;border-radius:8px 8px 0 0;margin-bottom:0;'>"
        f"<tr><td style='padding:24px 28px;'>"
        f"<p style='margin:0;{_FONT}font-size:20px;font-weight:bold;color:#FFFFFF;'>"
        f"SENTINEL 주간 규제 인텔리전스</p>"
        f"<p style='margin:4px 0 0;{_FONT}font-size:13px;color:#93C5FD;'>{run_date} 기준 | "
        f"2차전지 · 친환경 · 수소 · 우주환경</p>"
        f"</td></tr></table>"
    )


def _metric_cards(total: int, domain_counts: dict[str, int]) -> str:
    cells = _card_cell("신규 변화", str(total), "#0369A1", "#EFF6FF")
    for domain, cnt in domain_counts.items():
        label = DOMAIN_LABELS_KO.get(domain, domain)
        cells += _card_cell(label, str(cnt), "#065F46", "#ECFDF5")

    return (
        f"<table width='100%' cellpadding='0' cellspacing='8' border='0' "
        f"style='margin:16px 0;'><tr>{cells}</tr></table>"
    )


def _card_cell(label: str, value: str, text_color: str, bg: str) -> str:
    return (
        f"<td style='background:{bg};border-radius:8px;padding:16px;text-align:center;"
        f"border:1px solid #E5E7EB;'>"
        f"<p style='margin:0;{_FONT}font-size:28px;font-weight:bold;color:{text_color};'>{escape(value)}</p>"
        f"<p style='margin:4px 0 0;{_FONT}font-size:12px;color:#6B7280;'>{escape(label)}</p>"
        f"</td>"
    )


def _domain_section(domain: str, by_country: dict[str, list[ScreenedItem]]) -> str:
    label = DOMAIN_LABELS_KO.get(domain, domain)
    items_html = []

    # Country order: EU, US, KR, CN, JP, INTL, others
    country_order = ["EU", "US", "KR", "CN", "JP", "INTL"]
    sorted_countries = sorted(
        by_country.keys(),
        key=lambda c: country_order.index(c) if c in country_order else 999,
    )

    for country in sorted_countries:
        c_items = sorted(
            by_country[country],
            key=lambda it: _lifecycle_sort_key(it.lifecycle_stage),
        )
        for item in c_items:
            items_html.append(_item_card(item))

    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' border='0' "
        f"style='margin-top:24px;'>"
        f"<tr><td style='padding:12px 0 8px;border-bottom:2px solid #1E3A5F;'>"
        f"<span style='{_FONT}font-size:16px;font-weight:bold;color:#1E3A5F;'>{escape(label)}</span>"
        f"</td></tr>"
        f"<tr><td>{''.join(items_html)}</td></tr>"
        f"</table>"
    )


def _item_card(item: ScreenedItem) -> str:
    bg, fg = LIFECYCLE_COLORS.get(item.lifecycle_stage, ("#F3F4F6", "#374151"))
    stage_label = _lifecycle_label(item.lifecycle_stage)
    country_label = COUNTRY_LABELS_KO.get(item.country, item.country)

    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' border='0' "
        f"style='margin:12px 0;background:#FFFFFF;border:1px solid #E5E7EB;"
        f"border-radius:6px;border-left:4px solid {fg};'>"
        f"<tr><td style='padding:14px 16px;'>"
        # title row
        f"<table width='100%' cellpadding='0' cellspacing='0' border='0'><tr>"
        f"<td style='vertical-align:top;'>"
        f"<a href='{item.url}' style='{_FONT}font-size:14px;font-weight:bold;"
        f"color:#1E40AF;text-decoration:none;'>{escape(item.title)}</a>"
        f"</td>"
        f"<td style='white-space:nowrap;padding-left:12px;vertical-align:top;'>"
        f"<span style='background:{bg};color:{fg};{_FONT}font-size:11px;"
        f"font-weight:bold;padding:2px 8px;border-radius:10px;'>{escape(stage_label)}</span>"
        f"&nbsp;"
        f"<span style='background:#F3F4F6;color:#374151;{_FONT}font-size:11px;"
        f"padding:2px 8px;border-radius:10px;'>{escape(country_label)}</span>"
        f"</td></tr></table>"
        # meta
        f"<p style='margin:6px 0 0;{_FONT}font-size:12px;color:#6B7280;'>"
        f"출처: {escape(item.source_id)} &nbsp;|&nbsp; {escape(item.published_at)}</p>"
        # impact
        f"<p style='margin:8px 0 0;{_FONT}font-size:13px;color:#374151;'>"
        f"{escape(item.impact_summary)}</p>"
        # citation
        + (
            f"<blockquote style='margin:8px 0 0 0;padding:8px 12px;"
            f"background:#F8FAFC;border-left:3px solid #CBD5E1;"
            f"{_FONT}font-size:12px;color:#64748B;font-style:italic;'>"
            f"&ldquo;{escape(item.citation.quote[:300])}&rdquo;</blockquote>"
            if item.citation.quote else ""
        )
        + f"</td></tr></table>"
    )


def _footer(run_date: str) -> str:
    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' border='0' "
        f"style='margin-top:32px;border-top:1px solid #E5E7EB;'>"
        f"<tr><td style='padding:16px 0;text-align:center;{_FONT}font-size:11px;color:#9CA3AF;'>"
        f"SENTINEL 자동 생성 리포트 — {run_date}<br>"
        f"tier-1 출처 기반 | 모든 항목 원문 인용 포함 | lifecycle 단정은 원문 명시 기준"
        f"</td></tr></table>"
    )


def _lifecycle_label(stage: str) -> str:
    try:
        return LIFECYCLE_LABELS_KO[LifecycleStage(stage)]
    except (ValueError, KeyError):
        return stage


def _lifecycle_sort_key(stage: str) -> int:
    order = ["in_force", "enacted", "amended", "proposed", "repealed", "unclear"]
    return order.index(stage) if stage in order else 99
