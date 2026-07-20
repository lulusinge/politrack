"""Executive-branch disclosures (OGE) — Trump and Senate-confirmed appointees.

Scrapes the Office of Government Ethics Lotus Domino view "PAS Filings by Date"
and keeps the OGE Form 278-T rows (periodic transaction reports).
"""

from __future__ import annotations

import html as htmllib
import re
from datetime import datetime

import httpx

from .. import config
from ..models import FilingRef
from .base import SourceUnavailable

name = "oge"

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
HREF_RE = re.compile(r"""href=['"](https?://extapps2\.oge\.gov/201/Presiden\.nsf/[^'"]*?/([0-9A-F]{32})/\$FILE/[^'"]+)['"]""")
TAG_RE = re.compile(r"<[^>]+>")


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": config.USER_AGENT},
        timeout=config.HTTP_TIMEOUT,
        follow_redirects=True,
    )


def _text(cell: str) -> str:
    return htmllib.unescape(re.sub(r"\s+", " ", TAG_RE.sub("", cell))).strip()


def _person(last_first: str) -> str:
    parts = [p.strip() for p in last_first.split(",", 1)]
    return f"{parts[1]} {parts[0]}".strip() if len(parts) == 2 else last_first.strip()


def poll() -> list[FilingRef]:
    try:
        with _client() as client:
            resp = client.get(config.OGE_INDEX_URL)
            resp.raise_for_status()
            page = resp.text
    except httpx.HTTPError as e:
        raise SourceUnavailable(f"oge index fetch failed: {e}") from e
    return parse_view(page)


def parse_view(page: str) -> list[FilingRef]:
    rows = ROW_RE.findall(page)
    if len(rows) < 5:
        raise SourceUnavailable(f"oge index layout drift: only {len(rows)} rows")

    refs: list[FilingRef] = []
    parsed_any = False
    for row in rows:
        cells = CELL_RE.findall(row)
        if len(cells) < 5:
            continue
        posted, filing_label, person, agency, _position = (_text(c) for c in cells[:5])
        href = HREF_RE.search(row)
        if not href:
            continue
        parsed_any = True
        if not filing_label.startswith("278 Transaction"):
            continue
        doc_url, unid = href.group(1), href.group(2)
        try:
            filed_iso = datetime.strptime(posted, "%m/%d/%Y").date().isoformat()
        except ValueError:
            filed_iso = None
        refs.append(
            FilingRef(
                source="oge",
                external_id=unid,
                person_name=_person(person),
                chamber="executive",
                filing_type="278t",
                filed_date=filed_iso,
                doc_url=doc_url,
                doc_kind="pdf",
            )
        )

    if not parsed_any:
        raise SourceUnavailable("oge index layout drift: no parseable filing rows")
    return refs


def fetch_document(doc_url: str) -> tuple[bytes, str]:
    try:
        with _client() as client:
            resp = client.get(doc_url)
            resp.raise_for_status()
            content = resp.content
    except httpx.HTTPError as e:
        raise SourceUnavailable(f"oge document fetch failed: {e}") from e
    if not content.startswith(b"%PDF"):
        raise SourceUnavailable(f"oge document is not a PDF: {doc_url}")
    return content, "pdf"
