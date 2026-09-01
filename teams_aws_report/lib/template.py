"""Jinja template rendering and the display filters used by report templates.

Report templates are plain Jinja2 files under ``templates/``. This module owns
the Jinja environment and registers the display filters (for example
``calendar``) so the templates stay declarative and need no Python changes.
"""

from __future__ import annotations

import calendar as _calendar
import datetime as dt
import os
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined


def calendar_grid(rows: list[dict[str, Any]]) -> str:
    """Render daily cost rows as a compact list of days that have data.

    Each row is expected to have a ``usage_date`` like ``2026-08-01`` and a
    ``value``. Days without data are skipped, and days are grouped three per
    line as ``DD<weekday> <value>`` (for example ``01Sat 1962.74``) so the
    block stays narrow and short while still showing which day each cost
    belongs to. The target month comes from the rows, falling back to the
    current month when there is no data.
    """
    by_date: dict[str, Any] = {}
    for row in rows:
        usage_date = row.get("usage_date")
        if usage_date:
            by_date[str(usage_date)] = row.get("value")

    if by_date:
        first = min(by_date)
        year, month = int(first[:4]), int(first[5:7])
    else:
        today = dt.date.today()
        year, month = today.year, today.month

    items: list[str] = []
    for key in sorted(by_date):
        if len(key) >= 10 and int(key[:4]) == year and int(key[5:7]) == month:
            day = int(key[8:10])
            weekday = _calendar.day_abbr[dt.date(year, month, day).weekday()]
            items.append(f"{day:02d}{weekday} {by_date[key]}")

    lines = [f"{_calendar.month_name[month]} {year}"]
    if not items:
        lines.append("No data.")
    for start in range(0, len(items), 3):
        lines.append("  ".join(items[start : start + 3]))
    return "\n".join(lines)


def render_template(template_path: str, context: dict[str, Any]) -> str:
    """Render a UTF-8 Jinja template from the templates directory."""
    template_dir, filename = os.path.split(template_path)
    environment = Environment(
        loader=FileSystemLoader(template_dir or "."),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    environment.filters["calendar"] = calendar_grid
    return environment.get_template(filename).render(**context).strip()
