"""Page B — Historical risk review (frozen scores of the test block)."""

from __future__ import annotations

import math
from datetime import date

from dash import Input, Output, State, ctx, dcc, html

from delivery_risk.dashboard.components import (
    MODEL_LABELS,
    card,
    fmt_brl_exact,
    fmt_int,
    fmt_pct,
    historical_banner,
    kpi,
    message,
    safe,
    synthetic_banner,
    table,
)
from delivery_risk.dashboard.data import REVIEW_SORT_COLUMNS, DashboardData

PAGE_SIZE = 15
SORT_LABELS = {
    "risk_rank": "Risk rank",
    "score_calibrated": "Estimated risk",
    "promised_lead_days": "Promised lead time",
    "item_value_brl": "Item value",
    "n_distinct_sellers": "Number of sellers",
    "sh_known_outcomes_total_min": "Seller history support",
}


def _yes(v) -> str:
    return "✓" if v is True else ""


def _late(v) -> str:
    return "late" if v is True else ("on time" if v is False else "unknown")


@safe
def layout(data: DashboardData, mode: str):
    run = data.latest_run()
    header = [html.H2("Historical risk review"), historical_banner(), synthetic_banner(mode)]
    if run is None:
        return html.Div(
            [
                *header,
                message(
                    "info",
                    "No evaluated model run",
                    "Run `make train` and `make evaluate` for this mode, then reload.",
                ),
            ]
        )
    days = data.review_days(run["run_id"], run["selected_model"])
    if days.empty:
        return html.Div(
            [
                *header,
                message(
                    "empty",
                    "No saved test-block scores",
                    "The latest run has no stored predictions.",
                ),
            ]
        )
    return html.Div(
        [
            *header,
            html.P(
                "Orders approved on one historical day, ranked by the risk score frozen at approval. A reviewer with "
                "capacity q would look at the top ceil(q × N) orders of that day. Scores are estimated risks, not certainties.",
                className="page-lead",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("Model", className="filter-label", htmlFor="rv-model"),
                            dcc.Dropdown(
                                id="rv-model",
                                value=run["selected_model"],
                                clearable=False,
                                options=[
                                    {
                                        "label": label
                                        + (" (selected)" if key == run["selected_model"] else ""),
                                        "value": key,
                                    }
                                    for key, label in MODEL_LABELS.items()
                                ],
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Label(
                                "Approval day (test block)",
                                className="filter-label",
                                htmlFor="rv-day",
                            ),
                            dcc.DatePickerSingle(
                                id="rv-day",
                                date=days.prediction_date.iloc[0],
                                display_format="YYYY-MM-DD",
                                min_date_allowed=days.prediction_date.min(),
                                max_date_allowed=days.prediction_date.max(),
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Label(
                                "Daily review capacity q",
                                className="filter-label",
                                htmlFor="rv-capacity",
                            ),
                            dcc.Slider(
                                id="rv-capacity",
                                min=5,
                                max=30,
                                step=5,
                                value=10,
                                marks={v: f"{v}%" for v in range(5, 31, 5)},
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Label("View", className="filter-label"),
                            dcc.RadioItems(
                                id="rv-mode",
                                value="approval",
                                className="mode-switch",
                                options=[
                                    {"label": "At approval (no outcomes)", "value": "approval"},
                                    {"label": "Retrospective evaluation", "value": "retro"},
                                ],
                            ),
                        ]
                    ),
                ],
                className="filters review",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label(
                                "Sort table by", className="filter-label", htmlFor="rv-sort"
                            ),
                            dcc.Dropdown(
                                id="rv-sort",
                                value="risk_rank",
                                clearable=False,
                                options=[
                                    {"label": SORT_LABELS[k], "value": k}
                                    for k in REVIEW_SORT_COLUMNS
                                ],
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Label("Direction", className="filter-label"),
                            dcc.RadioItems(
                                id="rv-dir",
                                value="asc",
                                inline=True,
                                className="mode-switch",
                                options=[
                                    {"label": "Ascending", "value": "asc"},
                                    {"label": "Descending", "value": "desc"},
                                ],
                            ),
                        ]
                    ),
                ],
                className="filters",
                style={"gridTemplateColumns": "minmax(200px, 320px) minmax(200px, 1fr)"},
            ),
            dcc.Store(id="rv-page", data=0),
            dcc.Loading(html.Div(id="rv-content"), type="dot"),
            html.Div(
                [
                    html.Button("Previous", id="rv-prev", n_clicks=0),
                    html.Button("Next", id="rv-next", n_clicks=0),
                    html.Span(id="rv-page-label"),
                ],
                className="pager",
            ),
        ]
    )


