"""
All configuration — search queries, scoring weights, NWTN positioning, Slack channel.

WHY THIS FILE EXISTS (Separation of Concerns):
    Every tunable value in the entire pipeline lives here. No module has hardcoded
    API keys, magic numbers, or embedded business logic constants. This means:

    1. You can change search queries, scoring weights, or the Slack channel WITHOUT
       touching any pipeline code
    2. Sensitive values (API keys) are loaded from environment variables, so they
       never appear in source code or git history
    3. When onboarding someone new, they can read this one file to understand every
       knob they can turn

HOW IT WORKS:
    - load_dotenv() reads from a .env file in the project root and loads those
      values into os.environ. This is the standard Python pattern for secrets.
    - os.getenv("KEY", "default") reads from environment variables with a fallback.
    - The second argument ("") is the default if the env var isn't set — this lets
      the app start in placeholder mode without any API keys configured.
"""

import os
from dotenv import load_dotenv

# load_dotenv() reads the .env file (if it exists) and makes its values available
# via os.getenv(). This happens once when config.py is first imported. Every other
# module that does `import config` gets these values already loaded.
load_dotenv()

# ─── API Keys ────────────────────────────────────────────────────────────────
# These come from .env (never hardcoded). Each key authenticates with an external
# service. The empty string default means "not configured" — each module should
# check for this and handle gracefully (e.g., skip Slack delivery if no token).
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")         # Web search API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")    # Claude API for analysis
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")        # Slack bot for delivery
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")      # Which Slack channel to post to

# ─── Database ────────────────────────────────────────────────────────────────
# SQLite file path. SQLite stores everything in a single file — no server needed.
# Default "signals.db" creates the file in the project root.
DB_PATH = os.getenv("DB_PATH", "data/scanner.db")

# ─── Report Output ───────────────────────────────────────────────────────────
# Directory where generated Word docs are saved. Created automatically if missing.
REPORT_DIR = os.getenv("REPORT_DIR", "reports")

# ─── NWTN Positioning ───────────────────────────────────────────────────────
# This text is sent to Claude as context when analyzing search results, and used
# by the scoring module to evaluate relevance. It's the "lens" through which every
# signal is interpreted. If NWTN's positioning changes, update this and all future
# scans will automatically reflect the new focus.
NWTN_POSITIONING = """
NWTN AI is a CPG-focused AI consultancy positioned as "your AI department without
the full-time hire." We serve mid-market CPG brands ($5M-$200M revenue) who need
AI strategy, implementation, and ongoing support but can't justify a full internal
AI team. Core services: AI readiness assessments, workflow automation, custom agent
development, and fractional AI leadership.

Key differentiators:
- Deep CPG domain expertise (brand management, trade marketing, supply chain)
- Hands-on implementation, not just strategy decks
- Fractional model makes enterprise AI accessible to mid-market
- Focus on measurable ROI within 90 days
"""

# ─── Search Queries ──────────────────────────────────────────────────────────
# Each string becomes a separate Tavily API call. The queries are designed to
# cast a wide net across different angles of the competitive landscape:
# - Direct competitors (other AI consulting firms in CPG)
# - Adjacent competitors (general AI agencies moving into CPG)
# - Market positioning (fractional/mid-market AI services)
# - Vertical-specific (food, beverage, DTC brands)
SEARCH_QUERIES = [
    "AI consulting CPG brands",
    "AI integrator food beverage companies",
    "fractional AI officer CPG",
    "AI automation mid-market consumer packaged goods",
    "AI agency food and beverage DTC",
    "CPG AI consulting firm",
    "AI workflow automation consumer brands",
]

# ─── Scoring Weights ─────────────────────────────────────────────────────────
# Five criteria, weights must sum to 1.0. These control the composite score:
#   composite = (market * 0.30) + (service * 0.25) + (positioning * 0.25)
#             + (credibility * 0.15) + (recency * 0.05)
#
# - market_overlap (30%): Are they serving mid-market CPG? Weighted highest
#   because wrong market = wrong competitor, regardless of service similarity.
# - service_overlap (25%): Are they doing vendor-neutral AI integration?
# - positioning_overlap (25%): Do they position with operator credibility + AI?
# - credibility_score (15%): Do they have proof (case studies, brand names)?
# - recency (5%): How recently was this signal found? Slight boost for fresh intel.
SCORING_WEIGHTS = {
    "market_overlap": 0.30,
    "service_overlap": 0.25,
    "positioning_overlap": 0.25,
    "credibility_score": 0.15,
    "recency": 0.05,
}

# ─── Classification Thresholds ───────────────────────────────────────────────
# Applied to the weighted composite score to assign classification:
#   >= 0.7  → direct_competitor
#   >= 0.4  → adjacent_threat
#   >= 0.2 AND is_complementary → potential_partner
#   else    → irrelevant
CLASSIFICATION_THRESHOLDS = {
    "direct_competitor": 0.7,
    "adjacent_threat": 0.4,
    "potential_partner": 0.2,
}

# ─── High Priority Threshold ────────────────────────────────────────────────
# Signals with composite >= this value are flagged in the report header.
HIGH_PRIORITY_THRESHOLD = 0.7
