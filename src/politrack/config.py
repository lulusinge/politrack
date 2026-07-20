"""Central configuration: paths, model choice, budgets."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
# .env wins over inherited process env locally (override=True) so a stale
# long-lived process can't shadow a fresh edit. CI and Streamlit Cloud have no
# .env file, so their real env vars pass through untouched.
load_dotenv(REPO_ROOT / ".env", override=True)

DATA_DIR = REPO_ROOT / "data"
PDF_DIR = DATA_DIR / "pdfs"
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = REPO_ROOT / "reports"
DB_PATH = DATA_DIR / "politrack.db"

# --- Environment variables — the complete inventory. Each is read exactly
# --- once, here, with its single default. Nothing else calls os.getenv.
# Watcher (required — enforced via require() at CLI entry points)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
# Watcher (optional)
BUTTONDOWN_API_KEY = os.getenv("BUTTONDOWN_API_KEY", "")
NOTIFY_THRESHOLD = float(os.getenv("NOTIFY_THRESHOLD", "70"))
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "")
MAX_ANALYSES_PER_CYCLE = int(os.getenv("MAX_ANALYSES_PER_CYCLE", "0"))  # 0 = unlimited
# Dashboard (optional; features hidden while unset)
BUTTONDOWN_USERNAME = os.getenv("BUTTONDOWN_USERNAME", "")
BMC_SLUG = os.getenv("BMC_SLUG", "")

# Models
ANALYSIS_MODEL = "claude-opus-4-8"
EXTRACTION_MODEL = "claude-opus-4-8"

# Analysis guardrails (no daily cap by design; these bound a single runaway trade)
MAX_AGENT_ITERATIONS = 15
MAX_INPUT_TOKENS_PER_TRADE = 250_000
MAX_OUTPUT_TOKENS_PER_TRADE = 30_000
MAX_EXTRACT_ATTEMPTS = 3
MAX_ANALYZE_ATTEMPTS = 3

# Notifications: Buttondown broadcast when a trade scores above the threshold.
# Buttondown owns the subscriber list, double opt-in, and unsubscribe links —
# no addresses are ever stored on our side.
BUTTONDOWN_API_URL = "https://api.buttondown.com/v1/emails"

# Source polling
HOUSE_INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
HOUSE_PTR_PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
SENATE_BASE_URL = "https://efdsearch.senate.gov"
OGE_INDEX_URL = "https://extapps2.oge.gov/201/Presiden.nsf/PAS+Filings+by+Date?OpenView"
SENATE_LOOKBACK_DAYS = 10
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 30.0

for _d in (DATA_DIR, PDF_DIR, CACHE_DIR, REPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def require(*names: str) -> None:
    """Fail fast, by name, when required settings are empty."""
    missing = [n for n in names if not globals()[n]]
    if missing:
        raise SystemExit(
            f"Missing required configuration: {', '.join(missing)}. "
            f"Set as environment variables or in {REPO_ROOT / '.env'}."
        )
