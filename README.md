# NWTN.AI Competitive Landscape Scanner

An autonomous competitive intelligence agent that monitors the CPG + AI consulting market on a weekly schedule. It searches the web, analyzes results with Claude, deduplicates against a persistent database, scores signals on five weighted criteria, generates a polished Word doc report, and delivers it to Slack.

Built as a learning project using Claude Code. Every module is a separate pipeline stage — build, test, and improve each one independently.

## Architecture

```
                          config.py
                        preferences.json
                              │
                              ▼
┌─────────┐   ┌───────────┐   ┌──────────┐   ┌───────────┐
│ Tavily   │──▶│  Claude    │──▶│  SQLite   │──▶│  Scoring  │
│ search.py│   │ analyze.py │   │ memory.py │   │scoring.py │
└─────────┘   └───────────┘   └──────────┘   └───────────┘
     │              │               │               │
     │         5-dimension     dedup by          weighted
     │         extraction +    company name      composite +
     │         assessment      & URL             classification
     │                                              │
     │                                              ▼
     │                                     ┌───────────────┐
     │                                     │   Report       │
     │                                     │   report.py    │
     │                                     │   (python-docx)│
     │                                     └───────┬───────┘
     │                                             │
     │                                             ▼
     │                                     ┌───────────────┐
     │                                     │   Slack        │
     │                                     │   deliver.py   │
     │                                     │   (slack_sdk)  │
     │                                     └───────────────┘
     │
     └── main.py orchestrates the full pipeline
         server.py provides HTTP API + health checks
         retry.py wraps external calls with backoff
```

### File Structure

```
nwtn-competitive-scanner/
├── main.py              # Pipeline orchestrator + CLI
├── server.py            # FastAPI server (Railway deployment)
├── search.py            # Tavily web search
├── analyze.py           # Claude API — extraction + 5-dimension scoring
├── memory.py            # SQLite dedup + storage
├── scoring.py           # Weighted composite calculation + classification
├── report.py            # Word doc generation (python-docx)
├── deliver.py           # Slack delivery (slack_sdk)
├── models.py            # Pydantic data models
├── config.py            # All configuration + NWTN positioning
├── preferences.py       # Read/write config/preferences.json
├── retry.py             # Exponential backoff retry utility
├── feedback_session.py  # Interactive review script
├── config/
│   └── preferences.json # Feedback loop — exclusions, boosts, overrides
├── data/
│   └── scanner.db       # SQLite database (auto-created)
├── reports/             # Generated .docx files
├── Dockerfile           # Python 3.11 slim container
├── railway.toml         # Railway deployment config
├── requirements.txt
├── .env.example
├── .gitignore
└── .dockerignore
```

## Setup

### Prerequisites

- Python 3.11+
- API keys for Tavily, Anthropic (Claude), and Slack

### Install

```bash
git clone https://github.com/your-org/nwtn-competitive-scanner.git
cd nwtn-competitive-scanner

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in your API keys
```

### API Keys

