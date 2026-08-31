"""Load configured SQL queries, execute them, and normalize their results."""

from __future__ import annotations

import os
from typing import Any

from grafana_client import GrafanaClient, frames_to_rows


def load_query_sql(query: dict[str, Any], project_dir: str) -> str:
    """Read a query's SQL from the configured queries directory."""
    query_file = query.get("sql_file")
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
    """Run a Grafana query and expose both raw data and template-friendly rows."""
    model = dict(query.get("model", {}))
    # SQL-backed datasources load their statement from a file into rawSql.
    # Datasources that carry their whole query in the model (for example
    # Prometheus with an "expr") simply omit sql_file.
    if query.get("sql_file"):
        model["rawSql"] = load_query_sql(query, project_dir)
    response = client.query(query["datasource_uid"], model)
    return {
        "name": query["name"],
        "rows": frames_to_rows(response),
        "raw": response,
    }


def resolve_datasource(
    datasources: list[dict[str, Any]], name: str | None
) -> dict[str, Any]:
    """Return the datasource definition with the given name."""
    if not name:
        raise ValueError("Every query must specify a datasource name.")
    for datasource in datasources:
        if datasource.get("name") == name:
            return datasource
    raise ValueError(f"Unknown datasource: {name}")


def run_section(
    section: dict[str, Any],
    datasources: list[dict[str, Any]],
    client: GrafanaClient,
    project_dir: str,
) -> dict[str, Any]:
    """Run all queries in one section and expose them keyed by query name."""
    results: dict[str, dict[str, Any]] = {}
    for query_config in section.get("queries", []):
        name = query_config.get("name")
        if not name:
            raise ValueError("Every query must have a name.")
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
        }
        result = run_grafana_query(resolved, client, project_dir)
        # Optional layout hint for the template, for example "calendar".
        result["display"] = query_config.get("display", "rows")
        results[name] = result
    return {"title": section.get("title", ""), "results": results}


def run_sections(
    sections: list[dict[str, Any]],
    datasources: list[dict[str, Any]],
    client: GrafanaClient,
    project_dir: str,
) -> list[dict[str, Any]]:
    """Run every section and return them in report order."""
    return [
        run_section(section, datasources, client, project_dir)
        for section in sections
    ]
