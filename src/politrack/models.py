"""Pydantic models shared across sources, extraction, and analysis."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FilingRef(BaseModel):
    """A disclosure filing discovered by a source poll."""

    source: Literal["house", "senate", "oge"]
    external_id: str
    person_name: str | None = None
    chamber: Literal["house", "senate", "executive"] | None = None
    filing_type: str | None = None  # 'ptr' | 'annual' | '278t' | ...
    filed_date: str | None = None  # ISO date
    doc_url: str
    doc_kind: Literal["pdf", "html"] = "pdf"


class ExtractedTrade(BaseModel):
    """One transaction extracted from a disclosure document."""

    asset_description: str = Field(
        description="Full asset name exactly as written in the filing, e.g. 'Apple Inc. - Common Stock'"
    )
    ticker: str | None = Field(
        default=None,
        description="Stock/ETF ticker symbol if stated in the filing (often in parentheses), else null",
    )
    asset_type: Literal[
        "stock", "etf", "option", "bond", "crypto", "fund", "other"
    ] = Field(description="Best classification of the asset")
    transaction_type: Literal["purchase", "sale", "partial_sale", "exchange"] = Field(
        description="Type of transaction"
    )
    amount_range: str = Field(
        description="Disclosed dollar band verbatim, e.g. '$1,001 - $15,000'"
    )
    owner: Literal["self", "spouse", "child", "joint", "unknown"] = Field(
        description="Who owns the asset (SP=spouse, DC=dependent child, JT=joint)"
    )
    trade_date: str = Field(description="Transaction date as YYYY-MM-DD")
    notification_date: str | None = Field(
        default=None, description="Notification/received date as YYYY-MM-DD if present"
    )


class FilingExtraction(BaseModel):
    """Structured contents of one disclosure filing."""

    person_name: str = Field(description="Full name of the filer")
    filing_date: str | None = Field(
        default=None, description="Date the report was filed/signed, YYYY-MM-DD"
    )
    is_amendment: bool = Field(
        default=False, description="True if this filing amends a previous one"
    )
    trades: list[ExtractedTrade] = Field(
        description="Every securities transaction listed in the filing; empty if none"
    )
    extraction_confidence: Literal["high", "medium", "low"] = Field(
        description="high = clean digital document; low = poor scan or ambiguous entries"
    )


class AnalysisResult(BaseModel):
    """Scores + narrative produced by the analysis agent."""

    investable: bool
    insider_edge_score: float  # 0-10
    alpha_remaining_score: float  # 0-10
    legislative_score: float  # -10..+10
    interest_score: float  # 0-100
    direction_alignment: str
    summary: str
    thesis_markdown: str


AMOUNT_RANGE_MIDPOINTS = {
    # Standard STOCK Act disclosure bands -> midpoint dollars
    "$1,001 - $15,000": 8_000,
    "$15,001 - $50,000": 32_500,
    "$50,001 - $100,000": 75_000,
    "$100,001 - $250,000": 175_000,
    "$250,001 - $500,000": 375_000,
    "$500,001 - $1,000,000": 750_000,
    "$1,000,001 - $5,000,000": 3_000_000,
    "$5,000,001 - $25,000,000": 15_000_000,
    "$25,000,001 - $50,000,000": 37_500_000,
    "Over $50,000,000": 50_000_000,
}


def amount_midpoint(amount_range: str | None) -> float | None:
    """Best-effort midpoint of a disclosed dollar band."""
    if not amount_range:
        return None
    normalized = " ".join(amount_range.replace("–", "-").split())
    if normalized in AMOUNT_RANGE_MIDPOINTS:
        return float(AMOUNT_RANGE_MIDPOINTS[normalized])
    # Fallback: parse any dollar figures present and average them
    import re

    figures = [float(x.replace(",", "")) for x in re.findall(r"\$?([\d,]+\d)", normalized)]
    if not figures:
        return None
    return sum(figures) / len(figures)
