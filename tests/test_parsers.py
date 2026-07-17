"""Offline parser tests against recorded fixtures.

If a live source drifts, comparing its output against these contracts tells us
whether the parser or the site changed.
"""

import json
from pathlib import Path

import pytest

from politrack.models import amount_midpoint
from politrack.sources import house, oge, senate
from politrack.sources.base import SourceUnavailable

FIXTURES = Path(__file__).parent / "fixtures"


def test_house_index_parses_ptrs_only():
    text = (FIXTURES / "house_index.txt").read_text()
    refs = house.parse_index(text, 2026)
    assert refs, "expected at least one PTR row in fixture"
    for ref in refs:
        assert ref.source == "house"
        assert ref.filing_type == "ptr"
        assert ref.doc_url.endswith(f"{ref.external_id}.pdf")
        assert "\r" not in ref.external_id
        assert ref.person_name


def test_house_index_drift_raises():
    with pytest.raises(SourceUnavailable):
        house.parse_index("SomeColumn\tOther\nfoo\tbar\n", 2026)


def test_senate_row_parsing():
    payload = json.loads((FIXTURES / "senate_data.json").read_text())
    refs = [r for r in (senate._parse_row(row) for row in payload["data"]) if r]
    assert len(refs) == 3
    electronic = refs[0]
    assert electronic.filing_type == "ptr"
    assert electronic.doc_kind == "html"
    assert electronic.external_id == "392ac3e5-07f6-4f8c-840f-84e9066ffb29"
    assert electronic.person_name == "Thomas H Tuberville"
    assert electronic.filed_date == "2026-07-16"
    paper = refs[2]
    assert paper.filing_type == "ptr_paper"
    assert "/view/paper/" in paper.doc_url


def test_senate_row_drift_raises():
    with pytest.raises(SourceUnavailable):
        senate._parse_row(["only", "three", "cells"])
    with pytest.raises(SourceUnavailable):
        senate._parse_row(["a", "b", "c", "no link here", "07/01/2026"])


def test_oge_view_parses_278t_only():
    page = (FIXTURES / "oge_bydate.html").read_text()
    refs = oge.parse_view(page)
    assert refs, "expected at least one 278-T row in fixture"
    for ref in refs:
        assert ref.source == "oge"
        assert ref.chamber == "executive"
        assert ref.filing_type == "278t"
        assert len(ref.external_id) == 32
        assert ref.doc_url.startswith("https://extapps2.oge.gov/")
        assert "," not in ref.person_name  # "Last, First" flipped to "First Last"


def test_oge_drift_raises():
    with pytest.raises(SourceUnavailable):
        oge.parse_view("<html><body><p>maintenance page</p></body></html>")


def test_amount_midpoint():
    assert amount_midpoint("$1,001 - $15,000") == 8_000
    assert amount_midpoint("$15,001 - $50,000") == 32_500
    assert amount_midpoint(None) is None
    assert amount_midpoint("Over $50,000,000") == 50_000_000
