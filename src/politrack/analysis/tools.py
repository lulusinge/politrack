"""Tools exposed to the analysis agent via the SDK tool runner."""

from __future__ import annotations

from datetime import date

import httpx
from anthropic import beta_tool

from .. import config, db
from . import committees

TAVILY_BASE = "https://api.tavily.com"
MAX_TOOL_OUTPUT_CHARS = 7000

# The record_analysis tool writes its payload here; agent.py clears/reads it per trade.
ANALYSIS_SINK: dict = {}


def _clip(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[...truncated]"


def _tavily(path: str, payload: dict) -> dict:
    resp = httpx.post(
        f"{TAVILY_BASE}{path}",
        headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
        json=payload,
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()


@beta_tool
def web_search(query: str, topic: str = "general", days: int = 0, max_results: int = 5) -> str:
    """Search the web via Tavily. Use for news, lobbying ties, trading track records,
    executive-branch officials, and anything not covered by the other tools.

    Args:
        query: The search query.
        topic: "general" or "news". Use "news" for recent events.
        days: For topic="news", restrict to the last N days. 0 = no restriction.
        max_results: Number of results (1-10).
    """
    payload: dict = {
        "query": query,
        "topic": topic if topic in ("general", "news") else "general",
        "max_results": max(1, min(int(max_results), 10)),
    }
    if topic == "news" and days:
        payload["days"] = int(days)
    try:
        data = _tavily("/search", payload)
    except httpx.HTTPError as e:
        return f"Search failed: {e}"
    lines = []
    for r in data.get("results", []):
        lines.append(f"- {r.get('title')}\n  {r.get('url')}\n  {r.get('content', '')[:400]}")
    return _clip("\n".join(lines) or "No results.")


@beta_tool
def read_page(url: str) -> str:
    """Fetch and read the full text content of a web page.

    Args:
        url: The URL to read.
    """
    try:
        data = _tavily("/extract", {"urls": [url]})
    except httpx.HTTPError as e:
        return f"Extract failed: {e}"
    results = data.get("results", [])
    if not results:
        failed = data.get("failed_results", [])
        return f"Could not extract content from {url}. {failed[:1]}"
    return _clip(results[0].get("raw_content") or "")


@beta_tool
def resolve_ticker(asset_description: str) -> str:
    """Look up ticker candidates for an asset name. Use to determine whether the
    asset is a publicly tradeable stock/ETF and which ticker it maps to.

    Args:
        asset_description: The asset name from the disclosure, e.g. "Apple Inc Common Stock".
    """
    import yfinance as yf

    try:
        quotes = yf.Search(asset_description, max_results=8).quotes
    except Exception as e:
        return f"Lookup failed: {e}"
    if not quotes:
        return "No matching public instruments found."
    lines = [
        f"- {q.get('symbol')}: {q.get('shortname') or q.get('longname')} "
        f"({q.get('quoteType')}, {q.get('exchange')})"
        for q in quotes
    ]
    return "\n".join(lines)


@beta_tool
def get_stock_info(ticker: str) -> str:
    """Basic facts about a ticker: name, sector, industry, market cap, description.

    Args:
        ticker: The ticker symbol, e.g. "AAPL".
    """
    import yfinance as yf

    try:
        info = yf.Ticker(ticker).info
    except Exception as e:
        return f"Info fetch failed: {e}"
    if not info or info.get("quoteType") is None:
        return f"No data found for ticker {ticker}."
    fields = {
        "Name": info.get("longName") or info.get("shortName"),
        "Type": info.get("quoteType"),
        "Sector": info.get("sector"),
        "Industry": info.get("industry"),
        "Market cap": info.get("marketCap"),
        "Exchange": info.get("exchange"),
    }
    summary = (info.get("longBusinessSummary") or "")[:800]
    body = "\n".join(f"{k}: {v}" for k, v in fields.items() if v is not None)
    return f"{body}\n\n{summary}"


def _pct(a: float, b: float) -> float:
    return round((b - a) / a * 100, 2) if a else 0.0


def _window_change(hist, start: str, end: str | None) -> tuple[float, float] | None:
    """(first_close, last_close) within [start, end] or None if no data."""
    sub = hist.loc[start:end] if end else hist.loc[start:]
    if len(sub) < 2:
        return None
    return float(sub["Close"].iloc[0]), float(sub["Close"].iloc[-1])


@beta_tool
def get_price_history(ticker: str, trade_date: str, disclosure_date: str) -> str:
    """Price action for a ticker around a disclosed trade: % change from trade date
    to disclosure date, and from disclosure date to today, each benchmarked against
    SPY over the same windows. Use this to judge how much is already priced in.

    Args:
        ticker: The ticker symbol.
        trade_date: Trade execution date, YYYY-MM-DD.
        disclosure_date: Public disclosure date, YYYY-MM-DD.
    """
    import yfinance as yf

    try:
        start = trade_date
        today = date.today().isoformat()
        hist = yf.Ticker(ticker).history(start=start, end=None, auto_adjust=True)
        spy = yf.Ticker("SPY").history(start=start, end=None, auto_adjust=True)
    except Exception as e:
        return f"Price fetch failed: {e}"
    if hist is None or hist.empty:
        return f"No price data for {ticker} since {start}."
    hist.index = hist.index.strftime("%Y-%m-%d")
    spy.index = spy.index.strftime("%Y-%m-%d")

    out = [f"{ticker} price action (vs SPY benchmark):"]
    for label, s, e in [
        ("Trade -> disclosure", trade_date, disclosure_date),
        ("Disclosure -> today", disclosure_date, today),
        ("Trade -> today", trade_date, today),
    ]:
        w = _window_change(hist, s, e)
        b = _window_change(spy, s, e)
        if w is None:
            out.append(f"- {label}: insufficient data")
            continue
        line = f"- {label} ({s} to {e}): {_pct(*w):+.2f}%"
        if b is not None:
            line += f" (SPY {_pct(*b):+.2f}%)"
        out.append(line)
    last = hist["Close"].iloc[-1]
    out.append(f"- Latest close: {last:.2f} ({hist.index[-1]})")
    return "\n".join(out)


@beta_tool
def get_politician_profile(person_name: str) -> str:
    """Party, state, and committee/subcommittee assignments for a current member of
    Congress (free structured data). For executive-branch officials this returns a
    not-found note - use web_search for them.

    Args:
        person_name: Full name of the politician.
    """
    return committees.get_profile(person_name)


@beta_tool
def get_person_trade_history(person_name: str, limit: int = 25) -> str:
    """This person's previously disclosed trades from our own database, including
    interest scores where already analyzed. Use to assess their track record of
    prescient, 'Pelosi-style' trades.

    Args:
        person_name: Full name as stored in the trades table.
        limit: Max rows to return.
    """
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT t.trade_date, t.transaction_type, t.ticker, t.asset_description,
                      t.amount_range, t.disclosure_lag_days, a.interest_score, a.summary
               FROM trades t LEFT JOIN analyses a ON a.trade_id = t.id
               WHERE t.person_name LIKE ? ORDER BY t.trade_date DESC LIMIT ?""",
            (f"%{person_name.split()[-1]}%", int(limit)),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return "No prior trades on record for this person (database may still be young)."
    lines = []
    for r in rows:
        line = (
            f"- {r['trade_date']}: {r['transaction_type']} {r['ticker'] or r['asset_description']}"
            f" {r['amount_range']} (lag {r['disclosure_lag_days']}d)"
        )
        if r["interest_score"] is not None:
            line += f" | interest {r['interest_score']:.0f}: {r['summary']}"
        lines.append(line)
    return _clip("\n".join(lines))


@beta_tool
def search_bills(query: str) -> str:
    """Search for US federal legislation (bills, hearings, regulatory action)
    relevant to a company or industry.

    Args:
        query: What to look for, e.g. "semiconductor export controls bill 2026".
    """
    payload = {
        "query": query,
        "max_results": 6,
        "include_domains": ["congress.gov", "govtrack.us", "federalregister.gov"],
    }
    try:
        data = _tavily("/search", payload)
    except httpx.HTTPError as e:
        return f"Bill search failed: {e}"
    lines = [
        f"- {r.get('title')}\n  {r.get('url')}\n  {r.get('content', '')[:400]}"
        for r in data.get("results", [])
    ]
    return _clip("\n".join(lines) or "No results.")


@beta_tool
def record_analysis(
    investable: bool,
    insider_edge_score: float,
    alpha_remaining_score: float,
    legislative_score: float,
    interest_score: float,
    direction_alignment: str,
    summary: str,
    thesis_markdown: str,
) -> str:
    """Record your final verdict. Call this EXACTLY ONCE as your last action.

    Args:
        investable: True if the asset is publicly investable (stock/ETF/option/crypto with a resolvable ticker). If False, set all scores to 0 and explain in summary.
        insider_edge_score: 0-10. Likelihood the person had an informational advantage (committee overlap with the company's sector, timing vs non-public events, lobbying ties, track record).
        alpha_remaining_score: 0-10. How much of the edge is NOT yet priced in (10 = the thesis has not moved the price yet; 0 = fully priced in since the trade).
        legislative_score: -10 to +10. Net legislative/regulatory effect on the POSITION taken (positive = pending/likely legislation supports the trade direction).
        interest_score: 0-100 composite. How ACTIONABLE this trade is for a retail investor TODAY. Actionability dominates: if the thesis is already played out, the position is closed, or the move is fully priced in (low alpha_remaining_score), the score MUST be low regardless of how suspicious or newsworthy the conduct was. A juicy but fully-realized trade caps around 40; scores above 70 are reserved for setups where committee positioning, legislation, and remaining alpha all point to a move a member of the public could still participate in. Conduct/compliance findings belong in the summary and thesis, not in this score.
        direction_alignment: One sentence: does the trade direction match the thesis (e.g. "Buy aligned with expected defense-budget tailwind").
        summary: 2-3 sentence verdict shown in the dashboard feed.
        thesis_markdown: The full thesis in Markdown: what was traded, who traded it and their positioning, timing and price action, legislative angle, what is/isn't priced in, and why it is or isn't interesting. Use ## subheadings.
    """
    ANALYSIS_SINK.clear()
    ANALYSIS_SINK.update(
        investable=investable,
        insider_edge_score=max(0.0, min(10.0, float(insider_edge_score))),
        alpha_remaining_score=max(0.0, min(10.0, float(alpha_remaining_score))),
        legislative_score=max(-10.0, min(10.0, float(legislative_score))),
        interest_score=max(0.0, min(100.0, float(interest_score))),
        direction_alignment=direction_alignment,
        summary=summary,
        thesis_markdown=thesis_markdown,
    )
    return "Analysis recorded. You are done - do not call any more tools."


AGENT_TOOLS = [
    web_search,
    read_page,
    resolve_ticker,
    get_stock_info,
    get_price_history,
    get_politician_profile,
    get_person_trade_history,
    search_bills,
    record_analysis,
]
