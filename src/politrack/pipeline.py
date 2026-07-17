"""Cycle orchestration: poll -> detect new -> extract -> analyze -> report."""

from __future__ import annotations

import fcntl
import json
import sqlite3
import traceback
from datetime import date

from . import config, db, extract
from .analysis import agent, report
from .models import FilingExtraction, amount_midpoint
from .sources import house, oge, senate
from .sources.base import SourceUnavailable

SOURCES = {"house": house, "senate": senate, "oge": oge}

# Asset types that can never be bought by the public — skipped without spending tokens.
NEVER_INVESTABLE = {"bond", "other"}


def _lag_days(trade_date: str | None, disclosure_date: str | None) -> int | None:
    try:
        return (date.fromisoformat(disclosure_date) - date.fromisoformat(trade_date)).days
    except (TypeError, ValueError):
        return None


def _insert_trades(
    conn: sqlite3.Connection, filing: sqlite3.Row, extraction: FilingExtraction
) -> int:
    inserted = 0
    disclosure_date = filing["filed_date"] or extraction.filing_date
    person = extraction.person_name or filing["person_name"]
    for t in extraction.trades:
        key = db.trade_dedup_key(
            filing["source"],
            filing["external_id"],
            t.asset_description,
            t.transaction_type,
            t.trade_date,
            t.amount_range,
            t.owner,
        )
        status = "pending"
        if t.asset_type in NEVER_INVESTABLE and not t.ticker:
            status = "skipped_non_investable"
        cur = conn.execute(
            """INSERT OR IGNORE INTO trades
               (filing_id, dedup_key, person_name, chamber, asset_description, ticker,
                asset_type, transaction_type, amount_range, amount_mid, owner,
                trade_date, disclosure_date, disclosure_lag_days, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                filing["id"],
                key,
                person,
                filing["chamber"],
                t.asset_description,
                t.ticker,
                t.asset_type,
                t.transaction_type,
                t.amount_range,
                amount_midpoint(t.amount_range),
                t.owner,
                t.trade_date,
                disclosure_date,
                _lag_days(t.trade_date, disclosure_date),
                status,
            ),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def extract_filing_row(conn: sqlite3.Connection, filing: sqlite3.Row) -> int:
    """Download + extract one filing. Returns number of trades inserted."""
    source_mod = SOURCES[filing["source"]]
    content, doc_kind = source_mod.fetch_document(filing["doc_url"])

    pdf_dir = config.PDF_DIR / filing["source"]
    pdf_dir.mkdir(parents=True, exist_ok=True)
    ext = "pdf" if doc_kind == "pdf" else "html"
    doc_path = pdf_dir / f"{filing['external_id']}.{ext}"
    doc_path.write_bytes(content)

    extraction = extract.extract_filing(content, doc_kind)
    n = _insert_trades(conn, filing, extraction)
    conn.execute(
        "UPDATE filings SET status = ?, doc_path = ?, doc_kind = ?, extracted_at = ?, error = NULL WHERE id = ?",
        (
            "extracted" if extraction.trades else "no_trades",
            str(doc_path.relative_to(config.REPO_ROOT)),
            doc_kind,
            db.now_iso(),
            filing["id"],
        ),
    )
    conn.commit()
    return n


def analyze_trade_row(conn: sqlite3.Connection, trade: sqlite3.Row) -> bool:
    """Run the agent on one trade; persist result + report. Returns success."""
    conn.execute("UPDATE trades SET status = 'analyzing' WHERE id = ?", (trade["id"],))
    conn.commit()
    try:
        result, tokens_in, tokens_out = agent.analyze_trade(trade)
    except Exception as e:
        conn.execute(
            "UPDATE trades SET status = 'pending', attempts = attempts + 1, error = ? WHERE id = ?",
            (str(e)[:500], trade["id"]),
        )
        conn.commit()
        raise

    report_path = report.write_report(trade, result) if result.investable else None
    conn.execute(
        """INSERT OR REPLACE INTO analyses
           (trade_id, insider_edge_score, alpha_remaining_score, legislative_score,
            interest_score, direction_alignment, summary, report_path, model,
            input_tokens, output_tokens, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            trade["id"],
            result.insider_edge_score,
            result.alpha_remaining_score,
            result.legislative_score,
            result.interest_score,
            result.direction_alignment,
            result.summary,
            report_path,
            config.ANALYSIS_MODEL,
            tokens_in,
            tokens_out,
        db.now_iso(),
        ),
    )
    conn.execute(
        "UPDATE trades SET status = ?, error = NULL WHERE id = ?",
        ("analyzed" if result.investable else "skipped_non_investable", trade["id"]),
    )
    conn.commit()
    return True


