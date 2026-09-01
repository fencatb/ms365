#!/usr/bin/env python3
"""Query Grafana, render an external report template, and post it to Teams.

This is the entry point only. Each reusable unit lives in the ``lib`` package
so the code stays readable as more features are added:

- ``lib.config``          config loading + environment resolution
- ``lib.template``        Jinja rendering + display filters (``calendar``)
- ``lib.teams``           Adaptive Card building + Teams webhook delivery
- ``lib.grafana_client``  Grafana HTTP API client
- ``lib.query_runner``    query execution layer
- ``lib.aws_ec2``         AWS EC2 inventory
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import traceback

import requests

from lib.config import (
    PROJECT_DIR,
    build_aws_session,
    load_config,
    resolve_debug,
    resolve_optional,
    resolve_secret,
)
from lib.grafana_client import GrafanaClient
from lib.query_runner import run_sections
from lib.template import render_template
from lib.teams import build_card, post_webhook

__version__ = "0.3"
DEFAULT_REPORT_TITLE = "Daily Ops Report"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_RETRIES = 4


def main() -> None:
    """Run the configured Grafana query and Teams publishing workflow."""
    config = load_config()
    debug = resolve_debug(config)
    print(f"teams_aws_report v{__version__}", file=sys.stderr)
    session = requests.Session()
    grafana_config = config["grafana"]
    teams_config = config["teams"]
    grafana_token = resolve_secret(grafana_config, "token", "token_env")
    grafana_base_url = resolve_optional(
        grafana_config, "base_url", "GRAFANA_BASE_URL", None
    )
    if not grafana_base_url:
        raise RuntimeError(
            "Missing Grafana base URL. Set the GRAFANA_BASE_URL environment "
            "variable (or grafana.base_url in config)."
        )
    client = GrafanaClient(
        grafana_base_url,
        grafana_token,
        session,
        int(grafana_config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
        debug,
    )
    aws_session = build_aws_session(config)
    sections = run_sections(
        config["sections"], config["datasources"], client, PROJECT_DIR, aws_session
    )
    title = resolve_optional(config, "title", "REPORT_TITLE", DEFAULT_REPORT_TITLE)
    template_path = os.path.join(PROJECT_DIR, config["template"])
    report = render_template(
        template_path,
        {
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "title": title,
            "sections": sections,
        },
    )
    payload = build_card(report, title)
    webhook_url = resolve_secret(teams_config, "webhook_url", "webhook_url_env")
    post_webhook(
        webhook_url,
        payload,
        session,
        int(teams_config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
        int(teams_config.get("retries", DEFAULT_RETRIES)),
        debug,
    )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, requests.RequestException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if resolve_debug():
            traceback.print_exc()
        raise SystemExit(1)
