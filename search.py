"""
Tavily web search — takes a list of queries, returns raw results.

This module's ONLY job is talking to the Tavily search API. It knows nothing
about Claude, scoring, databases, or Slack. If we switch search providers,
only this file changes.
"""

import time
import logging

from tavily import TavilyClient

import config
from models import RawSearchResult

logger = logging.getLogger(__name__)


def search_web(queries: list[str]) -> list[RawSearchResult]:
    """Search the web for competitive signals using Tavily.

    Runs each query independently with a 2-second delay between calls.
    If a query fails (network error, rate limit, bad response), it logs the
    error and continues to the next query — one failure doesn't kill the scan.

    Args:
        queries: List of search query strings from config.SEARCH_QUERIES.

    Returns:
        Flat list of RawSearchResult objects across ALL queries.
        Each result includes: query, title, url, content, score.
    """
    if not config.TAVILY_API_KEY:
        raise ValueError(
            "TAVILY_API_KEY not set. Add it to your .env file. "
            "Get a key at https://tavily.com"
        )

    client = TavilyClient(api_key=config.TAVILY_API_KEY)
    all_results: list[RawSearchResult] = []

    print(f"[SEARCH] Searching Tavily for {len(queries)} queries:")

    for i, query in enumerate(queries):
        print(f"  [{i + 1}/{len(queries)}] {query}")

        try:
            response = client.search(
                query=query,
                search_depth="advanced",
                max_results=5,
            )

            for result in response.get("results", []):
                all_results.append(
                    RawSearchResult(
                        query=query,
                        title=result.get("title", ""),
                        url=result.get("url", ""),
                        content=result.get("content", ""),
                        score=result.get("score", 0.0),
                    )
                )

            result_count = len(response.get("results", []))
            print(f"         → {result_count} results")

        except Exception as e:
            logger.error(f"Query failed: '{query}' — {e}")
            print(f"         → ERROR: {e} (skipping)")

        # Rate limiting: wait 2 seconds between queries to avoid hitting
        # Tavily's rate limits. Skip the delay after the last query.
        if i < len(queries) - 1:
            time.sleep(2)

    print(f"[SEARCH] Total: {len(all_results)} results from {len(queries)} queries")
    return all_results
