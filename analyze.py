"""
Claude API — takes raw search results, returns structured competitive signals.

This module's ONLY job is calling the Claude API to transform unstructured
search results into structured competitive intelligence. Claude does both
extraction (company name, positioning, etc.) AND scoring (overlap, classification)
in a single call per result.

HOW STRUCTURED OUTPUT WORKS:
    We use Claude's "tool use" feature to guarantee the response matches our
    schema. Here's the trick: we define a fake tool called "record_signal"
    whose input parameters match exactly the fields we want Claude to fill out.
    When Claude is forced to "call" this tool, it must provide valid JSON that
    matches the schema. We then take that JSON and build a CompetitiveSignal
    from it. Claude never actually calls a real tool — we're using the tool
    use mechanism purely as a structured output guarantee.
"""

import json
import time
import logging

import anthropic

import config
from models import CompetitiveSignal, RawSearchResult, Classification

logger = logging.getLogger(__name__)

# ─── Tool Definition ─────────────────────────────────────────────────────────
# This defines the "shape" of data we want Claude to return. It mirrors the
# fields on CompetitiveSignal that Claude needs to fill in. Fields like id,
# first_seen, last_seen, source_query, and is_new are NOT here because those
# are set by our code, not by Claude.
SIGNAL_TOOL = {
    "name": "record_signal",
    "description": (
        "Record a competitive signal extracted from a search result. "
        "Call this tool with your analysis of the search result."
    ),
    "input_schema": {
        "type": "object",
        "required": [
            "company_name",
            "description",
            "positioning",
            "target_market",
            "service_type",
            "market_overlap",
            "service_overlap",
            "positioning_overlap",
            "credibility_score",
            "is_complementary",
            "overlap_reasoning",
        ],
        "properties": {
            "company_name": {
                "type": "string",
                "description": (
                    "Name of the company or individual. If the page is a general "
                    "industry article with no specific company, use 'N/A — industry article'."
                ),
            },
            "description": {
                "type": "string",
                "description": "What this company/person does, in 1-2 sentences.",
            },
            "positioning": {
                "type": "string",
                "description": (
                    "How they describe themselves — their tagline, value prop, or "
                    "market positioning. Use 'N/A' if not a company."
                ),
            },
            "target_market": {
                "type": "string",
                "description": (
                    "Who they serve: enterprise, mid-market, SMB, specific verticals, etc."
                ),
            },
            "service_type": {
                "type": "string",
                "description": (
                    "What they offer: consulting, dev shop, SaaS, platform, fractional "
                    "leadership, etc."
                ),
            },
            "market_overlap": {
                "type": "number",
                "description": (
                    "0.0-1.0: How much their target market overlaps NWTN's. "
                    "mid-market CPG = 1.0, enterprise CPG = 0.5, "
                    "mid-market non-CPG = 0.3, unrelated = 0.0"
                ),
            },
            "service_overlap": {
                "type": "number",
                "description": (
                    "0.0-1.0: How much their service overlaps NWTN's. "
                    "vendor-neutral AI integration = 1.0, AI consulting general = 0.6, "
                    "dev shop = 0.4, SaaS product = 0.2, unrelated = 0.0"
                ),
            },
            "positioning_overlap": {
                "type": "number",
                "description": (
                    "0.0-1.0: How similar their positioning is to NWTN's. "
                    "operator credibility + AI = 1.0, technical-first = 0.4, "
                    "generic digital transformation = 0.2, unrelated = 0.0"
                ),
            },
            "credibility_score": {
                "type": "number",
                "description": (
                    "0.0-1.0: Strength of credibility signals. "
                    "named brand experience = 1.0, published case studies = 0.7, "
                    "claims without evidence = 0.3, no credibility signals = 0.0"
                ),
            },
            "is_complementary": {
                "type": "boolean",
                "description": (
                    "True if this entity's offering COMPLEMENTS rather than competes "
                    "with NWTN — e.g., a tool NWTN could recommend, or a partner "
                    "serving a different vertical with similar methodology."
                ),
            },
            "overlap_reasoning": {
                "type": "string",
                "description": (
                    "2-3 sentences explaining your dimension scores. Reference "
                    "specific similarities and differences to NWTN AI across "
                    "market, service, positioning, and credibility."
                ),
            },
        },
    },
}