@safe
def render_review(
    data: DashboardData,
    model: str,
    day,
    capacity_pct: int,
    view: str,
    sort: str,
    direction: str,
    page: int,
):
    run = data.latest_run()
    if run is None:
        return message("info", "No evaluated model run", "Run `make train` and `make evaluate`.")
    if model not in MODEL_LABELS:
        model = run["selected_model"]
    day = day if isinstance(day, date) else date.fromisoformat(str(day)[:10])
    q = capacity_pct / 100.0
    retro = view == "retro"
    rows = data.review_table(
        run["run_id"],
        model,
        day,
        q,
        retro,
        sort=sort,
        descending=direction == "desc",
        page=page,
        page_size=PAGE_SIZE,
    )
    if rows.empty and page == 0:
        return message(
            "empty",
            f"No scorable orders approved on {day:%Y-%m-%d}",
            "Pick another day of the test block.",
        )
    day_sum = data.review_summary(run["run_id"], model, q, day=day, with_outcomes=retro)
    window = data.review_summary(run["run_id"], model, q, day=None, with_outcomes=retro)
    n_day = int(day_sum["scorable"])
    k = math.ceil(q * n_day)

    tiles = [
        kpi(
            f"Scorable orders on {day:%d %b %Y}",
            fmt_int(n_day),
            "approved that day, any final status",
        ),
        kpi(f"Review capacity at q = {capacity_pct}%", fmt_int(k), f"ceil({q:.2f} × {n_day})"),
        kpi(
            "Test block: orders selected",
            fmt_int(window["selected"]),
            f"{fmt_pct(window['selected'] / window['scorable'])} of {fmt_int(window['scorable'])} scorable over {fmt_int(window['days'])} days",
        ),
    ]
    columns = [
        ("risk_rank", "Rank", fmt_int),
        ("order_id", "Order", lambda v: str(v)[:10] + "…" if len(str(v)) > 12 else str(v)),
        ("score_calibrated", "Estimated risk", lambda v: fmt_pct(v)),
        ("selected", "In capacity", _yes),
        ("promised_lead_days", "Promised lead (days)", fmt_int),
        ("item_value_brl", "Item value", fmt_brl_exact),
        ("n_distinct_sellers", "Sellers", fmt_int),
        ("sh_known_outcomes_total_min", "Seller history support", fmt_int),
        ("customer_state", "Customer state", str),
        ("main_category", "Main category", str),
    ]
    notes = [
        html.P(
            "Seller history support = smallest number of known delivery outcomes among the order's sellers "
            "before approval (0 = a seller without history).",
            className="card-note",
        )
    ]
    if retro:
        known, late = int(day_sum["selected_known"]), int(day_sum["selected_late"])
        tiles += [
            kpi(
                "Selected with known outcome (day)",
                f"{known} of {k}",
                "canceled/undelivered orders have no outcome",
            ),
            kpi(
                "Late among selected, known (day)",
                f"{late} of {known}" if known else "—",
                f"day late rate (known): {fmt_pct(day_sum['late'] / day_sum['known']) if day_sum['known'] else '—'}",
            ),
            kpi(
                "Test block: precision among known",
                fmt_pct(window["selected_late"] / window["selected_known"])
                if window["selected_known"]
                else "—",
                f"vs late rate {fmt_pct(window['late'] / window['known']) if window['known'] else '—'} · outcome coverage "
                f"{fmt_pct(window['selected_known'] / window['selected']) if window['selected'] else '—'}",
            ),
        ]
        columns += [
            ("outcome_population", "Outcome", str),
            ("delivered_date", "Delivered", str),
            ("delay_days", "Delay (days)", fmt_int),
            ("is_late", "Late?", _late),
        ]
        notes.insert(
            0,
            html.P(
                "Retrospective evaluation: the outcome columns show what happened AFTER the score was "
                "produced. They were not available at approval and must not be read as explanations.",
                className="retro-warning",
            ),
        )
    return html.Div(
        [
            html.Div(tiles, className="kpis"),
            card(
                f"{MODEL_LABELS[model]} — orders approved on {day:%Y-%m-%d}",
                html.Div(
                    [
                        *notes,
                        table(
                            rows, columns, row_class=lambda r: "selected" if r["selected"] else None
                        ),
                    ]
                ),
            ),
        ]
    )


def register(app, data: DashboardData) -> None:
    @app.callback(
        Output("rv-page", "data"),
        Input("rv-prev", "n_clicks"),
        Input("rv-next", "n_clicks"),
        Input("rv-model", "value"),
        Input("rv-day", "date"),
        Input("rv-capacity", "value"),
        Input("rv-mode", "value"),
        Input("rv-sort", "value"),
        Input("rv-dir", "value"),
        State("rv-page", "data"),
    )
    def _page(_prev, _next, model, day, _cap, _mode, _sort, _dir, page):
        trigger = ctx.triggered_id
        if trigger not in ("rv-prev", "rv-next"):
            return 0
        run = data.latest_run()
        n_day = 0
        if run is not None and day:
            n_day = int(
                data.review_summary(
                    run["run_id"], model, 0.1, day=date.fromisoformat(str(day)[:10])
                )["scorable"]
            )
        last = max(0, math.ceil(n_day / PAGE_SIZE) - 1)
        return max(0, page - 1) if trigger == "rv-prev" else min(last, page + 1)

    @app.callback(
        Output("rv-content", "children"),
        Output("rv-page-label", "children"),
        Input("rv-model", "value"),
        Input("rv-day", "date"),
        Input("rv-capacity", "value"),
        Input("rv-mode", "value"),
        Input("rv-sort", "value"),
        Input("rv-dir", "value"),
        Input("rv-page", "data"),
    )
    def _update(model, day, capacity, view, sort, direction, page):
        content = render_review(data, model, day, capacity, view, sort, direction, page or 0)
        return content, f"Page {int(page or 0) + 1} · {PAGE_SIZE} rows per page"