def run_cycle(
    sources: list[str] | None = None,
    limit_filings: int = 0,
    analyze: bool = True,
    analyze_limit: int = 0,
) -> dict:
    """One full watcher cycle. Returns a stats dict."""
    lock_file = open(config.DATA_DIR / ".lock", "w")
    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if another cycle runs

    conn = db.connect()
    db.init_db(conn)
    source_names = sources or list(SOURCES)
    stats = {"new_filings": 0, "trades_extracted": 0, "analyses_run": 0}
    health = {s: None for s in SOURCES}
    errors: list[str] = []

    run_id = conn.execute(
        "INSERT INTO runs (started_at) VALUES (?)", (db.now_iso(),)
    ).lastrowid
    conn.commit()

    bootstrap = conn.execute("SELECT COUNT(*) c FROM filings").fetchone()["c"] == 0

    # 1. Poll
    for name in source_names:
        try:
            refs = SOURCES[name].poll()
            health[name] = 1
        except SourceUnavailable as e:
            health[name] = 0
            errors.append(f"{name}: {e}")
            continue
        for ref in refs:
            if db.insert_filing(conn, ref) is not None:
                stats["new_filings"] += 1

    # First run ever: park the historical backlog instead of extracting a year of
    # filings. `politrack backfill` promotes the most recent ones on demand.
    if bootstrap and stats["new_filings"]:
        conn.execute("UPDATE filings SET status = 'backlog' WHERE status = 'seen'")
        conn.commit()
        print(
            f"First run: {stats['new_filings']} filings parked as backlog. "
            "Run `politrack backfill --count 100` to populate the dashboard."
        )

    # 2. Extract new filings
    to_extract = db.filings_by_status(conn, "seen", config.MAX_EXTRACT_ATTEMPTS)
    if limit_filings:
        to_extract = to_extract[:limit_filings]
    for filing in to_extract:
        try:
            stats["trades_extracted"] += extract_filing_row(conn, filing)
        except Exception as e:
            errors.append(f"extract filing {filing['id']}: {e}")
            conn.execute(
                "UPDATE filings SET attempts = attempts + 1, error = ? WHERE id = ?",
                (str(e)[:500], filing["id"]),
            )
            conn.commit()

    # 3. Analyze pending trades
    if analyze:
        limit = analyze_limit or config.MAX_ANALYSES_PER_CYCLE
        for trade in db.pending_trades(conn, limit):
            try:
                analyze_trade_row(conn, trade)
                stats["analyses_run"] += 1
            except Exception as e:
                errors.append(f"analyze trade {trade['id']}: {e}")
                traceback.print_exc()

    conn.execute(
        """UPDATE runs SET finished_at = ?, house_ok = ?, senate_ok = ?, oge_ok = ?,
           new_filings = ?, trades_extracted = ?, analyses_run = ?, errors = ? WHERE id = ?""",
        (
            db.now_iso(),
            health["house"],
            health["senate"],
            health["oge"],
            stats["new_filings"],
            stats["trades_extracted"],
            stats["analyses_run"],
            json.dumps(errors) if errors else None,
            run_id,
        ),
    )
    conn.commit()
    conn.close()
    lock_file.close()
    stats["errors"] = errors
    return stats


def run_backfill(count: int = 100) -> dict:
    """Promote recent backlog filings, extract until ~`count` trades exist, analyze the top `count`."""
    conn = db.connect()
    db.init_db(conn)
    stats = {"filings_extracted": 0, "trades_extracted": 0, "analyses_run": 0}

    while True:
        pending_trades = conn.execute(
            "SELECT COUNT(*) c FROM trades WHERE status = 'pending'"
        ).fetchone()["c"]
        if pending_trades >= count:
            break
        batch = conn.execute(
            """SELECT * FROM filings WHERE status = 'backlog'
               ORDER BY filed_date DESC LIMIT 10"""
        ).fetchall()
        if not batch:
            break
        for filing in batch:
            try:
                stats["trades_extracted"] += extract_filing_row(conn, filing)
                stats["filings_extracted"] += 1
            except Exception as e:
                print(f"extract filing {filing['id']} failed: {e}")
                conn.execute(
                    "UPDATE filings SET status = 'extract_failed', error = ? WHERE id = ?",
                    (str(e)[:500], filing["id"]),
                )
                conn.commit()

    for trade in db.pending_trades(conn, count):
        try:
            analyze_trade_row(conn, trade)
            stats["analyses_run"] += 1
            print(f"analyzed trade {trade['id']} ({trade['ticker'] or trade['asset_description']})")
        except Exception as e:
            print(f"analyze trade {trade['id']} failed: {e}")

    conn.close()
    return stats
