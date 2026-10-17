"""Final report: figures, LaTeX tables and number macros from saved artifacts, compiled with Tectonic.

Every number quoted in `reports/final/report.tex` comes from `reports/final/generated/numbers.tex`,
which this module writes from the evaluation artifacts and read-only queries on the published
tables. Additional analyses here are descriptive diagnostics of the frozen run (no refit, no
recalibration, no model choice).
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from delivery_risk.config import PROJECT_ROOT, load_splits, mode_config, project_config  # noqa: E402
from delivery_risk.db import connect  # noqa: E402
from delivery_risk.evaluation.capacity import daily_capacity, summarise_capacity  # noqa: E402
from delivery_risk.evaluation.metrics import top_q  # noqa: E402

log = logging.getLogger(__name__)

FINAL_DIR = PROJECT_ROOT / "reports" / "final"
GEN_DIR = FINAL_DIR / "generated"
FIG_DIR = FINAL_DIR / "figures"
MODELS = ("constant_prevalence", "seller_history_baseline", "logistic_regression", "lightgbm")
LABEL = {
    "constant_prevalence": "Constant (prevalence)",
    "seller_history_baseline": "Seller-history rule",
    "logistic_regression": "Logistic regression",
    "lightgbm": "LightGBM",
}
COLOR = {
    "logistic_regression": "#2a78d6",
    "lightgbm": "#eb6834",
    "seller_history_baseline": "#1baf7a",
    "constant_prevalence": "#8a8984",
}
INK, MUTED, GRID = "#1a1a1a", "#555555", "#e3e3e3"
BLOCK_SHADES = {
    "train": "#eef3fb",
    "selection": "#fdf1e6",
    "calibration": "#eaf6f0",
    "test": "#f3eefb",
}
COL_W, FULL_W = 3.35, 7.0  # inches (two-column article)


# ---------------------------------------------------------------- style
def _style() -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["cmr10"],
            "mathtext.fontset": "cm",
            "axes.formatter.use_mathtext": True,
            "axes.unicode_minus": False,
            "font.size": 8,
            "axes.titlesize": 8.5,
            "axes.labelsize": 8,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "legend.fontsize": 7,
            "axes.edgecolor": "#bbbbbb",
            "axes.linewidth": 0.6,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "axes.labelcolor": INK,
            "axes.titlecolor": INK,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "legend.frameon": False,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.02,
        }
    )


def _save(fig, name: str) -> None:
    fig.savefig(FIG_DIR / f"{name}.pdf")
    plt.close(fig)


def _pct_axis(ax, axis: str = "y", digits: int = 0) -> None:
    fmt = matplotlib.ticker.PercentFormatter(1.0, decimals=digits)
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    d = 1 + z**2 / n
    c = (p + z**2 / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / d
    return c - h, c + h


# ---------------------------------------------------------------- data collection
def _q(conn, sql: str, params=None) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])


def collect(mode: str = "real") -> dict:
    eval_dir = mode_config(mode).report_dir("evaluation")
    dq_dir = mode_config(mode).report_dir("data_quality")
    perf_dir = mode_config(mode).report_dir("sql_performance")
    ctx: dict = {
        "summary": json.loads((eval_dir / "evaluation_summary.json").read_text()),
        "manifest": json.loads((eval_dir / "run_manifest.json").read_text()),
        "metrics": pd.read_csv(eval_dir / "test_metrics.csv"),
        "groups": pd.read_csv(eval_dir / "test_metrics_by_group.csv"),
        "bins": pd.read_csv(eval_dir / "test_calibration_bins.csv"),
        "ablations": pd.read_csv(eval_dir / "test_ablations.csv"),
        "selection": pd.read_csv(eval_dir / "selection_results.csv"),
        "importance": pd.read_csv(eval_dir / "permutation_importance_calibration_block.csv"),
        "coverage": pd.read_csv(dq_dir / "coverage_monthly.csv"),
        "bench": json.loads((perf_dir / "benchmark_results.json").read_text()),
        "splits": load_splits(mode),
    }
    run_id = ctx["summary"]["run_id"]
    test = ctx["splits"]["test"]
    with connect(mode) as conn:
        ctx["manifest_rows"] = _q(
            conn,
            """
            SELECT dataset, file_name, row_count, file_size_bytes, left(sha256, 12) AS sha
            FROM meta.load_manifest m WHERE load_id = meta.current_load_id(dataset) ORDER BY row_count DESC""",
        )
        ctx["populations"] = (
            _q(
                conn,
                """
            SELECT count(*) AS orders,
                   count(*) FILTER (WHERE prediction_at IS NOT NULL) AS approved,
                   count(*) FILTER (WHERE scoring_eligible) AS scorable,
                   count(*) FILTER (WHERE label_eligible) AS labelled,
                   count(*) FILTER (WHERE is_late) AS late,
                   count(*) FILTER (WHERE scoring_eligible AND outcome_population IN ('canceled', 'unavailable')) AS scorable_canceled,
                   count(*) FILTER (WHERE scoring_eligible AND NOT label_eligible
                                    AND outcome_population NOT IN ('canceled', 'unavailable')) AS scorable_other_unlabelled
            FROM evaluation.order_labels""",
            )
            .iloc[0]
            .to_dict()
        )
        ctx["by_state"] = _q(
            conn,
            """
            SELECT customer_state AS state, customer_region AS region,
                   count(*) FILTER (WHERE label_eligible) AS n, count(*) FILTER (WHERE is_late) AS late
            FROM mart.order_summary GROUP BY 1, 2 ORDER BY 1""",
        )
        ctx["by_promise"] = _q(
            conn,
            """
            SELECT width_bucket(promised_lead_days, 0, 50, 10) AS bucket, min(promised_lead_days) AS lo,
                   max(promised_lead_days) AS hi, count(*) AS n, count(*) FILTER (WHERE is_late) AS late
            FROM mart.order_summary WHERE label_eligible GROUP BY 1 ORDER BY 1""",
        )
        ctx["by_distance"] = _q(
            conn,
            """
            WITH d AS (SELECT f.max_seller_distance_km AS km, l.is_late
                       FROM feature.order_features f JOIN evaluation.order_labels l USING (order_id)
                       WHERE l.label_eligible AND f.max_seller_distance_km IS NOT NULL)
            SELECT ntile, min(km) AS lo, max(km) AS hi, count(*) AS n, count(*) FILTER (WHERE is_late) AS late
            FROM (SELECT km, is_late, ntile(10) OVER (ORDER BY km) AS ntile FROM d) x GROUP BY 1 ORDER BY 1""",
        )
        ctx["fanout"] = (
            _q(
                conn,
                """
            SELECT (SELECT sum(price_brl) FROM core.order_items) AS true_item_value,
                   (SELECT sum(i.price_brl) FROM core.order_items i JOIN core.payments p USING (order_id)
                        JOIN core.reviews r USING (order_id)) AS naive_item_value,
                   (SELECT count(*) FROM core.order_items i JOIN core.payments p USING (order_id)
                        JOIN core.reviews r USING (order_id)) AS naive_rows,
                   (SELECT count(*) FROM core.order_items) AS item_rows,
                   (SELECT count(*) FROM (SELECT order_id FROM core.order_items GROUP BY 1 HAVING count(*) > 1) x) AS multi_item_orders,
                   (SELECT count(*) FROM (SELECT order_id FROM core.payments GROUP BY 1 HAVING count(*) > 1) x) AS multi_payment_orders,
                   (SELECT count(*) FROM (SELECT order_id FROM core.reviews GROUP BY 1 HAVING count(*) > 1) x) AS multi_review_orders,
                   (SELECT count(*) FROM mart.order_summary WHERE n_distinct_sellers > 1) AS multi_seller_orders,
                   (SELECT count(DISTINCT customer_unique_id) FROM core.customers) AS unique_customers""",
            )
            .iloc[0]
            .to_dict()
        )
        ctx["assertions"] = _q(
            conn,
            "SELECT layer, check_name, severity, violation_count FROM quality.assertion_results",
        )
        ctx["reviews"] = _q(
            conn,
            """
            SELECT is_late, count(*) AS n, avg(review_score_mean)::float AS mean_score,
                   avg((review_score_mean <= 2)::int)::float AS share_low
            FROM mart.order_summary WHERE label_eligible AND n_reviews > 0 GROUP BY 1""",
        ).set_index("is_late")
        ctx["regions"] = _q(
            conn,
            """
            SELECT customer_region AS region, count(*) FILTER (WHERE label_eligible) AS n,
                   count(*) FILTER (WHERE is_late) AS late
            FROM mart.order_summary GROUP BY 1""",
        ).set_index("region")
        ctx["block_late"] = {
            b.name: _q(
                conn,
                """SELECT count(*) FILTER (WHERE scoring_eligible) AS scorable,
                                       count(*) FILTER (WHERE label_eligible) AS labelled,
                                       count(*) FILTER (WHERE is_late) AS late
                                FROM evaluation.order_labels WHERE prediction_at >= %s AND prediction_at < %s""",
                (b.start, b.end),
            )
            .iloc[0]
            .to_dict()
            for b in ctx["splits"].blocks
        }
        preds = _q(
            conn,
            """
            SELECT p.model_name, p.order_id, p.prediction_date, p.score_raw, l.label_eligible, l.is_late
            FROM evaluation.predictions p JOIN evaluation.order_labels l USING (order_id)
            WHERE p.run_id = %s AND p.block = 'test'""",
            (run_id,),
        )
        ctx["test_window"] = (test.start, test.end)
    preds["y"] = preds["is_late"].fillna(False).astype(int)
    preds["label_eligible"] = preds["label_eligible"].astype(bool)
    ctx["preds"] = preds
    return ctx


def metric(ctx, model: str, name: str, variant: str = "raw") -> pd.Series:
    m = ctx["metrics"]
    return m[(m.model_name == model) & (m.metric == name) & (m.variant == variant)].iloc[0]


# ---------------------------------------------------------------- derived curves
def topq_curves(ctx, qs: np.ndarray) -> pd.DataFrame:
    rows = []
    lab = ctx["preds"][ctx["preds"].label_eligible]
    for model in MODELS:
        g = lab[lab.model_name == model]
        for q in qs:
            r = top_q(g.y.to_numpy(), g.score_raw.to_numpy(), g.order_id.to_numpy(), float(q))
            rows.append(
                {
                    "model": model,
                    "q": q,
                    "precision": r.precision,
                    "recall": r.recall,
                    "lift": r.lift,
                }
            )
    return pd.DataFrame(rows)


def capacity_curves(ctx, qs: np.ndarray) -> pd.DataFrame:
    rows = []
    for model in (
        "constant_prevalence",
        "seller_history_baseline",
        ctx["summary"]["selected_model"],
    ):
        g = ctx["preds"][ctx["preds"].model_name == model]
        for q in qs:
            s = summarise_capacity(daily_capacity(g, "score_raw", float(q)))
            rows.append({"model": model, "q": q, **s})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- figures
def fig_timeline(ctx) -> None:
    cov = ctx["coverage"].copy()
    cov["month"] = pd.to_datetime(cov["approval_month"])
    cov = cov[cov.month >= "2016-10-01"]
    min_support = project_config()["analysis"]["min_support_for_ranking"]
    fig, (a1, a2) = plt.subplots(
        2, 1, figsize=(FULL_W, 2.6), sharex=True, gridspec_kw={"height_ratios": [1, 1]}
    )
    for ax in (a1, a2):
        for b in ctx["splits"].blocks:
            ax.axvspan(b.start, b.end, color=BLOCK_SHADES[b.name], zorder=0, lw=0)
        ax.grid(axis="y")
        ax.set_axisbelow(True)
    a1.bar(
        cov.month,
        cov.labelled,
        width=22,
        color="#2a78d6",
        zorder=2,
        label="scorable, outcome observed",
    )
    a1.bar(
        cov.month,
        cov.scorable - cov.labelled,
        bottom=cov.labelled,
        width=22,
        color="#b9cdea",
        zorder=3,
        label="scorable, no eligible outcome",
    )
    a1.set_ylabel("orders / month")
    a1.legend(loc="upper left", ncol=1, fontsize=6.5)
    trans = matplotlib.transforms.blended_transform_factory(a1.transData, a1.transAxes)
    for b in ctx["splits"].blocks:
        mid = b.start + (b.end - b.start) / 2
        y = 1.13 if b.name == "calibration" else 1.03  # stagger the two narrow middle blocks
        a1.text(
            mid,
            y,
            b.name,
            ha="center",
            va="bottom",
            fontsize=6.8,
            color=INK,
            style="italic",
            transform=trans,
        )
    rate = cov.late_rate.where(cov.labelled >= min_support)
    a2.plot(cov.month, rate, color="#2a78d6", lw=1.6, marker="o", ms=2.8, zorder=3)
    a2.set_ylabel("late rate")
    _pct_axis(a2)
    a2.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %Y"))
    fig.align_ylabels()
    _save(fig, "timeline")


def fig_diagnostic(ctx) -> None:
    st = ctx["by_state"].dropna(subset=["state"]).copy()
    st = st[st.n >= 30]
    st["rate"] = st.late / st.n
    st[["lo", "hi"]] = [wilson(int(k), int(n)) for k, n in zip(st.late, st.n, strict=True)]
    st = st.sort_values("rate")
    overall = st.late.sum() / st.n.sum()
    region_color = {
        "Southeast": "#2a78d6",
        "South": "#1baf7a",
        "Northeast": "#eb6834",
        "Central-West": "#eda100",
        "North": "#6250d6",
    }
    fig, axes = plt.subplots(
        1, 3, figsize=(FULL_W, 2.85), gridspec_kw={"width_ratios": [1.15, 1, 1]}
    )
    ax = axes[0]
    y = np.arange(len(st))
    ax.errorbar(
        st.rate,
        y,
        xerr=[st.rate - st.lo, st.hi - st.rate],
        fmt="none",
        ecolor="#bbbbbb",
        lw=0.8,
        zorder=1,
    )
    ax.scatter(st.rate, y, c=[region_color.get(r, MUTED) for r in st.region], s=12, zorder=2)
    ax.axvline(overall, color=MUTED, lw=0.7, ls="-")
    ax.set_yticks(y, [f"{s} ({n:,})" for s, n in zip(st.state, st.n, strict=True)], fontsize=5.6)
    _pct_axis(ax, "x")
    ax.set_title("(a) By customer state (95% Wilson CI)", loc="left")
    ax.grid(axis="x")
    handles = [
        matplotlib.lines.Line2D([], [], marker="o", ls="", color=c, ms=3.5, label=r)
        for r, c in region_color.items()
    ]
    ax.legend(handles=handles, loc="lower right", fontsize=5.6, handletextpad=0.1)

    pr = ctx["by_promise"]
    pr = pr[pr.n >= 200]
    ax = axes[1]
    ax.bar(np.arange(len(pr)), pr.late / pr.n, color="#2a78d6", width=0.75)
    ax.set_xticks(
        np.arange(len(pr)),
        [f"{int(a)}-{int(b)}" for a, b in zip(pr.lo, pr.hi, strict=True)],
        rotation=45,
        ha="right",
    )
    _pct_axis(ax)
    ax.set_xlabel("promised lead time (days)")
    ax.set_title("(b) By promised lead time", loc="left")
    ax.grid(axis="y")
    ax.set_axisbelow(True)

    di = ctx["by_distance"]
    ax = axes[2]
    ax.bar(np.arange(len(di)), di.late / di.n, color="#2a78d6", width=0.75)
    ax.set_xticks(
        np.arange(len(di)),
        [f"{int(a)}-{int(b)}" for a, b in zip(di.lo, di.hi, strict=True)],
        rotation=45,
        ha="right",
    )
    _pct_axis(ax)
    ax.set_xlabel("max customer-seller distance (km, deciles)")
    ax.set_title("(c) By customer-seller distance", loc="left")
    ax.grid(axis="y")
    ax.set_axisbelow(True)
    fig.tight_layout(w_pad=1.2)
    _save(fig, "diagnostic")


def fig_ap(ctx) -> None:
    fig, (a1, a2) = plt.subplots(
        1, 2, figsize=(FULL_W, 1.9), gridspec_kw={"width_ratios": [1.1, 1]}
    )
    for i, model in enumerate(MODELS):
        r = metric(ctx, model, "average_precision")
        a1.errorbar(
            r.value,
            i,
            xerr=[[r.value - r.ci_low], [r.ci_high - r.value]],
            fmt="o",
            color=COLOR[model],
            ms=4,
            capsize=2.5,
            lw=1.1,
        )
        a1.annotate(
            f"{r.value:.3f}",
            (r.ci_high, i),
            xytext=(4, -2.5),
            textcoords="offset points",
            fontsize=6.5,
            color=MUTED,
        )
    prev = metric(ctx, "constant_prevalence", "prevalence").value
    a1.axvline(prev, color=MUTED, lw=0.6)
    a1.set_yticks(range(len(MODELS)), [LABEL[m] for m in MODELS])
    a1.set_xlabel("average precision (AP)")
    a1.set_title("(a) AP on the test block, 95% weekly block-bootstrap CI", loc="left")
    a1.grid(axis="x")
    diffs = ctx["summary"]["bootstrap"]["differences_vs_reference"]
    comps = [
        ("logistic_regression", "average_precision"),
        ("lightgbm", "average_precision"),
        ("logistic_regression", "precision_at_10"),
        ("lightgbm", "precision_at_10"),
    ]
    labels = [
        "LR $-$ rule, AP",
        "LightGBM $-$ rule, AP",
        "LR $-$ rule, P@10%",
        "LightGBM $-$ rule, P@10%",
    ]
    for i, (m, k) in enumerate(comps):
        d = diffs[f"{m}|{k}"]
        a2.errorbar(
            d["mean"],
            i,
            xerr=[[d["mean"] - d["ci_low"]], [d["ci_high"] - d["mean"]]],
            fmt="s",
            color=COLOR[m],
            ms=3.5,
            capsize=2.5,
            lw=1.1,
        )
    a2.axvline(0, color=INK, lw=0.7)
    a2.set_yticks(range(len(comps)), labels)
    a2.invert_yaxis()
    a2.set_xlabel("paired difference vs. seller-history rule")
    a2.set_title("(b) Paired differences (same resampled weeks)", loc="left")
    a2.grid(axis="x")
    fig.tight_layout(w_pad=1.5)
    _save(fig, "ap_ci")


def fig_topq(ctx, curves: pd.DataFrame) -> None:
    prev = metric(ctx, "constant_prevalence", "prevalence").value
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(FULL_W, 2.0))
    for model in MODELS:
        c = curves[curves.model == model]
        a1.plot(c.q, c.precision, color=COLOR[model], lw=1.4, label=LABEL[model])
        a2.plot(c.q, c.recall, color=COLOR[model], lw=1.4)
    a1.axhline(prev, color=MUTED, lw=0.6, ls=(0, (3, 2)))
    a1.text(0.30, prev, " prevalence", va="bottom", ha="right", fontsize=6.5, color=MUTED)
    a2.plot([0, 0.3], [0, 0.3], color=MUTED, lw=0.6, ls=(0, (3, 2)))
    a2.text(0.3, 0.3, "random ", va="top", ha="right", fontsize=6.5, color=MUTED)
    for ax, title, ylab in (
        (a1, "(a) Precision among the top q% of labelled test orders", "precision@q"),
        (a2, "(b) Share of late orders captured (recall@q)", "recall@q"),
    ):
        _pct_axis(ax)
        ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
        ax.set_xlabel("share of orders reviewed, q")
        ax.set_ylabel(ylab)
        ax.set_title(title, loc="left")
        ax.grid()
        for q in (0.05, 0.10, 0.20):
            ax.axvline(q, color=GRID, lw=0.6, zorder=0)
    a1.legend(loc="upper right")
    fig.tight_layout(w_pad=1.5)
    _save(fig, "topq")


def fig_calibration(ctx) -> None:
    sel = ctx["summary"]["selected_model"]
    b = ctx["bins"]
    fig, ax = plt.subplots(figsize=(COL_W, 2.3))
    for variant, color, label in (
        ("raw", "#9a9a9a", "raw score"),
        ("calibrated", COLOR[sel], "after sigmoid calibration"),
    ):
        d = b[(b.model_name == sel) & (b.variant == variant)]
        ax.plot(d.mean_score, d.observed_rate, marker="o", ms=3, lw=1.3, color=color, label=label)
    hi = b[b.model_name == sel].mean_score.max()
    ax.plot([0, hi], [0, hi], color=MUTED, lw=0.6, ls=(0, (3, 2)), label="perfect calibration")
    prev = metric(ctx, "constant_prevalence", "prevalence").value
    ax.axhline(prev, color=GRID, lw=0.8)
    _pct_axis(ax)
    _pct_axis(ax, "x")
    ax.set_xlabel("mean predicted probability (test deciles)")
    ax.set_ylabel("observed late rate")
    ax.set_ylim(0, max(0.12, b[b.model_name == sel].observed_rate.max() * 1.3))
    leg = ax.legend(
        loc="upper left", frameon=True, facecolor="white", edgecolor="white", framealpha=1
    )
    leg.set_zorder(5)
    ax.grid()
    _save(fig, "calibration")


def fig_capacity(ctx, cap: pd.DataFrame) -> None:
    sel = ctx["summary"]["selected_model"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(FULL_W, 2.0))
    for model in ("constant_prevalence", "seller_history_baseline", sel):
        c = cap[cap.model == model]
        a1.plot(
            c.effective_review_fraction,
            c.precision_among_known,
            color=COLOR[model],
            lw=1.4,
            label=LABEL[model],
        )
        a2.plot(
            c.effective_review_fraction,
            c.share_of_known_late_orders_captured,
            color=COLOR[model],
            lw=1.4,
        )
    known_rate = cap.late_rate_all_known.iloc[0]
    a1.axhline(known_rate, color=MUTED, lw=0.6, ls=(0, (3, 2)))
    a2.plot([0, 0.32], [0, 0.32], color=MUTED, lw=0.6, ls=(0, (3, 2)))
    for ax, title, ylab in (
        (a1, "(a) Late rate among reviewed orders (known outcomes)", "late rate in review queue"),
        (a2, "(b) Share of known late orders reviewed", "late orders captured"),
    ):
        _pct_axis(ax)
        ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
        ax.set_xlabel("effective share of daily approvals reviewed")
        ax.set_ylabel(ylab)
        ax.set_title(title, loc="left")
        ax.grid()
    a1.legend(loc="upper right")
    fig.tight_layout(w_pad=1.5)
    _save(fig, "capacity")


def fig_importance(ctx) -> None:
    imp = ctx["importance"].head(10).iloc[::-1]
    fig, ax = plt.subplots(figsize=(COL_W, 2.2))
    ax.barh(
        np.arange(len(imp)),
        imp.importance_mean,
        xerr=imp.importance_std,
        color="#2a78d6",
        height=0.65,
        error_kw={"lw": 0.7, "ecolor": MUTED},
    )
    ax.set_yticks(
        np.arange(len(imp)), list(imp.feature), fontsize=6.3, fontfamily="DejaVu Sans Mono"
    )
    ax.set_xlabel("drop in AP when the feature is permuted")
    ax.grid(axis="x")
    ax.set_axisbelow(True)
    _save(fig, "importance")


def fig_months(ctx) -> None:
    sel = ctx["summary"]["selected_model"]
    g = ctx["groups"]
    g = g[(g.group_type == "prediction_month") & g.model_name.isin(MODELS)]
    months = sorted(g.group_value.unique())
    fig, ax = plt.subplots(figsize=(COL_W, 1.9))
    width = 0.2
    for i, model in enumerate(MODELS):
        d = g[g.model_name == model].set_index("group_value").reindex(months)
        ax.bar(
            np.arange(len(months)) + (i - 1.5) * width,
            d.average_precision,
            width=width * 0.92,
            color=COLOR[model],
            label=LABEL[model],
        )
    prev = g[g.model_name == sel].set_index("group_value").reindex(months)
    ax.scatter(
        np.arange(len(months)),
        prev.prevalence,
        marker="_",
        s=260,
        color=INK,
        zorder=3,
        label="prevalence",
    )
    ax.set_xticks(
        np.arange(len(months)),
        [
            f"{m}\n(n={int(n):,}, late={int(p)})"
            for m, n, p in zip(months, prev.n, prev.positives, strict=True)
        ],
        fontsize=6.2,
    )
    ax.set_ylabel("AP")
    ax.grid(axis="y")
    ax.set_axisbelow(True)
    ax.legend(
        ncol=3,
        fontsize=5.8,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.0),
        handlelength=1.2,
        columnspacing=0.8,
    )
    _save(fig, "months")


# ---------------------------------------------------------------- LaTeX outputs
def _tex(s: str) -> str:
    return (
        str(s)
        .replace("\\", r"\textbackslash{}")
        .replace("_", r"\_")
        .replace("%", r"\%")
        .replace("&", r"\&")
        .replace("#", r"\#")
    )


def _int(v) -> str:
    return f"{int(v):,}".replace(",", "{,}")


def _f(v, d=3) -> str:
    return "--" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{float(v):.{d}f}"


def _p(v, d=1) -> str:
    return (
        "--"
        if v is None or (isinstance(v, float) and math.isnan(v))
        else f"{100 * float(v):.{d}f}\\%"
    )


def write_tables(ctx, curves: pd.DataFrame, cap: pd.DataFrame) -> dict[str, str]:
    sel = ctx["summary"]["selected_model"]
    tables = {}
    rows = []
    for model in MODELS:
        ap, auc = metric(ctx, model, "average_precision"), metric(ctx, model, "roc_auc")
        p10, l10, r10 = (
            metric(ctx, model, "precision_at_10"),
            metric(ctx, model, "lift_at_10"),
            metric(ctx, model, "recall_at_10"),
        )
        br, bc = metric(ctx, model, "brier"), metric(ctx, model, "brier", "calibrated")
        name = LABEL[model] + (r"$^\star$" if model == sel else "")
        rows.append(
            f"{name} & {_f(ap.value)} & [{_f(ap.ci_low)}, {_f(ap.ci_high)}] & {_f(auc.value)} & {_p(p10.value)} & "
            f"{_p(r10.value)} & {_f(l10.value, 2)} & {_f(br.value, 4)} & {_f(bc.value, 4)} \\\\"
        )
    tables["models"] = "\n".join(rows)

    rows = []
    m = ctx["manifest"]
    fit_key = {
        "train": "fit_step1_train",
        "selection": "eval_step2_selection",
        "calibration": "fit_step4_calibration",
    }
    for b in ctx["splits"].blocks:
        bl = ctx["block_late"][b.name]
        if b.name in fit_key:
            fc = m["fit_counts"][fit_key[b.name]]
            known = f"{_int(fc['n'])} & {_int(fc['positives'])}"
        else:
            known = r"\multicolumn{2}{c}{all observed}"
        rows.append(
            f"{b.name.capitalize()} & {b.start:%Y-%m-%d} & {b.end:%Y-%m-%d} & {_int(bl['scorable'])} & {_int(bl['labelled'])} & "
            f"{_int(bl['late'])} & {_p(bl['late'] / bl['labelled'])} & {known} \\\\"
        )
    tables["blocks"] = "\n".join(rows)

    rows = []
    for _, r in ctx["manifest_rows"].iterrows():
        short = r.file_name.replace("olist_", "").replace("_dataset", "").replace(".csv", "")
        short = short.replace("product_category_name_translation", "category_translation")
        rows.append(
            f"\\texttt{{{_tex(short)}}} & {_int(r.row_count)} & "
            f"{r.file_size_bytes / 1e6:.1f} & \\texttt{{{r.sha[:8]}}} \\\\"
        )
    tables["files"] = "\n".join(rows)

    rows = []
    for model in ("constant_prevalence", "seller_history_baseline", sel):
        for q in project_config()["analysis"]["capacity_fractions"]:
            c = ctx["summary"]["capacity"][f"{model}|{q}"]
            short = {
                "constant_prevalence": "Constant",
                "seller_history_baseline": "Seller rule",
            }.get(model, LABEL[model])
            rows.append(
                f"{short} & {int(q * 100)}\\% & {_p(c['effective_review_fraction'])} & {_p(c['known_outcome_coverage_in_selection'])} & "
                f"{_p(c['precision_among_known'])} & {_f(c['lift_vs_known_late_rate'], 2)} & {_p(c['share_of_known_late_orders_captured'])} \\\\"
            )
        rows.append(r"\addlinespace")
    tables["capacity"] = "\n".join(rows[:-1])

    rows = []
    for case in ctx["bench"]["cases"]:
        for v in case["variants"]:
            label = (
                v["label"].replace("before: ", "").replace("after: ", "").replace("rewrite: ", "")
            )
            kind = v["label"].split(":")[0]
            t = (
                "$>$300\\,000 (timeout)"
                if v["status"] != "ok"
                else f"{v['server_ms_median']:,.1f}".replace(",", "{,}")
            )
            setup = sum(s["build_ms"] for s in v["setup"])
            rows.append(
                f"{case['key']} & {kind} & {_tex(label)} & {t} & {'--' if not v['setup'] else f'{setup:.0f}'} \\\\"
            )
        rows.append(r"\addlinespace")
    tables["bench"] = "\n".join(rows[:-1])

    sel_tbl = ctx["selection"]
    best = sel_tbl.loc[sel_tbl.groupby("model").selection_ap.idxmax()]
    rows = []
    for model in MODELS:
        r = best[best.model == model].iloc[0]
        n_cfg = int((sel_tbl.model == model).sum())
        params = json.loads(r.params)
        ptxt = ", ".join(f"{k}={v}" for k, v in params.items()) if params else "--"
        rows.append(f"{LABEL[model]} & {n_cfg} & {_tex(ptxt)} & {_f(r.selection_ap)} \\\\")
    tables["selection"] = "\n".join(rows)

    for name, body in tables.items():
        (GEN_DIR / f"table_{name}.tex").write_text(body + "\n")
    return tables


def write_numbers(ctx, curves: pd.DataFrame, cap: pd.DataFrame) -> None:
    s, m, sel = ctx["summary"], ctx["manifest"], ctx["summary"]["selected_model"]
    pop, fo = ctx["populations"], ctx["fanout"]
    bench = {c["key"]: c for c in ctx["bench"]["cases"]}
    q1 = {v["label"].split(":")[0]: v for v in bench["Q1"]["variants"]}
    q2 = {v["label"].split(":")[0]: v for v in bench["Q2"]["variants"]}
    q3 = {v["label"].split(":")[0]: v for v in bench["Q3"]["variants"]}
    dash = {d["callback"].split(" ")[0]: d for d in ctx["bench"]["dash_callbacks"]}
    crit = ctx["assertions"]
    months = ctx["groups"][
        (ctx["groups"].group_type == "prediction_month") & (ctx["groups"].model_name == sel)
    ].set_index("group_value")
    cap10 = s["capacity"][f"{sel}|0.1"]
    capb10 = s["capacity"]["seller_history_baseline|0.1"]
    d_ap = s["bootstrap"]["differences_vs_reference"][f"{sel}|average_precision"]
    d_p10 = s["bootstrap"]["differences_vs_reference"][f"{sel}|precision_at_10"]
    d_ap_lgb = s["bootstrap"]["differences_vs_reference"]["lightgbm|average_precision"]
    abl = ctx["ablations"].set_index("model")
    cal = m["fit_counts"]["fit_step4_calibration"]
    sel_late_total = ctx["block_late"]["selection"]["late"]
    lr_params = m["chosen_params"]["logistic_regression"]
    sel_ap = ctx["selection"].groupby("model").selection_ap.max()
    prev = metric(ctx, "constant_prevalence", "prevalence").value
    cap20 = s["capacity"][f"{sel}|0.2"]
    promise = ctx["by_promise"][ctx["by_promise"].n >= 200].reset_index(drop=True)
    dist = ctx["by_distance"]
    cov = ctx["coverage"].set_index(ctx["coverage"]["approval_month"].astype(str).str[:7])

    blocks_cov = cov[
        (cov.index >= f"{ctx['splits']['train'].start:%Y-%m}")
        & (cov.index < f"{ctx['splits']['test'].end:%Y-%m}")
    ]
    min_cov = float(blocks_cov.label_coverage.min())
    min_cov_month = pd.Timestamp(blocks_cov.label_coverage.idxmin() + "-01").strftime("%B %Y")

    def cov_rate(month: str) -> float:
        return float(cov.loc[month].late_rate)

    def warn(name: str) -> int:
        a = ctx["assertions"]
        return int(a[a.check_name == name].violation_count.iloc[0])

    vals = {
        "RunId": _tex(s["run_id"]),
        "NOrders": _int(pop["orders"]),
        "NApproved": _int(pop["approved"]),
        "NScorable": _int(pop["scorable"]),
        "NLabelled": _int(pop["labelled"]),
        "NLate": _int(pop["late"]),
        "LateRate": _p(pop["late"] / pop["labelled"]),
        "NScorableCanceled": _int(pop["scorable_canceled"]),
        "NScorableOther": _int(pop["scorable_other_unlabelled"]),
        "NUniqueCustomers": _int(fo["unique_customers"]),
        "NItems": _int(fo["item_rows"]),
        "NMultiItem": _int(fo["multi_item_orders"]),
        "NMultiPayment": _int(fo["multi_payment_orders"]),
        "NMultiReview": _int(fo["multi_review_orders"]),
        "NMultiSeller": _int(fo["multi_seller_orders"]),
        "TrueItemValue": f"{float(fo['true_item_value']) / 1e6:.2f}",
        "NaiveItemValue": f"{float(fo['naive_item_value']) / 1e6:.2f}",
        "NaiveRows": _int(fo["naive_rows"]),
        "FanoutPct": f"{100 * (float(fo['naive_item_value']) / float(fo['true_item_value']) - 1):.1f}\\%",
        "NCritical": str(int((crit.severity == "critical").sum())),
        "NWarningsFired": str(
            int(((crit.severity == "warning") & (crit.violation_count > 0)).sum())
        ),
        "NChecks": str(len(crit)),
        "TestStart": f"{ctx['splits']['test'].start:%B %-d, %Y}",
        "TestEnd": f"{ctx['splits']['test'].end:%B %-d, %Y}",
        "TestScorable": _int(s["test_scorable_orders"]),
        "TestLabelled": _int(s["test_labelled_orders"]),
        "TestLate": _int(s["test_late_orders"]),
        "TestPrev": _p(prev),
        "TestCoverage": _p(s["label_coverage"]),
        "NWeeks": str(s["bootstrap"]["n_weeks"]),
        "NBoot": str(s["bootstrap"]["n_resamples"]),
        "SelModel": LABEL[sel].lower(),
        "SelC": str(lr_params.get("C")),
        "SelCW": _tex(str(lr_params.get("class_weight"))),
        "SelAPLR": _f(sel_ap["logistic_regression"]),
        "SelAPLGB": _f(sel_ap["lightgbm"]),
        "SelAPRule": _f(sel_ap["seller_history_baseline"]),
        "SelAPConst": _f(sel_ap["constant_prevalence"]),
        "APSel": _f(metric(ctx, sel, "average_precision").value),
        "APRule": _f(metric(ctx, "seller_history_baseline", "average_precision").value),
        "APConst": _f(metric(ctx, "constant_prevalence", "average_precision").value),
        "APLGB": _f(metric(ctx, "lightgbm", "average_precision").value),
        "APSelLo": _f(metric(ctx, sel, "average_precision").ci_low),
        "APSelHi": _f(metric(ctx, sel, "average_precision").ci_high),
        "AUCSel": _f(metric(ctx, sel, "roc_auc").value),
        "AUCLGB": _f(metric(ctx, "lightgbm", "roc_auc").value),
        "PtenSel": _p(metric(ctx, sel, "precision_at_10").value),
        "PtenRule": _p(metric(ctx, "seller_history_baseline", "precision_at_10").value),
        "PtenLGB": _p(metric(ctx, "lightgbm", "precision_at_10").value),
        "LiftSel": _f(metric(ctx, sel, "lift_at_10").value, 2),
        "DiffAP": f"{d_ap['mean']:+.3f}",
        "DiffAPLo": f"{d_ap['ci_low']:+.3f}",
        "DiffAPHi": f"{d_ap['ci_high']:+.3f}",
        "DiffPten": f"{100 * d_p10['mean']:+.1f}",
        "DiffPtenLo": f"{100 * d_p10['ci_low']:+.1f}",
        "DiffPtenHi": f"{100 * d_p10['ci_high']:+.1f}",
        "DiffAPLGB": f"{d_ap_lgb['mean']:+.3f}",
        "DiffAPLGBLo": f"{d_ap_lgb['ci_low']:+.3f}",
        "DiffAPLGBHi": f"{d_ap_lgb['ci_high']:+.3f}",
        "BrierRaw": _f(metric(ctx, sel, "brier").value, 4),
        "BrierCal": _f(metric(ctx, sel, "brier", "calibrated").value, 4),
        "BrierConst": _f(metric(ctx, "constant_prevalence", "brier").value, 4),
        "CalPrev": _p(cal["positives"] / cal["n"]),
        "CalN": _int(cal["n"]),
        "CalSlope": _f(m["calibration"][sel]["slope"], 2),
        "CalIntercept": _f(m["calibration"][sel]["intercept"], 2),
        "SelLateKnown": _int(m["fit_counts"]["eval_step2_selection"]["positives"]),
        "SelLateTotal": _int(sel_late_total),
        "SelKnownN": _int(m["fit_counts"]["eval_step2_selection"]["n"]),
        "TrainFitN": _int(m["fit_counts"]["fit_step1_train"]["n"]),
        "TrainFitPos": _int(m["fit_counts"]["fit_step1_train"]["positives"]),
        "RefitN": _int(m["fit_counts"]["fit_step3_train_selection"]["n"]),
        "RefitPos": _int(m["fit_counts"]["fit_step3_train_selection"]["positives"]),
        "CensTrain": _int(m["eligible_but_not_yet_delivered_at_cutoff"]["fit_step1_train"]),
        "CensSel": _int(m["eligible_but_not_yet_delivered_at_cutoff"]["eval_step2_selection"]),
        "CensCal": _int(m["eligible_but_not_yet_delivered_at_cutoff"]["fit_step4_calibration"]),
        "APMay": _f(months.loc["2018-05"].average_precision),
        "PrevMay": _p(months.loc["2018-05"].prevalence),
        "APJun": _f(months.loc["2018-06"].average_precision),
        "PrevJun": _p(months.loc["2018-06"].prevalence),
        "APJul": _f(months.loc["2018-07"].average_precision),
        "PrevJul": _p(months.loc["2018-07"].prevalence),
        "AblLR": _f(abl.loc["logistic_regression__no_catalog_static"].average_precision),
        "AblLGB": _f(abl.loc["lightgbm__no_catalog_static"].average_precision),
        "CapEff": _p(cap10["effective_review_fraction"]),
        "CapPrec": _p(cap10["precision_among_known"]),
        "CapPrecRule": _p(capb10["precision_among_known"]),
        "CapKnownRate": _p(cap10["late_rate_all_known"]),
        "CapCaptured": _p(cap10["share_of_known_late_orders_captured"]),
        "CapCapturedRule": _p(capb10["share_of_known_late_orders_captured"]),
        "CapNoOutcome": _p(1 - cap10["known_outcome_coverage_in_selection"]),
        "CapLift": _f(cap10["lift_vs_known_late_rate"], 2),
        "CapSelected": _int(cap10["orders_selected"]),
        "CapDays": str(cap10["days"]),
        "CapMedianDaily": _int(cap10["median_daily_scorable"]),
        "CapCapturedTwenty": _p(cap20["share_of_known_late_orders_captured"]),
        "QOneBefore": "300",
        "QOneAfter": f"{q1['after']['server_ms_median'] / 1000:.1f}",
        "QOneWindow": f"{q1['rewrite']['server_ms_median'] / 1000:.1f}",
        "QOneIdxMs": f"{sum(x['build_ms'] for x in q1['after']['setup']):.0f}",
        "QTwoBefore": f"{q2['before']['server_ms_median']:.1f}",
        "QTwoAfter": f"{q2['after']['server_ms_median']:.1f}",
        "QThreeBefore": f"{q3['before']['server_ms_median']:.1f}",
        "QThreeAfter": f"{q3['after']['server_ms_median']:.2f}",
        "NPredictions": _int(ctx["bench"]["input_rows"]["predictions"]),
        "NTargets": _int(ctx["bench"]["input_rows"]["targets"]),
        "NOutcomeEvents": _int(ctx["bench"]["input_rows"]["seller_outcome_events"]),
        "DashOverviewCold": f"{dash['overview']['cold_cache_ms_median']:.0f}",
        "DashOverviewWarm": f"{dash['overview']['warm_cache_ms_median']:.0f}",
        "DashReviewCold": f"{dash['risk']['cold_cache_ms_median']:.0f}",
        "DashReviewWarm": f"{dash['risk']['warm_cache_ms_median']:.1f}",
        "PGVersion": _tex(
            ctx["bench"]["environment"]["postgres_version"].split(" on ")[0].split(" (")[0]
        ),
        "SplitSha": ctx["splits"].sha256[:12],
        "FeatureSha": m["feature_sha256"][:12],
        "PrecFiveSel": _p(
            curves[(curves.model == sel) & np.isclose(curves.q, 0.05)].precision.iloc[0]
        ),
        "ReviewLate": _f(ctx["reviews"].loc[True].mean_score, 2),
        "ReviewOnTime": _f(ctx["reviews"].loc[False].mean_score, 2),
        "LowLate": _p(ctx["reviews"].loc[True].share_low, 0),
        "LowOnTime": _p(ctx["reviews"].loc[False].share_low, 0),
        **{
            f"Rate{k.replace('-', '')}": _p(r.late / r.n)
            for k, r in ctx["regions"].dropna().iterrows()
            if r.n
        },
        "PromiseShortRate": _p(promise.iloc[0].late / promise.iloc[0].n),
        "PromiseShortN": _int(promise.iloc[0].n),
        "PromiseShortLo": str(int(promise.iloc[0].lo)),
        "PromiseShortHi": str(int(promise.iloc[0].hi)),
        "PromiseLongRate": _p(promise.iloc[-1].late / promise.iloc[-1].n),
        "PromiseLongLo": str(int(promise.iloc[-1].lo)),
        "DistFirst": _p(dist.iloc[0].late / dist.iloc[0].n),
        "DistLast": _p(dist.iloc[-1].late / dist.iloc[-1].n),
        "DistLastLo": _int(dist.iloc[-1].lo),
        "LateNov": _p(cov_rate("2017-11")),
        "LateFeb": _p(cov_rate("2018-02")),
        "LateMar": _p(cov_rate("2018-03")),
        "LateJun": _p(cov_rate("2018-06")),
        "WarnNoItems": _int(warn("orders_without_items")),
        "WarnNoApproval": _int(warn("orders_missing_approval")),
        "WarnCarrier": _int(warn("carrier_before_approval")),
        "WarnNoCategory": _int(warn("products_missing_category")),
        "WarnGap": _int(warn("orders_value_gap")),
        "WarnNoCentroid": _int(warn("prefixes_without_centroid")),
        "WarnNoDistance": _int(warn("orders_without_distance")),
        "NFeatures": str(len(m["features"])),
        "NCandidates": str(len(ctx["selection"])),
        "MinCoverage": _p(min_cov),
        "MinCoverageMonth": min_cov_month,
        "RiskRatio": f"{ctx['block_late']['calibration']['late'] / ctx['block_late']['calibration']['labelled'] / prev:.1f}",
        "NNotScorable": _int(pop["orders"] - pop["scorable"]),
        "NNoOutcome": _int(pop["scorable"] - pop["labelled"]),
        "NChecksTransform": str(int((crit.layer == "transform").sum())),
        "NChecksFeatures": str(int((crit.layer == "features").sum())),
        "NCritTransform": str(
            int(((crit.layer == "transform") & (crit.severity == "critical")).sum())
        ),
        "NCritFeatures": str(
            int(((crit.layer == "features") & (crit.severity == "critical")).sum())
        ),
    }
    lines = ["% Generated by delivery_risk.final_report — do not edit by hand."]
    for k, v in vals.items():
        assert k.isalpha(), k
        lines.append(f"\\newcommand{{\\{k}}}{{{v}}}")
    (GEN_DIR / "numbers.tex").write_text("\n".join(lines) + "\n")


def build(mode: str = "real", compile_pdf: bool = True) -> Path:
    _style()
    for d in (GEN_DIR, FIG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    ctx = collect(mode)
    qs = np.round(np.arange(0.01, 0.301, 0.01), 2)
    curves = topq_curves(ctx, qs)
    cap = capacity_curves(ctx, qs)
    fig_timeline(ctx)
    fig_diagnostic(ctx)
    fig_ap(ctx)
    fig_topq(ctx, curves)
    fig_calibration(ctx)
    fig_capacity(ctx, cap)
    fig_importance(ctx)
    fig_months(ctx)
    write_tables(ctx, curves, cap)
    write_numbers(ctx, curves, cap)
    shots = PROJECT_ROOT / "reports" / "screenshots" / mode
    from PIL import Image  # matplotlib dependency

    for name, height in (("overview_1440", 780), ("risk-review_1440", 1080)):
        if (shots / f"{name}.png").exists():
            with Image.open(shots / f"{name}.png") as im:
                im.crop((0, 0, im.width, min(height, im.height))).save(FIG_DIR / f"{name}_crop.png")
    pdf = FINAL_DIR / "report.pdf"
    if compile_pdf:
        if shutil.which("tectonic") is None:
            raise RuntimeError("tectonic is required to compile the report (brew install tectonic)")
        subprocess.run(["tectonic", "-X", "compile", "report.tex"], cwd=FINAL_DIR, check=True)
        log.info("Final report written to %s", pdf)
    return pdf
