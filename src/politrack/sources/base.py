"""Source protocol shared by House / Senate / OGE pollers."""

from __future__ import annotations

from typing import Protocol

from ..models import FilingRef


class SourceUnavailable(Exception):
    """Raised when a source can't be polled (network, 403, layout drift).

    Cycle-level code treats this as a soft failure: log it, mark the source
    unhealthy for this run, and continue with the other sources.
    """


class Source(Protocol):
    name: str

    def poll(self) -> list[FilingRef]:
        """Return refs for recent filings. Dedup happens downstream in the DB."""
        ...

    def fetch_document(self, ref_row: dict) -> tuple[bytes, str]:
        """Download the filing document. Returns (content, doc_kind)."""
        ...
