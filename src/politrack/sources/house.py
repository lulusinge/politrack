"""House of Representatives financial disclosures (Clerk of the House).

Index: yearly ZIP with a TSV listing every filing; FilingType 'P' = Periodic
Transaction Report. PTR documents are PDFs at a predictable URL.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime

import httpx

from .. import config
from ..models import FilingRef
from .base import SourceUnavailable

name = "house"

EXPECTED_COLUMNS = {"Last", "First", "FilingType", "FilingDate", "DocID", "Year"}


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": config.USER_AGENT},
        timeout=config.HTTP_TIMEOUT,
        follow_redirects=True,
    )


def _parse_filed_date(raw: str) -> str | None:
    try:
        return datetime.strptime(raw.strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def poll(year: int | None = None) -> list[FilingRef]:
    year = year or date.today().year
    url = config.HOUSE_INDEX_URL.format(year=year)
    try:
        with _client() as client:
            resp = client.get(url)
            resp.raise_for_status()
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            txt_name = next(n for n in zf.namelist() if n.endswith(".txt"))
            text = zf.read(txt_name).decode("utf-8", errors="replace")
    except (httpx.HTTPError, zipfile.BadZipFile, StopIteration) as e:
        raise SourceUnavailable(f"house index fetch failed: {e}") from e
    return parse_index(text, year)


def parse_index(text: str, year: int) -> list[FilingRef]:
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if reader.fieldnames is None or not EXPECTED_COLUMNS.issubset(set(reader.fieldnames)):
        raise SourceUnavailable(
            f"house index layout drift: columns {reader.fieldnames}"
        )

    refs: list[FilingRef] = []
    for row in reader:
        if (row.get("FilingType") or "").strip() != "P":
            continue
        doc_id = (row.get("DocID") or "").strip()
        if not doc_id:
            continue
        person = " ".join(
            p for p in [(row.get("First") or "").strip(), (row.get("Last") or "").strip()] if p
        )
        refs.append(
            FilingRef(
                source="house",
                external_id=doc_id,
                person_name=person or None,
                chamber="house",
                filing_type="ptr",
                filed_date=_parse_filed_date(row.get("FilingDate") or ""),
                doc_url=config.HOUSE_PTR_PDF_URL.format(year=year, doc_id=doc_id),
                doc_kind="pdf",
            )
        )
    return refs


def fetch_document(doc_url: str) -> tuple[bytes, str]:
    try:
        with _client() as client:
            resp = client.get(doc_url)
            resp.raise_for_status()
            content = resp.content
    except httpx.HTTPError as e:
        raise SourceUnavailable(f"house document fetch failed: {e}") from e
    if not content.startswith(b"%PDF"):
        raise SourceUnavailable(f"house document is not a PDF: {doc_url}")
    return content, "pdf"
