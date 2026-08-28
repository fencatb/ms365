"""Small Grafana HTTP API client for service-to-service authentication."""

from typing import Any

import requests


class GrafanaClient:
    """Execute datasource queries through Grafana's HTTP API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        session: requests.Session,
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session
        self.timeout = timeout
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
        )

    def query(
        self,
        datasource_uid: str,
        model: dict[str, Any],
    ) -> dict[str, Any]:
        """Run one Grafana datasource query and return its raw result."""
        query_model = dict(model)
        query_model.setdefault("refId", "A")
        payload = {
            "queries": [
                {
                    "refId": query_model["refId"],
                    "datasource": {"uid": datasource_uid},
                    "model": query_model,
                }
            ],
            "from": "now-24h",
            "to": "now",
        }
        response = self.session.post(
            f"{self.base_url}/api/ds/query",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()


def frames_to_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert Grafana data frames into simple row dictionaries for templates."""
    rows: list[dict[str, Any]] = []
    for result in response.get("results", {}).values():
        for frame in result.get("frames", []):
            fields = frame.get("schema", {}).get("fields", [])
            values = frame.get("data", {}).get("values", [])
            if not fields or not values:
                continue

            field_names = [field.get("name", f"field_{index}") for index, field in enumerate(fields)]
            row_count = max((len(column) for column in values), default=0)
            for row_index in range(row_count):
                rows.append(
                    {
                        field_names[column_index]: (
                            values[column_index][row_index]
                            if row_index < len(values[column_index])
                            else None
                        )
                        for column_index in range(min(len(field_names), len(values)))
                    }
                )
    return rows
