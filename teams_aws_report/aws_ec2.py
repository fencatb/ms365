"""AWS EC2 inventory via boto3, grouped by a budget tag.

Credentials are resolved from the environment (see the ``aws`` section in
``config.json``) and passed in as an ``aws_session`` dict, so this module is
easy to test with a mocked boto3 client.
"""

from __future__ import annotations

from typing import Any

import boto3

DEFAULT_REGION = "us-east-1"


def build_ec2_client(access_key: str, secret_key: str, region: str | None) -> Any:
    """Create an EC2 boto3 client from resolved credentials."""
    return boto3.client(
        "ec2",
        region_name=region or DEFAULT_REGION,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def fetch_instances(
    ec2: Any, budget_tag: str, untagged_label: str = "none"
) -> list[dict[str, Any]]:
    """Return all instances, grouped key set to the budget tag value.

    Instances without the budget tag are not dropped; their group key is set
    to ``untagged_label`` (default "none") so they can be reported and
    tagged. Each instance also carries its ``Name`` tag.
    """
    instances: list[dict[str, Any]] = []
    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate():
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                tags = {
                    tag.get("Key"): tag.get("Value")
                    for tag in instance.get("Tags", [])
                }
                code = tags.get(budget_tag) or untagged_label
                instances.append(
                    {
                        "budgetcode": code,
                        "name": tags.get("Name"),
                        "instance_id": instance.get("InstanceId"),
                        "instance_type": instance.get("InstanceType"),
                        "state": (instance.get("State") or {}).get("Name"),
                    }
                )
    return instances


def build_report(
    ec2: Any, budget_tag: str, untagged_label: str = "none"
) -> dict[str, Any]:
    """Fetch the EC2 inventory and compute per-budget-code statistics."""
    instances = fetch_instances(ec2, budget_tag, untagged_label)

    by_code: dict[str, list[dict[str, Any]]] = {}
    for instance in instances:
        by_code.setdefault(instance["budgetcode"], []).append(instance)

    grouped = sorted(
        ({"code": code, "instances": insts} for code, insts in by_code.items()),
        # The untagged group is sorted last so it stays easy to spot.
        key=lambda group: (group["code"] == untagged_label, group["code"]),
    )
    stats_by_code = [
        {
            "code": group["code"],
            "total": len(group["instances"]),
            "running": sum(1 for inst in group["instances"] if inst["state"] == "running"),
        }
        for group in grouped
    ]
    return {
        "total": len(instances),
        "running": sum(1 for inst in instances if inst["state"] == "running"),
        "by_code": grouped,
        "stats_by_code": stats_by_code,
    }


def run_ec2_query(
    query_config: dict[str, Any],
    aws_session: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run an EC2 inventory query and return a template-friendly result."""
    if not aws_session:
        raise RuntimeError(
            "EC2 query requires an 'aws' section with credentials in config."
        )
    budget_tag = query_config.get("budget_tag") or aws_session.get(
        "budget_tag", "budgetcode"
    )
    untagged_label = query_config.get("untagged_label", "none")
    ec2 = build_ec2_client(
        aws_session["access_key"],
        aws_session["secret_key"],
        aws_session.get("region"),
    )
    report = build_report(ec2, budget_tag, untagged_label)
    rows = [
        {
            "budgetcode": inst["budgetcode"],
            "name": inst["name"],
            "instance_id": inst["instance_id"],
            "instance_type": inst["instance_type"],
            "state": inst["state"],
        }
        for group in report["by_code"]
        for inst in group["instances"]
    ]
    return {
        "name": query_config["name"],
        "rows": rows,
        "by_code": report["by_code"],
        "stats": {
            "total": report["total"],
            "running": report["running"],
            "by_code": report["stats_by_code"],
        },
    }
