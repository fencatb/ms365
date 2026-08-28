#!/usr/bin/env python3
"""
Send AWS/FinOps data and an optional image to a Microsoft Teams Workflow Webhook.

Features:
- Posts an Adaptive Card to a Teams Workflow webhook URL.
- Queries AWS Cost Explorer for:
    * current month
    * current week
    * yesterday
- Optionally downloads an image URL or embeds a local image as Base64.
- Retries HTTP 429/5xx responses.
- Can run without AWS (--no-aws) for webhook/card testing.

Requirements:
    pip install requests boto3

AWS credentials are read by boto3's normal credential chain:
environment variables, AWS profile, EC2/ECS role, etc.

Usage examples:
    python teams_report.py --webhook "https://..."
    python teams_report.py --webhook "https://..." --no-aws
    python teams_report.py --webhook "https://..." --image-url "https://example.com/chart.png"
    python teams_report.py --webhook "https://..." --image-file ./cost.png

Recommended:
    Put the webhook URL in an environment variable instead of the command line:
        set TEAMS_WEBHOOK_URL=https://...
        python teams_report.py
"""

import argparse
import base64
import datetime as dt
import json
import mimetypes
import os
import sys
import time
from typing import Any, Optional

import requests

try:
    import boto3
except ImportError:
    boto3 = None


DEFAULT_REGION = os.getenv("AWS_REGION", "us-east-1")
DEFAULT_CURRENCY = "USD"


def money(value: float, currency: str = DEFAULT_CURRENCY) -> str:
    """Format a cost consistently for display in the Adaptive Card."""
    return f"${value:,.2f} {currency}"


def aws_cost(start: dt.date, end: dt.date, region: str) -> float:
    """Cost Explorer uses an exclusive end date."""
    if boto3 is None:
        raise RuntimeError("boto3 is not installed. Run: pip install boto3")

    ce = boto3.client("ce", region_name=region)

    response = ce.get_cost_and_usage(
        TimePeriod={
            "Start": start.isoformat(),
            "End": end.isoformat(),
        },
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
    )

    total = 0.0
    for result in response.get("ResultsByTime", []):
        amount = result.get("Total", {}).get("UnblendedCost", {}).get("Amount", "0")
        total += float(amount)

    return total


def collect_aws_costs(region: str) -> dict[str, Any]:
    """Collect the three reporting periods used by the Teams card.

    Cost Explorer treats the end date as exclusive, so each range below uses
    tomorrow as its end date when today's costs should be included.
    """
    today = dt.date.today()

    # Current calendar month.
    month_start = today.replace(day=1)

    # Monday -> today.
    week_start = today - dt.timedelta(days=today.weekday())

    # Yesterday.
    yesterday = today - dt.timedelta(days=1)

    return {
        "month": aws_cost(month_start, today + dt.timedelta(days=1), region),
        "week": aws_cost(week_start, today + dt.timedelta(days=1), region),
        "yesterday": aws_cost(yesterday, today, region),
        "currency": DEFAULT_CURRENCY,
        "region": region,
    }


def image_as_data_uri(path: str) -> str:
    """Convert a local image to a Base64 data URI for Adaptive Cards."""
    mime, _ = mimetypes.guess_type(path)
    if not mime or not mime.startswith("image/"):
        mime = "image/png"

    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")

    return f"data:{mime};base64,{encoded}"


