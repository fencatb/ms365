"""Run configured report queries and normalize their results."""

from typing import Any, Callable

from grafana_client import GrafanaClient, frames_to_rows


QueryHandler = Callable[[dict[str, Any], GrafanaClient], dict[str, Any]]


def run_grafana_query(query: dict[str, Any], client: GrafanaClient) -> dict[str, Any]:
    """Run a Grafana query and expose both raw data and template-friendly rows."""
    response = client.query(query["datasource_uid"], query["model"])
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
        results[name] = QUERY_HANDLERS[provider](query, client)
    return results
