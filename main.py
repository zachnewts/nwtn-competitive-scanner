"""
Orchestrator — runs the full competitive scanning pipeline.

Handles CLI arguments, logging, retries, graceful degradation, and alerting.
Designed to run unattended at 3am on Railway and produce useful output even
when individual steps fail.

CLI modes:
    python main.py                  Full pipeline run
    python main.py --dry-run        Everything except Slack delivery
    python main.py --query "x"      Add a one-off query to the configured list
    python main.py --report-only    Skip search/analysis, report from existing DB
"""

import sys
import argparse
import logging
from datetime import datetime

from search import search_web
from analyze import analyze_results
from memory import init_db, check_duplicates, insert_signals, get_recent_signals
from scoring import score_and_classify
from report import generate_report
from deliver import deliver_to_slack
from retry import with_retries
from models import CompetitiveSignal, ScanReport
import preferences
import config

# ─── Logging Setup ───────────────────────────────────────────────────────────
# Timestamps on every log line so Railway logs are readable on Monday morning.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scanner")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="NWTN AI Competitive Landscape Scanner",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run full pipeline but skip Slack delivery",
    )
    parser.add_argument(
        "--query",
        type=str,
        action="append",
        default=[],
        help="Add a one-off search query (can be used multiple times)",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Skip search/analysis, generate report from existing DB signals",
    )
    return parser.parse_args()


def send_failure_alert(error_summary: str) -> None:
    """Send a Slack message when the entire pipeline fails.

    This is the ALERTING pattern — no silent failures. If the pipeline
    crashes, you should see it in Slack on Monday morning.
    """
    if not config.SLACK_BOT_TOKEN or not config.SLACK_CHANNEL_ID:
        logger.error(f"Pipeline failed and Slack not configured: {error_summary}")
        return

    try:
        from slack_sdk import WebClient
        client = WebClient(token=config.SLACK_BOT_TOKEN)
        client.chat_postMessage(
            channel=config.SLACK_CHANNEL_ID,
            text=(
                f"🚨 *NWTN Competitive Scanner failed*\n"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Error: {error_summary}"
            ),
        )
        logger.info("Failure alert sent to Slack")
    except Exception as e:
        logger.error(f"Could not send failure alert to Slack: {e}")


def _build_query_list(args: argparse.Namespace, prefs: dict) -> list[str]:
    """Build the final query list from config + CLI + preferences.

    Order of operations:
    1. Start with config.SEARCH_QUERIES
    2. Remove any deprecated queries (from feedback)
    3. Add boosted queries (from feedback) at the front
    4. Add CLI --query args
    """
    deprecated = set(prefs.get("deprecated_queries", []))
    queries = [q for q in config.SEARCH_QUERIES if q not in deprecated]

    if deprecated:
        logger.info(f"Skipped {len(deprecated)} deprecated queries")

    boosted = prefs.get("boosted_queries", [])
    if boosted:
        queries = boosted + queries
        logger.info(f"Added {len(boosted)} boosted queries at front")

    if args.query:
        queries.extend(args.query)

    return queries


def _apply_exclusions(
    signals: list[CompetitiveSignal],
    prefs: dict,
) -> list[CompetitiveSignal]:
    """Filter out signals matching excluded companies or domains.

    Applied after analysis, before scoring. This is where feedback
    directly removes noise from future scans.
    """
    excluded_companies = {c.lower() for c in prefs.get("excluded_companies", [])}
    excluded_domains = set(prefs.get("excluded_domains", []))

    before = len(signals)
    filtered = []
    for s in signals:
        if s.company_name.lower() in excluded_companies:
            logger.info(f"Excluded company: {s.company_name}")
            continue
        if any(domain in s.url for domain in excluded_domains):
            logger.info(f"Excluded domain: {s.url}")
            continue
        filtered.append(s)

    removed = before - len(filtered)
    if removed:
        print(f"  [PREFS] Filtered out {removed} signals (excluded companies/domains)")

    return filtered


