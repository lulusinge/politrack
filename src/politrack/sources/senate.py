"""Senate financial disclosures (efdsearch.senate.gov).

The eFD site sits behind Akamai TLS-fingerprint bot protection (plain httpx/curl
get 403), so we use curl_cffi with Chrome impersonation. The flow: accept the
prohibition agreement (CSRF form), then query the DataTables JSON endpoint.
Screen-scrape territory: every response shape is validated and any drift raises
SourceUnavailable so the cycle degrades gracefully instead of ingesting garbage.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from curl_cffi import requests as cffi_requests

from .. import config
from ..models import FilingRef
from .base import SourceUnavailable

name = "senate"

HOME = f"{config.SENATE_BASE_URL}/search/home/"
SEARCH = f"{config.SENATE_BASE_URL}/search/"
DATA = f"{config.SENATE_BASE_URL}/search/report/data/"

PTR_REPORT_TYPE = "11"  # eFD internal code for Periodic Transaction Reports
LINK_RE = re.compile(r'<a\s+href="(/search/view/(ptr|paper)/([0-9a-f-]+)/)"[^>]*>(.*?)</a>', re.I)


def _session() -> cffi_requests.Session:
    """Open a Chrome-impersonating session and accept the prohibition agreement."""
    session = cffi_requests.Session(impersonate="chrome")
    try:
        resp = session.get(HOME, timeout=config.HTTP_TIMEOUT)
        if resp.status_code != 200:
            raise SourceUnavailable(f"senate eFD home returned {resp.status_code}")
        csrf = session.cookies.get("csrftoken")
        if not csrf:
            m = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', resp.text)
            csrf = m.group(1) if m else None
        if not csrf:
            raise SourceUnavailable("senate eFD: no CSRF token on agreement page")
        resp = session.post(
            HOME,
            data={"prohibition_agreement": "1", "csrfmiddlewaretoken": csrf},
            headers={"Referer": HOME},
            timeout=config.HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            raise SourceUnavailable(f"senate eFD agreement POST returned {resp.status_code}")
    except SourceUnavailable:
        session.close()
        raise
    except Exception as e:
        session.close()
        raise SourceUnavailable(f"senate eFD session setup failed: {e}") from e
    return session


def _parse_row(row: list) -> FilingRef | None:
    if not isinstance(row, list) or len(row) < 5:
        raise SourceUnavailable(f"senate eFD row shape drift: {row!r}")
    first, last, _filer, link_html, filed = (str(x) for x in row[:5])
    m = LINK_RE.search(link_html)
    if not m:
        raise SourceUnavailable(f"senate eFD link format drift: {link_html!r}")
    rel_url, kind, uuid, title = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
    if "periodic transaction" not in title.lower():
        return None
    try:
        filed_iso = datetime.strptime(filed.strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        filed_iso = None
    return FilingRef(
        source="senate",
        external_id=uuid,
        person_name=f"{first.strip().title()} {last.strip().title()}".strip(),
        chamber="senate",
        filing_type="ptr" if kind == "ptr" else "ptr_paper",
        filed_date=filed_iso,
        doc_url=f"{config.SENATE_BASE_URL}{rel_url}",
        doc_kind="html",
    )


def poll() -> list[FilingRef]:
    start_date = (date.today() - timedelta(days=config.SENATE_LOOKBACK_DAYS)).strftime(
        "%m/%d/%Y 00:00:00"
    )
    with _session() as session:
        csrf = session.cookies.get("csrftoken")
        try:
            resp = session.post(
                DATA,
                data={
                    "start": "0",
                    "length": "100",
                    "report_types": f"[{PTR_REPORT_TYPE}]",
                    "filer_types": "[]",
                    "submitted_start_date": start_date,
                    "submitted_end_date": "",
                    "candidate_state": "",
                    "senator_state": "",
                    "office_id": "",
                    "first_name": "",
                    "last_name": "",
                    "csrfmiddlewaretoken": csrf or "",
                },
                headers={"Referer": SEARCH, "X-CSRFToken": csrf or ""},
                timeout=config.HTTP_TIMEOUT,
            )
            if resp.status_code != 200:
                raise SourceUnavailable(f"senate eFD data query returned {resp.status_code}")
            payload = resp.json()
        except SourceUnavailable:
            raise
        except Exception as e:
            raise SourceUnavailable(f"senate eFD data query failed: {e}") from e

    if not isinstance(payload, dict) or "data" not in payload:
        raise SourceUnavailable(f"senate eFD payload shape drift: keys={list(payload)[:5]}")

    refs = []
    for row in payload["data"]:
        ref = _parse_row(row)
        if ref is not None:
            refs.append(ref)
    return refs


def fetch_document(doc_url: str) -> tuple[bytes, str]:
    if "/view/paper/" in doc_url:
        # Paper filings are per-page scanned GIFs behind the session; extraction
        # from those is not supported yet.
        raise SourceUnavailable("senate paper (scanned) filing not supported yet")
    with _session() as session:
        try:
            resp = session.get(
                doc_url, headers={"Referer": SEARCH}, timeout=config.HTTP_TIMEOUT
            )
            if resp.status_code != 200:
                raise SourceUnavailable(f"senate document returned {resp.status_code}")
        except SourceUnavailable:
            raise
        except Exception as e:
            raise SourceUnavailable(f"senate document fetch failed: {e}") from e
        html = resp.text
    if "Periodic Transaction Report" not in html and "<table" not in html:
        raise SourceUnavailable("senate document page shape drift (no report table found)")
    return html.encode(), "html"
