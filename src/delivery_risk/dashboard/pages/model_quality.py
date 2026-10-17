"""Page C — Model & data quality."""

from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, dcc, html

from delivery_risk.dashboard.components import (
    MODEL_COLORS,
    MODEL_LABELS,
    card,
    figure,
    fmt_int,
    fmt_num,
    fmt_pct,
    message,
    safe,
    synthetic_banner,
    table,
)
from delivery_risk.dashboard.data import DashboardData

GROUP_TYPES = {
    "prediction_month": "Approval month",
    "customer_region": "Customer region",
    "seller_history_status": "Seller history status",
    "multi_seller": "Single vs multi-seller",
}
LIMITATIONS = [
    "Retrospective study on a public historical export; no live marketplace connection.",
    "Performance is measured only on orders finally delivered with an eligible outcome; canceled or undelivered "
    "orders are scored but cannot be evaluated.",
    "Product, price, freight and promise attributes are assumed to be known at approval (final snapshot, no history).",
    "A better selection of risky orders does not show that contacting a seller prevents a delay.",
    "Seller late-order exposure is shared by all sellers of a multi-seller order; it does not identify the cause.",
    "Importances and coefficients are associations, not causes.",
]


def _ci(value, low, high, pct: bool = False) -> str:
    f = fmt_pct if pct else (lambda v: fmt_num(v, 3))
    if pd.isna(value):
        return "—"
    return f"{f(value)} [{f(low)} – {f(high)}]" if pd.notna(low) else f(value)


