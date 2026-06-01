"""EU EUR-Lex collector — OJ daily-view + TXT/HTML document fetch."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Union

import httpx

from app.models import RawItem

logger = logging.getLogger(__name__)

_DAILY_VIEW = "https://eur-lex.europa.eu/oj/daily-view/L-series/default.html"
_OJ_URI_RE = re.compile(r"/legal-content/EN/TXT/\?uri=(OJ:L_(\d{4})(\d{4})(\d+))")
# paragraph text stripping tags
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Map OJ URI type-digit patterns to readable labels (best-effort)
_DOC_TYPE_MAP: dict[str, str] = {
    "R": "Regulation",
    "L": "Directive",
    "D": "Decision",
}


def _oj_uri_to_html_url(oj_uri: str) -> str | None:
    """OJ:L_YYYYNNNNN → best-effort CELEX TXT/HTML URL."""
    m = re.match(r"OJ:L_(\d{4})(\d{4})(\d+)", oj_uri)
    if not m:
        return None
    year, issue_str, pos = m.groups()
    # The URI points directly to a TXT/HTML rendering via the OJ URI
    return f"https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri={oj_uri}"


def _clean(text: str) -> str:
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


async def _fetch_doc_snippet(
    client: httpx.AsyncClient,
    oj_uri: str,
    date: str,
) -> tuple[str, str]:
    """Fetch document HTML and extract title + opening snippet.

    Returns (title, snippet). Falls back to URI-derived label on failure.
    """
    url = f"https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri={oj_uri}"
    fallback_title = f"EU Official Journal {oj_uri} ({date})"
    try:
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            return fallback_title, ""
        html = resp.text[:8000]
        paras = re.findall(r"<p[^>]*>([^<]{5,600})</p>", html)
        cleaned = [_clean(p) for p in paras if len(p.strip()) > 8]
        title = cleaned[0] if cleaned else fallback_title
        snippet = " ".join(cleaned[:5])[:600]
        return title, snippet
    except Exception as exc:
        logger.debug("eurlex fetch_doc %s: %s", oj_uri, exc)
        return fallback_title, ""


class EurLexCollector:
    source_id = "eu-eurlex"

    def __init__(self, verify: Union[bool, str] = True) -> None:
        self._verify = verify

    async def collect(self, keywords: list[str], from_date: str) -> list[RawItem]:
        """Collect EU OJ L-series documents published in the past 7 days.

        Fetches daily-view pages for date range, then fetches each document's
        opening HTML to extract title and snippet.
        """
        # Build date list from from_date to today
        try:
            start = datetime.strptime(from_date, "%Y-%m-%d")
        except ValueError:
            start = datetime.now() - timedelta(days=7)
        today = datetime.now()
        dates = []
        d = start
        while d <= today:
            dates.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)

        oj_entries: list[tuple[str, str]] = []  # (date, oj_uri)

        async with httpx.AsyncClient(timeout=20, verify=self._verify) as client:
            # Collect OJ URIs per day
            for date_str in dates:
                try:
                    resp = await client.get(
                        _DAILY_VIEW,
                        params={"date": date_str},
                        follow_redirects=True,
                    )
                    if resp.status_code != 200:
                        continue
                    for m in _OJ_URI_RE.finditer(resp.text):
                        oj_uri = m.group(1)
                        oj_entries.append((date_str, oj_uri))
                except Exception as exc:
                    logger.warning("eurlex daily-view %s: %s", date_str, exc)

            if not oj_entries:
                logger.warning("eurlex: no OJ entries found for %d days", len(dates))
                return []

            # Deduplicate URIs
            seen_uris: set[str] = set()
            unique_entries = []
            for date_str, uri in oj_entries:
                if uri not in seen_uris:
                    seen_uris.add(uri)
                    unique_entries.append((date_str, uri))

            logger.info("eurlex: %d unique OJ documents to fetch", len(unique_entries))

            # Fetch document snippets concurrently (limit concurrency)
            sem = asyncio.Semaphore(5)

            async def fetch_with_sem(date_str: str, oj_uri: str) -> tuple[str, str, str, str]:
                async with sem:
                    title, snippet = await _fetch_doc_snippet(client, oj_uri, date_str)
                    return date_str, oj_uri, title, snippet

            tasks = [fetch_with_sem(d, u) for d, u in unique_entries]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        items: list[RawItem] = []
        for result in results:
            if isinstance(result, Exception):
                logger.debug("eurlex fetch error: %s", result)
                continue
            date_str, oj_uri, title, snippet = result
            url = f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri={oj_uri}"
            items.append(
                RawItem(
                    source_id=self.source_id,
                    title=title,
                    url=url,
                    published_at=date_str,
                    snippet=snippet,
                    country="EU",
                    raw={"oj_uri": oj_uri, "date": date_str, "title": title},
                )
            )

        logger.info("eurlex: %d items collected", len(items))
        return items