def run_scan(args: argparse.Namespace) -> None:
    """Execute the full competitive scanning pipeline.

    Implements four error handling patterns:
    1. RETRIES: External APIs retry 3x with exponential backoff
    2. LOGGING: Every step logs what it's doing with timestamps
    3. GRACEFUL DEGRADATION: Partial failures produce partial output
    4. ALERTING: Pipeline failure → Slack alert, step failure → report warning
    """
    start_time = datetime.now()
    warnings: list[str] = []

    print("=" * 60)
    print("  NWTN Competitive Scanner")
    print(f"  {start_time.strftime('%B %d, %Y at %I:%M %p')}")
    if args.dry_run:
        print("  MODE: Dry run (no Slack delivery)")
    if args.report_only:
        print("  MODE: Report only (from existing DB)")
    if args.query:
        print(f"  EXTRA QUERIES: {args.query}")
    print("=" * 60)
    print()

    # ── Load preferences (feedback loop config) ─────────────────────────────
    prefs = preferences.load()

    # ── Initialize database (always needed) ───────────────────────────────
    logger.info("Initializing database")
    init_db()

    # ── Report-only mode: skip search/analysis, pull from DB ──────────────
    if args.report_only:
        return _run_report_only(args, warnings, prefs)

    # ── Build query list (config + boosted - deprecated + CLI) ────────────
    queries = _build_query_list(args, prefs)
    logger.info(f"Running with {len(queries)} queries")

    # ── Step 1: Search ────────────────────────────────────────────────────
    print("--- Step 1: Web Search ---")
    raw_results = []
    try:
        raw_results = with_retries(
            fn=lambda: search_web(queries),
            description="Tavily search",
        )
        logger.info(f"Search returned {len(raw_results)} results")
    except Exception as e:
        warnings.append(f"Search failed: {e}")
        logger.error(f"Search failed after retries: {e}")
        print(f"  ⚠ Search failed: {e}")
    print()

    if not raw_results:
        msg = "No search results — nothing to analyze"
        logger.warning(msg)
        warnings.append(msg)
        _generate_warnings_only_report(args, queries, warnings)
        return

    # ── Step 2: Analysis ──────────────────────────────────────────────────
    print("--- Step 2: Claude Analysis ---")
    signals = []
    try:
        signals = analyze_results(raw_results)
        logger.info(f"Analysis produced {len(signals)} signals")
    except Exception as e:
        warnings.append(f"Analysis failed: {e}")
        logger.error(f"Analysis failed: {e}")
        print(f"  ⚠ Analysis failed: {e}")
    print()

    if not signals:
        msg = "No signals extracted — analysis returned empty"
        logger.warning(msg)
        warnings.append(msg)
        _generate_warnings_only_report(args, queries, warnings)
        return

    # ── Apply preference exclusions (feedback loop) ──────────────────────
    signals = _apply_exclusions(signals, prefs)

    if not signals:
        msg = "All signals filtered out by preferences"
        logger.warning(msg)
        warnings.append(msg)
        _generate_warnings_only_report(args, queries, warnings)
        return

    # ── Step 3: Deduplication ─────────────────────────────────────────────
    print("--- Step 3: Deduplication ---")
    new_signals = []
    updated_signals = []
    try:
        new_signals, updated_signals = check_duplicates(signals)
        logger.info(f"Dedup: {len(new_signals)} new, {len(updated_signals)} updated")
    except Exception as e:
        warnings.append(f"Deduplication failed, treating all as new: {e}")
        logger.error(f"Dedup failed: {e}")
        new_signals = signals
    print()

    if not new_signals and not updated_signals:
        logger.info("No new or updated signals found")
        print("  No new signals. Exiting.")
        return

    # ── Step 4: Scoring ───────────────────────────────────────────────────
    print("--- Step 4: Score & Classify ---")
    all_signals = new_signals + updated_signals
    scoring_overrides = prefs.get("scoring_overrides", {})
    try:
        all_signals = score_and_classify(all_signals, weight_overrides=scoring_overrides)
        logger.info(f"Scored {len(all_signals)} signals")
    except Exception as e:
        warnings.append(f"Scoring failed — signals included unscored: {e}")
        logger.error(f"Scoring failed: {e}")
        print(f"  ⚠ Scoring failed: {e}")
    print()

    # ── Step 5: Store ─────────────────────────────────────────────────────
    print("--- Step 5: Store Signals ---")
    try:
        stored = insert_signals(new_signals)
        logger.info(f"Stored {stored} new signals")
        print(f"  Inserted {stored} new signals")
    except Exception as e:
        warnings.append(f"Database storage failed: {e}")
        logger.error(f"Storage failed: {e}")
        print(f"  ⚠ Storage failed: {e}")
    print()

    # ── Step 6: Report ────────────────────────────────────────────────────
    print("--- Step 6: Generate Report ---")
    scan_report = None
    try:
        scan_report = generate_report(
            signals=all_signals,
            new_count=len(new_signals),
            updated_count=len(updated_signals),
            queries_run=len(queries),
            total_raw_results=len(raw_results),
        )
        logger.info(f"Report generated: {scan_report.file_path}")
    except Exception as e:
        warnings.append(f"Report generation failed: {e}")
        logger.error(f"Report generation failed: {e}")
        print(f"  ⚠ Report generation failed: {e}")
    print()

    if not scan_report:
        logger.error("No report generated — cannot deliver")
        return

    # ── Step 7: Deliver ───────────────────────────────────────────────────
    if args.dry_run:
        print("--- Step 7: Delivery (SKIPPED — dry run) ---")
        print(f"  Report saved locally: {scan_report.file_path}")
        logger.info("Dry run — skipping Slack delivery")
    else:
        print("--- Step 7: Deliver to Slack ---")
        try:
            success = with_retries(
                fn=lambda: deliver_to_slack(scan_report),
                description="Slack delivery",
            )
            if not success:
                warnings.append("Slack delivery returned False — report saved locally")
        except Exception as e:
            warnings.append(f"Slack delivery failed: {e}")
            logger.error(f"Delivery failed after retries: {e}")
            print(f"  ⚠ Delivery failed: {e}")
            print(f"  → Report saved locally: {scan_report.file_path}")
    print()

    # ── Summary ───────────────────────────────────────────────────────────
    elapsed = (datetime.now() - start_time).total_seconds()
    print("=" * 60)
    print(f"  Scan complete in {elapsed:.1f}s")
    print(f"  {scan_report.total_results_found} raw results searched")
    print(f"  {scan_report.new_signals} new signals")
    print(f"  {scan_report.updated_signals} updated signals")
    print(f"  Report: {scan_report.file_path}")
    if warnings:
        print(f"  ⚠ {len(warnings)} warning(s):")
        for w in warnings:
            print(f"    - {w}")
    print("=" * 60)

    logger.info(
        f"Scan complete: {scan_report.new_signals} new, "
        f"{scan_report.updated_signals} updated, "
        f"{len(warnings)} warnings, {elapsed:.1f}s"
    )


