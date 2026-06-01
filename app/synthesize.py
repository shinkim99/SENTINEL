"""HTML 합성 모듈.

build_email  : 변경 항목만 담은 이메일 HTML (email-safe, inline CSS, table 레이아웃, JS 없음).
build_dashboard: 전체 레지스트리 대시보드 HTML (카드/테이블 2뷰, 다크/라이트 토글,
                  도메인+국가 2축 필터, 항목 클릭 시 이력 타임라인).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from html import escape
from typing import Any

from app.models import (
    ALERT_COLORS,
    ALERT_LABELS_KO,
    COUNTRY_LABELS_KO,
    DOMAIN_ICONS,
    DOMAIN_LABELS_KO,
    IMPACT_TYPE_LABELS_KO,
    LIFECYCLE_COLORS,
    LIFECYCLE_LABELS_KO,
    ProfileSpec,
    Regulation,
)

_FONT = "font-family:Arial,Helvetica,sans-serif;"
_BASE = f"{_FONT}color:#1F2937;font-size:14px;line-height:1.6;"

_COUNTRY_ORDER = ["EU", "US", "KR", "CN", "JP", "INTL"]
_LIFECYCLE_SORT = ["in_force", "enacted", "amended", "proposed", "repealed", "unclear"]


# ══════════════════════════════════════════════════════════════════════════════
# EMAIL
# ══════════════════════════════════════════════════════════════════════════════

def build_email(
    changed_items: list[Regulation],
    profiles: list[ProfileSpec],
    dashboard_url: str = "",
) -> str:
    """Email-safe HTML 다이제스트. changed_this_week 항목만 포함.

    Design: templates/digest_reference.html
    """
    run_date = datetime.now().strftime("%Y-%m-%d")
    btn_url = dashboard_url or "#"

    by_domain: dict[str, dict[str, list[Regulation]]] = defaultdict(lambda: defaultdict(list))
    for item in changed_items:
        by_domain[item.domain][item.country].append(item)

    profile_domains = [p.domain for p in profiles]
    sorted_domains = sorted(
        by_domain.keys(),
        key=lambda d: profile_domains.index(d) if d in profile_domains else 999,
    )
    domain_counts = {d: sum(len(v) for v in by_domain[d].values()) for d in sorted_domains}

    sections = [
        _email_header(run_date, btn_url),
        _email_metric_cards(len(changed_items), domain_counts),
    ]
    for domain in sorted_domains:
        sections.append(_email_domain_section(domain, by_domain[domain]))
    sections.append(_email_footer(run_date, btn_url))

    body = "\n".join(sections)
    return (
        "<!DOCTYPE html><html lang='ko'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>SENTINEL 주간 규제 다이제스트 {run_date}</title>"
        "</head>"
        f"<body style='margin:0;padding:0;background:#F0F4F8;{_BASE}'>"
        "<table width='100%' cellpadding='0' cellspacing='0' border='0' style='background:#F0F4F8;'>"
        "<tr><td align='center' style='padding:24px 16px;'>"
        "<table width='680' cellpadding='0' cellspacing='0' border='0' style='max-width:680px;width:100%;'>"
        f"<tr><td>{body}</td></tr>"
        "</table></td></tr></table>"
        "</body></html>"
    )


def _email_header(run_date: str, btn_url: str) -> str:
    btn = (
        f"<a href='{escape(btn_url)}' "
        f"style='display:inline-block;background:#38BDF8;color:#0F172A;{_FONT}"
        f"font-size:12px;font-weight:bold;text-decoration:none;"
        f"padding:8px 16px;border-radius:20px;white-space:nowrap;'>"
        f"&#x1F4CA; 전체 레이더 보기 &rarr;</a>"
    ) if btn_url and btn_url != "#" else ""

    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' border='0' "
        f"style='background:#0F2944;border-radius:10px 10px 0 0;'>"
        f"<tr><td style='padding:24px 28px;'>"
        f"<table width='100%' cellpadding='0' cellspacing='0' border='0'><tr>"
        f"<td width='4' style='background:linear-gradient(180deg,#38BDF8 0%,#6366F1 50%,#F59E0B 100%);"
        f"border-radius:2px;vertical-align:top;'>&nbsp;</td>"
        f"<td style='padding-left:14px;vertical-align:top;'>"
        f"<p style='margin:0;{_FONT}font-size:21px;font-weight:bold;color:#FFFFFF;'>&#x26A1; SENTINEL</p>"
        f"<p style='margin:4px 0 0;{_FONT}font-size:13px;color:#93C5FD;'>"
        f"주간 규제 인텔리전스 &nbsp;|&nbsp; {escape(run_date)} &nbsp;|&nbsp; "
        f"2차전지 · 친환경 · 수소 · 우주환경</p>"
        f"</td>"
        f"<td align='right' valign='middle' style='padding-left:16px;white-space:nowrap;'>{btn}</td>"
        f"</tr></table>"
        f"</td></tr></table>"
    )


def _email_metric_cards(total: int, domain_counts: dict[str, int]) -> str:
    cells = _card_cell("신규 변화", str(total), "#0369A1", "#EFF6FF", "#DBEAFE")
    for domain, cnt in domain_counts.items():
        label = DOMAIN_LABELS_KO.get(domain, domain)
        cells += _card_cell(label, str(cnt), "#065F46", "#ECFDF5", "#D1FAE5")
    return (
        f"<table width='100%' cellpadding='0' cellspacing='8' border='0' "
        f"style='background:#FFFFFF;padding:0;border-left:1px solid #E2E8F0;"
        f"border-right:1px solid #E2E8F0;'>"
        f"<tr><td style='padding:16px 20px;'>"
        f"<table width='100%' cellpadding='0' cellspacing='8' border='0'>"
        f"<tr>{cells}</tr></table>"
        f"</td></tr></table>"
    )


def _card_cell(label: str, value: str, fg: str, bg: str, border: str) -> str:
    return (
        f"<td style='background:{bg};border-radius:8px;padding:14px;text-align:center;"
        f"border:1px solid {border};'>"
        f"<p style='margin:0;{_FONT}font-size:28px;font-weight:bold;color:{fg};'>{escape(value)}</p>"
        f"<p style='margin:4px 0 0;{_FONT}font-size:11px;color:#6B7280;'>{escape(label)}</p>"
        f"</td>"
    )


def _email_domain_section(domain: str, by_country: dict[str, list[Regulation]]) -> str:
    label = DOMAIN_LABELS_KO.get(domain, domain)
    icon = DOMAIN_ICONS.get(domain, "")
    total = sum(len(v) for v in by_country.values())

    country_order = _COUNTRY_ORDER
    sorted_countries = sorted(
        by_country.keys(),
        key=lambda c: country_order.index(c) if c in country_order else 999,
    )

    rows: list[str] = []
    for country in sorted_countries:
        c_label = COUNTRY_LABELS_KO.get(country, country)
        rows.append(
            f"<tr><td style='padding:12px 24px 4px;'>"
            f"<span style='{_FONT}font-size:12px;font-weight:bold;color:#374151;"
            f"background:#F1F5F9;padding:3px 10px;border-radius:10px;'>"
            f"{escape(c_label)}</span></td></tr>"
        )
        items_sorted = sorted(
            by_country[country],
            key=lambda r: _LIFECYCLE_SORT.index(r.lifecycle_stage)
            if r.lifecycle_stage in _LIFECYCLE_SORT else 99,
        )
        for reg in items_sorted:
            rows.append(f"<tr><td style='padding:0 20px 12px;'>{_email_reg_card(reg)}</td></tr>")

    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' border='0' "
        f"style='background:#FFFFFF;margin-top:16px;border-radius:8px;"
        f"border:1px solid #E2E8F0;overflow:hidden;'>"
        f"<tr><td style='background:#F8FAFC;padding:12px 24px;border-bottom:2px solid #0F2944;'>"
        f"<span style='{_FONT}font-size:15px;font-weight:bold;color:#0F2944;'>"
        f"{escape(icon)} {escape(label)}</span>"
        f"<span style='{_FONT}font-size:12px;color:#94A3B8;margin-left:8px;'>{total}건 신규</span>"
        f"</td></tr>"
        f"<tr><td>"
        f"<table width='100%' cellpadding='0' cellspacing='0' border='0'>"
        f"{''.join(rows)}"
        f"</table></td></tr>"
        f"</table>"
    )


def _email_reg_card(reg: Regulation) -> str:
    lc_bg, lc_fg = LIFECYCLE_COLORS.get(reg.lifecycle_stage, ("#F3F4F6", "#374151"))
    al_bg, al_fg = ALERT_COLORS.get(reg.alert, ("#F3F4F6", "#374151"))
    lc_label = LIFECYCLE_LABELS_KO.get(reg.lifecycle_stage, reg.lifecycle_stage)
    al_label = ALERT_LABELS_KO.get(reg.alert, reg.alert)
    it_label = IMPACT_TYPE_LABELS_KO.get(reg.impact_type, reg.impact_type)

    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' border='0' "
        f"style='background:#FFFFFF;border:1px solid #E2E8F0;border-radius:6px;"
        f"border-left:4px solid {lc_fg};'>"
        f"<tr><td style='padding:14px 16px;'>"
        # title row
        f"<table width='100%' cellpadding='0' cellspacing='0' border='0'><tr>"
        f"<td valign='top'>"
        f"<a href='{escape(reg.source_url)}' style='{_FONT}font-size:14px;font-weight:bold;"
        f"color:#1E40AF;text-decoration:none;'>{escape(reg.name)}</a>"
        f"</td>"
        f"<td style='white-space:nowrap;padding-left:10px;' valign='top'>"
        f"<span style='background:{lc_bg};color:{lc_fg};{_FONT}font-size:11px;"
        f"font-weight:bold;padding:2px 8px;border-radius:10px;'>{escape(lc_label)}</span>"
        f"&nbsp;"
        f"<span style='background:{al_bg};color:{al_fg};{_FONT}font-size:11px;"
        f"font-weight:bold;padding:2px 8px;border-radius:10px;'>{escape(al_label)}</span>"
        f"&nbsp;"
        f"<span style='background:#F3F4F6;color:#374151;{_FONT}font-size:11px;"
        f"padding:2px 8px;border-radius:10px;'>{escape(it_label)}</span>"
        f"</td></tr></table>"
        # meta
        f"<p style='margin:6px 0 0;{_FONT}font-size:12px;color:#6B7280;'>"
        f"출처: {escape(reg.source)} &nbsp;|&nbsp; {escape(reg.date_text)}"
        f" &nbsp;|&nbsp; 신뢰도 {escape(reg.confidence)}</p>"
        # impact
        f"<p style='margin:8px 0 0;{_FONT}font-size:13px;color:#374151;'>"
        f"{escape(reg.rd_impact)}</p>"
        # citation
        + (
            f"<blockquote style='margin:8px 0 0 0;padding:8px 12px;"
            f"background:#F8FAFC;border-left:3px solid #CBD5E1;"
            f"{_FONT}font-size:12px;color:#64748B;font-style:italic;'>"
            f"&ldquo;{escape(reg.citation_quote[:300])}&rdquo;</blockquote>"
            if reg.citation_quote else ""
        )
        + f"</td></tr></table>"
    )


def _email_footer(run_date: str, btn_url: str) -> str:
    link = (
        f"<a href='{escape(btn_url)}' style='color:#0369A1;text-decoration:none;'>"
        f"전체 규제 레이더 보기 &rarr;</a>"
    ) if btn_url and btn_url != "#" else ""
    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' border='0' "
        f"style='background:#FFFFFF;border-radius:0 0 8px 8px;"
        f"border:1px solid #E2E8F0;border-top:none;'>"
        f"<tr><td style='padding:16px 24px;text-align:center;"
        f"{_FONT}font-size:11px;color:#9CA3AF;'>"
        f"SENTINEL 자동 생성 리포트 — {escape(run_date)}<br>"
        f"tier-1 출처 기반 &nbsp;|&nbsp; 원문 인용 포함 &nbsp;|&nbsp; "
        f"lifecycle 단정은 원문 명시 기준<br>{link}"
        f"</td></tr></table>"
    )


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

def build_dashboard(all_regs: list[Regulation]) -> str:
    """Full-page dashboard HTML with all registry regulations.

    Design: templates/radar_reference_v3.html
    Data injected as REG JSON array into a <script> block.
    XSS-safe: </script> injection prevented via _safe_json().
    """
    reg_data = _safe_json([r.model_dump() for r in all_regs])
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    # Replace full placeholder (including empty-array fallback) with actual data
    return (
        _DASHBOARD_TEMPLATE
        .replace("/*SENTINEL_REG_DATA*/[]", reg_data)
        .replace("/*SENTINEL_GENERATED_AT*/", generated_at)
    )


def _safe_json(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False)
    return raw.replace("</", "<\\/").replace("<!--", "<\\!--")


# ── Dashboard HTML template ───────────────────────────────────────────────────
# Mirrors radar_reference_v3.html design; REG data injected at runtime.

_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="ko" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SENTINEL Regulatory Radar</title>
<style>
:root{--bg:#F0F4F8;--surface:#FFFFFF;--surface2:#F8FAFC;--border:#E2E8F0;
  --text:#1E293B;--text2:#64748B;--text3:#94A3B8;--accent:#0F2944;
  --accent-hi:#38BDF8;--shadow:rgba(0,0,0,.07);}
[data-theme="dark"]{--bg:#0A0F1E;--surface:#161D2E;--surface2:#1E293B;
  --border:#2D3748;--text:#E2E8F0;--text2:#94A3B8;--text3:#64748B;
  --accent:#38BDF8;--accent-hi:#7DD3FC;--shadow:rgba(0,0,0,.4);}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;}
.topbar{background:var(--surface);border-bottom:1px solid var(--border);
  padding:12px 24px;display:flex;align-items:center;gap:12px;
  position:sticky;top:0;z-index:100;box-shadow:0 1px 4px var(--shadow);}
.logo{font-weight:800;font-size:18px;color:var(--accent);letter-spacing:-.5px;}
.bdg{display:inline-block;font-size:11px;font-weight:700;padding:3px 10px;border-radius:12px;}
.bdg-tot{background:#DBEAFE;color:#1E40AF;}
.bdg-chg{background:#FEF3C7;color:#92400E;}
.spacer{flex:1;}
.topbar-meta{font-size:11px;color:var(--text3);}
.controls{background:var(--surface2);border-bottom:1px solid var(--border);
  padding:10px 24px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
.fg{display:flex;gap:4px;flex-wrap:wrap;}
.fb{background:var(--surface);border:1px solid var(--border);color:var(--text2);
  font-size:12px;padding:5px 12px;border-radius:6px;cursor:pointer;transition:all .15s;line-height:1;}
.fb:hover{border-color:var(--accent);color:var(--accent);}
.fb.active{background:var(--accent);border-color:var(--accent);color:#fff;}
[data-theme="dark"] .fb.active{color:#0A0F1E;}
.sep{height:22px;width:1px;background:var(--border);flex-shrink:0;}
.vt{display:flex;border:1px solid var(--border);border-radius:6px;overflow:hidden;}
.vb{padding:5px 14px;font-size:12px;background:var(--surface);color:var(--text2);
  cursor:pointer;border:none;transition:all .15s;line-height:1;}
.vb.active{background:var(--accent);color:#fff;}
[data-theme="dark"] .vb.active{color:#0A0F1E;}
.tbtn{background:var(--surface);border:1px solid var(--border);color:var(--text2);
  font-size:12px;padding:5px 12px;border-radius:6px;cursor:pointer;}
.count-bar{padding:10px 24px 0;font-size:12px;color:var(--text3);}
#card-view{padding:16px 24px 32px;}
.cg{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:14px;}
.rc{background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:16px;cursor:pointer;transition:box-shadow .15s,transform .1s;position:relative;}
.rc:hover{box-shadow:0 4px 18px var(--shadow);transform:translateY(-2px);}
.rc.chg{border-left:4px solid #F59E0B;}
.ctag{position:absolute;top:10px;right:10px;background:#FEF3C7;color:#92400E;
  font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px;}
.cb{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px;}
.badge{font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px;}
.ct{font-size:13px;font-weight:700;color:var(--text);margin-bottom:4px;line-height:1.4;}
.cm{font-size:11px;color:var(--text2);margin-bottom:6px;}
.cs{font-size:12px;color:var(--text2);line-height:1.5;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
#table-view{padding:16px 24px 32px;display:none;overflow-x:auto;}
.rt{width:100%;border-collapse:collapse;min-width:760px;}
.rt th{background:var(--surface2);font-size:11px;font-weight:700;color:var(--text2);
  padding:9px 12px;text-align:left;border-bottom:2px solid var(--border);white-space:nowrap;}
.rt td{padding:10px 12px;border-bottom:1px solid var(--border);font-size:12px;vertical-align:middle;}
.rt tr:hover td{background:var(--surface2);cursor:pointer;}
.nc{font-weight:600;color:var(--text);max-width:260px;}
.sc{max-width:200px;color:var(--text2);overflow:hidden;white-space:nowrap;text-overflow:ellipsis;}
.modal-ov{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);
  z-index:200;align-items:center;justify-content:center;}
.modal-ov.open{display:flex;}
.modal{background:var(--surface);border-radius:12px;max-width:560px;width:90%;
  max-height:82vh;overflow:auto;padding:28px;box-shadow:0 20px 60px rgba(0,0,0,.3);}
.mhd{display:flex;align-items:flex-start;gap:12px;margin-bottom:16px;}
.mtit{font-size:16px;font-weight:700;flex:1;line-height:1.4;}
.mclose{background:none;border:none;font-size:20px;cursor:pointer;color:var(--text2);padding:0;flex-shrink:0;}
.mbd{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;}
.mimp{font-size:13px;color:var(--text2);margin-bottom:8px;line-height:1.5;}
.msrc{font-size:12px;color:var(--text3);margin-bottom:16px;}
.tllbl{font-size:12px;font-weight:700;color:var(--text);margin-bottom:10px;}
.tl{position:relative;padding-left:22px;}
.tl::before{content:'';position:absolute;left:7px;top:4px;bottom:4px;width:2px;background:var(--border);}
.tli{position:relative;margin-bottom:16px;}
.tld{position:absolute;left:-18px;top:4px;width:10px;height:10px;border-radius:50%;
  background:var(--accent);border:2px solid var(--surface);}
.tldate{font-size:11px;color:var(--text3);margin-bottom:3px;}
.tlnote{font-size:13px;color:var(--text);line-height:1.4;}
.tlsrc{font-size:11px;color:var(--text3);margin-top:2px;}
.empty{text-align:center;padding:60px 24px;color:var(--text3);}
.empty-icon{font-size:40px;margin-bottom:8px;}
.lc-proposed{background:#DBEAFE;color:#1E40AF;}
.lc-enacted{background:#FEF3C7;color:#92400E;}
.lc-in_force{background:#D1FAE5;color:#065F46;}
.lc-amended{background:#EDE9FE;color:#5B21B6;}
.lc-repealed{background:#FEE2E2;color:#991B1B;}
.lc-unclear{background:#F3F4F6;color:#374151;}
.al-urgent{background:#FEE2E2;color:#991B1B;}
.al-watch{background:#FEF9C3;color:#854D0E;}
.al-opp{background:#D1FAE5;color:#065F46;}
.al-mon{background:#F3F4F6;color:#374151;}
.dm-secondary_battery{background:#EFF6FF;color:#1E40AF;}
.dm-green_eco{background:#ECFDF5;color:#065F46;}
.dm-hydrogen{background:#FFF7ED;color:#9A3412;}
.dm-space_environment{background:#F5F3FF;color:#5B21B6;}
.ct-EU{background:#FEF3C7;color:#92400E;}
.ct-US{background:#DBEAFE;color:#1E40AF;}
.ct-KR{background:#FEE2E2;color:#991B1B;}
.ct-CN{background:#FCE7F3;color:#9D174D;}
.ct-def{background:#F3F4F6;color:#374151;}
.cf-A{color:#065F46;font-weight:700;}
.cf-B{color:#92400E;}
.cf-C{color:#94A3B8;}
</style>
</head>
<body>
<script>
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
/* SENTINEL_REG_DATA */
var REG = /*SENTINEL_REG_DATA*/[];
var _GEN = "/*SENTINEL_GENERATED_AT*/";
</script>

<div class="topbar">
  <span class="logo">⚡ SENTINEL Regulatory Radar</span>
  <span class="bdg bdg-tot" id="tot-bdg">전체 0건</span>
  <span class="bdg bdg-chg" id="chg-bdg">이번 주 0건 변경</span>
  <span class="spacer"></span>
  <span class="topbar-meta" id="meta-ts"></span>
</div>

<div class="controls">
  <div class="fg" id="df">
    <button class="fb active" data-domain="all">전체 도메인</button>
    <button class="fb" data-domain="secondary_battery">🔋 2차전지</button>
    <button class="fb" data-domain="green_eco">🌿 친환경</button>
    <button class="fb" data-domain="hydrogen">⚡ 수소</button>
    <button class="fb" data-domain="space_environment">🛸 우주환경</button>
  </div>
  <div class="sep"></div>
  <div class="fg" id="cf">
    <button class="fb active" data-country="all">전체 국가</button>
    <button class="fb" data-country="EU">EU</button>
    <button class="fb" data-country="US">미국</button>
    <button class="fb" data-country="KR">한국</button>
    <button class="fb" data-country="CN">중국</button>
    <button class="fb" data-country="INTL">국제</button>
  </div>
  <div class="sep"></div>
  <div class="vt">
    <button class="vb active" id="bcard">▤ 카드</button>
    <button class="vb" id="btable">☰ 테이블</button>
  </div>
  <button class="tbtn" id="btheme">🌙 다크</button>
</div>

<div class="count-bar" id="cbar"></div>

<div id="card-view">
  <div class="cg" id="cgrid"></div>
  <div class="empty" id="cempty" style="display:none"><div class="empty-icon">📭</div><p>표시할 규제 항목이 없습니다</p></div>
</div>

<div id="table-view">
  <table class="rt">
    <thead><tr>
      <th>규제명</th><th>도메인</th><th>국가</th><th>단계</th>
      <th>Alert</th><th>영향 요약</th><th>신뢰도</th><th>확인일</th><th>변경</th>
    </tr></thead>
    <tbody id="tbody"></tbody>
  </table>
  <div class="empty" id="tempty" style="display:none"><div class="empty-icon">📭</div><p>표시할 규제 항목이 없습니다</p></div>
</div>

<div class="modal-ov" id="mov">
  <div class="modal">
    <div class="mhd"><div class="mtit" id="mtit"></div><button class="mclose" id="mclose">✕</button></div>
    <div class="mbd" id="mbd"></div>
    <div class="mimp" id="mimp"></div>
    <div class="msrc" id="msrc"></div>
    <div class="tllbl">이력 타임라인</div>
    <div class="tl" id="mtl"></div>
  </div>
</div>

<script>
var LCL={proposed:'입법예고',enacted:'공포',in_force:'시행',amended:'개정',repealed:'폐지',unclear:'불명확'};
var LCC={proposed:'lc-proposed',enacted:'lc-enacted',in_force:'lc-in_force',amended:'lc-amended',repealed:'lc-repealed',unclear:'lc-unclear'};
var ALL={urgent:'긴급',watch:'주시',opp:'기회',mon:'모니터링'};
var ALC={urgent:'al-urgent',watch:'al-watch',opp:'al-opp',mon:'al-mon'};
var DML={secondary_battery:'2차전지',green_eco:'친환경',hydrogen:'수소',space_environment:'우주환경'};
var DMI={secondary_battery:'🔋',green_eco:'🌿',hydrogen:'⚡',space_environment:'🛸'};
var CTL={EU:'EU',US:'미국',KR:'한국',CN:'중국',JP:'일본',INTL:'국제'};
function ctc(c){var m={EU:'ct-EU',US:'ct-US',KR:'ct-KR',CN:'ct-CN'};return m[c]||'ct-def';}
function b(t,c){return '<span class="badge '+esc(c)+'">'+esc(String(t))+'</span>';}
var adm='all',act='all',view='card',dark=false;
function filt(){return REG.filter(function(r){
  if(adm!=='all'&&r.domain!==adm)return false;
  if(act!=='all'&&r.country!==act)return false;
  return true;
});}
function bar(n){document.getElementById('cbar').textContent='표시 '+n+'건 / 전체 '+REG.length+'건';}
function renderCards(){
  var items=filt(),g=document.getElementById('cgrid'),e=document.getElementById('cempty');
  bar(items.length);
  if(!items.length){g.innerHTML='';e.style.display='block';return;}
  e.style.display='none';
  g.innerHTML=items.map(function(r){
    var ch=r.changed_this_week;
    return '<div class="rc'+(ch?' chg':'')+'" onclick="openM(\''+esc(r.regulation_id)+'\')">'
      +(ch?'<span class="ctag">이번 주 ★</span>':'')
      +'<div class="cb">'
      +b((DMI[r.domain]||'')+' '+(DML[r.domain]||r.domain),'badge dm-'+r.domain)
      +b(CTL[r.country]||r.country,'badge '+ctc(r.country))
      +b(LCL[r.lifecycle_stage]||r.lifecycle_stage,'badge '+(LCC[r.lifecycle_stage]||'lc-unclear'))
      +b(ALL[r.alert]||r.alert,'badge '+(ALC[r.alert]||'al-mon'))
      +'</div>'
      +'<div class="ct">'+esc(r.name)+'</div>'
      +'<div class="cm">'+esc(CTL[r.country]||r.country)+' &nbsp;|&nbsp; '+esc(r.date_text)+'</div>'
      +'<div class="cs">'+esc(r.summary)+'</div>'
      +'</div>';
  }).join('');
}
function renderTable(){
  var items=filt(),tb=document.getElementById('tbody'),e=document.getElementById('tempty');
  bar(items.length);
  if(!items.length){tb.innerHTML='';e.style.display='block';return;}
  e.style.display='none';
  tb.innerHTML=items.map(function(r){
    return '<tr onclick="openM(\''+esc(r.regulation_id)+'\')">'
      +'<td class="nc">'+esc(r.name)+(r.changed_this_week?' '+b('이번주','badge al-watch'):'')+'</td>'
      +'<td>'+b((DMI[r.domain]||'')+' '+(DML[r.domain]||r.domain),'badge dm-'+r.domain)+'</td>'
      +'<td>'+b(CTL[r.country]||r.country,'badge '+ctc(r.country))+'</td>'
      +'<td>'+b(LCL[r.lifecycle_stage]||r.lifecycle_stage,'badge '+(LCC[r.lifecycle_stage]||'lc-unclear'))+'</td>'
      +'<td>'+b(ALL[r.alert]||r.alert,'badge '+(ALC[r.alert]||'al-mon'))+'</td>'
      +'<td class="sc">'+esc(r.rd_impact)+'</td>'
      +'<td><span class="cf-'+esc(r.confidence)+'">'+esc(r.confidence)+'</span></td>'
      +'<td style="white-space:nowrap">'+esc(r.checked_at)+'</td>'
      +'<td style="text-align:center">'+(r.changed_this_week?'★':'—')+'</td>'
      +'</tr>';
  }).join('');
}
function openM(id){
  var r=REG.find(function(x){return x.regulation_id===id;});
  if(!r)return;
  document.getElementById('mtit').textContent=r.name;
  document.getElementById('mbd').innerHTML=
    b((DMI[r.domain]||'')+' '+(DML[r.domain]||r.domain),'badge dm-'+r.domain)+' '
    +b(CTL[r.country]||r.country,'badge '+ctc(r.country))+' '
    +b(LCL[r.lifecycle_stage]||r.lifecycle_stage,'badge '+(LCC[r.lifecycle_stage]||'lc-unclear'))+' '
    +b(ALL[r.alert]||r.alert,'badge '+(ALC[r.alert]||'al-mon'));
  document.getElementById('mimp').textContent=r.rd_impact;
  document.getElementById('msrc').innerHTML=
    '출처: '+esc(r.source)+'&nbsp;&nbsp;<a href="'+esc(r.source_url)+'" target="_blank" '
    +'style="color:#0369A1;text-decoration:none;">원문 →</a>'
    +'&nbsp;&nbsp;신뢰도: <span class="cf-'+esc(r.confidence)+'">'+esc(r.confidence)+'</span>'
    +'&nbsp;&nbsp;확인일: '+esc(r.checked_at);
  var hist=(r.history||[]).slice().sort(function(a,b2){return b2.date.localeCompare(a.date);});
  document.getElementById('mtl').innerHTML=hist.length
    ?hist.map(function(h){
      return '<div class="tli"><div class="tld"></div>'
        +'<div class="tldate">'+esc(h.date)+'</div>'
        +'<div class="tlnote">'+b(LCL[h.stage]||h.stage,'badge '+(LCC[h.stage]||'lc-unclear'))+' '+esc(h.note)+'</div>'
        +'<div class="tlsrc">'+esc(h.source)+'</div></div>';
    }).join('')
    :'<p style="color:var(--text3);font-size:12px">이력 없음</p>';
  document.getElementById('mov').classList.add('open');
}
function updateStats(){
  var tot=REG.length,ch=REG.filter(function(r){return r.changed_this_week;}).length;
  document.getElementById('tot-bdg').textContent='전체 '+tot+'건';
  document.getElementById('chg-bdg').textContent='이번 주 '+ch+'건 변경';
  document.getElementById('meta-ts').textContent=_GEN?'생성: '+_GEN:'';
}
function render(){if(view==='card')renderCards();else renderTable();}
document.getElementById('df').addEventListener('click',function(e){
  var bx=e.target.closest('[data-domain]');if(!bx)return;
  adm=bx.dataset.domain;
  document.querySelectorAll('[data-domain]').forEach(function(x){x.classList.toggle('active',x===bx);});
  render();
});
document.getElementById('cf').addEventListener('click',function(e){
  var bx=e.target.closest('[data-country]');if(!bx)return;
  act=bx.dataset.country;
  document.querySelectorAll('[data-country]').forEach(function(x){x.classList.toggle('active',x===bx);});
  render();
});
document.getElementById('bcard').addEventListener('click',function(){
  view='card';
  document.getElementById('card-view').style.display='block';
  document.getElementById('table-view').style.display='none';
  this.classList.add('active');document.getElementById('btable').classList.remove('active');
  render();
});
document.getElementById('btable').addEventListener('click',function(){
  view='table';
  document.getElementById('card-view').style.display='none';
  document.getElementById('table-view').style.display='block';
  this.classList.add('active');document.getElementById('bcard').classList.remove('active');
  render();
});
document.getElementById('btheme').addEventListener('click',function(){
  dark=!dark;
  document.documentElement.setAttribute('data-theme',dark?'dark':'light');
  this.textContent=dark?'☀️ 라이트':'🌙 다크';
});
document.getElementById('mclose').addEventListener('click',function(){
  document.getElementById('mov').classList.remove('open');
});
document.getElementById('mov').addEventListener('click',function(e){
  if(e.target===this)this.classList.remove('open');
});
updateStats();render();
</script>
</body>
</html>"""
