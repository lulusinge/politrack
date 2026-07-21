"""Streamlit dashboard: feed of analyzed politician trades ranked by interest."""

from __future__ import annotations

import importlib
import io
import sqlite3
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import frontmatter
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

REPO_ROOT = Path(__file__).resolve().parents[1]
# All env/config comes from the watcher's config module (stdlib + dotenv only)
# so every variable has exactly one read site and one default. The dashboard
# runs from a bare checkout on Streamlit Cloud, so import via src/ rather than
# requiring the package to be installed. Streamlit caches imports across
# reruns while re-executing this script, so reload — that re-runs load_dotenv
# and keeps .env edits live on refresh.
sys.path.insert(0, str(REPO_ROOT / "src"))
from politrack import config  # noqa: E402

importlib.reload(config)
DB_PATH = config.DB_PATH
NOTIFY_THRESHOLD = config.NOTIFY_THRESHOLD
BUTTONDOWN_USERNAME = config.BUTTONDOWN_USERNAME
BMC_SLUG = config.BMC_SLUG

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
      /* palette — single source of truth; --pt-accent/--pt-text mirror
         primaryColor/textColor in .streamlit/config.toml */
      :root {
        --pt-muted: #8A93A6; --pt-accent: #E8A13D; --pt-text: #E8EAED;
        --pt-ok: #3FB68B; --pt-off: #5A6372;
      }

      #MainMenu, footer {visibility: hidden;}
      .stAppDeployButton {display: none;}
      header[data-testid="stHeader"] {background: transparent;}
      .block-container {padding-top: 1.4rem; padding-bottom: 4rem; max-width: 1240px;}
      div[data-testid="stSidebarUserContent"] {padding-top: 0;}
      div[data-testid="stSidebarHeader"] {padding-top: 0.6rem; padding-bottom: 0;}

      /* wordmark — high specificity + !important: Streamlit's own markdown
         styles otherwise win over a bare class selector */
      div[data-testid="stMarkdownContainer"] p.pt-overline {
        font-size: 0.72rem !important; text-transform: uppercase;
        letter-spacing: 0.22em; color: var(--pt-muted); font-weight: 700;
        margin: 0 0 0.2rem 0;
      }
      div[data-testid="stMarkdownContainer"] h1.pt-title {
        font-size: 2.9rem !important; font-weight: 800 !important;
        letter-spacing: -0.02em; line-height: 1.05 !important;
        margin: 0 0 0.3rem 0; padding: 0 !important; color: var(--pt-text);
      }
      div[data-testid="stMarkdownContainer"] h1.pt-title span {color: var(--pt-accent);}
      div[data-testid="stMarkdownContainer"] p.pt-sub {
        color: var(--pt-muted); font-size: 0.95rem !important; margin: 0; line-height: 1.55;
      }
      .pt-status {text-align: right; color: var(--pt-muted); font-size: 0.8rem;
                  padding-top: 1.4rem; line-height: 1.7;}
      .pt-dot-ok {color: var(--pt-ok);} .pt-dot-warn {color: var(--pt-accent);}
      .pt-dot-off {color: var(--pt-off);}

      /* coffee link — styled like the rest of the header, not BMC's branded image */
      a.pt-coffee, a.pt-coffee:visited {
        display: inline-block; margin-top: 0.6rem; padding: 0.28rem 0.85rem;
        border: 1px solid color-mix(in srgb, var(--pt-accent) 40%, transparent);
        border-radius: 8px;
        color: var(--pt-accent) !important; background: transparent;
        font-size: 0.78rem; font-weight: 600; letter-spacing: 0.02em;
        text-decoration: none !important;
      }
      a.pt-coffee:hover {
        background: color-mix(in srgb, var(--pt-accent) 8%, transparent);
        border-color: var(--pt-accent);
      }

      /* metric cards */
      div[data-testid="stMetric"] {
        background: linear-gradient(180deg, #1A2030 0%, #171C28 100%);
        border: 1px solid #2A3140;
        border-radius: 12px;
        padding: 14px 18px 12px 18px;
        min-height: 128px;   /* equal card heights whether or not a delta badge shows */
      }
      div[data-testid="stMetric"]:hover {border-color: #3A4356;}
      [data-testid="stMetricLabel"] p {
        font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em;
        color: var(--pt-muted); font-weight: 600;
      }
      [data-testid="stMetricValue"] {font-size: 1.85rem; font-weight: 650;}
      [data-testid="stMetricDelta"] {font-size: 0.8rem;}
      /* the rules above are sized for the header KPI row — keep the small
         metrics inside trade-card expanders compact */
      div[data-testid="stExpander"] div[data-testid="stMetric"] {
        min-height: 0; padding: 12px 16px;
      }
      div[data-testid="stExpander"] [data-testid="stMetricValue"] {font-size: 1.35rem;}

      /* trade cards */
      div[data-testid="stExpander"] {
        border: 1px solid #262E3D;
        border-radius: 12px;
        background: #141924;
        margin-bottom: 0.55rem;
      }
      div[data-testid="stExpander"]:hover {
        border-color: color-mix(in srgb, var(--pt-accent) 33%, transparent);
      }
      div[data-testid="stExpander"] summary {padding: 0.8rem 1rem;}
      /* feed rows are space-padded to fixed column widths; monospace + pre
         turns that padding into aligned columns (blotter style). If Streamlit
         ever changes this DOM, rows fall back to collapsed single spaces. */
      div[data-testid="stExpander"] summary [data-testid="stMarkdownContainer"] p {
        font-family: "Source Code Pro", monospace;
        font-size: 0.85rem;
        white-space: pre;
      }

      /* misc */
      hr {border-color: #232A38; margin: 1.1rem 0;}
      button[data-baseweb="tab"] {font-size: 0.95rem; padding-top: 0.6rem; padding-bottom: 0.6rem;}
      section[data-testid="stSidebar"] h2 {
        font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.12em;
        color: var(--pt-muted); font-weight: 700;
        padding-top: 0.25rem; margin-top: 0;
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


@st.cache_data(ttl=300)
def load_report(path_str: str, mtime: float) -> tuple[str, str]:
    """Raw report text plus frontmatter-stripped body; mtime busts the cache on edits."""
    text = (REPO_ROOT / path_str).read_text()
    return text, frontmatter.loads(text).content


def esc_md(text: str | None) -> str:
    """Escape $ so Streamlit's markdown doesn't render dollar amounts as LaTeX math."""
    return (text or "").replace("$", "\\$")


def display_ticker(row: pd.Series, width: int = 24) -> str:
    """Ticker if present; asset description otherwise (NaN-safe — pandas NaN is truthy)."""
    if isinstance(row.ticker, str) and row.ticker:
        return row.ticker
    return (row.asset_description or "")[:width] if isinstance(row.asset_description, str) else "?"


def score_label(score: float) -> str:
    txt = f"{score:.0f}"
    if score >= HOT_THRESHOLD:
        color = "red"
    elif score >= 40:
        color = "orange"
    else:
        color = "gray"
    # pad outside the markup: spaces inside **…** break markdown emphasis
    return " " * (3 - len(txt)) + f":{color}[**{txt}**]"


def pad(text: str, width: int) -> str:
    """Trim (ellipsis) or pad to a fixed width for the aligned feed header."""
    if len(text) > width:
        text = text[: width - 1] + "…"
    return text.ljust(width)


def render_trade(row: pd.Series, key_prefix: str = "feed") -> None:
    buy = row.transaction_type == "purchase"
    direction = ":green[**BUY**] " if buy else ":red[**SELL**]"
    ticker = display_ticker(row, width=10)
    header = (
        f"{score_label(row.interest_score)}  "
        f"**{ticker}**{' ' * (11 - len(ticker))} "
        f"{direction}  "
        f"{pad(f'{row.person_name} ({row.chamber})', 32)}  "
        f"{esc_md(pad(row.amount_range or '?', 20))}  "
        f"traded {row.trade_date or '?'}  disclosed {row.disclosure_date or '?'}"
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
            report_text, body = load_report(row.report_path, report_file.stat().st_mtime)
            st.divider()
            st.markdown(esc_md(body))
            st.download_button(
                "Download report (.md)",
                data=report_text,
                file_name=report_file.name,
                mime="text/markdown",
                key=f"dl-{key_prefix}-{row.trade_id}",
            )


# ---------------------------------------------------------------- header
stats = load_stats()
left, right = st.columns([3, 1])
with left:
    st.markdown(
        '<p class="pt-overline">Politician trading intelligence</p>', unsafe_allow_html=True
    )
    st.markdown('<h1 class="pt-title">Poli<span>Track</span></h1>', unsafe_allow_html=True)
    st.markdown(
        '<p class="pt-sub">Stocks surfaced from US politician trading disclosures —<br/>'
        "House, Senate and executive branch — analyzed by an AI research agent. "
        "Not investment advice.</p>",
        unsafe_allow_html=True,
    )
with right:
    if stats:
        dots = []
        for src, behind in stats["cycles_since_ok"].items():
            if behind == 0:
                cls, state = "pt-dot-ok", "up to date"
            elif behind is None:
                cls, state = "pt-dot-off", "no successful update yet"
            else:
                cls, state = "pt-dot-warn", f"{behind} cycles behind"
            dots.append(
                f'<span title="{src} data source: {state}">'
                f'<span class="{cls}">●</span> {src}</span>'
            )
        last = (stats["last_cycle"] or "").replace("T", " ").split("+")[0]
        updated = f"<br/>updated {last} UTC" if last else ""
        st.markdown(
            f'<div class="pt-status">Data sources: &nbsp;{" &nbsp; ".join(dots)}{updated}</div>',
            unsafe_allow_html=True,
        )
    if BMC_SLUG:
        st.markdown(
            f'<div style="text-align:right;">'
            f'<a class="pt-coffee" href="https://buymeacoffee.com/{BMC_SLUG}" '
            f'target="_blank" rel="noopener">Buy me a coffee</a></div>',
            unsafe_allow_html=True,
        )

st.write("")
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

st.write("")

df = load_feed()
if df.empty:
    st.info("No analyzed trades yet — new disclosures are checked hourly on US business days. Check back soon.")
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
    if BUTTONDOWN_USERNAME:
        st.header("Notifications")
        st.caption(
            f"Get an email when a trade scores ≥ {NOTIFY_THRESHOLD:g}. "
            "Powered by Buttondown; confirm via the email it sends you, "
            "unsubscribe anytime."
        )
        components.html(
            f"""
            <form action="https://buttondown.com/api/emails/embed-subscribe/{BUTTONDOWN_USERNAME}"
                  method="post" target="popupwindow"
                  onsubmit="window.open('https://buttondown.com/{BUTTONDOWN_USERNAME}', 'popupwindow')"
                  style="margin:0; display:flex; flex-direction:column; gap:8px;
                         font-family:'Source Sans Pro',sans-serif;">
              <input type="email" name="email" required placeholder="you@example.com"
                     style="background:#1A1F2B; color:#E8EAED; border:1px solid #31394A;
                            border-radius:8px; padding:9px 12px; font-size:14px;
                            outline:none; width:100%; box-sizing:border-box;" />
              <input type="submit" value="Subscribe"
                     style="background:#E8A13D; color:#0E1117; border:none; cursor:pointer;
                            border-radius:8px; padding:9px 12px; font-size:14px;
                            font-weight:600; width:100%;" />
            </form>
            """,
            height=96,
        )

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
        if len(view) < len(df):
            msg = f"No trades at or above {HOT_THRESHOLD} match the current filters."
        else:
            msg = (
                f"Nothing actionable above {HOT_THRESHOLD} right now — scores "
                "measure whether you can still act on a trade, and most disclosed "
                "moves are routine or already played out. New disclosures are "
                "checked hourly on US business days."
            )
        preview = view.head(3)  # already interest_score DESC from the query
        if not preview.empty:
            msg += " Meanwhile, the most actionable recent trades:"
        st.caption(msg)
        for _, row in preview.iterrows():
            render_trade(row, key_prefix="hot-preview")
    else:
        st.caption(
            f"{len(hot)} actionable trades at or above {HOT_THRESHOLD}, newest first."
        )
        for _, row in hot.iterrows():
            render_trade(row, key_prefix="hot")

with tab_feed:
    sort_by = st.segmented_control(
        "Sort",
        ["Hottest", "Recent"],
        default="Hottest",
        label_visibility="collapsed",
        required=True,
    )
    if sort_by == "Recent":
        feed_view = view.sort_values(
            ["disclosure_date", "analyzed_at"], ascending=[False, False]
        )
    else:
        feed_view = view  # already interest_score DESC
    order = "most recently disclosed" if sort_by == "Recent" else "most interesting"
    st.caption(f"{len(feed_view)} analyzed trades, {order} first.")
    for _, row in feed_view.iterrows():
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
