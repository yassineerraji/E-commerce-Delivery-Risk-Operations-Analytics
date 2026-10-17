"""Page A — Operations overview (descriptive, all orders)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, dcc, html

from delivery_risk.dashboard.components import (
    SERIES_1,
    SERIES_2,
    card,
    figure,
    fmt_brl,
    fmt_int,
    fmt_num,
    fmt_pct,
    kpi,
    message,
    safe,
    synthetic_banner,
    table,
)
from delivery_risk.dashboard.data import DashboardData

CONVENTION = (
    "Period = order purchase date (timestamps as recorded in the source, no time zone). "
    "Region = customer macro-region. Category filter = orders containing at least one item of a selected "
    "category; amounts then include ALL items of those orders. Late rate = late / eligible delivered orders."
)


def _parse(d: str | date | None, default: date) -> date:
    if d is None:
        return default
    return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])


def population_title(start: date, end: date, regions, categories) -> str:
    parts = [f"Orders purchased {start:%d %b %Y} – {end:%d %b %Y}"]
    parts.append("regions: " + ", ".join(regions) if regions else "all regions")
    if categories:
        shown = ", ".join(categories[:3]) + (
            f" +{len(categories) - 3}" if len(categories) > 3 else ""
        )
        parts.append("orders containing: " + shown)
    else:
        parts.append("all categories")
    return " · ".join(parts)


@safe
def layout(data: DashboardData, mode: str):
    opts = data.filter_options()
    return html.Div(
        [
            html.H2("Operations overview"),
            html.P(
                "Descriptive view of orders, delivery outcomes and late-order exposure. Associations shown here are "
                "not causal.",
                className="page-lead",
            ),
            synthetic_banner(mode),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label(
                                "Purchase period", className="filter-label", htmlFor="ov-dates"
                            ),
                            dcc.DatePickerRange(
                                id="ov-dates",
                                min_date_allowed=opts["min_date"],
                                max_date_allowed=opts["max_date"],
                                start_date=opts["min_date"],
                                end_date=opts["max_date"],
                                display_format="YYYY-MM-DD",
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Label(
                                "Customer region", className="filter-label", htmlFor="ov-regions"
                            ),
                            dcc.Dropdown(
                                id="ov-regions",
                                options=opts["regions"],
                                multi=True,
                                placeholder="All regions",
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Label(
                                "Product category (orders containing)",
                                className="filter-label",
                                htmlFor="ov-categories",
                            ),
                            dcc.Dropdown(
                                id="ov-categories",
                                options=opts["categories"],
                                multi=True,
                                placeholder="All categories",
                            ),
                        ]
                    ),
                ],
                className="filters",
            ),
            html.P(CONVENTION, className="convention"),
            dcc.Loading(html.Div(id="ov-content"), type="dot"),
            html.Div(
                [
                    html.Label("Segment by", className="filter-label"),
                    dcc.RadioItems(
                        id="ov-seg-dim",
                        value="customer_region",
                        inline=True,
                        className="mode-switch",
                        options=[
                            {"label": "Customer region", "value": "customer_region"},
                            {"label": "Customer state", "value": "customer_state"},
                            {"label": "Main category", "value": "main_category"},
                        ],
                    ),
                ],
                style={"marginTop": "8px"},
            ),
            dcc.Loading(html.Div(id="ov-segments"), type="dot"),
            html.Button(
                "Download monthly table (CSV)",
                id="ov-export",
                className="pager",
                n_clicks=0,
                style={
                    "marginTop": "12px",
                    "border": "1px solid #e4e3df",
                    "background": "#fff",
                    "padding": "6px 10px",
                    "borderRadius": "6px",
                },
            ),
            dcc.Download(id="ov-download"),
        ]
    )


@safe
def render_overview(data: DashboardData, start, end, regions, categories):
    opts = data.filter_options()
    start, end = _parse(start, opts["min_date"]), _parse(end, opts["max_date"])
    k = data.overview_kpis(start, end, regions, categories)
    if not k["orders"]:
        return message(
            "empty",
            "No orders match these filters",
            "Widen the period or remove a region/category filter.",
        )
    monthly = data.overview_monthly(start, end, regions, categories)
    exposure = data.seller_exposure(start, end, regions, categories)
    title = population_title(start, end, regions, categories)

    kpis = html.Div(
        [
            kpi("Orders", fmt_int(k["orders"]), "all statuses"),
            kpi("Item value", fmt_brl(k["item_value_brl"]), "sum of item prices (not revenue)"),
            kpi("Freight", fmt_brl(k["freight_value_brl"]), "sum of freight values"),
            kpi(
                "Late rate",
                fmt_pct(k["late"] / k["eligible_delivered"]) if k["eligible_delivered"] else "—",
                f"{fmt_int(k['late'])} late of {fmt_int(k['eligible_delivered'])} eligible delivered",
            ),
            kpi(
                "Median delivery time",
                f"{fmt_num(k['median_delivery_days'], 0)} days",
                f"median promise {fmt_num(k['median_promised_days'], 0)} days (eligible delivered)",
            ),
            kpi(
                "Outcome coverage",
                fmt_pct(k["eligible_delivered"] / k["orders"]),
                f"{fmt_int(k['canceled_or_unavailable'])} canceled/unavailable · {fmt_int(k['outcome_unknown'])} other",
            ),
        ],
        className="kpis",
    )

    months = pd.to_datetime(monthly["purchase_month"])
    vol = go.Figure(
        go.Bar(
            x=months,
            y=monthly["orders"],
            marker={"color": SERIES_1, "cornerradius": 4},
            hovertemplate="%{x|%b %Y}<br>%{y:,} orders<extra></extra>",
            name="Orders",
        )
    )
    rate = go.Figure(
        go.Scatter(
            x=months,
            # months below the minimum support are left out instead of showing e.g. 100% on 1 order
            y=monthly["late_rate"].where(monthly["eligible_delivered"] >= data.min_support),
            mode="lines+markers",
            line={"color": SERIES_1, "width": 2},
            marker={"size": 8},
            customdata=monthly[["late", "eligible_delivered"]].to_numpy(),
            name="Late rate",
            hovertemplate="%{x|%b %Y}<br>late rate %{y:.1%}<br>%{customdata[0]:,} late / %{customdata[1]:,} eligible delivered<extra></extra>",
        )
    )
    promise = go.Figure(
        [
            go.Scatter(
                x=months,
                y=monthly["median_promised_days"].where(
                    monthly["eligible_delivered"] >= data.min_support
                ),
                mode="lines+markers",
                name="Median promised lead time",
                line={"color": SERIES_1, "width": 2},
                marker={"size": 8},
                hovertemplate="%{x|%b %Y}<br>promised %{y:.0f} days<extra></extra>",
            ),
            go.Scatter(
                x=months,
                y=monthly["median_delivery_days"].where(
                    monthly["eligible_delivered"] >= data.min_support
                ),
                mode="lines+markers",
                name="Median actual delivery time",
                line={"color": SERIES_2, "width": 2},
                marker={"size": 8},
                hovertemplate="%{x|%b %Y}<br>actual %{y:.0f} days<extra></extra>",
            ),
        ]
    )

    exposure_view = (
        table(
            exposure,
            [
                ("seller_id", "Seller", lambda v: str(v)[:12] + "…"),
                ("seller_state", "State", str),
                ("eligible_delivered", "Eligible delivered orders", fmt_int),
                ("late_orders", "Late orders", fmt_int),
                ("late_order_exposure", "Late-order exposure", fmt_pct),
                ("multi_seller_share", "Multi-seller", fmt_pct),
            ],
        )
        if not exposure.empty
        else message(
            "empty",
            "No seller reaches the minimum support",
            f"Rankings are hidden below {data.min_support} eligible delivered orders.",
        )
    )
    return html.Div(
        [
            html.H3(title, className="card-title", id="ov-title", style={"marginTop": "4px"}),
            kpis,
            html.Div(
                [
                    card(
                        "Orders per purchase month",
                        dcc.Graph(
                            figure=figure(vol, height=260, y_title="Orders"),
                            config={"displayModeBar": False},
                        ),
                    ),
                    card(
                        "Late rate per purchase month",
                        dcc.Graph(
                            figure=figure(rate, height=260, y_title="Late rate", y_format=".0%"),
                            config={"displayModeBar": False},
                        ),
                        note=f"Denominator: eligible delivered orders of the month (hover for counts). Months with fewer "
                        f"than {data.min_support} eligible delivered orders are not plotted.",
                    ),
                ],
                className="grid-2",
            ),
            html.Div(
                [
                    card(
                        "Promised vs actual delivery time (eligible delivered)",
                        dcc.Graph(
                            figure=figure(promise, height=280, y_title="Days (civil dates)"),
                            config={"displayModeBar": False},
                        ),
                        note="Promised = estimated delivery date − purchase date; actual = delivery date − purchase date. "
                        f"Months with fewer than {data.min_support} eligible delivered orders are not plotted.",
                    ),
                    card(
                        "Seller late-order exposure (highest first)",
                        exposure_view,
                        note=f"Exposure = share of a seller's eligible delivered orders that were late. It does NOT identify who "
                        f"caused the delay: every seller of a multi-seller order shares its outcome, so seller counts do not add "
                        f"up to the marketplace total. Sellers with fewer than {data.min_support} eligible delivered orders are hidden.",
                    ),
                ],
                className="grid-2",
            ),
        ]
    )


@safe
def render_segments(data: DashboardData, dimension, start, end, regions, categories):
    opts = data.filter_options()
    start, end = _parse(start, opts["min_date"]), _parse(end, opts["max_date"])
    seg = data.overview_segments(dimension, start, end, regions, categories)
    if seg.empty:
        return message("empty", "No segment", "No orders match these filters.")
    seg["late_rate_display"] = seg.apply(
        lambda r: r.late_rate if r.eligible_delivered >= data.min_support else None, axis=1
    )
    return card(
        "Late rate by segment",
        table(
            seg,
            [
                ("segment", "Segment", str),
                ("orders", "Orders", fmt_int),
                ("eligible_delivered", "Eligible delivered", fmt_int),
                ("late", "Late", fmt_int),
                ("late_rate_display", "Late rate", fmt_pct),
            ],
            row_class=lambda r: "low-support" if r.eligible_delivered < data.min_support else None,
        ),
        note=f"Late rates are hidden (—) for segments with fewer than {data.min_support} eligible delivered orders. "
        "Differences between segments are associations, not causes.",
    )


def register(app, data: DashboardData) -> None:
    @app.callback(
        Output("ov-content", "children"),
        Input("ov-dates", "start_date"),
        Input("ov-dates", "end_date"),
        Input("ov-regions", "value"),
        Input("ov-categories", "value"),
    )
    def _update(start, end, regions, categories):
        return render_overview(data, start, end, regions, categories)

    @app.callback(
        Output("ov-segments", "children"),
        Input("ov-seg-dim", "value"),
        Input("ov-dates", "start_date"),
        Input("ov-dates", "end_date"),
        Input("ov-regions", "value"),
        Input("ov-categories", "value"),
    )
    def _segments(dimension, start, end, regions, categories):
        return render_segments(data, dimension, start, end, regions, categories)

    @app.callback(
        Output("ov-download", "data"),
        Input("ov-export", "n_clicks"),
        State("ov-dates", "start_date"),
        State("ov-dates", "end_date"),
        State("ov-regions", "value"),
        State("ov-categories", "value"),
        prevent_initial_call=True,
    )
    def _export(_, start, end, regions, categories):
        opts = data.filter_options()
        start, end = _parse(start, opts["min_date"]), _parse(end, opts["max_date"])
        df = data.overview_monthly(start, end, regions, categories)
        df.insert(0, "population", population_title(start, end, regions, categories))
        df.insert(1, "data_mode", data.mode)
        return dcc.send_data_frame(
            df.to_csv, f"monthly_operations_{data.mode}_{start}_{end}.csv", index=False
        )
