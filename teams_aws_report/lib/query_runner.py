"""Load configured SQL queries, execute them, and normalize their results."""

from __future__ import annotations

import os
from typing import Any

from lib.config import resolve_optional
from lib.grafana_client import GrafanaClient, frames_to_rows


def load_query_sql(
    query: dict[str, Any], project_dir: str, sql_file: str | None = None
) -> str:
    """Read a query's SQL from the configured queries directory."""
    query_file = sql_file or query.get("sql_file")
    if not query_file:
        raise ValueError(f"Query {query.get('name', '<unnamed>')} has no sql_file.")

    sql_path = os.path.abspath(os.path.join(project_dir, query_file))
    queries_dir = os.path.abspath(os.path.join(project_dir, "queries"))
    if not sql_path.startswith(queries_dir + os.sep):
        raise ValueError(f"SQL file must be inside the queries directory: {query_file}")

    try:
        with open(sql_path, encoding="utf-8") as sql_file:
            sql = sql_file.read().strip()
    except FileNotFoundError as exc:
        raise ValueError(f"SQL file not found: {query_file}") from exc

    if not sql:
        raise ValueError(f"SQL file is empty: {query_file}")
    return sql


def run_grafana_query(
    query: dict[str, Any], client: GrafanaClient, project_dir: str
) -> dict[str, Any]:
    """Run a Grafana query and expose both raw data and template-friendly rows.

    A SQL-backed query may use ``sql_file`` (single) or ``sql_files`` (a list,
    for example one file per billing period); when several files are given,
    their rows are concatenated into one result so the report renders them as
    a single query with several rows.
    """
    model = dict(query.get("model", {}))
    # SQL-backed datasources load their statements from files into rawSql.
    # Datasources that carry their whole query in the model (for example
    # Prometheus with an "expr") simply omit sql_file.
    sql_files = query.get("sql_files") or (
        [query["sql_file"]] if query.get("sql_file") else []
    )
    # Model-only datasources (for example Prometheus "expr" or an inline model
    # query) carry no SQL file; run the model once as-is.
    if not sql_files:
        sql_files = [None]
    rows: list[dict[str, Any]] = []
    last_response: dict[str, Any] | None = None
    for sql_file in sql_files:
        if sql_file:
            model["rawSql"] = load_query_sql(query, project_dir, sql_file)
        response = client.query(query["datasource_uid"], model)
        rows.extend(frames_to_rows(response))
        last_response = response
    return {
        "name": query["name"],
        "rows": rows,
        "raw": last_response,
    }


def resolve_datasource(
    datasources: list[dict[str, Any]], name: str | None
) -> dict[str, Any]:
    """Return the datasource definition with the given name.

    The datasource ``uid`` is resolved from the environment variable
    ``DATASOURCE_<NAME>_UID`` first (for example ``DATASOURCE_ATHENA_UID``),
    falling back to the ``uid`` in the config. This keeps per-environment
    UIDs out of the config file.
    """
    if not name:
        raise ValueError("Every query must specify a datasource name.")
    for datasource in datasources:
        if datasource.get("name") == name:
            resolved = dict(datasource)
            env_key = f"DATASOURCE_{name.upper().replace('-', '_')}_UID"
            resolved["uid"] = resolve_optional(resolved, "uid", env_key, None)
            if not resolved["uid"]:
                raise RuntimeError(
                    f"Datasource '{name}' has no uid. Set the {env_key} "
                    f"environment variable or provide uid in the datasources "
                    f"config."
                )
            return resolved
    raise ValueError(f"Unknown datasource: {name}")


def run_section(
    section: dict[str, Any],
    datasources: list[dict[str, Any]],
    client: GrafanaClient,
    project_dir: str,
    aws_session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run all queries in one section and expose them keyed by query name."""
    results: dict[str, dict[str, Any]] = {}
    for query_config in section.get("queries", []):
        # "enabled": false acts like commenting the query out.
        if query_config.get("enabled", True) is False:
            continue
        name = query_config.get("name")
        if not name:
            raise ValueError("Every query must have a name.")
        query_type = query_config.get("type", "grafana")
        if query_type == "ec2":
            from lib.aws_ec2 import run_ec2_query

            result = run_ec2_query(query_config, aws_session)
        else:
            datasource = resolve_datasource(datasources, query_config.get("datasource"))
            # A query's own model replaces the datasource's default model, so a
            # query can use a completely different shape than the datasource.
            model = dict(
                query_config["model"]
                if "model" in query_config
                else datasource.get("model") or {}
            )
            resolved = {
                "name": name,
                "datasource_uid": datasource["uid"],
                "model": model,
                "sql_file": query_config.get("sql_file"),
                "sql_files": query_config.get("sql_files"),
            }
            result = run_grafana_query(resolved, client, project_dir)
        # Optional layout hint for the template, for example "calendar" or "ec2".
        result["display"] = query_config.get(
            "display", "ec2" if query_type == "ec2" else "rows"
        )
        results[name] = result
    return {"title": section.get("title", ""), "results": results}


def run_sections(
    sections: list[dict[str, Any]],
    datasources: list[dict[str, Any]],
    client: GrafanaClient,
    project_dir: str,
    aws_session: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run every section and return them in report order."""
    return [
        run_section(section, datasources, client, project_dir, aws_session)
        for section in sections
        # "enabled": false acts like commenting the section out.
        if section.get("enabled", True) is not False
    ]
