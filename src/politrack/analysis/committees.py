"""Committee membership data from the unitedstates/congress-legislators project.

Free, maintained YAML files on GitHub. Cached locally and refreshed weekly.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import yaml

from .. import config

BASE = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"
FILES = {
    "legislators": f"{BASE}/legislators-current.yaml",
    "memberships": f"{BASE}/committee-membership-current.yaml",
    "committees": f"{BASE}/committees-current.yaml",
}
CACHE_TTL_SECONDS = 7 * 24 * 3600


def _cached_fetch(key: str) -> object:
    cache_file = config.CACHE_DIR / f"{key}.json"
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < CACHE_TTL_SECONDS:
        return json.loads(cache_file.read_text())
    resp = httpx.get(FILES[key], timeout=60.0, follow_redirects=True)
    resp.raise_for_status()
    data = yaml.safe_load(resp.text)
    cache_file.write_text(json.dumps(data))
    return data


def _build_lookup() -> tuple[dict, dict, dict]:
    legislators = _cached_fetch("legislators")
    memberships = _cached_fetch("memberships")
    committees = _cached_fetch("committees")

    committee_names = {}
    for c in committees:
        committee_names[c.get("thomas_id")] = c.get("name")
        for sub in c.get("subcommittees", []) or []:
            committee_names[f"{c.get('thomas_id')}{sub.get('thomas_id')}"] = (
                f"{c.get('name')} — Subcommittee on {sub.get('name')}"
            )

    by_bioguide: dict[str, dict] = {}
    for leg in legislators:
        bioguide = leg.get("id", {}).get("bioguide")
        if bioguide:
            by_bioguide[bioguide] = leg

    committees_by_bioguide: dict[str, list[str]] = {}
    for thomas_id, members in memberships.items():
        cname = committee_names.get(thomas_id, thomas_id)
        for m in members:
            bg = m.get("bioguide")
            if not bg:
                continue
            label = cname + (f" ({m['title']})" if m.get("title") else "")
            committees_by_bioguide.setdefault(bg, []).append(label)

    return by_bioguide, committees_by_bioguide, committee_names


def get_profile(name: str) -> str:
    """Human-readable profile for a member of Congress, or a not-found note."""
    try:
        by_bioguide, committees_by_bioguide, _ = _build_lookup()
    except Exception as e:  # network failure shouldn't kill the agent turn
        return f"Committee data temporarily unavailable ({e}). Use web_search instead."

    tokens = {t.strip(".,").lower() for t in name.split() if len(t.strip(".,")) > 1}
    best: tuple[int, dict] | None = None
    for leg in by_bioguide.values():
        n = leg.get("name", {})
        full = {
            str(n.get(k, "")).lower()
            for k in ("first", "last", "nickname", "official_full")
        }
        full_tokens = set(" ".join(full).split())
        score = len(tokens & full_tokens)
        if str(n.get("last", "")).lower() in tokens:
            score += 2
        if best is None or score > best[0]:
            best = (score, leg)

    if best is None or best[0] < 3:
        return (
            f"No confident match for '{name}' in current-Congress data. "
            "They may be a former member or an executive-branch official — use web_search."
        )

    leg = best[1]
    n = leg.get("name", {})
    term = (leg.get("terms") or [{}])[-1]
    bg = leg.get("id", {}).get("bioguide", "")
    committees = committees_by_bioguide.get(bg, [])
    lines = [
        f"Name: {n.get('official_full') or (n.get('first', '') + ' ' + n.get('last', ''))}",
        f"Chamber: {'Senate' if term.get('type') == 'sen' else 'House'}",
        f"Party: {term.get('party')}, State: {term.get('state')}"
        + (f"-{term.get('district')}" if term.get("district") is not None else ""),
        f"Current term: {term.get('start')} to {term.get('end')}",
        "Committee assignments:",
    ]
    lines += [f"  - {c}" for c in committees] or ["  (none found)"]
    return "\n".join(lines)
