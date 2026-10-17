"""Shared UI pieces: banners, KPI tiles, tables, states and the Plotly theme."""

from __future__ import annotations

import functools
import logging
import math
from collections.abc import Callable, Sequence
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import psycopg
from dash import html

from delivery_risk.db import DatabaseUnavailable

log = logging.getLogger(__name__)

MODEL_LABELS = {
    "constant_prevalence": "Constant (prevalence)",
    "seller_history_baseline": "Seller-history baseline",
    "logistic_regression": "Logistic regression",
    "lightgbm": "LightGBM",
}
# Colour follows the model (entity), never its rank. Baseline references are muted.
MODEL_COLORS = {
    "logistic_regression": "#2a78d6",
    "lightgbm": "#eb6834",
    "seller_history_baseline": "#1baf7a",
    "constant_prevalence": "#8a8984",
}
SERIES_1 = "#2a78d6"
SERIES_2 = "#eb6834"
TEXT_SECONDARY = "#52514e"
GRID = "#ecebe7"


# ---------------------------------------------------------------- formatting
def _is_missing(v: Any) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA or v is pd.NaT


def fmt_int(v: Any) -> str:
    return "—" if _is_missing(v) else f"{int(v):,}"


def fmt_pct(v: Any, digits: int = 1) -> str:
    return "—" if _is_missing(v) else f"{100 * float(v):.{digits}f}%"


def fmt_num(v: Any, digits: int = 3) -> str:
    return "—" if _is_missing(v) else f"{float(v):,.{digits}f}"


def fmt_brl_exact(v: Any) -> str:
    return "—" if _is_missing(v) else f"R$ {float(v):,.2f}"


def fmt_brl(v: Any) -> str:
    if _is_missing(v):
        return "—"
    v = float(v)
    if abs(v) >= 1e6:
        return f"R$ {v / 1e6:,.2f} M"
    if abs(v) >= 1e3:
        return f"R$ {v / 1e3:,.1f} k"
    return f"R$ {v:,.2f}"


# ---------------------------------------------------------------- banners & states
def synthetic_banner(mode: str) -> html.Div | None:
    if mode == "real":
        return None
    return html.Div(
        [
            html.Strong("SYNTHETIC TEST DATA. "),
            "Every figure on this page comes from a simulated dataset with the Olist file layout. "
            "It says nothing about the real marketplace.",
        ],
        className="banner banner-synthetic",
        role="status",
    )


def historical_banner() -> html.Div:
    return html.Div(
        [
            html.Strong("Historical simulation — not live operations. "),
            "Scores were frozen at approval time on past orders. Nothing here contacts a seller or a customer.",
        ],
        className="banner banner-historical",
        role="status",
    )


def message(kind: str, title: str, body: str) -> html.Div:
    """kind: empty | error | info."""
    return html.Div(
        [html.Div(title, className="message-title"), html.Div(body)],
        className=f"message message-{kind}",
    )


def safe(render: Callable) -> Callable:
    """Turn database or unexpected failures into a readable message (no stack trace, no secret)."""

    @functools.wraps(render)
    def wrapper(*args, **kwargs):
        try:
            return render(*args, **kwargs)
        except DatabaseUnavailable:
            log.exception("Database unavailable")
            return message(
                "error",
                "Database unavailable",
                "PostgreSQL cannot be reached with the read-only role. Start it with `make db-up` and reload.",
            )
        except psycopg.Error as exc:
            log.exception("Query failed")
            return message(
                "error",
                "Query failed",
                f"The database returned an error ({type(exc).__name__}). Check that the pipeline has been run for this mode.",
            )
        except Exception:  # noqa: BLE001
            log.exception("Rendering failed")
            return message(
                "error",
                "Something went wrong",
                "This view could not be rendered. Details are in the server log.",
            )

    return wrapper


def kpi(label: str, value: str, detail: str | None = None) -> html.Div:
    children = [html.Div(label, className="kpi-label"), html.Div(value, className="kpi-value")]
    if detail:
        children.append(html.Div(detail, className="kpi-detail"))
    return html.Div(children, className="kpi")


def card(title: str, children: Any, note: str | None = None, cls: str = "") -> html.Section:
    body = [html.H3(title, className="card-title"), children]
    if note:
        body.append(html.P(note, className="card-note"))
    return html.Section(body, className=f"card {cls}".strip())


def table(
    df: pd.DataFrame,
    columns: Sequence[tuple[str, str, Callable[[Any], str]]],
    row_class: Callable | None = None,
) -> html.Table:
    """columns: (dataframe column, header label, formatter)."""
    head = html.Thead(html.Tr([html.Th(label, scope="col") for _, label, _ in columns]))
    rows = []
    for _, r in df.iterrows():
        cls = row_class(r) if row_class else None
        rows.append(
            html.Tr([html.Td(f(r[c]) if c in r else "—") for c, _, f in columns], className=cls)
        )
    return html.Div(
        html.Table([head, html.Tbody(rows)], className="data-table"), className="table-wrap"
    )


def figure(
    fig: go.Figure,
    title: str | None = None,
    height: int = 300,
    y_title: str | None = None,
    x_title: str | None = None,
    y_format: str | None = None,
) -> go.Figure:
    fig.update_layout(
        template="plotly_white",
        title={"text": title, "font": {"size": 14, "color": "#0b0b0b"}, "x": 0, "xanchor": "left"}
        if title
        else None,
        height=height,
        margin={"l": 56, "r": 16, "t": 40 if title else 16, "b": 44},
        font={
            "family": "Inter, system-ui, -apple-system, Segoe UI, sans-serif",
            "size": 12,
            "color": TEXT_SECONDARY,
        },
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1,
            "title": None,
        },
        hoverlabel={"bgcolor": "#ffffff", "font": {"color": "#0b0b0b"}},
        bargap=0.25,
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, title=x_title, ticks="outside", tickcolor=GRID)
    fig.update_yaxes(
        gridcolor=GRID,
        zeroline=False,
        linecolor=GRID,
        title=y_title,
        tickformat=y_format,
        rangemode="tozero",
    )
    return fig
