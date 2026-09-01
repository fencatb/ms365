"""Microsoft Teams Adaptive Card building and webhook delivery."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import requests

MAX_PAYLOAD_BYTES = 28 * 1024


def build_card(text: str, title: str) -> dict[str, Any]:
    """Build a compact Adaptive Card containing the rendered report text."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.2",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": title,
                            "weight": "Bolder",
                            "size": "Medium",
                            "wrap": True,
                            "spacing": "None",
                        },
                        {
                            "type": "TextBlock",
                            "text": text,
                            "wrap": True,
                            "fontType": "Monospace",
                            "size": "Small",
                            "spacing": "None",
                        },
                    ],
                },
            }
        ],
    }


def payload_size(payload: dict[str, Any]) -> int:
    """Return the exact UTF-8 size of the JSON body sent to Teams."""
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def post_webhook(
    webhook_url: str,
    payload: dict[str, Any],
    session: requests.Session,
    timeout: int,
    retries: int,
    debug: bool = False,
) -> None:
    """Send the payload and retry throttling or transient server failures."""
    size = payload_size(payload)
    if size >= MAX_PAYLOAD_BYTES:
        raise RuntimeError(
            f"Teams payload is {size} bytes; it must be smaller than "
            f"{MAX_PAYLOAD_BYTES} bytes. Shorten the external template."
        )

    for attempt in range(retries + 1):
        if debug:
            print(
                f"[DEBUG] Teams POST {webhook_url} (attempt {attempt + 1}/{retries + 1}, "
                f"{size} bytes)",
                file=sys.stderr,
            )
        # The Grafana client sets "Authorization: Bearer" on the shared session.
        # Setting it to None here strips it for Teams, whose webhook URL already
        # carries its own SAS authentication in the query string. Sending both
        # makes Microsoft reject the request with 401
        # (DirectApiRequestHasMoreThanOneAuthorization).
        response = session.post(
            webhook_url,
            json=payload,
            headers={
                "Authorization": None,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
        if 200 <= response.status_code < 300:
            print(f"Teams webhook OK: HTTP {response.status_code} ({size} bytes)")
            return
        if response.status_code == 429 or 500 <= response.status_code < 600:
            if attempt < retries:
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after is not None else None
                except (TypeError, ValueError):
                    delay = None
                time.sleep(delay if delay is not None else min(2 ** attempt, 16))
                continue
        error_body = response.text[:1000]
        print(f"[ERROR] Teams webhook returned HTTP {response.status_code}", file=sys.stderr)
        print(f"[ERROR] response body: {error_body}", file=sys.stderr)
        raise RuntimeError(
            f"Teams webhook failed: HTTP {response.status_code}: {error_body}"
        )
