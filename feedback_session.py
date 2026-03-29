"""
Interactive feedback session — review recent scans and tune preferences.

Workflow: every few weeks, open Claude Code, load this project, and say
"let's review the last 3 scans." This script loads recent signals from the
database, prints a summary, and lets you mark companies/domains to exclude,
queries to boost or deprecate, and adjust scoring weights.

Usage:
    python feedback_session.py              Review last 30 days
    python feedback_session.py --days 14    Review last 14 days
"""

import argparse
import json
from collections import Counter

from memory import init_db, get_recent_signals
from models import Classification
import preferences


def main():
    parser = argparse.ArgumentParser(description="Review recent scans and tune preferences")
    parser.add_argument("--days", type=int, default=30, help="Number of days to look back")
    args = parser.parse_args()

    init_db()
    prefs = preferences.load()
    signals = get_recent_signals(days=args.days)

    if not signals:
        print(f"No signals found in the last {args.days} days.")
        return

    _print_summary(signals, args.days)
    _print_by_classification(signals)
    _print_top_signals(signals)
    _print_current_preferences(prefs)
    _interactive_review(signals, prefs)


def _print_summary(signals, days):
    """Print a high-level summary of what the scanner found."""
    print("=" * 60)
    print(f"  Feedback Session — Last {days} Days")
    print("=" * 60)
    print()

    classifications = Counter(s.classification.value for s in signals)
    print(f"  Total signals: {len(signals)}")
    for cls, count in classifications.most_common():
        print(f"    {cls}: {count}")

    avg_score = sum(s.overlap_score for s in signals) / len(signals) if signals else 0
    print(f"  Average overlap score: {avg_score:.2f}")

    queries_used = Counter(s.source_query for s in signals)
    print(f"\n  Queries that produced signals:")
    for query, count in queries_used.most_common():
        print(f"    [{count}] {query}")
    print()


def _print_by_classification(signals):
    """Print signals grouped by classification."""
    groups = {}
    for s in signals:
        groups.setdefault(s.classification.value, []).append(s)

    for cls in ["direct_competitor", "adjacent_threat", "potential_partner", "irrelevant"]:
        group = groups.get(cls, [])
        if not group:
            continue
        print(f"--- {cls.upper().replace('_', ' ')} ({len(group)}) ---")
        for s in sorted(group, key=lambda x: x.overlap_score, reverse=True):
            print(f"  [{s.overlap_score:.0%}] {s.company_name}")
            print(f"        {s.description[:80]}...")
            print(f"        {s.url}")
        print()


def _print_top_signals(signals):
    """Print the top 5 highest-scoring signals."""
    top = sorted(signals, key=lambda s: s.overlap_score, reverse=True)[:5]
    print("--- TOP 5 SIGNALS ---")
    for i, s in enumerate(top, 1):
        print(f"  {i}. [{s.overlap_score:.0%}] {s.company_name} ({s.classification.value})")
        print(f"     mkt:{s.market_overlap} svc:{s.service_overlap} "
              f"pos:{s.positioning_overlap} cred:{s.credibility_score}")
    print()


def _print_current_preferences(prefs):
    """Print the current preferences configuration."""
    print("--- CURRENT PREFERENCES ---")
    if prefs.get("excluded_companies"):
        print(f"  Excluded companies: {prefs['excluded_companies']}")
    if prefs.get("excluded_domains"):
        print(f"  Excluded domains: {prefs['excluded_domains']}")
    if prefs.get("boosted_queries"):
        print(f"  Boosted queries: {prefs['boosted_queries']}")
    if prefs.get("deprecated_queries"):
        print(f"  Deprecated queries: {prefs['deprecated_queries']}")
    if prefs.get("scoring_overrides"):
        print(f"  Scoring overrides: {prefs['scoring_overrides']}")
    if not any(prefs.get(k) for k in ["excluded_companies", "excluded_domains",
                                       "boosted_queries", "deprecated_queries",
                                       "scoring_overrides"]):
        print("  (no customizations yet)")
    print()


