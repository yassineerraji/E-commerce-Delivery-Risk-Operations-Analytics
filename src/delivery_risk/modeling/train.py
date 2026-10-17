"""Temporal training protocol (docs/temporal_validation.md). The test block is never loaded here.

1. Fit each candidate on TRAIN orders whose label was known before the selection start.
2. Score the SELECTION block; compare a small fixed grid with AP on selection labels known
   before the calibration start. Pick one configuration per family.
3. Refit the chosen configurations on TRAIN + SELECTION with labels known before the
   calibration start.
4. Score the CALIBRATION block with these frozen models; fit a sigmoid calibrator on
   calibration labels known before the test start.
5. Freeze everything (models, preprocessing, calibrators) for `evaluate`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance

from delivery_risk.config import PROJECT_ROOT, SQL_DIR, load_splits, mode_config, project_config
from delivery_risk.db import connect, sql_dir_sha256
from delivery_risk.evaluation.metrics import classification_metrics, top_q
from delivery_risk.features.catalog import FEATURE_NAMES, FEATURES, select_features
from delivery_risk.modeling.data import assign_blocks, feature_sha256, fit_mask, load_frame
from delivery_risk.modeling.models import (
    ConstantModel,
    SellerHistoryBaseline,
    SigmoidCalibrator,
    make_lightgbm,
    make_logistic,
    positive_scores,
)

log = logging.getLogger(__name__)

MODEL_NAMES = ("constant_prevalence", "seller_history_baseline", "logistic_regression", "lightgbm")
CALIBRATED_MODELS = ("seller_history_baseline", "logistic_regression", "lightgbm")
MIN_ROWS, MIN_CLASS = 200, 20


class InsufficientData(RuntimeError):
    """A block lacks rows or classes for the protocol; boundaries are NOT changed silently."""


def run_dir(mode: str, run_id: str) -> Path:
    return mode_config(mode).artifacts_dir / "runs" / run_id


def latest_run_id(mode: str) -> str:
    pointer = mode_config(mode).artifacts_dir / "runs" / "LATEST"
    if not pointer.exists():
        raise FileNotFoundError(f"No trained run for mode {mode!r}; run the train step first.")
    return pointer.read_text().strip()


def _factory(name: str, params: dict[str, Any], features, seed: int, min_freq: int):
    if name == "constant_prevalence":
        return ConstantModel()
    if name == "seller_history_baseline":
        return SellerHistoryBaseline()
    if name == "logistic_regression":
        return make_logistic(params, features, seed, min_freq)
    if name == "lightgbm":
        return make_lightgbm(params, features, seed, min_freq)
    raise ValueError(name)


def _check(name: str, y: pd.Series) -> dict[str, int]:
    n, pos = int(len(y)), int(y.sum())
    if n < MIN_ROWS or pos < MIN_CLASS or n - pos < MIN_CLASS:
        raise InsufficientData(
            f"{name}: {n} labelled rows with {pos} positives (need >= {MIN_ROWS} rows and >= {MIN_CLASS} per class). "
            "Check coverage before changing split boundaries; never fall back to a random split."
        )
    return {"n": n, "positives": pos}


def _git_state() -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=PROJECT_ROOT, capture_output=True, text=True
            ).stdout.strip()
        )
        return {"commit": commit, "dirty": dirty}
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"commit": None, "dirty": None}


def _versions() -> dict[str, str]:
    import lightgbm
    import sklearn

    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scikit-learn": sklearn.__version__,
        "lightgbm": lightgbm.__version__,
    }


def logistic_coefficients(pipeline) -> list[dict[str, Any]]:
    names = pipeline.named_steps["prep"].get_feature_names_out()
    coefs = pipeline.named_steps["model"].coef_[0]
    rows = [{"term": str(n), "coefficient": float(c)} for n, c in zip(names, coefs, strict=True)]
    return sorted(rows, key=lambda r: -abs(r["coefficient"]))


def train(mode: str) -> str:
    splits = load_splits(mode)
    if splits.status != "frozen":
        raise RuntimeError(
            f"Splits for mode {mode!r} are '{splits.status}'. Review the coverage audit "
            f"(reports/data_quality/{mode}/coverage.md), then set status: frozen in config/splits.yaml."
        )
    cfg = project_config()
    mcfg = cfg["modeling"]
    seed, min_freq, clip = (
        mcfg["random_seed"],
        mcfg["one_hot_min_frequency"],
        mcfg["probability_clip"],
    )
    version = cfg["project"]["feature_version"]
    S0, C0, X0 = splits["selection"].start, splits["calibration"].start, splits["test"].start

    with connect(mode) as conn:
        fsha = feature_sha256(conn, version)
        frame = load_frame(conn, version, splits["train"].start, X0)  # test block never loaded
    frame["block"] = assign_blocks(frame, splits)
    if (frame["prediction_at"] >= X0).any():
        raise AssertionError("Test-block rows leaked into training data")
    X, y, ids = frame[list(FEATURE_NAMES)], frame["y"], frame["order_id"].to_numpy()

    masks = {
        "fit_step1_train": fit_mask(frame, ("train",), S0),
        "eval_step2_selection": fit_mask(frame, ("selection",), C0),
        "fit_step3_train_selection": fit_mask(frame, ("train", "selection"), C0),
        "fit_step4_calibration": fit_mask(frame, ("calibration",), X0),
    }
    counts = {name: _check(name, y[m]) for name, m in masks.items()}
    cutoffs = {
        "fit_step1_train": S0,
        "eval_step2_selection": C0,
        "fit_step3_train_selection": C0,
        "fit_step4_calibration": X0,
    }
    censored = {
        name: int((frame["block"].isin(blocks) & frame["label_eligible"] & ~masks[name]).sum())
        for name, blocks in (
            ("fit_step1_train", ("train",)),
            ("eval_step2_selection", ("selection",)),
            ("fit_step3_train_selection", ("train", "selection")),
            ("fit_step4_calibration", ("calibration",)),
        )
    }
    for name in masks:
        log.info(
            "%-26s n=%-6d positives=%-5d label cutoff=%s (eligible but not yet delivered: %d)",
            name,
            counts[name]["n"],
            counts[name]["positives"],
            cutoffs[name].date(),
            censored[name],
        )

    # ---- Steps 1-2: bounded selection on the selection block only
    grids = {
        "constant_prevalence": [{}],
        "seller_history_baseline": [{}],
        "logistic_regression": mcfg["logistic_regression_grid"],
        "lightgbm": mcfg["lightgbm_grid"],
    }
    m1, m2 = masks["fit_step1_train"], masks["eval_step2_selection"]
    selection_rows = []
    for name in MODEL_NAMES:
        for i, params in enumerate(grids[name]):
            model = _factory(name, params, FEATURES, seed, min_freq).fit(
                X[m1], y[m1].astype(int).to_numpy()
            )
            scores = positive_scores(model, X[m2])
            met = classification_metrics(y[m2].astype(int).to_numpy(), scores, clip)
            tq = top_q(y[m2].astype(int).to_numpy(), scores, ids[m2.to_numpy()], 0.10)
            selection_rows.append(
                {
                    "model": name,
                    "config_index": i,
                    "params": json.dumps(params),
                    "selection_ap": met["average_precision"],
                    "selection_roc_auc": met["roc_auc"],
                    "selection_precision_at_10": tq.precision,
                    "selection_lift_at_10": tq.lift,
                }
            )
            log.info(
                "selection %-24s cfg=%d AP=%.4f P@10=%.4f",
                name,
                i,
                met["average_precision"],
                tq.precision,
            )
    sel = pd.DataFrame(selection_rows)
    best = {
        name: int(
            sel[sel.model == name]
            .sort_values(["selection_ap", "config_index"], ascending=[False, True])
            .iloc[0]
            .config_index
        )
        for name in MODEL_NAMES
    }
    best_ap = {
        name: float(
            sel[(sel.model == name) & (sel.config_index == best[name])].selection_ap.iloc[0]
        )
        for name in MODEL_NAMES
    }
    selected_model = max(MODEL_NAMES, key=lambda n: (best_ap[n], -MODEL_NAMES.index(n)))
    log.info(
        "Selected configuration per family: %s; overall selected model: %s", best, selected_model
    )

    # ---- Step 3: refit chosen configurations on train + selection
    m3 = masks["fit_step3_train_selection"]
    final = {
        name: _factory(name, grids[name][best[name]], FEATURES, seed, min_freq).fit(
            X[m3], y[m3].astype(int).to_numpy()
        )
        for name in MODEL_NAMES
    }

    # ---- Step 4: calibrators on the calibration block (frozen base models)
    m4 = masks["fit_step4_calibration"]
    y4 = y[m4].astype(int).to_numpy()
    calibrators, calib_diag = {}, {}
    for name in CALIBRATED_MODELS:
        raw = positive_scores(final[name], X[m4])
        calibrators[name] = SigmoidCalibrator(clip).fit(raw, y4)
        cal = calibrators[name].transform(raw)
        calib_diag[name] = {
            "slope": calibrators[name].slope_,
            "intercept": calibrators[name].intercept_,
            "calibration_block_raw": classification_metrics(y4, raw, clip),
            "calibration_block_calibrated": classification_metrics(y4, cal, clip),
        }

    # ---- Sensitivity: ablations refit with the chosen configs (diagnostic, not used for choice)
    ablation_models = {}
    for ablation, groups in mcfg["ablations"].items():
        feats = select_features(tuple(groups))
        for name in ("logistic_regression", "lightgbm"):
            cols = [f.name for f in feats]
            ablation_models[f"{name}__{ablation}"] = {
                "features": cols,
                "model": _factory(name, grids[name][best[name]], feats, seed, min_freq).fit(
                    X.loc[m3, cols], y[m3].astype(int).to_numpy()
                ),
            }

    # ---- Interpretation (associative, computed on the calibration block, never on test)
    coefficients = logistic_coefficients(final["logistic_regression"])
    importance = None
    interpret_model = (
        selected_model if selected_model in ("logistic_regression", "lightgbm") else "lightgbm"
    )
    pi = permutation_importance(
        final[interpret_model],
        X[m4],
        y4,
        scoring="average_precision",
        n_repeats=5,
        random_state=seed,
        n_jobs=1,
    )
    importance = sorted(
        [
            {"feature": f, "importance_mean": float(m), "importance_std": float(s)}
            for f, m, s in zip(FEATURE_NAMES, pi.importances_mean, pi.importances_std, strict=True)
        ],
        key=lambda r: -r["importance_mean"],
    )

    # ---- Persist artifacts
    created = datetime.now(UTC)
    run_id = created.strftime("%Y%m%dT%H%M%SZ") + "-" + splits.sha256[:6]
    out = run_dir(mode, run_id)
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {"models": final, "calibrators": calibrators, "ablations": ablation_models},
        out / "models.joblib",
    )
    model_sha = hashlib.sha256((out / "models.joblib").read_bytes()).hexdigest()
    sel.to_csv(out / "selection_results.csv", index=False)
    pd.DataFrame(coefficients).to_csv(out / "logistic_coefficients.csv", index=False)
    pd.DataFrame(importance).to_csv(
        out / "permutation_importance_calibration_block.csv", index=False
    )
    manifest = {
        "run_id": run_id,
        "data_mode": mode,
        "synthetic": mode != "real",
        "created_at": created.isoformat(),
        "git": _git_state(),
        "versions": _versions(),
        "random_seed": seed,
        "splits": splits.as_dict(),
        "feature_version": version,
        "feature_sha256": fsha,
        "feature_sql_sha256": sql_dir_sha256(SQL_DIR / "features"),
        "features": list(FEATURE_NAMES),
        "max_prediction_at_loaded": frame["prediction_at"].max().isoformat(),
        "label_cutoffs": {k: v.isoformat() for k, v in cutoffs.items()},
        "fit_counts": counts,
        "eligible_but_not_yet_delivered_at_cutoff": censored,
        "grids": grids,
        "chosen_config_index": best,
        "chosen_params": {n: grids[n][best[n]] for n in MODEL_NAMES},
        "selection_ap": best_ap,
        "selected_model": selected_model,
        "selection_rule": "highest AP on the selection block (labels known before calibration start); ties -> simpler model",
        "calibration": calib_diag,
        "interpretation_model": interpret_model,
        "ablations": {k: v["features"] for k, v in ablation_models.items()},
        "models_sha256": model_sha,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    (out.parent / "LATEST").write_text(run_id)

    with connect(mode) as conn:
        conn.execute(
            "INSERT INTO evaluation.model_runs (run_id, data_mode, status, split_sha256, feature_version, feature_sha256,"
            " selected_model, manifest) VALUES (%s, %s, 'trained', %s, %s, %s, %s, %s)",
            (
                run_id,
                mode,
                splits.sha256,
                version,
                fsha,
                selected_model,
                json.dumps(manifest, default=str),
            ),
        )
    log.info("Run %s trained; artifacts in %s", run_id, out)
    return run_id
