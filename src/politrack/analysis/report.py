"""Markdown report writer: YAML frontmatter + summary + thesis."""

from __future__ import annotations

import re
import sqlite3

import yaml

from .. import config, db
from ..models import AnalysisResult


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower() or "unknown"


def write_report(trade: sqlite3.Row, result: AnalysisResult) -> str:
    """Write reports/{trade_id}-{ticker}.md and return its repo-relative path."""
    label = _slug(trade["ticker"] or trade["asset_description"][:30])
    filename = f"{trade['id']}-{label}.md"
    path = config.REPORTS_DIR / filename

    frontmatter = {
        "trade_id": trade["id"],
        "person": trade["person_name"],
        "chamber": trade["chamber"],
        "ticker": trade["ticker"],
        "asset": trade["asset_description"],
        "transaction": trade["transaction_type"],
        "amount_range": trade["amount_range"],
        "trade_date": trade["trade_date"],
        "disclosure_date": trade["disclosure_date"],
        "disclosure_lag_days": trade["disclosure_lag_days"],
        "investable": result.investable,
        "insider_edge_score": result.insider_edge_score,
        "alpha_remaining_score": result.alpha_remaining_score,
        "legislative_score": result.legislative_score,
        "interest_score": result.interest_score,
        "generated_at": db.now_iso(),
    }
    body = (
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
        + "---\n\n"
        + f"# {trade['person_name']}: {trade['transaction_type']} "
        + f"{trade['ticker'] or trade['asset_description']} ({trade['amount_range']})\n\n"
        + f"**Interest score: {result.interest_score:.0f}/100** — {result.direction_alignment}\n\n"
        + "## Summary\n\n"
        + result.summary.strip()
        + "\n\n## Thesis\n\n"
        + result.thesis_markdown.strip()
        + "\n"
    )
    path.write_text(body)
    return f"reports/{filename}"