def _interactive_review(signals, prefs):
    """Walk through interactive feedback prompts."""
    print("=" * 60)
    print("  Interactive Review")
    print("  (press Enter to skip any question)")
    print("=" * 60)
    print()

    # Exclude companies
    company_names = sorted(set(s.company_name for s in signals))
    print("Companies found this period:")
    for i, name in enumerate(company_names, 1):
        print(f"  {i}. {name}")
    exclude_input = input("\nCompanies to EXCLUDE from future scans (comma-separated names or numbers): ").strip()
    if exclude_input:
        for item in exclude_input.split(","):
            item = item.strip()
            if item.isdigit() and 1 <= int(item) <= len(company_names):
                name = company_names[int(item) - 1]
            else:
                name = item
            if name and name not in prefs.get("excluded_companies", []):
                prefs.setdefault("excluded_companies", []).append(name)
                print(f"  → Excluded: {name}")

    # Exclude domains
    domains = sorted(set(s.url.split("/")[2] for s in signals if "/" in s.url))
    print(f"\nDomains found:")
    for i, d in enumerate(domains, 1):
        print(f"  {i}. {d}")
    domain_input = input("\nDomains to EXCLUDE from future scans (comma-separated): ").strip()
    if domain_input:
        for item in domain_input.split(","):
            item = item.strip()
            if item.isdigit() and 1 <= int(item) <= len(domains):
                domain = domains[int(item) - 1]
            else:
                domain = item
            if domain and domain not in prefs.get("excluded_domains", []):
                prefs.setdefault("excluded_domains", []).append(domain)
                print(f"  → Excluded: {domain}")

    # Boost/deprecate queries
    queries_used = Counter(s.source_query for s in signals)
    print(f"\nQueries and their hit counts:")
    query_list = [q for q, _ in queries_used.most_common()]
    for i, q in enumerate(query_list, 1):
        print(f"  {i}. [{queries_used[q]}] {q}")

    boost_input = input("\nQueries to BOOST (run first, comma-separated numbers): ").strip()
    if boost_input:
        for num in boost_input.split(","):
            num = num.strip()
            if num.isdigit() and 1 <= int(num) <= len(query_list):
                q = query_list[int(num) - 1]
                if q not in prefs.get("boosted_queries", []):
                    prefs.setdefault("boosted_queries", []).append(q)
                    print(f"  → Boosted: {q}")

    deprecate_input = input("Queries to DEPRECATE (too noisy, comma-separated numbers): ").strip()
    if deprecate_input:
        for num in deprecate_input.split(","):
            num = num.strip()
            if num.isdigit() and 1 <= int(num) <= len(query_list):
                q = query_list[int(num) - 1]
                if q not in prefs.get("deprecated_queries", []):
                    prefs.setdefault("deprecated_queries", []).append(q)
                    print(f"  → Deprecated: {q}")

    # Scoring weight adjustments
    print(f"\nCurrent scoring weights:")
    print(f"  market_overlap: {prefs.get('scoring_overrides', {}).get('market_overlap', '(default)')}")
    print(f"  service_overlap: {prefs.get('scoring_overrides', {}).get('service_overlap', '(default)')}")
    print(f"  positioning_overlap: {prefs.get('scoring_overrides', {}).get('positioning_overlap', '(default)')}")
    print(f"  credibility_score: {prefs.get('scoring_overrides', {}).get('credibility_score', '(default)')}")
    print(f"  recency: {prefs.get('scoring_overrides', {}).get('recency', '(default)')}")
    weight_input = input("\nAdjust a weight? (e.g., 'market_overlap=0.40') or Enter to skip: ").strip()
    if weight_input and "=" in weight_input:
        key, val = weight_input.split("=", 1)
        key = key.strip()
        try:
            prefs.setdefault("scoring_overrides", {})[key] = float(val.strip())
            print(f"  → Set {key} = {float(val.strip())}")
        except ValueError:
            print(f"  → Invalid value: {val}")

    # Freeform notes
    note = input("\nAny notes from this review session? ").strip()
    if note:
        preferences.add_note(prefs, note)
        print("  → Note saved")

    # Save
    preferences.save(prefs)
    print(f"\n✓ Preferences saved to {preferences.PREFS_PATH}")
    print("  Changes will take effect on the next scan run.")


if __name__ == "__main__":
    main()
