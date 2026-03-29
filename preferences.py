"""
Reads and writes config/preferences.json — the feedback loop configuration.

This module is the bridge between human judgment and agent behavior.
Every preference here changes how the next scan runs without touching code.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PREFS_PATH = Path("config/preferences.json")

# Default structure if preferences.json doesn't exist
_DEFAULTS = {
    "excluded_companies": [],
    "excluded_domains": [],
    "boosted_queries": [],
    "deprecated_queries": [],
    "scoring_overrides": {},
    "notes": [],
}


def load() -> dict:
    """Load preferences from config/preferences.json.

    Returns defaults if the file doesn't exist or is invalid.
    """
    if not PREFS_PATH.exists():
        logger.info("No preferences.json found — using defaults")
        return dict(_DEFAULTS)

    try:
        with open(PREFS_PATH) as f:
            prefs = json.load(f)
        logger.info(f"Loaded preferences: {_summary(prefs)}")
        return prefs
    except Exception as e:
        logger.error(f"Failed to read preferences.json: {e} — using defaults")
        return dict(_DEFAULTS)


def save(prefs: dict) -> None:
    """Save preferences to config/preferences.json."""
    os.makedirs(PREFS_PATH.parent, exist_ok=True)
    with open(PREFS_PATH, "w") as f:
        json.dump(prefs, f, indent=2)
    logger.info("Preferences saved")


def add_note(prefs: dict, note: str) -> None:
    """Add a timestamped note to the preferences."""
    prefs.setdefault("notes", []).append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "note": note,
    })


def _summary(prefs: dict) -> str:
    """One-line summary of what's configured."""
    parts = []
    if prefs.get("excluded_companies"):
        parts.append(f"{len(prefs['excluded_companies'])} excluded companies")
    if prefs.get("excluded_domains"):
        parts.append(f"{len(prefs['excluded_domains'])} excluded domains")
    if prefs.get("boosted_queries"):
        parts.append(f"{len(prefs['boosted_queries'])} boosted queries")
    if prefs.get("deprecated_queries"):
        parts.append(f"{len(prefs['deprecated_queries'])} deprecated queries")
    if prefs.get("scoring_overrides"):
        parts.append(f"{len(prefs['scoring_overrides'])} scoring overrides")
    return ", ".join(parts) if parts else "no customizations"
