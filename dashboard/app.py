"""Streamlit dashboard: feed of analyzed politician trades ranked by interest."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "politrack.db"

HOT_THRESHOLD = 70

st.set_page_config(
    page_title="PoliTrack — politician trades",
    page_icon="P",
    layout="wide",
    menu_items={"about": "PoliTrack — AI-analyzed politician trading disclosures."},
)

st.markdown(
    """
    <style>
      #MainMenu, footer {visibility: hidden;}
      .stAppDeployButton {display: none;}
      div[data-testid="stMetric"] {
        background: #1A1F2B;
        border: 1px solid #2A3140;
        border-radius: 10px;
        padding: 12px 16px;
      }
      div[data-testid="stExpander"] {
        border: 1px solid #2A3140;
        border-radius: 10px;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


def _ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_data(ttl=60)
def load_feed() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        return pd.read_sql_query(
            """SELECT t.id AS trade_id, t.person_name, t.chamber, t.ticker,
                      t.asset_description, t.transaction_type, t.amount_range,
                      t.amount_mid, t.trade_date, t.disclosure_date,
                      t.disclosure_lag_days,
                      a.insider_edge_score, a.alpha_remaining_score, a.legislative_score,
                      a.interest_score, a.direction_alignment, a.summary, a.report_path,
                      a.created_at AS analyzed_at
               FROM analyses a
               JOIN trades t ON t.id = a.trade_id
               WHERE t.status = 'analyzed'
               ORDER BY a.interest_score DESC, t.disclosure_date DESC""",
            conn,
        )


@st.cache_data(ttl=60)
def load_stats() -> dict:
    if not DB_PATH.exists():
        return {}
    day_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    two_days_ago = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    with _ro() as conn:
        q = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
        run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        stale = {}
        for src, col in [("House", "house_ok"), ("Senate", "senate_ok"), ("OGE", "oge_ok")]:
            last_ok = q(f"SELECT MAX(id) FROM runs WHERE {col} = 1")
            stale[src] = None if (last_ok is None or run is None) else run["id"] - last_ok
        return {
            "analyzed_24h": q(
                "SELECT COUNT(*) FROM analyses WHERE created_at >= ?", day_ago
            ),
            "analyzed_prev_24h": q(
                "SELECT COUNT(*) FROM analyses WHERE created_at >= ? AND created_at < ?",
                two_days_ago,
                day_ago,
            ),
            "hot_total": q(
                """SELECT COUNT(*) FROM analyses a JOIN trades t ON t.id = a.trade_id
                   WHERE t.status = 'analyzed' AND a.interest_score >= ?""",
                HOT_THRESHOLD,
            ),
            "filings_24h": q(
                "SELECT COUNT(*) FROM filings WHERE first_seen_at >= ?", day_ago
            ),
            "pending": q("SELECT COUNT(*) FROM trades WHERE status = 'pending'"),
            "volume_analyzed": q(
                """SELECT COALESCE(SUM(t.amount_mid), 0) FROM trades t
                   JOIN analyses a ON a.trade_id = t.id WHERE t.status = 'analyzed'"""
            ),
            "last_cycle": (run["finished_at"] or run["started_at"]) if run else None,
            "cycles_since_ok": stale,
        }


def subscribe(email: str, threshold: int) -> str:
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return "That doesn't look like an email address."
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """INSERT INTO subscribers (email, threshold, created_at) VALUES (?, ?, ?)
               ON CONFLICT(email) DO UPDATE SET threshold = excluded.threshold""",
            (email.strip().lower(), threshold, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    load_stats.clear()
    return f"Subscribed: {email} (threshold ≥ {threshold})"


def esc_md(text: str | None) -> str:
    """Escape $ so Streamlit's markdown doesn't render dollar amounts as LaTeX math."""
    return (text or "").replace("$", "\\$")


def display_ticker(row: pd.Series, width: int = 24) -> str:
    """Ticker if present; asset description otherwise (NaN-safe — pandas NaN is truthy)."""
    if isinstance(row.ticker, str) and row.ticker:
        return row.ticker
    return (row.asset_description or "")[:width] if isinstance(row.asset_description, str) else "?"


def score_label(score: float) -> str:
    if score >= HOT_THRESHOLD:
        return f":red[**{score:.0f}**]"
    if score >= 40:
        return f":orange[**{score:.0f}**]"
    return f":gray[**{score:.0f}**]"


def render_trade(row: pd.Series, key_prefix: str = "feed") -> None:
    direction = ":green[**BUY**]" if row.transaction_type == "purchase" else ":red[**SELL**]"
    ticker = display_ticker(row)
    header = (
        f"{score_label(row.interest_score)} · **{ticker}** · {direction} · "
        f"{row.person_name} ({row.chamber}) · {esc_md(row.amount_range)}"
    )
    with st.expander(header):
        m = st.columns(4)
        m[0].metric("Interest", f"{row.interest_score:.0f}/100")
        m[1].metric("Insider edge", f"{row.insider_edge_score:.1f}/10")
        m[2].metric("Alpha remaining", f"{row.alpha_remaining_score:.1f}/10")
        m[3].metric("Legislative", f"{row.legislative_score:+.1f}")
        st.caption(
            f"Traded {row.trade_date} · disclosed {row.disclosure_date} "
            f"({row.disclosure_lag_days} days later) · {esc_md(row.direction_alignment)}"
        )
        st.markdown(esc_md(row.summary))
        report_file = (
            REPO_ROOT / row.report_path if isinstance(row.report_path, str) else None
        )
        if report_file is not None and report_file.exists():
            import frontmatter

            report_text = report_file.read_text()
            post = frontmatter.loads(report_text)
            st.divider()
            st.markdown(esc_md(post.content))
            st.download_button(
                "Download report (.md)",
                data=report_text,
                file_name=report_file.name,
                mime="text/markdown",
                key=f"dl-{key_prefix}-{row.trade_id}",
            )


# ---------------------------------------------------------------- header
left, right = st.columns([3, 1])
with left:
    st.title("PoliTrack")
    st.caption(
        "Stocks surfaced from US politician trading disclosures — House, Senate "
        "and executive branch — analyzed by an AI research agent. Not investment advice."
    )

stats = load_stats()
if stats:
    delta = stats["analyzed_24h"] - stats["analyzed_prev_24h"]
    c = st.columns(5)
    c[0].metric("Analyzed (24h)", stats["analyzed_24h"], delta=delta or None)
    c[1].metric(f"Hot trades (≥{HOT_THRESHOLD})", stats["hot_total"])
    c[2].metric("New filings (24h)", stats["filings_24h"])
    c[3].metric("Queue", stats["pending"])
    vol = stats["volume_analyzed"]
    vol_label = f"${vol / 1e6:,.1f}M" if vol >= 1e6 else f"${vol / 1e3:,.0f}K"
    c[4].metric("Volume analyzed", vol_label)

    health_bits = []
    for src, behind in stats["cycles_since_ok"].items():
        dot = ":green[●]" if behind == 0 else (":gray[●]" if behind is None else ":orange[●]")
        health_bits.append(f"{dot} {src}")
    last = (stats["last_cycle"] or "").replace("T", " ").split("+")[0]
    updated = f"  ·  updated {last} UTC" if last else ""
    st.caption("Data sources:  " + "  ·  ".join(health_bits) + updated)

st.divider()

df = load_feed()
if df.empty:
    st.info("No analyzed trades yet — new disclosures are checked every 30 minutes. Check back soon.")
    st.stop()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Filters")
    people = st.multiselect("Person", sorted(df.person_name.dropna().unique()))
    chambers = st.multiselect("Chamber", sorted(df.chamber.dropna().unique()))
    ticker_q = st.text_input("Ticker contains").strip().upper()
    min_score = st.slider("Min interest score", 0, 100, 0)
    direction = st.multiselect("Direction", sorted(df.transaction_type.dropna().unique()))

    st.divider()
    st.header("Notifications")
    st.caption("Get an email whenever a new trade crosses your interest threshold.")
    email = st.text_input("Email", placeholder="you@example.com")
    threshold = st.slider("Notify at score ≥", 40, 95, HOT_THRESHOLD, step=5)
    if st.button("Subscribe", width="stretch"):
        if email:
            st.success(subscribe(email, threshold))
        else:
            st.warning("Enter an email first.")

view = df
if people:
    view = view[view.person_name.isin(people)]
if chambers:
    view = view[view.chamber.isin(chambers)]
if ticker_q:
    view = view[view.ticker.fillna("").str.upper().str.contains(ticker_q, regex=False)]
if direction:
    view = view[view.transaction_type.isin(direction)]
view = view[view.interest_score >= min_score]

# ---------------------------------------------------------------- tabs
tab_hot, tab_feed, tab_table = st.tabs(["Hot", "Feed", "Table"])

with tab_hot:
    hot = view[view.interest_score >= HOT_THRESHOLD].sort_values(
        ["analyzed_at", "interest_score"], ascending=[False, False]
    )
    if hot.empty:
        st.caption(
            f"Nothing above {HOT_THRESHOLD} right now — that's normal: most "
            "disclosed trades are routine rebalances. New disclosures are checked every 30 minutes."
        )
    else:
        st.caption(f"{len(hot)} trades at or above {HOT_THRESHOLD}, newest first.")
        for _, row in hot.iterrows():
            render_trade(row, key_prefix="hot")

with tab_feed:
    st.caption(f"{len(view)} analyzed trades, most interesting first.")
    for _, row in view.iterrows():
        render_trade(row, key_prefix="feed")

with tab_table:
    table = view[
        [
            "interest_score",
            "ticker",
            "person_name",
            "chamber",
            "transaction_type",
            "amount_range",
            "trade_date",
            "disclosure_date",
            "disclosure_lag_days",
            "insider_edge_score",
            "alpha_remaining_score",
            "legislative_score",
            "summary",
        ]
    ]
    st.caption("Select a row to download its report.")
    event = st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="table-select",
        column_config={
            "interest_score": st.column_config.ProgressColumn(
                "Interest", min_value=0, max_value=100, format="%.0f"
            ),
            "person_name": "Person",
            "transaction_type": "Direction",
            "amount_range": "Amount",
            "trade_date": "Traded",
            "disclosure_date": "Disclosed",
            "disclosure_lag_days": "Lag (d)",
            "insider_edge_score": "Edge",
            "alpha_remaining_score": "Alpha left",
            "legislative_score": "Legislative",
            "summary": st.column_config.TextColumn("Summary", width="large"),
        },
    )
    selected = event.selection.rows if event and event.selection else []
    if selected:
        sel_row = view.iloc[selected[0]]
        sel_report = (
            REPO_ROOT / sel_row.report_path if isinstance(sel_row.report_path, str) else None
        )
        if sel_report is not None and sel_report.exists():
            st.download_button(
                f"Download {display_ticker(sel_row)} report (.md)",
                data=sel_report.read_text(),
                file_name=sel_report.name,
                mime="text/markdown",
                key="dl-table",
            )
        else:
            st.caption("No report file for that row.")

    # Bulk download of every report currently in the filtered view
    import io
    import zipfile

    report_files = [
        REPO_ROOT / p
        for p in view.report_path.dropna().unique()
        if isinstance(p, str) and (REPO_ROOT / p).exists()
    ]
    if report_files:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in report_files:
                zf.write(f, arcname=f.name)
        st.download_button(
            f"Download all {len(report_files)} reports (.zip)",
            data=buf.getvalue(),
            file_name="politrack-reports.zip",
            mime="application/zip",
            key="dl-zip",
        )