# ─── System Prompt ────────────────────────────────────────────────────────────
SYSTEM_PROMPT = f"""You are a competitive intelligence analyst for NWTN AI.

NWTN AI's positioning:
{config.NWTN_POSITIONING}

Your job: analyze a search result and extract structured intelligence. For each
result, score overlap across four dimensions AND extract company details.

SCORING DIMENSIONS (score each 0.0 to 1.0):

1. MARKET OVERLAP — Do they serve the same customers?
   - 1.0: mid-market CPG brands ($5M-$200M) — identical to NWTN
   - 0.5: enterprise CPG (same vertical, different company size)
   - 0.3: mid-market non-CPG (same size, different vertical)
   - 0.0: unrelated market

2. SERVICE OVERLAP — Do they offer the same thing?
   - 1.0: vendor-neutral AI integration + fractional leadership — identical to NWTN
   - 0.6: AI consulting (general, not vendor-neutral)
   - 0.4: dev shop / custom AI development
   - 0.2: SaaS product (tool, not services)
   - 0.0: unrelated service

3. POSITIONING OVERLAP — Do they position themselves similarly?
   - 1.0: operator credibility (industry experience) + AI expertise — identical to NWTN
   - 0.4: technical-first positioning (engineering-led, no domain experience)
   - 0.2: generic "digital transformation" consulting
   - 0.0: unrelated positioning

4. CREDIBILITY — How strong are their proof points?
   - 1.0: named brand experience in CPG / F&B (worked at Nestlé, P&G, etc.)
   - 0.7: published case studies with measurable results
   - 0.3: claims expertise but no evidence (no case studies, no named clients)
   - 0.0: no credibility signals found

COMPLEMENTARY CHECK:
   Set is_complementary=true ONLY if their offering could work WITH NWTN rather
   than against it — e.g., a SaaS tool NWTN could recommend to clients, or a
   consultancy in an adjacent vertical that could refer business.

GENERAL GUIDELINES:
- If the page is a general industry article with no specific company, use
  company_name "N/A — industry article" and score all dimensions 0.0.
- Score dimensions independently. A company can have high service overlap
  but zero market overlap (e.g., same service, wrong vertical).
- Always explain your scores in overlap_reasoning.

You MUST call the record_signal tool with your analysis."""


def analyze_results(raw_results: list[RawSearchResult]) -> list[CompetitiveSignal]:
    """Analyze raw search results using Claude to extract structured signals.

    Sends each result to Claude one at a time. Claude reads the title, URL,
    and content, then uses the record_signal tool to return structured data
    matching the CompetitiveSignal schema.

    Args:
        raw_results: List of RawSearchResult objects from search.py.

    Returns:
        List of CompetitiveSignal objects — one per result that Claude
        successfully analyzed.
    """
    if not config.ANTHROPIC_API_KEY:
        raise ValueError(
            "ANTHROPIC_API_KEY not set. Add it to your .env file. "
            "Get a key at https://console.anthropic.com"
        )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    signals: list[CompetitiveSignal] = []

    print(f"[ANALYZE] Sending {len(raw_results)} results to Claude for analysis")

    for i, result in enumerate(raw_results):
        print(f"  [{i + 1}/{len(raw_results)}] {result.title[:60]}...")

        try:
            signal = _analyze_single_result(client, result)
            signals.append(signal)
            print(
                f"         → {signal.company_name} | "
                f"mkt:{signal.market_overlap} svc:{signal.service_overlap} "
                f"pos:{signal.positioning_overlap} cred:{signal.credibility_score}"
            )

        except Exception as e:
            logger.error(f"Analysis failed for '{result.title}' — {e}")
            print(f"         → ERROR: {e} (skipping)")

        # Rate limiting: 1-second delay between Claude calls
        if i < len(raw_results) - 1:
            time.sleep(1)

    print(f"[ANALYZE] Extracted {len(signals)} signals from {len(raw_results)} results")
    return signals


def _analyze_single_result(
    client: anthropic.Anthropic,
    result: RawSearchResult,
) -> CompetitiveSignal:
    """Send a single search result to Claude and get back a CompetitiveSignal.

    This is where the actual API call happens. We send the result as a user
    message and force Claude to respond by calling the record_signal tool.

    Args:
        client: Authenticated Anthropic API client.
        result: A single RawSearchResult to analyze.

    Returns:
        A CompetitiveSignal built from Claude's tool call response.

    Raises:
        ValueError: If Claude doesn't call the tool or returns invalid data.
    """
    # Build the user message with the search result to analyze
    user_message = (
        f"Analyze this search result:\n\n"
        f"Title: {result.title}\n"
        f"URL: {result.url}\n"
        f"Content: {result.content[:2000]}\n\n"
        f"Search query that found this: {result.query}"
    )

    # Call Claude with the tool definition — Claude MUST call record_signal
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[SIGNAL_TOOL],
        tool_choice={"type": "tool", "name": "record_signal"},
        messages=[{"role": "user", "content": user_message}],
    )

    # Extract the tool call from Claude's response
    tool_input = _extract_tool_input(response)

    # Build a CompetitiveSignal from Claude's output + our metadata.
    # Note: overlap_score and classification are NOT set here — they're
    # computed by scoring.py from the dimension scores + recency.
    return CompetitiveSignal(
        company_name=tool_input["company_name"],
        url=result.url,
        description=tool_input["description"],
        positioning=tool_input["positioning"],
        target_market=tool_input["target_market"],
        service_type=tool_input["service_type"],
        market_overlap=tool_input["market_overlap"],
        service_overlap=tool_input["service_overlap"],
        positioning_overlap=tool_input["positioning_overlap"],
        credibility_score=tool_input["credibility_score"],
        is_complementary=tool_input["is_complementary"],
        overlap_reasoning=tool_input["overlap_reasoning"],
        source_query=result.query,
    )


def _extract_tool_input(response: anthropic.types.Message) -> dict:
    """Extract the tool input dict from Claude's response.

    Claude's response contains content blocks. When tool_choice forces a
    specific tool, the response will contain a tool_use block with the
    structured data as its 'input' field.

    Args:
        response: The raw API response from Claude.

    Returns:
        Dictionary of tool input values matching our schema.

    Raises:
        ValueError: If no tool_use block is found in the response.
    """
    for block in response.content:
        if block.type == "tool_use":
            return block.input

    raise ValueError("Claude did not return a tool_use block")