def comparison_table(metrics: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for model in MODEL_LABELS:
        m = metrics[metrics.model_name == model]
        if m.empty:
            continue

        def get(metric, variant="raw", m=m):
            r = m[(m.metric == metric) & (m.variant == variant)]
            return r.iloc[0] if not r.empty else None

        ap, auc, p10, r10, l10 = (
            get(x)
            for x in (
                "average_precision",
                "roc_auc",
                "precision_at_10",
                "recall_at_10",
                "lift_at_10",
            )
        )
        b_raw, b_cal, ll_raw, ll_cal = (
            get("brier"),
            get("brier", "calibrated"),
            get("log_loss"),
            get("log_loss", "calibrated"),
        )
        rows.append(
            {
                "model": MODEL_LABELS[model],
                "ap": _ci(ap.value, ap.ci_low, ap.ci_high),
                "roc_auc": fmt_num(auc.value, 3) if auc is not None else "—",
                "brier": f"{fmt_num(b_raw.value, 4)} → {fmt_num(b_cal.value, 4)}",
                "log_loss": f"{fmt_num(ll_raw.value, 4)} → {fmt_num(ll_cal.value, 4)}",
                "p10": _ci(p10.value, p10.ci_low, p10.ci_high, pct=True),
                "r10": fmt_pct(r10.value),
                "lift10": _ci(l10.value, l10.ci_low, l10.ci_high).replace("[", "[")
                if l10 is not None
                else "—",
                "n": fmt_int(ap.n),
                "positives": fmt_int(ap.positives),
            }
        )
    return pd.DataFrame(rows)


@safe
def layout(data: DashboardData, mode: str):
    run = data.latest_run()
    header = [html.H2("Model & data quality"), synthetic_banner(mode)]
    quality = data_quality_section(data)
    if run is None:
        return html.Div(
            [
                *header,
                message("info", "No evaluated model run", "Run `make train` and `make evaluate`."),
                quality,
            ]
        )
    metrics = data.model_metrics(run["run_id"])
    splits = run["splits"] if isinstance(run["splits"], dict) else json.loads(run["splits"])
    blocks = pd.DataFrame([{"block": b, **v} for b, v in splits["blocks"].items()]).sort_values(
        "start"
    )
    pubs = data.publications()
    ap_row = metrics[(metrics.metric == "average_precision") & (metrics.variant == "raw")]
    n_test = int(ap_row.n.iloc[0]) if not ap_row.empty else 0
    prevalence = (
        metrics[(metrics.metric == "prevalence") & (metrics.variant == "raw")].value.iloc[0]
        if n_test
        else None
    )

    run_info = html.Dl(
        [
            html.Dt("Data mode"),
            html.Dd(f"{mode}{' (synthetic)' if mode != 'real' else ''}"),
            html.Dt("Run"),
            html.Dd(run["run_id"]),
            html.Dt("Trained at"),
            html.Dd(str(run["created_at"])[:19]),
            html.Dt("Selected model"),
            html.Dd(
                f"{MODEL_LABELS.get(run['selected_model'], run['selected_model'])} "
                "(highest AP on the selection block, chosen before test)"
            ),
            html.Dt("Feature version"),
            html.Dd(f"{run['feature_version']} · sha256 {run['feature_sha256'][:12]}…"),
            html.Dt("Split definition"),
            html.Dd(f"sha256 {run['split_sha256'][:12]}… · status {splits['status']}"),
            *[
                x
                for _, p in pubs.iterrows()
                for x in (html.Dt(f"Last {p.layer} build"), html.Dd(str(p.published_at)[:19]))
            ],
        ],
        className="kv",
    )

    topq = metrics[
        (metrics.variant == "raw")
        & metrics.metric.isin(["precision_at_5", "precision_at_10", "precision_at_20"])
    ]
    bars = go.Figure()
    for model in MODEL_LABELS:
        m = (
            topq[topq.model_name == model]
            .set_index("metric")
            .reindex(["precision_at_5", "precision_at_10", "precision_at_20"])
        )
        bars.add_bar(
            name=MODEL_LABELS[model],
            x=["Top 5%", "Top 10%", "Top 20%"],
            y=m.value,
            marker={"color": MODEL_COLORS[model], "cornerradius": 4},
            hovertemplate=MODEL_LABELS[model] + "<br>%{x}: precision %{y:.1%}<extra></extra>",
        )
    if prevalence is not None:
        bars.add_hline(y=prevalence, line={"color": "#52514e", "width": 1})
        bars.add_scatter(
            x=[None],
            y=[None],
            mode="lines",
            line={"color": "#52514e", "width": 1},
            name=f"Prevalence {prevalence:.1%} (random selection)",
        )
    bars.update_layout(barmode="group", bargroupgap=0.08)

    return html.Div(
        [
            *header,
            html.P(
                "Final test-block results of a frozen run. Model choice, hyper-parameters and calibration were fixed "
                "before the test block was scored.",
                className="page-lead",
            ),
            card(
                "Model comparison — test block",
                table(
                    comparison_table(metrics),
                    [
                        ("model", "Model", str),
                        ("ap", "AP [95% CI]", str),
                        ("roc_auc", "ROC-AUC", str),
                        ("brier", "Brier raw → calibrated", str),
                        ("log_loss", "Log loss raw → calibrated", str),
                        ("p10", "Precision@10% [95% CI]", str),
                        ("r10", "Recall@10%", str),
                        ("lift10", "Lift@10% [95% CI]", str),
                        ("n", "n", str),
                        ("positives", "late", str),
                    ],
                ),
                note=f"Population: {n_test:,} test orders finally delivered with an eligible outcome (prevalence "
                f"{fmt_pct(prevalence)}). AP = sklearn average_precision_score. Intervals: paired bootstrap over ISO weeks "
                "(500 resamples). Ranking metrics are identical for raw and calibrated scores.",
            ),
            html.Div(style={"height": "16px"}),
            html.Div(
                [
                    card(
                        "Precision in the top q% of test orders",
                        dcc.Graph(
                            figure=figure(bars, height=300, y_title="Precision", y_format=".0%"),
                            config={"displayModeBar": False},
                        ),
                        note="Global top-q on the labelled test subset (model comparison). The daily capacity policy is on the risk review page.",
                    ),
                    card(
                        "Calibration — predicted vs observed",
                        html.Div(
                            [
                                dcc.Dropdown(
                                    id="mq-model",
                                    value=run["selected_model"],
                                    clearable=False,
                                    options=[
                                        {"label": v, "value": k} for k, v in MODEL_LABELS.items()
                                    ],
                                ),
                                dcc.Graph(id="mq-calibration", config={"displayModeBar": False}),
                            ]
                        ),
                        note="Quantile bins of the score on the test block. Calibrator fitted on the calibration block only.",
                    ),
                ],
                className="grid-2",
            ),
            html.Div(
                [
                    card(
                        "Run, data and temporal blocks",
                        html.Div(
                            [
                                run_info,
                                html.Div(
                                    table(
                                        blocks,
                                        [
                                            ("block", "Block", str),
                                            ("start", "Start", str),
                                            ("end_exclusive", "End (exclusive)", str),
                                        ],
                                    ),
                                    style={"marginTop": "10px"},
                                ),
                            ]
                        ),
                        note="Blocks are on the approval timestamp. Labels are used only once the delivery has happened.",
                    ),
                    card(
                        "Results by period and group (selected model)",
                        html.Div(
                            [
                                dcc.Dropdown(
                                    id="mq-group",
                                    value="prediction_month",
                                    clearable=False,
                                    style={"maxWidth": "320px"},
                                    options=[
                                        {"label": v, "value": k} for k, v in GROUP_TYPES.items()
                                    ],
                                ),
                                html.Div(id="mq-group-table"),
                            ]
                        ),
                        note=f"Metrics are hidden (—) for groups with fewer than {data.min_support} labelled orders or {data.min_positives} late orders.",
                    ),
                ],
                className="grid-2",
            ),
            html.Div(style={"height": "16px"}),
            quality,
        ]
    )


def data_quality_section(data: DashboardData):
    assertions = data.assertions()
    flagged = assertions[(assertions.violation_count > 0) & (assertions.severity != "info")]
    n_pass = int(((assertions.violation_count == 0) & (assertions.severity != "info")).sum())
    return html.Div(
        [
            html.Div(
                [
                    card(
                        "Data-quality checks",
                        html.Div(
                            [
                                html.P(
                                    f"{n_pass} checks pass. Critical checks block publication; the warnings below are counted anomalies.",
                                    className="card-note",
                                ),
                                table(
                                    flagged,
                                    [
                                        ("layer", "Layer", str),
                                        ("check_name", "Check", str),
                                        ("severity", "Severity", str),
                                        ("violation_count", "Count", fmt_int),
                                        ("description", "Description", str),
                                    ],
                                )
                                if not flagged.empty
                                else message("empty", "No warning", "All checks pass."),
                            ]
                        ),
                    ),
                    card(
                        "Quarantined rows",
                        table(
                            data.quarantine_summary(),
                            [
                                ("dataset", "Dataset", str),
                                ("reason", "Reason", str),
                                ("rows", "Rows", fmt_int),
                            ],
                        )
                        if not data.quarantine_summary().empty
                        else message("empty", "Nothing quarantined", ""),
                    ),
                ],
                className="grid-2",
            ),
            html.Div(
                [
                    card(
                        "Outcome populations and exclusions",
                        table(
                            data.coverage(),
                            [
                                ("outcome_population", "Outcome population", str),
                                ("scoring_status", "Scoring", str),
                                ("label_status", "Label", str),
                                ("orders", "Orders", fmt_int),
                            ],
                        ),
                        note="Every order is counted once. Canceled, unavailable and undelivered orders are never counted as on time.",
                    ),
                    card(
                        "Main limitations",
                        html.Ul([html.Li(x) for x in LIMITATIONS], className="limits"),
                    ),
                ],
                className="grid-2",
            ),
        ]
    )


@safe
def calibration_figure(data: DashboardData, model: str):
    run = data.latest_run()
    bins = data.calibration_bins(run["run_id"], model)
    fig = go.Figure()
    fig.add_scatter(
        x=[0, bins.mean_score.max() * 1.05 if not bins.empty else 1],
        y=[0, bins.mean_score.max() * 1.05 if not bins.empty else 1],
        mode="lines",
        line={"color": "#b5b4ae", "width": 1},
        name="Perfect calibration",
        hoverinfo="skip",
    )
    for variant, color, label in (
        ("raw", "#8a8984", "Raw score"),
        ("calibrated", MODEL_COLORS.get(model, "#2a78d6"), "Calibrated"),
    ):
        b = bins[bins.variant == variant]
        fig.add_scatter(
            x=b.mean_score,
            y=b.observed_rate,
            mode="lines+markers",
            name=label,
            line={"color": color, "width": 2},
            marker={"size": 8},
            customdata=b.n,
            hovertemplate=label
            + "<br>mean score %{x:.1%}<br>observed %{y:.1%}<br>n = %{customdata}<extra></extra>",
        )
    return figure(
        fig,
        height=280,
        x_title="Mean predicted probability",
        y_title="Observed late rate",
        y_format=".0%",
    ).update_xaxes(tickformat=".0%")


@safe
def group_table(data: DashboardData, group_type: str):
    run = data.latest_run()
    if group_type not in GROUP_TYPES:
        group_type = "prediction_month"
    df = data.metrics_by_group(run["run_id"], run["selected_model"], group_type)
    small = (df["n"] < data.min_support) | (df["positives"] < data.min_positives)
    df.loc[small, ["average_precision", "precision_at_10", "lift_at_10"]] = None
    return table(
        df,
        [
            ("group_value", GROUP_TYPES[group_type], str),
            ("n", "Labelled orders", fmt_int),
            ("positives", "Late", fmt_int),
            ("prevalence", "Late rate", fmt_pct),
            ("average_precision", "AP", lambda v: fmt_num(v, 3)),
            ("precision_at_10", "Precision@10%", fmt_pct),
            ("lift_at_10", "Lift@10%", lambda v: fmt_num(v, 2)),
        ],
        row_class=lambda r: (
            "low-support"
            if r["n"] < data.min_support or r["positives"] < data.min_positives
            else None
        ),
    )


def register(app, data: DashboardData) -> None:
    @app.callback(Output("mq-calibration", "figure"), Input("mq-model", "value"))
    def _calibration(model):
        fig = calibration_figure(data, model)
        return fig if isinstance(fig, go.Figure) else go.Figure()

    @app.callback(Output("mq-group-table", "children"), Input("mq-group", "value"))
    def _groups(group_type):
        return group_table(data, group_type)
