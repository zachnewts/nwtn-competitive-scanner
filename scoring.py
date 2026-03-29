"""
Scores signals using 5 weighted criteria and assigns classifications.

Claude scores 4 dimensions during analysis (market, service, positioning,
credibility). This module adds the 5th dimension (recency), calculates the
weighted composite score, and assigns a classification based on thresholds
in config.py.

WHY SCORING IS SEPARATE FROM ANALYSIS:
    - Claude does the JUDGMENT (how much does this overlap with NWTN?)
    - This module does the MATH (weighted average + threshold classification)
    - You can re-tune weights and thresholds in config.py without re-running
      Claude — no extra API cost, instant results
"""

from datetime import datetime, timedelta

from models import CompetitiveSignal, Classification
import config


def score_and_classify(
    signals: list[CompetitiveSignal],
    weight_overrides: dict | None = None,
) -> list[CompetitiveSignal]:
    """Calculate composite scores, assign classifications, sort by score.

    For each signal:
    1. Calculate recency score from first_seen timestamp
    2. Compute weighted composite from all 5 dimensions
    3. Assign classification based on composite + thresholds
    4. Sort descending by composite score

    Args:
        signals: List of CompetitiveSignal objects with dimension scores
                 already set by Claude in analyze.py.
        weight_overrides: Optional dict from preferences.json that overrides
                          specific weights in config.SCORING_WEIGHTS.
                          Example: {"market_overlap": 0.40} bumps market weight.

    Returns:
        The same signals with overlap_score and classification now set,
        sorted highest composite first.
    """
    # Merge config weights with any overrides from preferences
    weights = dict(config.SCORING_WEIGHTS)
    if weight_overrides:
        weights.update(weight_overrides)
        print(f"[SCORING] Applied weight overrides: {weight_overrides}")

    print(f"[SCORING] Scoring {len(signals)} signals across 5 dimensions")
    print(f"  → Weights: {weights}")

    for signal in signals:
        recency = _calculate_recency(signal)
        signal.overlap_score = _weighted_composite(signal, recency, weights)
        signal.classification = _classify(signal)

    # Sort by composite score, highest first
    signals.sort(key=lambda s: s.overlap_score, reverse=True)

    # Summary counts
    counts = _count_classifications(signals)
    print(
        f"[SCORING] Results: {counts['direct_competitor']} direct, "
        f"{counts['adjacent_threat']} adjacent, "
        f"{counts['potential_partner']} partner, "
        f"{counts['irrelevant']} irrelevant"
    )
    return signals


def _calculate_recency(signal: CompetitiveSignal) -> float:
    """Score how recently this signal was discovered.

    Uses the signal's first_seen timestamp compared to now.

    Returns:
        1.0 if within last 7 days, 0.7 if within 30 days, 0.4 if older.
    """
    age = datetime.now() - signal.first_seen
    if age <= timedelta(days=7):
        return 1.0
    elif age <= timedelta(days=30):
        return 0.7
    else:
        return 0.4


def _weighted_composite(signal: CompetitiveSignal, recency: float, weights: dict) -> float:
    """Calculate the weighted composite score from all 5 dimensions.

    Args:
        signal: Signal with Claude-scored dimensions.
        recency: Recency score (0.4, 0.7, or 1.0).
        weights: Scoring weights dict (may include overrides from preferences).

    Returns:
        Weighted composite score between 0.0 and 1.0.
    """
    composite = (
        signal.market_overlap * weights["market_overlap"]
        + signal.service_overlap * weights["service_overlap"]
        + signal.positioning_overlap * weights["positioning_overlap"]
        + signal.credibility_score * weights["credibility_score"]
        + recency * weights["recency"]
    )
    return round(composite, 3)


def _classify(signal: CompetitiveSignal) -> Classification:
    """Assign a classification based on composite score and thresholds.

    Rules (from config.py thresholds):
        >= 0.7                          → direct_competitor
        >= 0.4                          → adjacent_threat
        >= 0.2 AND is_complementary     → potential_partner
        everything else                 → irrelevant

    Args:
        signal: Signal with overlap_score already computed.

    Returns:
        Classification enum value.
    """
    thresholds = config.CLASSIFICATION_THRESHOLDS
    score = signal.overlap_score

    if score >= thresholds["direct_competitor"]:
        return Classification.DIRECT_COMPETITOR
    elif score >= thresholds["adjacent_threat"]:
        return Classification.ADJACENT_THREAT
    elif score >= thresholds["potential_partner"] and signal.is_complementary:
        return Classification.POTENTIAL_PARTNER
    else:
        return Classification.IRRELEVANT


def _count_classifications(signals: list[CompetitiveSignal]) -> dict[str, int]:
    """Count how many signals fall into each classification."""
    counts = {c.value: 0 for c in Classification}
    for s in signals:
        counts[s.classification.value] += 1
    return counts
