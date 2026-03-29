"""
SQLite — stores all signals, checks for duplicates, returns only new ones.

This module is the pipeline's "memory." Without it, every scan would report
the same companies as new. SQLite stores previously seen signals so the report
only surfaces genuinely NEW intelligence.

DEDUPLICATION LOGIC (two tiers):
    1. URL match (exact): same article seen before → skip entirely
    2. Company name match (fuzzy): same company, different article →
       update last_seen timestamp, mark as updated, don't re-score
    3. Neither match → genuinely new signal, insert it

FUZZY MATCHING:
    Simple normalization instead of a heavy library. We lowercase the name,
    strip common suffixes (Inc, LLC, AI, Corp, etc.), and collapse whitespace.
    "Faye Digital", "FAYE DIGITAL INC.", and "Faye" all normalize to "faye digital"
    or "faye". This catches most duplicates with zero dependencies.
"""

import os
import re
import sqlite3
from datetime import datetime, timedelta

from models import CompetitiveSignal, Classification
import config

# Suffixes to strip during name normalization
_STRIP_SUFFIXES = [
    "inc", "llc", "ltd", "corp", "corporation", "co",
    "ai", "consulting", "solutions", "group", "partners",
    "services", "labs", "digital", "technologies",
]


def _normalize_name(name: str) -> str:
    """Normalize a company name for fuzzy matching.

    Lowercases, strips common business suffixes, removes punctuation,
    and collapses whitespace.

    Examples:
        "Faye Digital"       → "faye"
        "FAYE DIGITAL INC."  → "faye"
        "Prime AI Solutions"  → "prime"
        "Cascade Insights®"  → "cascade insights"

    Args:
        name: Raw company name string.

    Returns:
        Normalized name for comparison.
    """
    # Lowercase and strip whitespace
    normalized = name.lower().strip()

    # Remove punctuation (®, ™, commas, periods, etc.)
    normalized = re.sub(r"[^\w\s]", "", normalized)

    # Remove common suffixes — applied repeatedly because order matters
    # (e.g., "Prime AI Solutions" → strip "solutions" → strip "ai" → "prime")
    for suffix in _STRIP_SUFFIXES:
        normalized = re.sub(rf"\b{suffix}\b", "", normalized)

    # Collapse whitespace
    normalized = re.sub(r"\s+", " ", normalized).strip()

    return normalized


