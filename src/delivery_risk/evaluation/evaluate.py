"""Final evaluation of a FROZEN run on the test block.

- verifies that splits, features and model files are unchanged since training (hashes);
- scores every scorable order of the calibration and test blocks (no refit, no recalibration);
- measures performance on test orders with an observed, eligible outcome;
- adds a paired weekly block bootstrap, calibration bins, metrics by group, the daily capacity
  simulation and the ablation diagnostics;
- logs every evaluation: a repeated evaluation of the same run is a "verification_rerun"; a run
  trained after another run of the same split was evaluated is flagged "post_test_exploration".
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from delivery_risk.config import load_splits, mode_config, project_config
from delivery_risk.db import connect, fetch_all, transaction
from delivery_risk.evaluation.bootstrap import paired_block_bootstrap
from delivery_risk.evaluation.capacity import daily_capacity, summarise_capacity
from delivery_risk.evaluation.metrics import calibration_bins, classification_metrics, top_q
from delivery_risk.features.catalog import FEATURE_NAMES
from delivery_risk.modeling.data import assign_blocks, feature_sha256, load_frame
from delivery_risk.modeling.models import positive_scores
from delivery_risk.modeling.train import MODEL_NAMES, latest_run_id, run_dir

log = logging.getLogger(__name__)
REFERENCE_MODEL = "seller_history_baseline"


def _ap(y, s, ids):
    return float(average_precision_score(y, s)) if 0 < y.sum() < len(y) else None


def _p10(y, s, ids):
    return top_q(y, s, ids, 0.10).precision if y.sum() > 0 else None


def _lift10(y, s, ids):
    return top_q(y, s, ids, 0.10).lift


def _classify_evaluation(mode: str, run_id: str, manifest: dict) -> str:
    log_path = mode_config(mode).artifacts_dir / "evaluation_log.jsonl"
    if not log_path.exists():
        return "final"
    entries = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    if any(e["run_id"] == run_id for e in entries):
        return "verification_rerun"
    created = datetime.fromisoformat(manifest["created_at"])
    earlier = [
        e
        for e in entries
        if e["split_sha256"] == manifest["splits"]["sha256"]
        and datetime.fromisoformat(e["evaluated_at"]) < created
    ]
    return "post_test_exploration" if earlier else "final"


def evaluate(mode: str, run_id: str | None = None) -> dict:
    run_id = run_id or latest_run_id(mode)
    out = run_dir(mode, run_id)
    manifest = json.loads((out / "manifest.json").read_text())
    cfg = project_config()
    clip = cfg["modeling"]["probability_clip"]
    fractions = cfg["analysis"]["capacity_fractions"]
    min_support = cfg["analysis"]["min_support_for_ranking"]
    splits = load_splits(mode)

    if splits.sha256 != manifest["splits"]["sha256"]:
        raise RuntimeError(
            "config/splits.yaml changed since this run was trained; evaluation refused."
        )
    if (
        hashlib.sha256((out / "models.joblib").read_bytes()).hexdigest()
        != manifest["models_sha256"]
    ):
        raise RuntimeError("Model file differs from the trained artifact; evaluation refused.")
    kind = _classify_evaluation(mode, run_id, manifest)
    if kind == "post_test_exploration":
        log.warning(
            "This run was trained AFTER another run of the same split had been evaluated on test: "
            "results are exploration, not an independent evaluation."
        )

    bundle = joblib.load(out / "models.joblib")
    models, calibrators, ablations = bundle["models"], bundle["calibrators"], bundle["ablations"]
    with connect(mode) as conn:
        if feature_sha256(conn, manifest["feature_version"]) != manifest["feature_sha256"]:
            raise RuntimeError(
                "Feature matrix changed since training (data or SQL); retrain before evaluating."
            )
        frame = load_frame(
            conn, manifest["feature_version"], splits["calibration"].start, splits["test"].end
        )
        _, region_rows = fetch_all(conn, "SELECT state, region FROM core.brazil_states")
    regions = dict(region_rows)

    frame["block"] = assign_blocks(frame, splits)
    frame["prediction_date"] = frame["prediction_at"].dt.date
    iso = frame["prediction_at"].dt.isocalendar()
    frame["week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    frame["prediction_month"] = frame["prediction_at"].dt.strftime("%Y-%m")
    frame["customer_region"] = frame["customer_state"].map(regions).fillna("Unknown")
    X = frame[list(FEATURE_NAMES)]
    for name in MODEL_NAMES:
        raw = positive_scores(models[name], X)
        frame[f"raw__{name}"] = raw
        frame[f"cal__{name}"] = calibrators[name].transform(raw) if name in calibrators else raw

    test = frame[frame["block"] == "test"].copy()
    lab = test[test["label_eligible"]].copy()
    lab["y"] = lab["y"].astype(int)
    y, ids = lab["y"].to_numpy(), lab["order_id"].to_numpy()
    log.info(
        "Test block: %d scorable orders, %d with eligible observed outcome (%d late)",
        len(test),
        len(lab),
        y.sum(),
    )

    # ---- headline metrics, raw and calibrated
    metric_rows, topq_rows = [], []
    for name in MODEL_NAMES:
        for variant in ("raw", "calibrated"):
            s = lab[f"{'raw' if variant == 'raw' else 'cal'}__{name}"].to_numpy()
            met = classification_metrics(y, s, clip)
            for metric in ("average_precision", "roc_auc", "brier", "log_loss", "prevalence"):
                metric_rows.append(
                    {
                        "model_name": name,
                        "variant": variant,
                        "metric": metric,
                        "value": met[metric],
                        "n": met["n"],
                        "positives": met["positives"],
                    }
                )
            for q in fractions:
                tq = top_q(y, s, ids, q)
                topq_rows.append({"model_name": name, "variant": variant, **tq.as_dict()})
                for metric, value in (
                    ("precision", tq.precision),
                    ("recall", tq.recall),
                    ("lift", tq.lift),
                ):
                    metric_rows.append(
                        {
                            "model_name": name,
                            "variant": variant,
                            "metric": f"{metric}_at_{int(q * 100)}",
                            "value": value,
                            "n": tq.n,
                            "positives": tq.positives,
                            "note": None if tq.status == "ok" else tq.status,
                        }
                    )
    metrics = pd.DataFrame(metric_rows)

    # ---- paired weekly block bootstrap (ranking metrics; identical for raw and calibrated)
    bcfg = cfg["evaluation"]["bootstrap"]
    boot = paired_block_bootstrap(
        lab,
        {m: f"raw__{m}" for m in MODEL_NAMES},
        {"average_precision": _ap, "precision_at_10": _p10, "lift_at_10": _lift10},
        reference=REFERENCE_MODEL,
        n_resamples=bcfg["n_resamples"],
        seed=bcfg["seed"],
    )
    for row in metric_rows:
        key = f"{row['model_name']}|{row['metric']}"
        if key in boot["intervals"]:
            row["ci_low"], row["ci_high"] = (
                boot["intervals"][key]["ci_low"],
                boot["intervals"][key]["ci_high"],
            )
    metrics = pd.DataFrame(metric_rows)

    # ---- calibration bins
    bins = []
    for name in MODEL_NAMES:
        for variant, prefix in (("raw", "raw"), ("calibrated", "cal")):
            for b in calibration_bins(
                y, lab[f"{prefix}__{name}"].to_numpy(), cfg["evaluation"]["calibration_bins"]
            ):
                bins.append({"model_name": name, "variant": variant, **b})
    bins = pd.DataFrame(bins)

    # ---- metrics by group (sample sizes always shown; small groups flagged)
    lab["seller_history_status"] = np.where(
        lab["sh_any_seller_no_history"] == 1, "at least one new seller", "all sellers with history"
    )
    lab["multi_seller"] = np.where(lab["n_distinct_sellers"] > 1, "multi-seller", "single seller")
    group_rows = []
    for gtype in ("prediction_month", "customer_region", "seller_history_status", "multi_seller"):
        for gval, g in lab.groupby(gtype):
            gy, gids = g["y"].to_numpy(), g["order_id"].to_numpy()
            for name in MODEL_NAMES:
                gs = g[f"raw__{name}"].to_numpy()
                tq = top_q(gy, gs, gids, 0.10)
                group_rows.append(
                    {
                        "model_name": name,
                        "group_type": gtype,
                        "group_value": str(gval),
                        "n": len(g),
                        "positives": int(gy.sum()),
                        "prevalence": float(gy.mean()) if len(g) else None,
                        "average_precision": _ap(gy, gs, gids) if len(g) >= min_support else None,
                        "precision_at_10": tq.precision if len(g) >= min_support else None,
                        "lift_at_10": tq.lift if len(g) >= min_support else None,
                    }
                )
    groups = pd.DataFrame(group_rows)

    # ---- daily capacity simulation on ALL scorable test orders
    cap_frames, cap_summary = [], {}
    for name in MODEL_NAMES:
        for q in fractions:
            daily = daily_capacity(
                test.assign(y=test["y"].fillna(0).astype(int)), f"raw__{name}", q
            )
            daily.insert(0, "model_name", name)
            cap_frames.append(daily)
            cap_summary[f"{name}|{q}"] = summarise_capacity(daily)
    capacity = pd.concat(cap_frames, ignore_index=True)

    # ---- ablations (sensitivity analysis; never used to choose a model)
    ablation_rows = []
    for key, entry in ablations.items():
        s = positive_scores(entry["model"], lab[entry["features"]])
        tq = top_q(y, s, ids, 0.10)
        ablation_rows.append(
            {
                "model": key,
                "n_features": len(entry["features"]),
                "average_precision": _ap(y, s, ids),
                "precision_at_10": tq.precision,
                "lift_at_10": tq.lift,
            }
        )

    # ---- label coverage of the test block
    coverage = (
        test.groupby(["prediction_month", "outcome_population"])
        .size()
        .rename("orders")
        .reset_index()
    )

    summary = {
        "run_id": run_id,
        "data_mode": mode,
        "synthetic": mode != "real",
        "evaluation_kind": kind,
        "evaluated_at": datetime.now(UTC).isoformat(),
        "selected_model": manifest["selected_model"],
        "test_block": splits.as_dict()["blocks"]["test"],
        "test_scorable_orders": int(len(test)),
        "test_labelled_orders": int(len(lab)),
        "test_late_orders": int(y.sum()),
        "label_coverage": float(len(lab) / len(test)) if len(test) else None,
        "population_note": "Performance measured on test orders finally delivered with an eligible outcome; "
        "canceled/unavailable/undelivered orders are scored but cannot be evaluated.",
        "bootstrap": boot,
        "capacity": cap_summary,
        "ablations": ablation_rows,
    }

    # ---- write artifacts (run folder and versioned report folder)
    eval_dir = out / "evaluation"
    eval_dir.mkdir(exist_ok=True)
    report_dir = mode_config(mode).report_dir("evaluation")
    for target in (eval_dir, report_dir):
        metrics.to_csv(target / "test_metrics.csv", index=False)
        pd.DataFrame(topq_rows).to_csv(target / "test_top_q.csv", index=False)
        bins.to_csv(target / "test_calibration_bins.csv", index=False)
        groups.to_csv(target / "test_metrics_by_group.csv", index=False)
        pd.DataFrame(ablation_rows).to_csv(target / "test_ablations.csv", index=False)
        coverage.to_csv(target / "test_label_coverage.csv", index=False)
        capacity.to_csv(target / "test_capacity_daily.csv", index=False)
        (target / "evaluation_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    for name in (
        "selection_results.csv",
        "logistic_coefficients.csv",
        "permutation_importance_calibration_block.csv",
    ):
        (report_dir / name).write_bytes((out / name).read_bytes())
    (report_dir / "run_manifest.json").write_bytes((out / "manifest.json").read_bytes())

    _publish(mode, run_id, frame, metrics, capacity, bins, groups)
    with open(mode_config(mode).artifacts_dir / "evaluation_log.jsonl", "a") as fh:
        fh.write(
            json.dumps(
                {
                    "run_id": run_id,
                    "evaluated_at": summary["evaluated_at"],
                    "kind": kind,
                    "split_sha256": manifest["splits"]["sha256"],
                    "feature_sha256": manifest["feature_sha256"],
                    "models_sha256": manifest["models_sha256"],
                }
            )
            + "\n"
        )
    log.info("Evaluation (%s) of run %s written to %s", kind, run_id, report_dir)
    return summary


def _publish(mode, run_id, frame, metrics, capacity, bins, groups) -> None:
    """Replace this run's rows in one transaction (idempotent re-publication)."""
    preds = []
    for name in MODEL_NAMES:
        sub = frame[frame["block"].isin(["calibration", "test"])]
        for row in sub[
            [
                "order_id",
                "block",
                "prediction_at",
                "prediction_date",
                f"raw__{name}",
                f"cal__{name}",
            ]
        ].itertuples(index=False):
            preds.append(
                (
                    run_id,
                    name,
                    row[0],
                    row[1],
                    row[2].to_pydatetime(),
                    row[3],
                    float(row[4]),
                    float(row[5]),
                )
            )
    with transaction(mode) as conn, conn.cursor() as cur:
        for table in (
            "predictions",
            "model_metrics",
            "capacity_daily",
            "calibration_bins",
            "metrics_by_group",
        ):
            cur.execute(f"DELETE FROM evaluation.{table} WHERE run_id = %s", (run_id,))  # noqa: S608 (fixed names)
        with cur.copy(
            "COPY evaluation.predictions (run_id, model_name, order_id, block, prediction_at, prediction_date,"
            " score_raw, score_calibrated) FROM STDIN"
        ) as copy:
            for p in preds:
                copy.write_row(p)
        cur.executemany(
            "INSERT INTO evaluation.model_metrics (run_id, model_name, block, variant, metric, value, ci_low, ci_high, n, positives, note)"
            " VALUES (%s, %s, 'test', %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    run_id,
                    r.model_name,
                    r.variant,
                    r.metric,
                    _f(r.value),
                    _f(getattr(r, "ci_low", None)),
                    _f(getattr(r, "ci_high", None)),
                    int(r.n),
                    int(r.positives),
                    r.note if isinstance(getattr(r, "note", None), str) else None,
                )
                for r in metrics.itertuples()
            ],
        )
        cur.executemany(
            "INSERT INTO evaluation.capacity_daily VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    run_id,
                    r.model_name,
                    r.prediction_date,
                    r.capacity_fraction,
                    r.n_scorable,
                    r.k_selected,
                    r.n_selected_labeled,
                    r.n_selected_late,
                    r.n_day_labeled,
                    r.n_day_late,
                )
                for r in capacity.itertuples()
            ],
        )
        cur.executemany(
            "INSERT INTO evaluation.calibration_bins VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (run_id, r.model_name, r.variant, r.bin, r.mean_score, r.observed_rate, r.n)
                for r in bins.itertuples()
            ],
        )
        cur.executemany(
            "INSERT INTO evaluation.metrics_by_group VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    run_id,
                    r.model_name,
                    r.group_type,
                    r.group_value,
                    r.n,
                    r.positives,
                    _f(r.prevalence),
                    _f(r.average_precision),
                    _f(r.precision_at_10),
                    _f(r.lift_at_10),
                )
                for r in groups.itertuples()
            ],
        )
        cur.execute(
            "UPDATE evaluation.model_runs SET status = 'evaluated' WHERE run_id = %s", (run_id,)
        )
        cur.execute(
            "INSERT INTO meta.publication_log (layer, data_mode, details) VALUES ('predictions', %s, %s)",
            (mode, json.dumps({"run_id": run_id, "rows": len(preds)})),
        )


def _f(value):
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(v) else v
