"""Configuration loading and environment resolution for the report tool.

Everything that varies per environment (Grafana URL, datasource UIDs, secrets,
budget tag, report title, ...) is read from environment variables, so the
config file only needs the static structure and the ``enabled`` switches.
"""

from __future__ import annotations

import json
import os
from typing import Any

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(PROJECT_DIR, "config.json")
EXAMPLE_CONFIG_FILE = os.path.join(PROJECT_DIR, "config.example.json")

_ON_VALUES = ("1", "true", "yes", "on")
_OFF_VALUES = ("0", "false", "no", "off")


def load_config(path: str = CONFIG_FILE) -> dict[str, Any]:
    """Load and validate the JSON configuration.

    ``config.json`` is preferred; when it is missing, ``config.example.json``
    is used as a read-only reference so running the tool needs no copy step.
    Sections and queries can be turned on/off per environment with
    ``REPORT_SECTION_<NAME>_ENABLED`` / ``REPORT_QUERY_<NAME>_ENABLED``.
    """
    source = path
    if not os.path.exists(source) and os.path.exists(EXAMPLE_CONFIG_FILE):
        source = EXAMPLE_CONFIG_FILE
    try:
        with open(source, encoding="utf-8") as config_file:
            config = json.load(config_file)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Configuration file not found: {source}. "
            "Copy config.example.json to config.json and fill in the values."
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in configuration file: {exc}") from exc

    if not isinstance(config, dict):
        raise RuntimeError("The configuration file must contain a JSON object.")
    for key in ("grafana", "teams", "datasources", "sections", "template"):
        if key not in config:
            raise RuntimeError(f"Missing required configuration section: {key}")
    return apply_env_switches(config)


def _env_bool(env_name: str) -> bool | None:
    """Return True/False for an on/off env var, or None when it is unset."""
    value = os.getenv(env_name, "").strip().lower()
    if value in _ON_VALUES:
        return True
    if value in _OFF_VALUES:
        return False
    return None


def _envify(name: str) -> str:
    """Upper-case a name and replace non-alphanumeric runs with underscores."""
    sanitized = "".join(ch if ch.isalnum() else "_" for ch in name)
    return sanitized.strip("_").upper()


def apply_env_switches(config: dict[str, Any]) -> dict[str, Any]:
    """Apply REPORT_SECTION_<NAME>_ENABLED / REPORT_QUERY_<NAME>_ENABLED.

    Environment variables override the ``enabled`` switch in the config, so
    sections and queries can be turned on/off per environment without editing
    a file. For example ``REPORT_SECTION_SERVICE_STATUS_ENABLED=0`` disables
    the "Service Status" section.
    """
    for section in config.get("sections", []):
        section_value = _env_bool(
            "REPORT_SECTION_" + _envify(section.get("title", "")) + "_ENABLED"
        )
        if section_value is not None:
            section["enabled"] = section_value
        for query in section.get("queries", []):
            query_value = _env_bool(
                "REPORT_QUERY_" + _envify(query.get("name", "")) + "_ENABLED"
            )
            if query_value is not None:
                query["enabled"] = query_value
    return config


def resolve_optional(
    config: dict[str, Any],
    key: str,
    env_name: str,
    default: Any = None,
) -> Any:
    """Resolve a value from the environment first, then config, then default."""
    env_value = os.getenv(env_name)
    if env_value:
        return env_value
    config_value = config.get(key)
    if config_value is not None:
        return config_value
    return default


def resolve_debug(config: dict[str, Any] | None = None) -> bool:
    """Resolve the debug flag from the environment or the config file."""
    env_value = os.getenv("TEAMS_AWS_DEBUG", "").strip().lower()
    if env_value in ("1", "true", "yes", "on"):
        return True
    if env_value in ("0", "false", "no", "off"):
        return False
    if config is not None:
        return bool(config.get("debug", False))
    try:
        return bool(load_config().get("debug", False))
    except RuntimeError:
        return False


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


def build_aws_session(config: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve AWS credentials from the environment for EC2 queries.

    Returns None when no ``aws`` section is configured, so the report works
    without AWS access. The ``aws`` section maps credential env vars like
    ``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` / ``AWS_REGION``. The
    budget tag comes from ``EC2_BUDGET_TAG`` (or ``aws.ec2_budget_tag``),
    defaulting to ``budgetcode``.
    """
    aws_config = config.get("aws")
    if not aws_config:
        return None
    return {
        "access_key": resolve_secret(aws_config, "access_key", "access_key_env"),
        "secret_key": resolve_secret(aws_config, "secret_key", "secret_key_env"),
        "region": resolve_optional(aws_config, "region", "AWS_REGION", None)
        or os.getenv(aws_config.get("region_env", "")),
        "budget_tag": resolve_optional(
            aws_config, "ec2_budget_tag", "EC2_BUDGET_TAG", "budgetcode"
        ),
    }
