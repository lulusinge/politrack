"""Streamlit dashboard: feed of analyzed politician trades ranked by interest."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

DB_PATH = REPO_ROOT / "data" / "politrack.db"

st.set_page_config(page_title="PoliTrack — politician trades", page_icon="🏛️", layout="wide")


@st.cache_data(ttl=60)
def load_feed() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        return pd.read_sql_query(
            """SELECT t.id AS trade_id, t.person_name, t.chamber, t.ticker,
                      t.asset_description, t.transaction_type, t.amount_range,
                      t.trade_date, t.disclosure_date, t.disclosure_lag_days,
                      a.insider_edge_score, a.alpha_remaining_score, a.legislative_score,
                      a.interest_score, a.direction_alignment, a.summary, a.report_path
               FROM analyses a
               JOIN trades t ON t.id = a.trade_id
               WHERE t.status = 'analyzed'
               ORDER BY a.interest_score DESC, t.disclosure_date DESC""",
            conn,
        )
    finally:
        conn.close()


@st.cache_data(ttl=60)
def load_health() -> dict:
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        pending = conn.execute(
            "SELECT COUNT(*) c FROM trades WHERE status = 'pending'"
        ).fetchone()["c"]
        analyzed = conn.execute(
            "SELECT COUNT(*) c FROM trades WHERE status = 'analyzed'"
        ).fetchone()["c"]
        stale = {}
        for src, col in [("House", "house_ok"), ("Senate", "senate_ok"), ("OGE", "oge_ok")]:
            row = conn.execute(
                f"SELECT MAX(id) FROM runs WHERE {col} = 1"
            ).fetchone()
            last_ok_id = row[0]
            latest_id = run["id"] if run else None
            stale[src] = (
                None
                if last_ok_id is None or latest_id is None
                else latest_id - last_ok_id
            )
        return {
            "run": dict(run) if run else None,
            "pending": pending,
            "analyzed": analyzed,
            "cycles_since_ok": stale,
        }
    finally:
        conn.close()


def render_health(health: dict) -> None:
    run = health.get("run")
    cols = st.columns([2, 1, 1, 1, 1, 1])
    if run:
        finished = run.get("finished_at") or run.get("started_at") or ""
        cols[0].caption(f"Last cycle: {finished} UTC")
    else:
        cols[0].caption("No watcher cycles recorded yet")
    for i, src in enumerate(["House", "Senate", "OGE"]):
        behind = health.get("cycles_since_ok", {}).get(src)
        if behind is None:
            label = "○ never polled"
        elif behind == 0:
            label = "● healthy"
        else:
            label = f"◐ {behind} cycles behind"
        cols[i + 1].caption(f"{src}: {label}")
    cols[4].caption(f"Analyzed: {health.get('analyzed', 0)}")
    cols[5].caption(f"Queue: {health.get('pending', 0)}")


def render_trade(row: pd.Series) -> None:
    direction = "🟢 BUY" if row.transaction_type == "purchase" else "🔴 SELL"
    ticker = row.ticker or (row.asset_description or "")[:24]
    header = (
        f"**{row.interest_score:.0f}** · {ticker} · {direction} · "
        f"{row.person_name} ({row.chamber}) · {row.amount_range}"
    )
    with st.expander(header):
        m = st.columns(4)
        m[0].metric("Interest", f"{row.interest_score:.0f}/100")
        m[1].metric("Insider edge", f"{row.insider_edge_score:.1f}/10")
        m[2].metric("Alpha remaining", f"{row.alpha_remaining_score:.1f}/10")
        m[3].metric("Legislative", f"{row.legislative_score:+.1f}")
        st.caption(
            f"Traded {row.trade_date} · disclosed {row.disclosure_date} "
            f"({row.disclosure_lag_days} days later) · {row.direction_alignment}"
        )
        st.markdown(row.summary)
        report_file = REPO_ROOT / (row.report_path or "")
        if row.report_path and report_file.exists():
            import frontmatter

            post = frontmatter.loads(report_file.read_text())
            st.divider()
            st.markdown(post.content)


st.title("🏛️ PoliTrack")
st.caption(
    "Stocks surfaced from US politician trading disclosures — House, Senate, and "
    "executive branch — analyzed by an AI research agent. Not investment advice."
)

health = load_health()
render_health(health)
st.divider()

df = load_feed()
if df.empty:
    st.info(
        "No analyzed trades yet. Run `politrack cycle` (or `politrack backfill --count 100`) "
        "to populate the feed."
    )
    st.stop()

with st.sidebar:
    st.header("Filters")
    people = st.multiselect("Person", sorted(df.person_name.dropna().unique()))
    chambers = st.multiselect("Chamber", sorted(df.chamber.dropna().unique()))
    ticker_q = st.text_input("Ticker contains").strip().upper()
    min_score = st.slider("Min interest score", 0, 100, 0)
    direction = st.multiselect("Direction", sorted(df.transaction_type.dropna().unique()))

view = df
if people:
    view = view[view.person_name.isin(people)]
if chambers:
    view = view[view.chamber.isin(chambers)]
if ticker_q:
    view = view[view.ticker.fillna("").str.upper().str.contains(ticker_q)]
if direction:
    view = view[view.transaction_type.isin(direction)]
view = view[view.interest_score >= min_score]

st.subheader(f"Feed — {len(view)} trades")
tab_feed, tab_table = st.tabs(["Feed", "Table"])

with tab_feed:
    for _, row in view.iterrows():
        render_trade(row)

with tab_table:
    st.dataframe(
        view.drop(columns=["report_path"]),
        use_container_width=True,
        hide_index=True,
    )
