#!/usr/bin/env python3
"""Query Grafana, render an external report template, and post it to Teams."""

import datetime as dt
import json
import os
import sys
import time
from typing import Any

import requests
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from grafana_client import GrafanaClient
from query_runner import run_queries


PROJECT_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(PROJECT_DIR, "config.json")
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_RETRIES = 4
MAX_PAYLOAD_BYTES = 28 * 1024


def load_config(path: str = CONFIG_FILE) -> dict[str, Any]:
    """Load and validate the external JSON configuration file."""
    try:
        with open(path, encoding="utf-8") as config_file:
            config = json.load(config_file)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Configuration file not found: {path}. "
            "Copy config.example.json to config.json and fill in the values."
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in configuration file: {exc}") from exc

    if not isinstance(config, dict):
        raise RuntimeError("The configuration file must contain a JSON object.")
    for key in ("grafana", "teams", "queries", "template"):
        if key not in config:
            raise RuntimeError(f"Missing required configuration section: {key}")
    return config


def resolve_secret(config: dict[str, Any], key: str, env_key: str) -> str:
    """Resolve a secret from config or, preferably, an environment variable."""
    value = config.get(key)
    if value:
        return str(value)
    environment_key = config.get(env_key)
    if environment_key:
        value = os.getenv(environment_key)
        if value:
            return value
    raise RuntimeError(f"Missing secret configuration: {key} or {env_key}")


def render_template(template_path: str, context: dict[str, Any]) -> str:
    """Render a UTF-8 Jinja template from the templates directory."""
    template_dir, filename = os.path.split(template_path)
    environment = Environment(
        loader=FileSystemLoader(template_dir or "."),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    return environment.get_template(filename).render(**context).strip()


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
                            "wrap": True,
                        },
                        {"type": "TextBlock", "text": text, "wrap": True},
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
) -> None:
    """Send the payload and retry throttling or transient server failures."""
    size = payload_size(payload)
    if size >= MAX_PAYLOAD_BYTES:
        raise RuntimeError(
            f"Teams payload is {size} bytes; it must be smaller than "
            f"{MAX_PAYLOAD_BYTES} bytes. Shorten the external template."
        )

    for attempt in range(retries + 1):
        response = session.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
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
        raise RuntimeError(
            f"Teams webhook failed: HTTP {response.status_code}: {response.text[:1000]}"
        )


def main() -> None:
    """Run the configured Grafana query and Teams publishing workflow."""
    config = load_config()
    session = requests.Session()
    grafana_config = config["grafana"]
    teams_config = config["teams"]
    grafana_token = resolve_secret(grafana_config, "token", "token_env")
    client = GrafanaClient(
        grafana_config["base_url"],
        grafana_token,
        session,
        int(grafana_config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
    )
    results = run_queries(config["queries"], client, PROJECT_DIR)
    template_path = os.path.join(PROJECT_DIR, config["template"])
    report = render_template(
        template_path,
        {
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "results": results,
        },
    )
    payload = build_card(report, config.get("title", "AWS Cost Report"))
    webhook_url = resolve_secret(teams_config, "webhook_url", "webhook_url_env")
    post_webhook(
        webhook_url,
        payload,
        session,
        int(teams_config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
        int(teams_config.get("retries", DEFAULT_RETRIES)),
    )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
