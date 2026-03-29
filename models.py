"""
Pydantic data models defining the schema for competitive signals.

This file is the single source of truth for every data structure in the pipeline.
Every other module imports from here. No logic, no API calls — just blueprints.

WHAT IS PYDANTIC?
    Pydantic is a Python library for data validation using type hints. Instead of
    plain dictionaries (which have no structure enforcement), Pydantic models:
    - Validate automatically: pass a string where a float is expected → clear error
    - Serialize easily: .model_dump() → dict, .model_dump_json() → JSON
    - Self-document: Field(description=...) describes each field's purpose
    - Support IDE autocomplete: your editor knows every field and its type

    Think of Pydantic models as "smart dictionaries" that enforce your data is
    always valid and well-structured.
"""

from datetime import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


# ─── Classification ─────────────────────────────────────────────────────────
# A string enum that categorizes HOW a signal relates to NWTN competitively.
# Using (str, Enum) means values serialize cleanly to JSON and are human-readable
# in logs and reports. If we used plain strings, a typo like "direct_compettitor"
# would silently pass. With an enum, Python catches invalid values immediately.
class Classification(str, Enum):
    """How a competitive signal relates to NWTN AI's business."""
    DIRECT_COMPETITOR = "direct_competitor"   # Does the same thing for the same market
    ADJACENT_THREAT = "adjacent_threat"       # Different offering but could move into our space
    POTENTIAL_PARTNER = "potential_partner"    # Complementary — could collaborate with NWTN
    IRRELEVANT = "irrelevant"                 # Surfaced by search but not competitively meaningful


# ─── RawSearchResult ─────────────────────────────────────────────────────────
# What comes back from Tavily before Claude analyzes it. Intentionally minimal —
# just the raw data from the search engine. We keep this separate from
# CompetitiveSignal because not every search result IS a signal (some are noise),
# and the raw result doesn't have analysis fields like overlap_score.
class RawSearchResult(BaseModel):
    """A single unprocessed result from web search.

    Created by: search.py (from Tavily API responses)
    Consumed by: analyze.py (transformed into CompetitiveSignal objects)
    """
    query: str = Field(description="The search query that produced this result")
    title: str = Field(description="Headline from the search result")
    url: str = Field(description="Source URL of the article/page")
    content: str = Field(description="Extracted page content from Tavily (advanced depth)")
    score: float = Field(
        ge=0.0, le=1.0,
        description="Tavily's relevance score for this result (0-1)",
    )


# ─── CompetitiveSignal ───────────────────────────────────────────────────────
# The core data structure of the entire pipeline. This is what Claude produces
# when it reads a raw search result and extracts structured intelligence.
#
# Unlike the previous design where scoring was a separate model (ScoredSignal),
# this schema puts overlap scoring directly on the signal. This makes sense
# because Claude assesses overlap WHILE analyzing the result — it's one
# reasoning step, not two. The signal carries its own competitive assessment.
#
# The id uses uuid4 (random) rather than a deterministic hash. Deduplication
# is handled by matching on `url` in memory.py, not by ID comparison.
class CompetitiveSignal(BaseModel):
    """A structured competitive signal extracted and scored by Claude.

    Created by: analyze.py (Claude interprets raw search results)
    Consumed by: memory.py (dedup), report.py (Word doc), deliver.py (Slack)
    Stored in: SQLite via memory.py
    """
    # ── Identity ──────────────────────────────────────────────────────────
    id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier (UUID). Auto-generated, not user-supplied.",
    )

    # ── What we found ─────────────────────────────────────────────────────
    company_name: str = Field(description="Name of the company or individual")
    url: str = Field(description="Source URL where we found this signal")
    description: str = Field(
        description="What this company/person does (1-2 sentences)",
    )
    positioning: str = Field(
        description="How they describe themselves — their tagline or value prop",
    )

    # ── Market context ────────────────────────────────────────────────────
    target_market: str = Field(
        description="Who they serve: enterprise, mid-market, SMB, or specific verticals",
    )
    service_type: str = Field(
        description="What they offer: consulting, dev shop, SaaS, platform, etc.",
    )

    # ── Five-criteria scoring (criteria 1-4 scored by Claude, 5 by scoring.py) ──
    market_overlap: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="How much their target market overlaps with NWTN's (mid-market CPG)",
    )
    service_overlap: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="How much their service offering overlaps with NWTN's (vendor-neutral AI integration)",
    )
    positioning_overlap: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="How similar their market positioning is to NWTN's (operator credibility + AI)",
    )
    credibility_score: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Strength of their credibility signals (brand experience, case studies, evidence)",
    )
    is_complementary: bool = Field(
        default=False,
        description="Whether their offering complements rather than competes with NWTN",
    )
    overlap_reasoning: str = Field(
        description="Claude's explanation of the dimension scores and overall assessment",
    )

    # ── Computed by scoring.py (not set by Claude) ───────────────────────
    overlap_score: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Weighted composite of all 5 criteria — calculated by scoring.py",
    )
    classification: Classification = Field(
        default=Classification.IRRELEVANT,
        description="Competitive relationship — assigned by scoring.py based on composite score",
    )

    # ── Lifecycle tracking ────────────────────────────────────────────────
    first_seen: datetime = Field(
        default_factory=datetime.now,
        description="When this signal was first discovered by the scanner",
    )
    last_seen: datetime = Field(
        default_factory=datetime.now,
        description="Most recent scan that surfaced this signal (updated on re-discovery)",
    )
    source_query: str = Field(
        description="Which search query from config.py surfaced this signal",
    )
    is_new: bool = Field(
        default=True,
        description="True if this is the first time we've seen this signal, False if it's a re-discovery",
    )


# ─── ScanReport ──────────────────────────────────────────────────────────────
# Metadata about a completed scan. This is what report.py produces and
# deliver.py consumes to post to Slack. It summarizes the scan WITHOUT
# embedding every signal — the signals live in the Word doc and the database.
class ScanReport(BaseModel):
    """Summary of a completed competitive scan run.

    Created by: report.py (after generating the Word doc)
    Consumed by: deliver.py (posted to Slack as a summary message)
    """
    scan_date: datetime = Field(
        default_factory=datetime.now,
        description="When this scan was executed",
    )
    queries_run: int = Field(
        default=0,
        description="How many search queries were executed",
    )
    total_results_found: int = Field(
        default=0,
        description="Total raw search results across all queries",
    )
    new_signals: int = Field(
        default=0,
        description="How many signals were seen for the first time",
    )
    updated_signals: int = Field(
        default=0,
        description="How many previously-seen signals were re-discovered (last_seen updated)",
    )
    summary: str = Field(
        default="",
        description="Human-readable summary of the scan results for Slack",
    )
    file_path: str = Field(
        default="",
        description="Path to the generated .docx report file",
    )