| Service | Purpose | Where to get it |
|---|---|---|
| Tavily | Web search | [tavily.com](https://tavily.com) — free tier: 1,000 searches/mo |
| Anthropic | Signal analysis + report summaries | [console.anthropic.com](https://console.anthropic.com) |
| Slack | Report delivery | [api.slack.com/apps](https://api.slack.com/apps) — needs `chat:write` + `files:write` scopes |

### Slack Bot Setup

1. Create a Slack app at [api.slack.com/apps](https://api.slack.com/apps)
2. Add scopes: `chat:write`, `files:write`
3. Install to your workspace
4. Copy the Bot User OAuth Token (`xoxb-...`) to `.env`
5. Get the Channel ID (right-click channel → View details → scroll to bottom)
6. Invite the bot to the channel: `/invite @your-bot-name`

## Usage

### On-Demand (Local)

```bash
# Full scan — search, analyze, score, report, deliver to Slack
python main.py

# Dry run — everything except Slack delivery
python main.py --dry-run

# Add a one-off search query
python main.py --query "AI consulting beverage brands"

# Report from existing database (no search/analysis, no API cost)
python main.py --report-only

# Combine flags
python main.py --dry-run --query "fractional AI CPG" --query "AI agency food brands"
```

### HTTP API (Deployed)

```bash
# Start the server locally
uvicorn server:app --host 0.0.0.0 --port 8000

# Health check
curl http://localhost:8000/health

# Trigger a scan
curl -X POST http://localhost:8000/run

# Check scan status
curl http://localhost:8000/status
```

### Scheduled (Railway)

When deployed to Railway, the scanner runs as a FastAPI service with a cron job:

1. Push to GitHub and connect the repo to Railway
2. Add environment variables (the 4 API keys from `.env`)
3. Add a volume mounted at `/data` (persists SQLite + reports across deploys)
4. Create a cron service: `curl -X POST https://your-app.railway.app/run`
5. Set schedule: `0 6 * * 1` (every Monday at 6am)
6. Set timezone: `America/Los_Angeles` (Pacific)

## Scoring System

Every signal is scored on 5 weighted dimensions. Criteria 1–4 are assessed by Claude during analysis. Criterion 5 is calculated automatically.

| # | Dimension | Weight | What it measures |
|---|---|---|---|
| 1 | Market Overlap | 30% | Do they serve mid-market CPG? |
| 2 | Service Overlap | 25% | Do they offer vendor-neutral AI integration? |
| 3 | Positioning Overlap | 25% | Do they position with operator credibility + AI? |
| 4 | Credibility | 15% | Named brand experience, case studies, evidence? |
| 5 | Recency | 5% | How recently was this signal discovered? |

### Classification Thresholds

| Composite Score | Classification |
|---|---|
| >= 0.70 | Direct Competitor |
| >= 0.40 | Adjacent Threat |
| >= 0.20 + complementary | Potential Partner |
| < 0.20 | Irrelevant |

All weights and thresholds are configurable in `config.py` and overridable via `config/preferences.json`.

## Feedback Loop

The scanner improves over time through a review cycle:

```
Run scan → Read report → Review signals → Update preferences → Next scan is better
```

### Interactive Review

```bash
python feedback_session.py            # Review last 30 days
python feedback_session.py --days 14  # Review last 2 weeks
```

The script walks you through:
1. Summary of what was found (companies, classifications, query hit counts)
2. Option to exclude noisy companies or domains
3. Option to boost productive queries or deprecate noisy ones
4. Option to adjust scoring weights
5. Freeform notes

### Preferences File

All feedback is stored in `config/preferences.json`:

```json
{
  "excluded_companies": ["Accenture", "N/A — industry article"],
  "excluded_domains": ["wikipedia.org", "linkedin.com"],
  "boosted_queries": ["fractional AI officer CPG"],
  "deprecated_queries": ["AI agency food and beverage DTC"],
  "scoring_overrides": {"market_overlap": 0.40},
  "notes": [
    {"date": "2026-03-28", "note": "Inspire11 is the one to watch"}
  ]
}
```

| Preference | What it does |
|---|---|
| `excluded_companies` | Signals from these companies are filtered after analysis |
| `excluded_domains` | URLs containing these domains are filtered out |
| `boosted_queries` | Run first, prioritized in results |
| `deprecated_queries` | Skipped entirely (too noisy) |
| `scoring_overrides` | Override any scoring weight from config.py |
| `notes` | Timestamped review notes for history |

### Claude Code Workflow

You can also review and tune directly in Claude Code:

> "Let's review the last 3 scans. Exclude Accenture, boost the fractional AI query, and bump market_overlap weight to 0.40."

Claude Code will update `config/preferences.json` directly. Next scan picks up the changes automatically.

## Error Handling

The pipeline implements four resilience patterns:

| Pattern | What it does |
|---|---|
| **Retries** | External API calls (Tavily, Claude, Slack) retry 3x with exponential backoff (2s → 4s → 8s) |
| **Logging** | Every step logs with timestamps for Railway debugging |
| **Graceful Degradation** | If scoring fails, unscored signals are still saved and reported. Partial output > no output |
| **Alerting** | Pipeline crash → Slack error message. Step failures → warnings in report summary. No silent failures |

## Cost Per Run

Approximate cost for a full scan with 7 queries:

| Service | Usage | Cost |
|---|---|---|
| Tavily | 7 queries × advanced depth | ~$0.07 (free tier: 1,000/mo) |
| Claude (analysis) | ~35 signals × ~1K tokens each | ~$1.50–2.50 |
| Claude (exec summary) | 1 call × ~500 tokens | ~$0.01 |
| Slack | 1 file upload + 1 message | Free |
| **Total per run** | | **~$1.50–2.60** |
| **Monthly (4 runs)** | | **~$6–10** |

Railway hosting adds ~$5/mo for the hobby plan.

## Report Output

The generated Word doc includes:

- **Title page** with NWTN.AI wordmark, executive summary, and confidentiality notice
- **Running header** on content pages with wordmark + "CONFIDENTIAL — NWTN AI INTERNAL USE ONLY"
- **New Signals section** — company name, classification badge (color-coded), composite score, 5-dimension breakdown, positioning, reasoning, source URL
- **Updated Signals section** — lighter formatting for re-discovered entities
- **Scan Details** — metadata about the run
- **Footer** — "Generated by NWTN.AI Competitive Scanner"

Design: Calibri body, Lora serif wordmark, navy #1B365D accents, ember #C45D3E brand period.
