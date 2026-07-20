"""The per-trade analysis agent: a Claude tool-use loop over research tools."""

from __future__ import annotations

import sqlite3

import anthropic

from .. import config
from ..models import AnalysisResult
from . import tools

SYSTEM_PROMPT = """\
You are an investment-research analyst investigating a single US politician \
stock trade that was just publicly disclosed. Your job is to decide how \
interesting this trade is for a member of the general public to know about, \
and to back that up with evidence.

Work through these questions using your tools:

1. INSTRUMENT - Is this a publicly investable asset (stock, ETF, listed option, \
crypto)? Resolve the ticker with resolve_ticker/get_stock_info. If it is not \
publicly investable (private fund, municipal bond, real estate), call \
record_analysis immediately with investable=false and stop.

2. TIMING - Compare the trade date and disclosure date. A long lag or a trade \
just before market-moving news is a signal.

3. THE PERSON - Use get_politician_profile for committee assignments. Does any \
committee, subcommittee, or leadership role give them non-public visibility \
into this company's sector (contracts, regulation, investigations)? Use \
web_search for lobbying ties and for executive-branch officials. Use \
get_person_trade_history for their track record of well-timed trades.

4. LEGISLATION - Use search_bills and web_search to find legislation, hearings, \
or regulatory actions around the trade window and upcoming ones that could \
move this stock or its industry, in either direction.

5. PRICED IN? - Use get_price_history (trade->disclosure->today vs SPY) and \
news search to judge whether whatever the person might have known has already \
moved the price, or whether the thesis still has room to play out.

Then call record_analysis EXACTLY ONCE with your scores and thesis. Scoring \
discipline: interest_score measures ACTIONABILITY for a retail investor today, \
not newsworthiness. Most trades are routine and score near 0. Reserve scores \
above 70 for setups the public can still act on: committee positioning plus a \
live catalyst plus meaningful alpha remaining. If the move already happened, \
the position is closed, or the edge is fully priced in, score low (a scandalous \
but played-out trade caps around 40) and put the conduct story in the thesis - \
the dashboard surfaces hot trades as trade ideas, not as headlines. Be concrete: \
name the committees, bills, dates, and price moves you found. If evidence is \
thin, say so and score low rather than inventing a story.

Be efficient: you have a budget of roughly a dozen tool calls. Do not repeat \
searches that returned nothing."""


class AnalysisFailed(Exception):
    pass


def _trade_prompt(trade: sqlite3.Row) -> str:
    return f"""Analyze this newly disclosed trade:

Person: {trade['person_name']} ({trade['chamber']})
Asset: {trade['asset_description']}
Ticker (from filing, may be missing): {trade['ticker'] or 'not stated'}
Asset type (from filing): {trade['asset_type']}
Transaction: {trade['transaction_type']}
Amount band: {trade['amount_range']}
Owner: {trade['owner']}
Trade date: {trade['trade_date']}
Disclosure date: {trade['disclosure_date']} (lag: {trade['disclosure_lag_days']} days)"""


def analyze_trade(
    trade: sqlite3.Row, client: anthropic.Anthropic | None = None
) -> tuple[AnalysisResult, int, int]:
    """Run the agent on one trade. Returns (result, input_tokens, output_tokens)."""
    client = client or anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    tools.ANALYSIS_SINK.clear()

    messages: list[dict] = [{"role": "user", "content": _trade_prompt(trade)}]
    total_in = 0
    total_out = 0

    for attempt in range(2):  # initial run + one "call record_analysis now" nudge
        runner = client.beta.messages.tool_runner(
            model=config.ANALYSIS_MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=tools.AGENT_TOOLS,
            messages=messages,
        )
        iterations = 0
        for message in runner:
            iterations += 1
            if message.usage:
                total_in += (message.usage.input_tokens or 0) + (
                    message.usage.cache_creation_input_tokens or 0
                )
                total_out += message.usage.output_tokens or 0
            # Mirror history so a follow-up runner can continue the conversation
            messages.append({"role": "assistant", "content": message.content})
            tool_response = runner.generate_tool_call_response()
            if tool_response is not None:
                messages.append(tool_response)
            if tools.ANALYSIS_SINK:
                break
            if iterations >= config.MAX_AGENT_ITERATIONS:
                break
            if (
                total_in > config.MAX_INPUT_TOKENS_PER_TRADE
                or total_out > config.MAX_OUTPUT_TOKENS_PER_TRADE
            ):
                break

        if tools.ANALYSIS_SINK:
            break
        messages.append(
            {
                "role": "user",
                "content": (
                    "Stop researching. Call record_analysis now with your best "
                    "judgment based on what you have found so far."
                ),
            }
        )

    if not tools.ANALYSIS_SINK:
        raise AnalysisFailed(
            f"agent never called record_analysis (tokens in={total_in} out={total_out})"
        )
    result = AnalysisResult(**tools.ANALYSIS_SINK)
    tools.ANALYSIS_SINK.clear()
    return result, total_in, total_out
