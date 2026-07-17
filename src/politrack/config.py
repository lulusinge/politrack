"""Central configuration: paths, model choice, budgets."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

DATA_DIR = REPO_ROOT / "data"
PDF_DIR = DATA_DIR / "pdfs"
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = REPO_ROOT / "reports"
DB_PATH = DATA_DIR / "politrack.db"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
CONGRESS_GOV_API_KEY = os.getenv("CONGRESS_GOV_API_KEY", "")

# Models
ANALYSIS_MODEL = "claude-opus-4-8"
EXTRACTION_MODEL = "claude-opus-4-8"

# Analysis guardrails (no daily cap by design; these bound a single runaway trade)
MAX_AGENT_ITERATIONS = 15
MAX_INPUT_TOKENS_PER_TRADE = 250_000
MAX_OUTPUT_TOKENS_PER_TRADE = 30_000
MAX_ANALYSES_PER_CYCLE = int(os.getenv("MAX_ANALYSES_PER_CYCLE", "0"))  # 0 = unlimited
MAX_EXTRACT_ATTEMPTS = 3
MAX_ANALYZE_ATTEMPTS = 3

# Source polling
HOUSE_INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
HOUSE_PTR_PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
SENATE_BASE_URL = "https://efdsearch.senate.gov"
OGE_INDEX_URL = "https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index?OpenView"
SENATE_LOOKBACK_DAYS = 10
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 30.0

for _d in (DATA_DIR, PDF_DIR, CACHE_DIR, REPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
