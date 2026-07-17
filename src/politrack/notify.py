"""Email notifications: digest of newly analyzed trades above a recipient's threshold.

Recipients come from two places, merged:
- the NOTIFY_EMAILS env var (used in GitHub Actions, threshold = NOTIFY_THRESHOLD)
- the `subscribers` table (filled by the dashboard's notification form)

Requires SMTP credentials (SMTP_USER / SMTP_PASS — for Gmail use an app
password). Without them, notification sending is silently skipped.
"""

from __future__ import annotations

import smtplib
import sqlite3
from email.message import EmailMessage

from . import config, db


def _recipients(conn: sqlite3.Connection) -> dict[str, float]:
    recipients = {email: config.NOTIFY_THRESHOLD for email in config.NOTIFY_EMAILS}
    for row in conn.execute("SELECT email, threshold FROM subscribers"):
        recipients[row["email"]] = float(row["threshold"])
    return recipients


def _pending_for(conn: sqlite3.Connection, email: str, threshold: float) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT t.id, t.person_name, t.chamber, t.ticker, t.asset_description,
                  t.transaction_type, t.amount_range, t.trade_date, t.disclosure_date,
                  a.interest_score, a.summary
           FROM analyses a
           JOIN trades t ON t.id = a.trade_id
           WHERE t.status = 'analyzed'
             AND a.interest_score >= ?
             AND NOT EXISTS (
               SELECT 1 FROM notifications n WHERE n.trade_id = t.id AND n.email = ?
             )
           ORDER BY a.interest_score DESC""",
        (threshold, email),
    ).fetchall()


def _build_email(email: str, rows: list[sqlite3.Row]) -> EmailMessage:
    msg = EmailMessage()
    top = rows[0]
    ticker = top["ticker"] or top["asset_description"][:20]
    msg["Subject"] = (
        f"PoliTrack: {len(rows)} hot trade{'s' if len(rows) > 1 else ''} — "
        f"{ticker} {top['interest_score']:.0f}/100"
    )
    msg["From"] = config.SMTP_USER
    msg["To"] = email

    lines = ["Interesting politician trades just crossed your threshold:\n"]
    for r in rows:
        lines.append(
            f"● {r['interest_score']:.0f}/100 — {r['person_name']} ({r['chamber']}) "
            f"{r['transaction_type'].upper()} {r['ticker'] or r['asset_description']} "
            f"{r['amount_range']}\n"
            f"  traded {r['trade_date']}, disclosed {r['disclosure_date']}\n"
            f"  {r['summary']}\n"
        )
    if config.DASHBOARD_URL:
        lines.append(f"\nFull theses: {config.DASHBOARD_URL}")
    lines.append("\n—\nPoliTrack. Public disclosure data; not investment advice.")
    msg.set_content("\n".join(lines))
    return msg


def notify_hot_trades(conn: sqlite3.Connection) -> int:
    """Send digests for un-notified hot trades. Returns number of emails sent."""
    recipients = _recipients(conn)
    if not recipients:
        return 0
    if not (config.SMTP_USER and config.SMTP_PASS):
        print("notify: SMTP_USER/SMTP_PASS not set — skipping email notifications")
        return 0

    sent = 0
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(config.SMTP_USER, config.SMTP_PASS)
        for email, threshold in recipients.items():
            rows = _pending_for(conn, email, threshold)
            if not rows:
                continue
            smtp.send_message(_build_email(email, rows))
            conn.executemany(
                "INSERT OR IGNORE INTO notifications (trade_id, email, sent_at) VALUES (?, ?, ?)",
                [(r["id"], email, db.now_iso()) for r in rows],
            )
            conn.commit()
            sent += 1
    return sent
