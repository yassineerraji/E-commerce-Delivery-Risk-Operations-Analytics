"""Figures and the detailed evaluation report — generated from saved artifacts.

Every number comes from files written by `evaluate`/`quality` or from the published marts.
Conclusions are conditional on the measured intervals; nothing is hard-coded as a result.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from delivery_risk.config import PROJECT_ROOT, mode_config, project_config  # noqa: E402
from delivery_risk.db import connect, fetch_all  # noqa: E402
from delivery_risk.mdtable import md_table  # noqa: E402

log = logging.getLogger(__name__)

LABELS = {
    "constant_prevalence": "Constant (prevalence)",
    "seller_history_baseline": "Seller-history baseline",
    "logistic_regression": "Logistic regression",
    "lightgbm": "LightGBM",
}
COLORS = {
    "logistic_regression": "#2a78d6",
    "lightgbm": "#eb6834",
    "seller_history_baseline": "#1baf7a",
    "constant_prevalence": "#8a8984",
}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def _style(ax, title: str, ylabel: str | None = None) -> None:
    ax.set_title(title, loc="left", fontsize=11, color=INK)
    if ylabel:
        ax.set_ylabel(ylabel, color=MUTED, fontsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=8)


def _pct(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{100 * float(v):.1f}%"


def _num(v, d=3) -> str:
    return "—" if v is None or pd.isna(v) else f"{float(v):.{d}f}"


class Artifacts:
    def __init__(self, mode: str):
        self.mode = mode
        self.eval_dir = mode_config(mode).report_dir("evaluation")
        self.dq_dir = mode_config(mode).report_dir("data_quality")
        self.fig_dir = mode_config(mode).report_dir("figures")
        summary = self.eval_dir / "evaluation_summary.json"
        if not summary.exists():
            raise FileNotFoundError(
                f"No evaluation artifacts for mode {mode!r}; run `make evaluate` first."
            )
        self.summary = json.loads(summary.read_text())
        self.manifest = json.loads((self.eval_dir / "run_manifest.json").read_text())
        self.metrics = pd.read_csv(self.eval_dir / "test_metrics.csv")
        self.topq = pd.read_csv(self.eval_dir / "test_top_q.csv")
        self.bins = pd.read_csv(self.eval_dir / "test_calibration_bins.csv")
        self.groups = pd.read_csv(self.eval_dir / "test_metrics_by_group.csv")
        self.ablations = pd.read_csv(self.eval_dir / "test_ablations.csv")
        self.selection = pd.read_csv(self.eval_dir / "selection_results.csv")
        self.coverage = (
            pd.read_csv(self.dq_dir / "coverage_monthly.csv")
            if (self.dq_dir / "coverage_monthly.csv").exists()
            else None
        )
        self.importance = pd.read_csv(
            self.eval_dir / "permutation_importance_calibration_block.csv"
        )
        self.coefficients = pd.read_csv(self.eval_dir / "logistic_coefficients.csv")

    def metric(self, model: str, metric: str, variant: str = "raw") -> pd.Series:
        m = self.metrics[
            (self.metrics.model_name == model)
            & (self.metrics.metric == metric)
            & (self.metrics.variant == variant)
        ]
        return m.iloc[0]

    def diff(self, model: str, metric: str) -> dict:
        return self.summary["bootstrap"]["differences_vs_reference"][f"{model}|{metric}"]

    def capacity(self, model: str, q: float) -> dict:
        return self.summary["capacity"][f"{model}|{q}"]


def _operations(mode: str) -> dict:
    with connect(mode) as conn:
        _, overall = fetch_all(
            conn,
            """SELECT count(*), count(*) FILTER (WHERE label_eligible), count(*) FILTER (WHERE is_late),
                                               min(purchase_at)::date, max(purchase_at)::date FROM mart.order_summary""",
        )
        monthly_cols, monthly = fetch_all(
            conn,
            """
            SELECT purchase_month, sum(orders)::int, sum(eligible_delivered_orders)::int, sum(late_orders)::int,
                   sum(late_orders)::float / NULLIF(sum(eligible_delivered_orders), 0)
            FROM mart.monthly_operations GROUP BY 1 ORDER BY 1""",
        )
        _, regions = fetch_all(
            conn,
            """
            SELECT customer_region, sum(eligible_delivered_orders)::int, sum(late_orders)::int,
                   sum(late_orders)::float / NULLIF(sum(eligible_delivered_orders), 0)
            FROM mart.monthly_operations GROUP BY 1 ORDER BY 2 DESC""",
        )
        _, sellers = fetch_all(
            conn,
            """
            SELECT is_multi_seller_order, count(DISTINCT order_id) FILTER (WHERE label_eligible),
                   count(DISTINCT order_id) FILTER (WHERE is_late)
            FROM mart.seller_order_exposure GROUP BY 1 ORDER BY 1""",
        )
        _, promise = fetch_all(
            conn,
            """
            WITH q AS (SELECT ntile(4) OVER (ORDER BY promised_lead_days) AS quartile, promised_lead_days, is_late
                       FROM mart.order_summary WHERE label_eligible)
            SELECT quartile, min(promised_lead_days), max(promised_lead_days), count(*), avg(is_late::int)::float
            FROM q GROUP BY 1 ORDER BY 1""",
        )
    return {
        "overall": overall[0],
        "monthly": pd.DataFrame(
            monthly, columns=["month", "orders", "eligible", "late", "late_rate"]
        ),
        "regions": regions,
        "multi_seller": sellers,
        "promise_quartiles": promise,
    }


# ------------------------------------------------------------------ figures
def make_figures(a: Artifacts, ops: dict) -> dict[str, str]:
    figs = {}
    m = ops["monthly"]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 5.2), sharex=True)
    ax1.bar(pd.to_datetime(m.month), m.orders, width=20, color="#2a78d6")
    _style(ax1, "Orders per purchase month", "orders")
    min_support = project_config()["analysis"]["min_support_for_ranking"]
    supported = m.late_rate.where(m.eligible >= min_support)  # small months are not plotted
    ax2.plot(
        pd.to_datetime(m.month), supported, color="#2a78d6", linewidth=2, marker="o", markersize=4
    )
    _style(
        ax2,
        f"Late rate per purchase month (late / eligible delivered; months < {min_support} not shown)",
        "late rate",
    )
    ax2.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    fig.tight_layout()
    figs["operations"] = _save(fig, a.fig_dir / "monthly_operations.png")

    fig, ax = plt.subplots(figsize=(8, 3.6))
    models = list(LABELS)
    for i, model in enumerate(models):
        r = a.metric(model, "average_precision")
        ax.errorbar(
            r.value,
            i,
            xerr=[[r.value - r.ci_low], [r.ci_high - r.value]],
            fmt="o",
            color=COLORS[model],
            capsize=4,
            markersize=7,
        )
        ax.annotate(
            f"{r.value:.3f}",
            (r.ci_high, i),
            xytext=(6, -3),
            textcoords="offset points",
            fontsize=8,
            color=MUTED,
        )
    ax.set_yticks(range(len(models)), [LABELS[x] for x in models])
    prev = a.metric("constant_prevalence", "prevalence").value
    ax.axvline(prev, color=MUTED, linewidth=0.8)
    ax.annotate(
        "prevalence",
        (prev, len(models) - 0.5),
        xytext=(4, 0),
        textcoords="offset points",
        fontsize=8,
        color=MUTED,
    )
    _style(ax, "Average precision on the test block (95% weekly block-bootstrap interval)")
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    figs["ap"] = _save(fig, a.fig_dir / "average_precision_ci.png")

    fig, ax = plt.subplots(figsize=(8, 3.6))
    qs = sorted(a.topq.q.unique())
    width = 0.2
    for i, model in enumerate(models):
        t = (
            a.topq[(a.topq.model_name == model) & (a.topq.variant == "raw")]
            .set_index("q")
            .reindex(qs)
        )
        ax.bar(
            [j + (i - 1.5) * width for j in range(len(qs))],
            t.precision,
            width=width * 0.9,
            color=COLORS[model],
            label=LABELS[model],
        )
    ax.axhline(
        a.metric("constant_prevalence", "prevalence").value,
        color=MUTED,
        linewidth=0.8,
        label="prevalence",
    )
    ax.set_xticks(range(len(qs)), [f"top {int(q * 100)}%" for q in qs])
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    _style(ax, "Precision among the top q% of labelled test orders", "precision")
    ax.legend(frameon=False, fontsize=8, ncol=3)
    fig.tight_layout()
    figs["topq"] = _save(fig, a.fig_dir / "top_q_precision.png")

    sel = a.summary["selected_model"]
    fig, ax = plt.subplots(figsize=(5, 4))
    for variant, color in (("raw", "#8a8984"), ("calibrated", COLORS[sel])):
        b = a.bins[(a.bins.model_name == sel) & (a.bins.variant == variant)]
        ax.plot(
            b.mean_score,
            b.observed_rate,
            marker="o",
            markersize=5,
            linewidth=2,
            color=color,
            label=variant,
        )
    hi = a.bins[a.bins.model_name == sel].mean_score.max() * 1.05
    ax.plot([0, hi], [0, hi], color=GRID, linewidth=1, label="perfect calibration")
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    _style(ax, f"Calibration on test — {LABELS[sel]}", "observed late rate")
    ax.set_xlabel("mean predicted probability (quantile bins)", color=MUTED, fontsize=9)
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    figs["calibration"] = _save(fig, a.fig_dir / "calibration_selected.png")
    return figs


def _save(fig, path: Path) -> str:
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path.relative_to(PROJECT_ROOT))


# ------------------------------------------------------------------ text helpers
def _verdict(a: Artifacts, model: str) -> tuple[bool, str]:
    d = a.diff(model, "average_precision")
    robust = d["ci_low"] > 0
    text = (
        f"{LABELS[model]} minus seller-history baseline, AP: {d['mean']:+.3f} "
        f"[95% interval {d['ci_low']:+.3f} to {d['ci_high']:+.3f}] over {a.summary['bootstrap']['n_weeks']} test weeks"
    )
    return robust, text


def _model_table(a: Artifacts) -> str:
    rows = []
    for model in LABELS:
        ap, p10, l10 = (
            a.metric(model, "average_precision"),
            a.metric(model, "precision_at_10"),
            a.metric(model, "lift_at_10"),
        )
        rows.append(
            (
                LABELS[model],
                f"{ap.value:.3f} [{ap.ci_low:.3f}–{ap.ci_high:.3f}]",
                _num(a.metric(model, "roc_auc").value),
                f"{_pct(p10.value)} [{_pct(p10.ci_low)}–{_pct(p10.ci_high)}]",
                _num(l10.value, 2),
                _pct(a.metric(model, "recall_at_10").value),
                f"{a.metric(model, 'brier').value:.4f} → {a.metric(model, 'brier', 'calibrated').value:.4f}",
            )
        )
    return md_table(
        [
            "Model",
            "AP [95% CI]",
            "ROC-AUC",
            "Precision@10% [CI]",
            "Lift@10%",
            "Recall@10%",
            "Brier raw → calibrated",
        ],
        rows,
    )


def _capacity_table(a: Artifacts) -> str:
    rows = []
    sel = a.summary["selected_model"]
    for model in dict.fromkeys([sel, "seller_history_baseline", "constant_prevalence"]):
        for q in project_config()["analysis"]["capacity_fractions"]:
            c = a.capacity(model, q)
            rows.append(
                (
                    LABELS[model],
                    f"{int(q * 100)}%",
                    f"{c['orders_selected']:,}",
                    _pct(c["effective_review_fraction"]),
                    _pct(c["known_outcome_coverage_in_selection"]),
                    _pct(c["precision_among_known"]),
                    _pct(c["late_rate_all_known"]),
                    _pct(c["share_of_known_late_orders_captured"]),
                )
            )
    return md_table(
        [
            "Ranking",
            "q",
            "orders selected",
            "effective share reviewed",
            "selected with known outcome",
            "late among selected (known)",
            "late rate, all known",
            "share of known late orders captured",
        ],
        rows,
    )


def _banner(mode: str) -> str:
    if mode == "real":
        return ""
    return (
        "> **SYNTHETIC TEST DATA — NOT REAL RESULTS.** Every number below comes from the seeded synthetic test "
        "dataset (Olist file layout, simulated values). It checks the pipeline only and must not be presented as a "
        "finding about Olist or any marketplace.\n\n"
    )


def build_reports(mode: str) -> None:
    a = Artifacts(mode)
    ops = _operations(mode)
    make_figures(a, ops)
    sel = a.summary["selected_model"]
    blocks = a.manifest["splits"]["blocks"]
    test = blocks["test"]
    prevalence = a.metric("constant_prevalence", "prevalence").value
    n_test, pos_test = (
        int(a.metric(sel, "average_precision").n),
        int(a.metric(sel, "average_precision").positives),
    )
    brier_raw, brier_cal = a.metric(sel, "brier").value, a.metric(sel, "brier", "calibrated").value
    brier_const = a.metric("constant_prevalence", "brier").value
    cal_counts = a.manifest["fit_counts"]["fit_step4_calibration"]
    cal_prev = cal_counts["positives"] / cal_counts["n"]
    calib_note = f"calibration {'improved' if brier_cal < brier_raw else 'did NOT improve'} the Brier score relative to the raw score"
    if brier_cal > brier_const:
        calib_note += (
            f", but the calibrated probabilities are still worse than a constant prevalence (Brier {brier_const:.4f}): "
            f"the calibrator learned a late rate of {cal_prev:.1%} on the calibration block versus {prevalence:.1%} on test, "
            "so probabilities over-estimate risk on test (rankings are unaffected)"
        )
    min_support = project_config()["analysis"]["min_support_for_ranking"]
    min_pos = project_config()["analysis"]["min_positives_for_group_metrics"]

    def _shown(r) -> bool:
        return r.n >= min_support and r.positives >= min_pos

    kind = a.summary["evaluation_kind"]
    now = f"{datetime.now(UTC):%Y-%m-%d}"
    src = f"run `{a.summary['run_id']}` ({mode} mode, evaluation kind: {kind})"

    # ---------------------------------------------------------- evaluation report (detailed)
    group_rows = a.groups[a.groups.model_name == sel]
    eval_md = [
        f"# Evaluation report — {mode}",
        "",
        _banner(mode),
        f"Source: {src}. Generated {now}. Selected model: **{LABELS[sel]}** (highest AP on the selection block).",
        "",
        "## Population",
        "",
        f"Test block (approval date) {test['start']} to {test['end_exclusive']} (exclusive): {a.summary['test_scorable_orders']:,} scorable "
        f"orders, of which {n_test:,} were finally delivered with an eligible outcome ({pos_test:,} late, prevalence {_pct(prevalence)}). "
        f"Label coverage {_pct(a.summary['label_coverage'])}. {a.summary['population_note']}",
        "",
        "## Models vs baselines (test block)",
        "",
        _model_table(a),
        f"Intervals: paired bootstrap over ISO weeks, {a.summary['bootstrap']['n_resamples']} resamples, "
        f"{a.summary['bootstrap']['n_skipped_undefined']} skipped (undefined metric). With only "
        f"{a.summary['bootstrap']['n_weeks']} weeks the intervals are themselves uncertain.",
        "",
        "Paired differences against the seller-history baseline:",
        "",
        md_table(
            ["comparison", "metric", "mean diff", "95% interval", "share of resamples > 0"],
            [
                (
                    LABELS[k.split("|")[0]],
                    k.split("|")[1],
                    f"{v['mean']:+.4f}",
                    f"{v['ci_low']:+.4f} to {v['ci_high']:+.4f}",
                    _pct(v["share_above_zero"]),
                )
                for k, v in a.summary["bootstrap"]["differences_vs_reference"].items()
            ],
        ),
        f"![AP](../../figures/{mode}/average_precision_ci.png)",
        "",
        f"![top-q](../../figures/{mode}/top_q_precision.png)",
        "",
        "## Selection block results (used to choose configurations, before test)",
        "",
        md_table(
            ["model", "config", "params", "selection AP", "selection P@10%"],
            [
                (
                    LABELS[r.model],
                    r.config_index,
                    r.params,
                    _num(r.selection_ap),
                    _pct(r.selection_precision_at_10),
                )
                for r in a.selection.itertuples()
            ],
        ),
        "## Calibration",
        "",
        f"Sigmoid calibrator fitted on the calibration block ({blocks['calibration']['start']} to {blocks['calibration']['end_exclusive']}) "
        f"on labels known before the test start. Brier {LABELS[sel]}: raw {brier_raw:.4f}, calibrated {brier_cal:.4f} — {calib_note}.",
        "",
        f"![calibration](../../figures/{mode}/calibration_selected.png)",
        "",
        "## Daily review-capacity simulation (all scorable test orders)",
        "",
        "Each day, the top ceil(q × N_day) orders by frozen score are selected before outcomes are looked at. Precision is "
        "computed among selected orders whose outcome is known; ceil() makes the effective share reviewed larger than q on small days.",
        "",
        _capacity_table(a),
        "## Results by group (selected model, test block)",
        "",
        md_table(
            ["group type", "group", "n", "late", "late rate", "AP", "P@10%", "Lift@10%"],
            [
                (
                    r.group_type,
                    r.group_value,
                    r.n,
                    r.positives,
                    _pct(r.prevalence),
                    _num(r.average_precision) if _shown(r) else "—",
                    _pct(r.precision_at_10) if _shown(r) else "—",
                    _num(r.lift_at_10, 2) if _shown(r) else "—",
                )
                for r in group_rows.itertuples()
            ],
        ),
        f"Model metrics are hidden (—) for groups with fewer than {min_support} labelled orders or fewer than "
        f"{min_pos} late orders; raw values remain in `test_metrics_by_group.csv` with their counts.",
        "",
        "## Sensitivity: ablation without static catalog attributes (diagnostic only)",
        "",
        md_table(
            ["model", "features", "AP", "P@10%", "Lift@10%"],
            [
                (
                    r.model,
                    r.n_features,
                    _num(r.average_precision),
                    _pct(r.precision_at_10),
                    _num(r.lift_at_10, 2),
                )
                for r in a.ablations.itertuples()
            ],
        ),
        "## Interpretation (associative, calibration block)",
        "",
        f"Permutation importance of {LABELS[a.manifest['interpretation_model']]} (drop in AP when a feature is shuffled):",
        "",
        md_table(
            ["feature", "mean AP drop", "std"],
            [
                (r.feature, _num(r.importance_mean, 4), _num(r.importance_std, 4))
                for r in a.importance.head(10).itertuples()
            ],
        ),
        "Largest logistic-regression coefficients (standardised numeric features / one-hot indicators):",
        "",
        md_table(
            ["term", "coefficient"],
            [(r.term, _num(r.coefficient, 3)) for r in a.coefficients.head(10).itertuples()],
        ),
        "These are associations within this dataset, not causes.",
        "",
    ]
    (a.eval_dir / "evaluation_report.md").write_text("\n".join(eval_md))

    log.info("Evaluation report and figures written to %s and %s", a.eval_dir, a.fig_dir)
