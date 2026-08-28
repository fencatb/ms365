"""Load configured SQL queries, execute them, and normalize their results."""

import os
from typing import Any, Callable

from grafana_client import GrafanaClient, frames_to_rows


QueryHandler = Callable[[dict[str, Any], GrafanaClient, str], dict[str, Any]]


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
    model["rawSql"] = load_query_sql(query, project_dir)
    response = client.query(query["datasource_uid"], model)
    return {
        "name": query["name"],
        "rows": frames_to_rows(response),
        "raw": response,
    }


QUERY_HANDLERS: dict[str, QueryHandler] = {
    "grafana": run_grafana_query,
}


def run_queries(
    query_configs: list[dict[str, Any]],
    client: GrafanaClient,
    project_dir: str,
) -> dict[str, dict[str, Any]]:
    """Dispatch each configured query through its selected provider."""
    results: dict[str, dict[str, Any]] = {}
    for query in query_configs:
        name = query.get("name")
        provider = query.get("provider", "grafana")
        if not name:
            raise ValueError("Every query must have a name.")
        if provider not in QUERY_HANDLERS:
            raise ValueError(f"Unsupported query provider: {provider}")
        results[name] = QUERY_HANDLERS[provider](query, client, project_dir)
    return results