def _run_report_only(args: argparse.Namespace, warnings: list[str], prefs: dict) -> None:
    """Generate a report from existing database signals (no search/analysis)."""
    print("--- Report-Only Mode ---")
    logger.info("Report-only mode — pulling signals from database")

    signals = get_recent_signals(days=30)
    if not signals:
        print("  No signals in database from last 30 days.")
        logger.warning("No recent signals in DB for report-only mode")
        return

    # Apply exclusions and re-score (in case prefs or weights changed)
    signals = _apply_exclusions(signals, prefs)
    scoring_overrides = prefs.get("scoring_overrides", {})
    signals = score_and_classify(signals, weight_overrides=scoring_overrides)

    new_count = sum(1 for s in signals if s.is_new)
    updated_count = len(signals) - new_count

    scan_report = generate_report(
        signals=signals,
        new_count=new_count,
        updated_count=updated_count,
        queries_run=0,
        total_raw_results=0,
    )
    print()

    if not args.dry_run:
        print("--- Deliver to Slack ---")
        try:
            with_retries(
                fn=lambda: deliver_to_slack(scan_report),
                description="Slack delivery",
            )
        except Exception as e:
            warnings.append(f"Delivery failed: {e}")
            logger.error(f"Delivery failed: {e}")

    print(f"\n  Report: {scan_report.file_path}")
    print(f"  {len(signals)} signals from database")


def _generate_warnings_only_report(
    args: argparse.Namespace,
    queries: list[str],
    warnings: list[str],
) -> None:
    """Generate a minimal report when the pipeline failed early.

    This is GRACEFUL DEGRADATION — even if search or analysis fails,
    produce something that documents what happened.
    """
    logger.info("Generating warnings-only report")

    scan_report = ScanReport(
        queries_run=len(queries),
        total_results_found=0,
        new_signals=0,
        updated_signals=0,
        summary="Pipeline encountered errors. See warnings: " + "; ".join(warnings),
    )

    if not args.dry_run and config.SLACK_BOT_TOKEN and config.SLACK_CHANNEL_ID:
        try:
            from slack_sdk import WebClient
            client = WebClient(token=config.SLACK_BOT_TOKEN)
            client.chat_postMessage(
                channel=config.SLACK_CHANNEL_ID,
                text=(
                    f"⚠️ *Competitive scan completed with errors*\n"
                    + "\n".join(f"• {w}" for w in warnings)
                ),
            )
        except Exception as e:
            logger.error(f"Could not send warning alert: {e}")


# ─── Entry Point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = parse_args()
    try:
        run_scan(args)
    except Exception as e:
        logger.critical(f"Pipeline crashed: {e}", exc_info=True)
        send_failure_alert(str(e))
        sys.exit(1)