def build_card(
    costs: dict[str, Any],
    image_url: Optional[str] = None,
    image_file: Optional[str] = None,
    title: str = "AWS Cost Report",
) -> dict[str, Any]:
    """Build the Teams Workflow payload without sending it."""
    now = dt.datetime.now().astimezone()
    currency = costs.get("currency", DEFAULT_CURRENCY)

    body = [
        {
            "type": "TextBlock",
            "text": title,
            "weight": "Bolder",
            "size": "Large",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": f"Updated: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}",
            "isSubtle": True,
            "spacing": "None",
            "wrap": True,
        },
        {
            "type": "FactSet",
            "facts": [
                {
                    "title": "This month",
                    "value": money(costs["month"], currency),
                },
                {
                    "title": "This week",
                    "value": money(costs["week"], currency),
                },
                {
                    "title": "Yesterday",
                    "value": money(costs["yesterday"], currency),
                },
            ],
        },
    ]

    if costs.get("region"):
        body.append(
            {
                "type": "TextBlock",
                "text": f"AWS Region: {costs['region']}",
                "isSubtle": True,
                "spacing": "Small",
                "wrap": True,
            }
        )

    if image_file:
        uri = image_as_data_uri(image_file)
        body.append(
            {
                "type": "Image",
                "url": uri,
                "size": "Large",
                "altText": "AWS cost chart",
            }
        )
    elif image_url:
        body.append(
            {
                "type": "Image",
                "url": image_url,
                "size": "Large",
                "altText": "AWS cost chart",
            }
        )

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
                    "body": body,
                },
            }
        ],
    }


def post_webhook(
    webhook_url: str, payload: dict[str, Any], retries: int = 4
) -> None:
    """Send a payload and retry only throttling or transient server errors."""
    session = requests.Session()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    for attempt in range(retries + 1):
        response = session.post(
            webhook_url,
            json=payload,
            headers=headers,
            timeout=30,
        )

        if 200 <= response.status_code < 300:
            print(f"Teams webhook OK: HTTP {response.status_code}")
            if response.text:
                print(response.text[:500])
            return

        # Teams/Power Automate can throttle. Retry 429 and transient 5xx.
        if response.status_code == 429 or 500 <= response.status_code < 600:
            if attempt < retries:
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after is not None else None
                except (TypeError, ValueError):
                    delay = None
                if delay is None:
                    delay = min(2 ** attempt, 16)

                print(
                    f"HTTP {response.status_code}; retrying in {delay:.1f}s...",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue

        raise RuntimeError(
            f"Teams webhook failed: HTTP {response.status_code}: "
            f"{response.text[:1000]}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Send AWS/FinOps data to Teams via a Workflow webhook."
    )
    parser.add_argument(
        "--webhook",
        default=os.getenv("TEAMS_WEBHOOK_URL"),
        help="Teams Workflow webhook URL. Can also use TEAMS_WEBHOOK_URL.",
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help="AWS region used for Cost Explorer client (default: us-east-1).",
    )
    parser.add_argument(
        "--title",
        default="AWS Cost Report",
        help="Adaptive Card title.",
    )
    parser.add_argument(
        "--image-url",
        help="HTTPS image URL. Prefer this for larger images.",
    )
    parser.add_argument(
        "--image-file",
        help="Local image file. It will be embedded as Base64.",
    )
    parser.add_argument(
        "--no-aws",
        action="store_true",
        help="Skip AWS Cost Explorer and send sample values. Useful for testing.",
    )
    parser.add_argument(
        "--month",
        type=float,
        help="Override this-month cost.",
    )
    parser.add_argument(
        "--week",
        type=float,
        help="Override this-week cost.",
    )
    parser.add_argument(
        "--yesterday",
        type=float,
        help="Override yesterday cost.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print JSON payload without sending it.",
    )

    args = parser.parse_args()

    if not args.webhook:
        parser.error(
            "Webhook URL is required. Use --webhook or set TEAMS_WEBHOOK_URL."
        )

    if args.image_url and args.image_file:
        parser.error("Use only one of --image-url and --image-file.")

    if args.no_aws:
        costs = {
            "month": 1284.32,
            "week": 312.45,
            "yesterday": 48.21,
            "currency": DEFAULT_CURRENCY,
            "region": args.region,
        }
    else:
        print(f"Querying AWS Cost Explorer in {args.region}...")
        costs = collect_aws_costs(args.region)

    # Allow individual values to be overridden.
    if args.month is not None:
        costs["month"] = args.month
    if args.week is not None:
        costs["week"] = args.week
    if args.yesterday is not None:
        costs["yesterday"] = args.yesterday

    payload = build_card(
        costs,
        image_url=args.image_url,
        image_file=args.image_file,
        title=args.title,
    )

    if args.dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    post_webhook(args.webhook, payload)


if __name__ == "__main__":
    main()
