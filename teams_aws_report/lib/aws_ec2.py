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
    ec2: Any,
    budget_tag: str,
    untagged_label: str = "none",
    include_values: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return all instances, grouped key set to the budget tag value.

    Instances without the budget tag are not dropped; their group key is set
    to ``untagged_label`` (default "none") so they can be reported and
    tagged. Each instance also carries its ``Name`` tag.

    When ``include_values`` is given, only instances whose budget tag value is
    in that list are fetched, using the EC2 API's server-side ``Filters``
    parameter (via the AWS SDK). Instances without the tag are then not
    returned at all.
    """
    instances: list[dict[str, Any]] = []
    filters: list[dict[str, Any]] = []
    if include_values:
        filters.append(
            {
                "Name": f"tag:{budget_tag}",
                "Values": [str(value) for value in include_values],
            }
        )
    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate(**({"Filters": filters} if filters else {})):
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


def filter_instances(
    instances: list[dict[str, Any]],
    exclude_values: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Drop instances whose budget tag value is in ``exclude_values``.

    The EC2 API cannot exclude tag values server-side (it only supports
    equality filters), so exclusions are applied here after fetching. This
    only affects the detailed per-tag listings; statistics always cover every
    fetched instance.
    """
    if not exclude_values:
        return instances
    excluded = {str(value) for value in exclude_values}
    return [inst for inst in instances if inst["budgetcode"] not in excluded]


def _group_by_code(
    instances: list[dict[str, Any]], untagged_label: str
) -> list[dict[str, Any]]:
    """Group instances by budget code; the untagged group sorts last."""
    by_code: dict[str, list[dict[str, Any]]] = {}
    for instance in instances:
        by_code.setdefault(instance["budgetcode"], []).append(instance)
    return sorted(
        ({"code": code, "instances": insts} for code, insts in by_code.items()),
        # The untagged group is sorted last so it stays easy to spot.
        key=lambda group: (group["code"] == untagged_label, group["code"]),
    )


def build_report(
    ec2: Any,
    budget_tag: str,
    untagged_label: str = "none",
    include_values: list[str] | None = None,
    exclude_values: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch the EC2 inventory and compute per-budget-code statistics.

    The top-level statistics (``total``/``running``/``stats_by_code``) always
    cover every fetched instance, so every budget tag is shown. Only the
    detailed per-tag listings (``by_code``) drop instances whose budget tag
    value is in ``exclude_values``.
    """
    instances = fetch_instances(ec2, budget_tag, untagged_label, include_values)
    detail_instances = filter_instances(instances, exclude_values)

    all_groups = _group_by_code(instances, untagged_label)
    detail_groups = _group_by_code(detail_instances, untagged_label)

    stats_by_code = [
        {
            "code": group["code"],
            "total": len(group["instances"]),
            "running": sum(
                1 for inst in group["instances"] if inst["state"] == "running"
            ),
        }
        for group in all_groups
    ]
    return {
        "total": len(instances),
        "running": sum(1 for inst in instances if inst["state"] == "running"),
        "by_code": detail_groups,
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
    # Server-side tag filter (AWS SDK): only fetch these budget tag values.
    include_values = query_config.get("include_tag_values")
    # Client-side exclusion: never show these budget tag values.
    exclude_values = query_config.get("exclude_tag_values")
    # The instance fields to display, in order, editable from config.
    fields = query_config.get("fields") or ["name", "instance_id", "instance_type", "state"]
    ec2 = build_ec2_client(
        aws_session["access_key"],
        aws_session["secret_key"],
        aws_session.get("region"),
    )
    report = build_report(
        ec2, budget_tag, untagged_label, include_values, exclude_values
    )

    filtered_by_code = []
    rows: list[dict[str, Any]] = []
    for group in report["by_code"]:
        instances = [
            {field: (inst.get(field) if inst.get(field) is not None else "-")
             for field in fields}
            for inst in group["instances"]
        ]
        filtered_by_code.append({"code": group["code"], "instances": instances})
        rows.extend(instances)

    return {
        "name": query_config["name"],
        "rows": rows,
        "by_code": filtered_by_code,
        "fields": fields,
        "stats": {
            "total": report["total"],
            "running": report["running"],
            "by_code": report["stats_by_code"],
        },
    }
