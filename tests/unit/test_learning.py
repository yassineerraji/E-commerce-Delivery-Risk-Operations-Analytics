"""Synthetic learning test (sized for learning, distinct from the tiny SQL fixtures)."""

import numpy as np
import pandas as pd
import pytest

from delivery_risk.evaluation.metrics import classification_metrics
from delivery_risk.features.catalog import (
    FEATURES,
    FORBIDDEN_COLUMNS,
    KEY_COLUMNS,
    categorical_names,
)
from delivery_risk.modeling.models import (
    ConstantModel,
    SellerHistoryBaseline,
    SigmoidCalibrator,
    make_lightgbm,
    make_logistic,
    positive_scores,
)

LGBM_PARAMS = {
    "num_leaves": 15,
    "learning_rate": 0.05,
    "n_estimators": 150,
    "min_child_samples": 30,
}


def synthetic_matrix(n: int, seed: int) -> tuple[pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({f.name: rng.normal(size=n) for f in FEATURES if f.kind == "numeric"})
    for name in categorical_names(FEATURES):
        X[name] = rng.choice(["a", "b", "c", None], size=n).astype(object)
    X["sh_late_rate_90d_mean"] = np.where(rng.random(n) < 0.2, np.nan, rng.beta(2, 15, n))
    logit = (
        -2.3
        + 1.2 * X["promised_lead_days"]
        + 0.8 * (X["customer_state"] == "b")
        + 3 * np.nan_to_num(X["sh_late_rate_90d_mean"])
    )
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return X, y


@pytest.mark.parametrize(
    "factory, params",
    [(make_logistic, {"C": 1.0, "class_weight": None}), (make_lightgbm, LGBM_PARAMS)],
)
def test_models_learn_planted_signal(factory, params):
    X, y = synthetic_matrix(6000, 0)
    Xt, yt = synthetic_matrix(3000, 1)
    model = factory(params, FEATURES, seed=0, min_frequency=20).fit(X, y)
    m = classification_metrics(yt, positive_scores(model, Xt))
    assert m["average_precision"] > 1.8 * m["prevalence"]
    assert m["roc_auc"] > 0.7


@pytest.mark.parametrize(
    "factory, params",
    [(make_logistic, {"C": 1.0, "class_weight": None}), (make_lightgbm, LGBM_PARAMS)],
)
def test_unknown_category_in_test_does_not_fail(factory, params):
    X, y = synthetic_matrix(2000, 2)
    Xt, _ = synthetic_matrix(50, 3)
    Xt["main_category"] = "never_seen_category"
    Xt.loc[0, "customer_state"] = None
    model = factory(params, FEATURES, seed=0, min_frequency=20).fit(X, y)
    s = positive_scores(model, Xt)
    assert np.isfinite(s).all() and len(s) == 50


def test_preprocessing_is_learned_on_fit_rows_only():
    X, y = synthetic_matrix(2000, 4)
    model = make_logistic({"C": 1.0, "class_weight": None}, FEATURES, 0, 20).fit(X, y)
    imputer = model.named_steps["prep"].named_transformers_["num"].named_steps["impute"]
    col = list(model.named_steps["prep"].transformers_[0][2]).index("sh_late_rate_90d_mean")
    assert imputer.statistics_[col] == pytest.approx(np.nanmedian(X["sh_late_rate_90d_mean"]))
    shifted = X.copy()
    shifted["sh_late_rate_90d_mean"] += 10  # scoring other data must not refit the imputer
    positive_scores(model, shifted)
    assert imputer.statistics_[col] == pytest.approx(np.nanmedian(X["sh_late_rate_90d_mean"]))


def test_baselines():
    X, y = synthetic_matrix(1000, 5)
    const = ConstantModel().fit(X, y)
    assert np.allclose(positive_scores(const, X), y.mean())
    base = SellerHistoryBaseline().fit(X, y)
    s = positive_scores(base, X)
    missing = X["sh_late_rate_90d_mean"].isna().to_numpy()
    assert np.allclose(s[missing], y.mean())  # fallback = prevalence of the fitting rows
    assert np.allclose(s[~missing], X["sh_late_rate_90d_mean"].to_numpy()[~missing])


def test_calibrator_is_monotone_and_fitted_on_given_rows():
    rng = np.random.default_rng(6)
    raw = rng.random(5000) * 0.5
    y = (rng.random(5000) < raw * 0.4).astype(int)  # raw scores over-estimate risk
    cal = SigmoidCalibrator().fit(raw, y)
    out = cal.transform(np.sort(raw))
    assert np.all(np.diff(out) >= -1e-12)
    assert abs(out.mean() - y.mean()) < 0.02
    with pytest.raises(ValueError):
        SigmoidCalibrator().fit(raw[:10], np.zeros(10, int))


def test_allow_list_has_no_forbidden_column():
    names = {f.name for f in FEATURES}
    assert not names & FORBIDDEN_COLUMNS
    assert not names & set(KEY_COLUMNS)
