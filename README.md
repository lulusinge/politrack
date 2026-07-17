# PoliTrack — politician-trade dashboard

Surfaces which stocks are interesting based on US politician trading disclosures —
House, Senate, and executive branch (OGE 278-T, including the President). Every
newly disclosed trade is investigated by an AI research agent (Claude Opus 4.8 +
Tavily web research + yfinance prices) that scores it and writes a Markdown thesis.

**Not investment advice.** All data comes from public STOCK Act / Ethics in
Government Act disclosures.

## How it works

```
every 30 min (GitHub Actions)
  poll House Clerk index ─┐
  poll Senate eFD ────────┼─> new filings -> Claude extracts trades from the PDFs/HTML
  poll OGE 278-T index ───┘        │
                                   v
                    research agent per trade (committees, bills,
                    price action vs SPY, news, track record)
                                   │
                                   v
              scores + report committed to this repo (data/ + reports/)
                                   │
                                   v
                  Streamlit Community Cloud renders the feed
```

Indicators per trade:

| Indicator | Range | Meaning |
|---|---|---|
| `insider_edge_score` | 0–10 | Likelihood of informational advantage (committees, timing, lobbying, track record) |
| `alpha_remaining_score` | 0–10 | How much of the edge is *not* yet priced in |
| `legislative_score` | −10…+10 | Legislative headwind ↔ tailwind for the position |
| `interest_score` | 0–100 | Composite; the feed sort key |

## Persistence

The git repo **is** the datastore: every watcher run commits `data/politrack.db`
and `reports/*.md`. The Actions runner and the dashboard are stateless — redeploys
lose nothing, and git history is a versioned backup of every analysis.

## Local usage

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env            # add ANTHROPIC_API_KEY and TAVILY_API_KEY

politrack init-db
politrack cycle --no-analyze     # poll + extract only (first run parks history as backlog)
politrack cycle                  # full cycle incl. analysis
politrack analyze --trade-id 42  # analyze one trade
politrack backfill --count 100   # launch content: analyze the 100 most significant recent trades
streamlit run dashboard/app.py   # dashboard at http://localhost:8501
pytest                           # offline parser tests
```

## Deployment (free)

1. **Push to GitHub** (public repo recommended — unlimited Actions minutes).
2. **Secrets**: repo → Settings → Secrets and variables → Actions → add
   `ANTHROPIC_API_KEY` and `TAVILY_API_KEY`.
3. The `watch` workflow runs every 30 minutes and commits results. Trigger it
   manually via the Actions tab (`workflow_dispatch`); pass `backfill_count` to
   run a backfill in the cloud. **Tip:** run the initial
   `politrack backfill --count 100` locally instead — 100 Opus analyses take
   several hours and real API spend (~$50–150), better watched from a terminal.
4. **Dashboard**: share.streamlit.io → deploy `dashboard/app.py` from the repo.
   No secrets needed (read-only over committed data).

## Cost notes

- Extraction: pennies per filing.
- Analysis: ~$0.50–1.50 per trade on Opus 4.8 (`ANALYSIS_MODEL` in
  `src/politrack/config.py`). There is deliberately no daily cap; set
  `MAX_ANALYSES_PER_CYCLE` env var if costs surprise you.

## Source fragility

Senate eFD sits behind Akamai bot protection (handled via `curl_cffi` Chrome
impersonation) and both Senate and OGE are screen-scrapes: any layout drift makes
the source fail *soft* (`SourceUnavailable`) — the cycle continues and the
dashboard health strip shows how many cycles a source has been failing. Senate
paper (scanned) filings are not extracted yet.
