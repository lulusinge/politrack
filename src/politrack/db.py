"""SQLite persistence: schema, connection, typed helpers."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .models import FilingRef

SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL CHECK (source IN ('house','senate','oge')),
  external_id TEXT NOT NULL,
  person_name TEXT,
  chamber TEXT,
  filing_type TEXT,
  filed_date TEXT,
  doc_url TEXT,
  doc_path TEXT,
  doc_kind TEXT DEFAULT 'pdf',
  status TEXT NOT NULL DEFAULT 'seen',
  attempts INTEGER NOT NULL DEFAULT 0,
  first_seen_at TEXT,
  extracted_at TEXT,
  error TEXT,
  UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY,
  filing_id INTEGER NOT NULL REFERENCES filings(id),
  dedup_key TEXT NOT NULL UNIQUE,
  person_name TEXT,
  chamber TEXT,
  asset_description TEXT,
  ticker TEXT,
  asset_type TEXT,
  transaction_type TEXT,
  amount_range TEXT,
  amount_mid REAL,
  owner TEXT,
  trade_date TEXT,
  disclosure_date TEXT,
  disclosure_lag_days INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT
);

CREATE TABLE IF NOT EXISTS analyses (
  id INTEGER PRIMARY KEY,
  trade_id INTEGER NOT NULL UNIQUE REFERENCES trades(id),
  insider_edge_score REAL,
  alpha_remaining_score REAL,
  legislative_score REAL,
  interest_score REAL,
  direction_alignment TEXT,
  summary TEXT,
  report_path TEXT,
  model TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT,
  finished_at TEXT,
  house_ok INTEGER,
  senate_ok INTEGER,
  oge_ok INTEGER,
  new_filings INTEGER DEFAULT 0,
  trades_extracted INTEGER DEFAULT 0,
  analyses_run INTEGER DEFAULT 0,
  errors TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
  trade_id INTEGER PRIMARY KEY REFERENCES trades(id),
  sent_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_person ON trades(person_name);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA_VERSION = 1


def init_db(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        # v1: pre-Buttondown notify tables go away. subscribers held plaintext
        # emails; notifications was keyed per recipient. Dropping notifications
        # loses only sent-dedup state (one possible duplicate digest).
        conn.execute("DROP TABLE IF EXISTS subscribers")
        conn.execute("DROP TABLE IF EXISTS notifications")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.executescript(SCHEMA)
    conn.commit()


def insert_filing(conn: sqlite3.Connection, ref: FilingRef) -> int | None:
    """INSERT OR IGNORE a filing ref. Returns the new row id, or None if already seen."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO filings
           (source, external_id, person_name, chamber, filing_type, filed_date,
            doc_url, doc_kind, status, first_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'seen', ?)""",
        (
            ref.source,
            ref.external_id,
            ref.person_name,
            ref.chamber,
            ref.filing_type,
            ref.filed_date,
            ref.doc_url,
            ref.doc_kind,
            now_iso(),
        ),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


def trade_dedup_key(
    source: str,
    external_id: str,
    asset_description: str,
    transaction_type: str,
    trade_date: str,
    amount_range: str,
    owner: str,
) -> str:
    raw = "|".join(
        [source, external_id, asset_description, transaction_type, trade_date, amount_range, owner]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def filings_by_status(conn: sqlite3.Connection, status: str, max_attempts: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM filings WHERE status = ? AND attempts < ? ORDER BY id",
        (status, max_attempts),
    ).fetchall()


def pending_trades(conn: sqlite3.Connection, limit: int = 0) -> list[sqlite3.Row]:
    """Pending trades, biggest and freshest first. limit=0 means no limit."""
    sql = """SELECT * FROM trades WHERE status = 'pending' AND attempts < ?
             ORDER BY COALESCE(amount_mid, 0) DESC, COALESCE(disclosure_lag_days, 9999) ASC"""
    params: list = [config.MAX_ANALYZE_ATTEMPTS]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()
