"""Tiny Markdown table renderer (avoids an extra dependency for reports)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float | Decimal):
        f = float(value)
        if f != f:  # NaN
            return "—"
        return f"{f:,.{digits}f}" if abs(f) < 1000 else f"{f:,.0f}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).replace("|", "\\|")


def md_table(columns: Sequence[str], rows: Sequence[Sequence[Any]], digits: int = 4) -> str:
    if not rows:
        return "_No rows._\n"
    head = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    body = ["| " + " | ".join(fmt(v, digits) for v in row) + " |" for row in rows]
    return "\n".join([head, sep, *body]) + "\n"
