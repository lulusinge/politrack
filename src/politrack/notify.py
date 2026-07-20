"""Email notifications via Buttondown: one broadcast digest per cycle covering
newly analyzed trades at or above NOTIFY_THRESHOLD.

Buttondown owns the subscriber list, double opt-in, and unsubscribe links, so
no email addresses ever touch this codebase or the committed DB. The
`notifications` table only records which trades have been broadcast (dedup
across watcher runs). Without BUTTONDOWN_API_KEY, sending is silently skipped.
"""

from __future__ import annotations

import sqlite3

import httpx

from . import config, db

# One digest lists at most this many trades; the rest stay pending and drain
# in later cycles. Bounds the email (and the first-ever broadcast, which would
# otherwise bundle the entire qualifying backlog).
MAX_TRADES_PER_DIGEST = 20


def _pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT t.id, t.person_name, t.chamber, t.ticker, t.asset_description,
                  t.transaction_type, t.amount_range, t.trade_date, t.disclosure_date,
                  a.interest_score, a.summary
           FROM analyses a
           JOIN trades t ON t.id = a.trade_id
           WHERE t.status = 'analyzed'
             AND a.interest_score >= ?
             AND NOT EXISTS (SELECT 1 FROM notifications n WHERE n.trade_id = t.id)
           ORDER BY a.interest_score DESC""",
        (config.NOTIFY_THRESHOLD,),
    ).fetchall()


def _build_digest(rows: list[sqlite3.Row], held_back: int) -> tuple[str, str]:
    """Subject and markdown body for the Buttondown broadcast."""
    top = rows[0]
    ticker = top["ticker"] or top["asset_description"][:20]
    subject = (
        f"PoliTrack: {len(rows)} hot trade{'s' if len(rows) > 1 else ''} — "
        f"{ticker} {top['interest_score']:.0f}/100"
    )

    lines = ["Interesting politician trades just crossed the threshold:", ""]
    for r in rows:
        lines.append(
            f"- **{r['interest_score']:.0f}/100 — {r['person_name']} ({r['chamber']})** "
            f"{r['transaction_type'].upper()} {r['ticker'] or r['asset_description']} "
            f"{r['amount_range']}  \n"
            f"  traded {r['trade_date']}, disclosed {r['disclosure_date']}  \n"
            f"  {r['summary']}"
        )
    if held_back:
        lines.append(f"\n…and {held_back} more in the next digest.")
    if config.DASHBOARD_URL:
        lines.append(f"\n[Full theses on the dashboard]({config.DASHBOARD_URL})")
    lines.append("\n---\nPoliTrack. Public disclosure data; not investment advice.")
    return subject, "\n".join(lines)


def notify_hot_trades(conn: sqlite3.Connection) -> int:
    """Broadcast a digest of un-notified hot trades. Returns trades notified."""
    if not config.BUTTONDOWN_API_KEY:
        print("notify: BUTTONDOWN_API_KEY not set — skipping notifications")
        return 0
    pending = _pending(conn)
    if not pending:
        return 0
    rows = pending[:MAX_TRADES_PER_DIGEST]

    subject, body = _build_digest(rows, held_back=len(pending) - len(rows))
    resp = httpx.post(
        config.BUTTONDOWN_API_URL,
        headers={
            "Authorization": f"Token {config.BUTTONDOWN_API_KEY}",
            # Since API version 2026-04-01 an explicit about_to_send is
            # rejected (400 sending_requires_confirmation) without this header.
            # Pin the version so future default changes can't break sends.
            "X-API-Version": "2026-04-01",
            "X-Buttondown-Live-Dangerously": "true",
        },
        json={"subject": subject, "body": body, "status": "about_to_send"},
        timeout=config.HTTP_TIMEOUT,
    )
    resp.raise_for_status()

    conn.executemany(
        "INSERT OR IGNORE INTO notifications (trade_id, sent_at) VALUES (?, ?)",
        [(r["id"], db.now_iso()) for r in rows],
    )
    conn.commit()
    return len(rows)
