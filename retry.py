"""
Retry utility with exponential backoff for external API calls.

Every external call (Tavily, Claude, Slack) should go through this.
On failure, it waits 2 seconds, then 4, then 8 before giving up.
"""

import time
import logging
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def with_retries(
    fn: Callable[[], T],
    description: str,
    max_attempts: int = 3,
    base_delay: float = 2.0,
) -> T:
    """Call fn() with exponential backoff retries.

    Args:
        fn: Zero-argument callable to retry.
        description: Human-readable name for logging (e.g., "Tavily search").
        max_attempts: Maximum number of attempts (default 3).
        base_delay: Initial delay in seconds (doubles each retry).

    Returns:
        The return value of fn() on success.

    Raises:
        The last exception if all attempts fail.
    """
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt < max_attempts:
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    f"{description} failed (attempt {attempt}/{max_attempts}): {e}. "
                    f"Retrying in {delay}s..."
                )
                time.sleep(delay)
            else:
                logger.error(
                    f"{description} failed after {max_attempts} attempts: {e}"
                )

    raise last_error
