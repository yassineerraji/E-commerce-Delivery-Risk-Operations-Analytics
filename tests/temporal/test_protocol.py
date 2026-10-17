"""Training/evaluation protocol on the small synthetic end-to-end run."""

import hashlib
import json
from datetime import datetime

import joblib
import numpy as np
import pytest
from helpers import query, scalar

from delivery_risk.config import load_splits
from delivery_risk.db import connect
from delivery_risk.evaluation.evaluate import evaluate
from delivery_risk.features.catalog import FEATURE_NAMES
from delivery_risk.modeling.data import load_frame
from delivery_risk.modeling.models import positive_scores
from delivery_risk.modeling.train import latest_run_id, run_dir

pytestmark = [pytest.mark.integration, pytest.mark.slow]


@pytest.fixture(scope="module")
def run(e2e_db):
    run_id = latest_run_id("e2e")
    manifest = json.loads((run_dir("e2e", run_id) / "manifest.json").read_text())
    return run_id, manifest


def _count_known(block: str, cutoff: str) -> int:
    s = load_splits("e2e")[block]
    return scalar(
        "e2e",
        """SELECT count(*) FROM evaluation.order_labels WHERE scoring_eligible AND label_eligible
                            AND prediction_at >= %s AND prediction_at < %s AND label_available_at < %s""",
        (s.start, s.end, cutoff),
    )


def test_training_never_loaded_the_test_block(run):
    _, manifest = run
    assert (
        datetime.fromisoformat(manifest["max_prediction_at_loaded"])
        < load_splits("e2e")["test"].start
    )


def test_no_label_after_cutoff_used_for_fit_selection_or_calibration(run):
    _, m = run
    s = load_splits("e2e")
    assert m["fit_counts"]["fit_step1_train"]["n"] == _count_known("train", s["selection"].start)
    assert m["fit_counts"]["eval_step2_selection"]["n"] == _count_known(
        "selection", s["calibration"].start
    )
    assert m["fit_counts"]["fit_step3_train_selection"]["n"] == _count_known(
        "train", s["calibration"].start
    ) + _count_known("selection", s["calibration"].start)
    assert m["fit_counts"]["fit_step4_calibration"]["n"] == _count_known(
        "calibration", s["test"].start
    )
    # some labelled orders were still in transit at each cutoff and were therefore NOT used
    assert all(v > 0 for v in m["eligible_but_not_yet_delivered_at_cutoff"].values())


def test_selection_grid_is_bounded(run):
    _, m = run
    assert len(m["grids"]["lightgbm"]) <= 12 and len(m["grids"]["logistic_regression"]) <= 12


def test_test_scores_come_from_the_frozen_models(run):
    run_id, m = run
    bundle = joblib.load(run_dir("e2e", run_id) / "models.joblib")
    s = load_splits("e2e")
    with connect("e2e") as conn:
        frame = load_frame(conn, m["feature_version"], s["test"].start, s["test"].end)
    stored = dict(
        query(
            "e2e",
            "SELECT order_id, score_raw FROM evaluation.predictions WHERE run_id = %s AND model_name = 'lightgbm' AND block = 'test'",
            (run_id,),
        )
    )
    recomputed = positive_scores(bundle["models"]["lightgbm"], frame[list(FEATURE_NAMES)])
    assert len(stored) == len(frame)
    assert np.allclose([stored[o] for o in frame.order_id], recomputed, atol=1e-12)
    cal = dict(
        query(
            "e2e",
            "SELECT order_id, score_calibrated FROM evaluation.predictions WHERE run_id = %s AND model_name = 'lightgbm' AND block = 'test'",
            (run_id,),
        )
    )
    assert np.allclose(
        [cal[o] for o in frame.order_id],
        bundle["calibrators"]["lightgbm"].transform(recomputed),
        atol=1e-12,
    )


def test_reevaluation_is_logged_as_verification_and_identical(run):
    run_id, _ = run
    snap = "SELECT md5(string_agg(order_id || model_name || score_raw::text, '|' ORDER BY model_name, order_id)) FROM evaluation.predictions WHERE run_id = %s"
    before = scalar("e2e", snap, (run_id,))
    summary = evaluate("e2e", run_id)
    assert summary["evaluation_kind"] == "verification_rerun"
    assert scalar("e2e", snap, (run_id,)) == before


def test_evaluation_refuses_changed_features(run):
    run_id, _ = run
    order_id, value = query(
        "e2e",
        "SELECT order_id, item_value_brl FROM feature.order_features ORDER BY order_id LIMIT 1",
    )[0]
    with connect("e2e") as conn:
        conn.execute(
            "UPDATE feature.order_features SET item_value_brl = item_value_brl + 1 WHERE order_id = %s",
            (order_id,),
        )
    try:
        with pytest.raises(RuntimeError, match="Feature matrix changed"):
            evaluate("e2e", run_id)
    finally:
        with connect("e2e") as conn:
            conn.execute(
                "UPDATE feature.order_features SET item_value_brl = %s WHERE order_id = %s",
                (value, order_id),
            )


def test_model_file_hash_matches_manifest(run):
    run_id, m = run
    assert (
        hashlib.sha256((run_dir("e2e", run_id) / "models.joblib").read_bytes()).hexdigest()
        == m["models_sha256"]
    )


def test_all_scorable_test_orders_scored_regardless_of_outcome(run):
    run_id, _ = run
    s = load_splits("e2e")
    scorable = scalar(
        "e2e",
        "SELECT count(*) FROM evaluation.order_labels WHERE scoring_eligible AND prediction_at >= %s AND prediction_at < %s",
        (s["test"].start, s["test"].end),
    )
    scored = scalar(
        "e2e",
        "SELECT count(*) FROM evaluation.predictions WHERE run_id = %s AND model_name = 'lightgbm' AND block = 'test'",
        (run_id,),
    )
    unlabelled = scalar(
        "e2e",
        """SELECT count(*) FROM evaluation.predictions p JOIN evaluation.order_labels l USING (order_id)
                                   WHERE p.run_id = %s AND p.model_name = 'lightgbm' AND NOT l.label_eligible""",
        (run_id,),
    )
    assert scored == scorable and unlabelled > 0


def test_window_function_rewrite_matches_lateral_join(e2e_db):
    """The benchmark's window-function variant must return exactly the pipeline's history."""
    from delivery_risk.benchmark import create_inputs
    from delivery_risk.config import SQL_DIR

    with connect("e2e") as conn:
        create_inputs(conn, schema=None)  # temporary tables, unqualified names resolve to them
        got = conn.execute(
            (SQL_DIR / "benchmarks" / "seller_history_window.sql").read_text()
        ).fetchall()
        lateral = conn.execute(
            (SQL_DIR / "benchmarks" / "seller_history_lateral.sql").read_text()
        ).fetchall()
    expected = query(
        "e2e",
        """SELECT order_id, seller_id, known_outcomes_total, known_outcomes_90d, late_outcomes_90d, approvals_30d
                               FROM feature.order_seller_history ORDER BY order_id, seller_id""",
    )
    assert [tuple(r[:6]) for r in got] == expected
    assert [tuple(r[:6]) for r in lateral] == expected