def init_db() -> None:
    """Initialize the SQLite database and create the signals table.

    Creates the data/ directory and database file if they don't exist.
    Uses CREATE TABLE IF NOT EXISTS so it's safe to call every run.
    """
    # Ensure the directory exists
    db_dir = os.path.dirname(config.DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            url TEXT NOT NULL,
            description TEXT,
            positioning TEXT,
            target_market TEXT,
            service_type TEXT,
            market_overlap REAL DEFAULT 0,
            service_overlap REAL DEFAULT 0,
            positioning_overlap REAL DEFAULT 0,
            credibility_score REAL DEFAULT 0,
            is_complementary INTEGER DEFAULT 0,
            overlap_score REAL DEFAULT 0,
            overlap_reasoning TEXT,
            classification TEXT,
            first_seen TIMESTAMP,
            last_seen TIMESTAMP,
            source_query TEXT,
            is_new INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()
    print(f"[MEMORY] Database initialized at: {config.DB_PATH}")


def check_duplicates(
    signals: list[CompetitiveSignal],
) -> tuple[list[CompetitiveSignal], list[CompetitiveSignal]]:
    """Check signals against the database, split into new vs. updated.

    For each incoming signal:
    1. If URL already in DB → skip (exact duplicate)
    2. If normalized company name matches an existing row → update last_seen,
       return the EXISTING signal (with is_new=False) in the updated list
    3. Neither match → genuinely new signal

    Args:
        signals: List of CompetitiveSignal objects from analyze.py.

    Returns:
        Tuple of (new_signals, updated_signals).
    """
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    # Load existing URLs and normalized names for comparison
    existing_urls = {
        row["url"] for row in conn.execute("SELECT url FROM signals")
    }
    existing_names = {
        row["normalized_name"]: row["id"]
        for row in conn.execute("SELECT id, normalized_name FROM signals")
    }

    new_signals: list[CompetitiveSignal] = []
    updated_signals: list[CompetitiveSignal] = []

    for signal in signals:
        # Tier 1: exact URL match → skip entirely
        if signal.url in existing_urls:
            print(f"  [DEDUP] URL already seen, skipping: {signal.url[:60]}...")
            continue

        # Tier 2: fuzzy company name match → update last_seen
        normalized = _normalize_name(signal.company_name)
        if normalized and normalized in existing_names:
            existing_id = existing_names[normalized]
            _update_last_seen(conn, existing_id)
            signal.is_new = False
            updated_signals.append(signal)
            print(f"  [DEDUP] Company re-found: {signal.company_name} → updating last_seen")
            continue

        # Tier 3: genuinely new
        new_signals.append(signal)

    conn.close()
    print(f"[MEMORY] {len(new_signals)} new, {len(updated_signals)} updated, "
          f"{len(signals) - len(new_signals) - len(updated_signals)} skipped (URL dupes)")
    return new_signals, updated_signals


def insert_signals(signals: list[CompetitiveSignal]) -> int:
    """Insert new signals into the database.

    Args:
        signals: List of genuinely new CompetitiveSignal objects.

    Returns:
        Number of signals inserted.
    """
    if not signals:
        return 0

    conn = sqlite3.connect(config.DB_PATH)
    inserted = 0

    for signal in signals:
        conn.execute(
            """
            INSERT OR IGNORE INTO signals
            (id, company_name, normalized_name, url, description, positioning,
             target_market, service_type, market_overlap, service_overlap,
             positioning_overlap, credibility_score, is_complementary,
             overlap_score, overlap_reasoning, classification,
             first_seen, last_seen, source_query, is_new)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.id,
                signal.company_name,
                _normalize_name(signal.company_name),
                signal.url,
                signal.description,
                signal.positioning,
                signal.target_market,
                signal.service_type,
                signal.market_overlap,
                signal.service_overlap,
                signal.positioning_overlap,
                signal.credibility_score,
                int(signal.is_complementary),
                signal.overlap_score,
                signal.overlap_reasoning,
                signal.classification.value,
                signal.first_seen.isoformat(),
                signal.last_seen.isoformat(),
                signal.source_query,
                1,
            ),
        )
        inserted += 1

    conn.commit()
    conn.close()
    print(f"[MEMORY] Inserted {inserted} new signals")
    return inserted


def _update_last_seen(conn: sqlite3.Connection, signal_id: str) -> None:
    """Update the last_seen timestamp for an existing signal.

    Args:
        conn: Active SQLite connection.
        signal_id: The UUID of the existing signal to update.
    """
    conn.execute(
        "UPDATE signals SET last_seen = ? WHERE id = ?",
        (datetime.now().isoformat(), signal_id),
    )
    conn.commit()


def get_recent_signals(days: int = 30) -> list[CompetitiveSignal]:
    """Get all signals from the last N days for reporting.

    Args:
        days: Number of days to look back. Default 30.

    Returns:
        List of CompetitiveSignal objects from the database.
    """
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM signals WHERE last_seen >= ? ORDER BY overlap_score DESC",
        (cutoff,),
    ).fetchall()
    conn.close()

    signals = []
    for row in rows:
        signals.append(
            CompetitiveSignal(
                id=row["id"],
                company_name=row["company_name"],
                url=row["url"],
                description=row["description"] or "",
                positioning=row["positioning"] or "",
                target_market=row["target_market"] or "",
                service_type=row["service_type"] or "",
                market_overlap=row["market_overlap"] or 0.0,
                service_overlap=row["service_overlap"] or 0.0,
                positioning_overlap=row["positioning_overlap"] or 0.0,
                credibility_score=row["credibility_score"] or 0.0,
                is_complementary=bool(row["is_complementary"]),
                overlap_score=row["overlap_score"] or 0.0,
                overlap_reasoning=row["overlap_reasoning"] or "",
                classification=row["classification"] or "irrelevant",
                first_seen=datetime.fromisoformat(row["first_seen"]),
                last_seen=datetime.fromisoformat(row["last_seen"]),
                source_query=row["source_query"] or "",
                is_new=bool(row["is_new"]),
            )
        )

    print(f"[MEMORY] Retrieved {len(signals)} signals from last {days} days")
    return signals
