"""Turn a disclosure document (PDF or HTML) into structured trades via Claude."""

from __future__ import annotations

import base64

import anthropic

from . import config
from .models import FilingExtraction

EXTRACTION_INSTRUCTIONS = """\
This document is a US financial disclosure filing (a Periodic Transaction Report \
or OGE Form 278-T). Extract every securities transaction it lists.

Rules:
- One entry per transaction row, even if the same asset appears multiple times.
- asset_description: copy the asset name verbatim.
- ticker: only if explicitly stated in the document (often in parentheses); never guess.
- Owner codes: SP = spouse, DC = dependent child, JT = joint; blank/self = self.
- Dates as YYYY-MM-DD. amount_range verbatim (e.g. "$1,001 - $15,000").
- If the document is a scanned image of poor quality, extract what you can and set
  extraction_confidence to "low". If it contains no transactions, return an empty
  trades list."""


def extract_filing(
    document: bytes, doc_kind: str, client: anthropic.Anthropic | None = None
) -> FilingExtraction:
    """Extract structured trades from a filing document."""
    client = client or anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    if doc_kind == "pdf":
        doc_block: dict = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(document).decode(),
            },
        }
    else:  # html/text (Senate electronic filings)
        doc_block = {
            "type": "document",
            "source": {
                "type": "text",
                "media_type": "text/plain",
                "data": document.decode("utf-8", errors="replace"),
            },
        }

    response = client.messages.parse(
        model=config.EXTRACTION_MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        messages=[
            {
                "role": "user",
                "content": [doc_block, {"type": "text", "text": EXTRACTION_INSTRUCTIONS}],
            }
        ],
        output_format=FilingExtraction,
    )
    if response.parsed_output is None:
        raise ValueError(f"extraction produced no parseable output (stop_reason={response.stop_reason})")
    return response.parsed_output
