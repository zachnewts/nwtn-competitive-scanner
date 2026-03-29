"""
Sends the Word doc report to a Slack channel.

The slack_sdk dependency is contained to this one file. If Slack delivery
fails, the report is already saved locally (report.py handles that) — this
module just logs the error and returns False so the orchestrator knows.
"""

import logging

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from models import ScanReport
import config

logger = logging.getLogger(__name__)


def deliver_to_slack(report: ScanReport) -> bool:
    """Upload the Word doc to Slack and post a summary message.

    Uses files_upload_v2 (the current Slack API for file uploads).
    If delivery fails for any reason, the report still exists locally
    at report.file_path — nothing is lost.

    Args:
        report: ScanReport with file_path, new_signals, updated_signals,
                and summary fields.

    Returns:
        True if delivery succeeded, False if it failed.
    """
    if not config.SLACK_BOT_TOKEN:
        print("[DELIVER] SLACK_BOT_TOKEN not configured — skipping delivery")
        print(f"  → Report saved locally: {report.file_path}")
        return False

    if not config.SLACK_CHANNEL_ID:
        print("[DELIVER] SLACK_CHANNEL_ID not configured — skipping delivery")
        print(f"  → Report saved locally: {report.file_path}")
        return False

    client = WebClient(token=config.SLACK_BOT_TOKEN)

    message = (
        f"📊 *New competitive scan ready* — "
        f"{report.new_signals} new signals, "
        f"{report.updated_signals} updated. "
        f"Open the attached report."
    )

    print(f"[DELIVER] Uploading to Slack channel: {config.SLACK_CHANNEL_ID}")
    print(f"  → File: {report.file_path}")

    try:
        client.files_upload_v2(
            channel=config.SLACK_CHANNEL_ID,
            file=report.file_path,
            title=f"NWTN AI Competitive Scan — {report.scan_date.strftime('%B %d, %Y')}",
            initial_comment=message,
        )
        print("[DELIVER] Sent to Slack successfully")
        return True

    except SlackApiError as e:
        logger.error(f"Slack API error: {e.response['error']}")
        print(f"[DELIVER] Slack API error: {e.response['error']}")
        print(f"  → Report saved locally: {report.file_path}")
        return False

    except Exception as e:
        logger.error(f"Slack delivery failed: {e}")
        print(f"[DELIVER] Delivery failed: {e}")
        print(f"  → Report saved locally: {report.file_path}")
        return False
