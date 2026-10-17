"""Model definitions: two baselines, logistic regression, LightGBM, sigmoid calibrator.

All preprocessing (imputation, scaling, one-hot encoding) lives inside sklearn Pipelines so that
it is learned on the fitting rows only and re-applied unchanged to later blocks.
"""

from __future__ import annotations

from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from delivery_risk.features.catalog import Feature, categorical_names, numeric_names

MISSING_CATEGORY = "__missing__"


class ConstantModel:
    """Predicts the prevalence of the fitting rows for every order."""

    name = "constant_prevalence"

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> ConstantModel:
        self.prevalence_ = float(np.mean(y))
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        p = np.full(len(X), self.prevalence_)
        return np.column_stack([1 - p, p])


class SellerHistoryBaseline:
    """Operational rule: equal-weight mean of the order's sellers' 90-day known late rates.

    Orders whose sellers have no known outcome in the window fall back to the prevalence of the
    fitting rows (no information from later blocks).
    """

    name = "seller_history_baseline"
    column = "sh_late_rate_90d_mean"

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> SellerHistoryBaseline:
        self.fallback_ = float(np.mean(y))
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        p = X[self.column].astype(float).fillna(self.fallback_).to_numpy()
        return np.column_stack([1 - p, p])


def _preprocessor(
    features: tuple[Feature, ...], scale: bool, min_frequency: int
) -> ColumnTransformer:
    numeric_steps: list[tuple[str, Any]] = [
        ("impute", SimpleImputer(strategy="median", add_indicator=True))
    ]
    if scale:
        numeric_steps.append(("scale", StandardScaler()))
    categorical = Pipeline(
        [
            ("impute", SimpleImputer(strategy="constant", fill_value=MISSING_CATEGORY)),
            # Categories unseen during fit (or rarer than min_frequency) map to the
            # "infrequent" bucket instead of failing.
            (
                "onehot",
                OneHotEncoder(
                    handle_unknown="infrequent_if_exist",
                    min_frequency=min_frequency,
                    sparse_output=False,
                ),
            ),
        ]
    )
    return ColumnTransformer(
        [
            ("num", Pipeline(numeric_steps), numeric_names(features)),
            ("cat", categorical, categorical_names(features)),
        ],
        remainder="drop",
        verbose_feature_names_out=True,
    )


def make_logistic(
    params: dict[str, Any], features: tuple[Feature, ...], seed: int, min_frequency: int
) -> Pipeline:
    return Pipeline(
        [
            ("prep", _preprocessor(features, scale=True, min_frequency=min_frequency)),
            (
                "model",
                LogisticRegression(
                    C=float(params["C"]),
                    l1_ratio=0.0,  # pure L2 penalty (the `penalty` argument is deprecated in sklearn 1.9)
                    class_weight=params.get("class_weight"),
                    solver="lbfgs",
                    max_iter=2000,
                    random_state=seed,
                ),
            ),
        ]
    )


def make_lightgbm(
    params: dict[str, Any], features: tuple[Feature, ...], seed: int, min_frequency: int
) -> Pipeline:
    return Pipeline(
        [
            ("prep", _preprocessor(features, scale=False, min_frequency=min_frequency)),
            (
                "model",
                lgb.LGBMClassifier(
                    num_leaves=int(params["num_leaves"]),
                    learning_rate=float(params["learning_rate"]),
                    n_estimators=int(params["n_estimators"]),
                    min_child_samples=int(params["min_child_samples"]),
                    subsample=1.0,
                    colsample_bytree=1.0,
                    reg_lambda=1.0,
                    random_state=seed,
                    n_jobs=4,
                    deterministic=True,
                    force_col_wise=True,
                    verbose=-1,
                ),
            ),
        ]
    )


class SigmoidCalibrator:
    """Platt-style calibration on FROZEN scores: P(y=1) = sigmoid(a * logit(score) + b).

    Fitted on the calibration block only (labels known before the test start). Monotone, so
    it changes probabilities (Brier, log loss, calibration curve) but not rankings.
    """

    def __init__(self, clip: float = 1e-6):
        self.clip = clip

    def _logit(self, scores: np.ndarray) -> np.ndarray:
        p = np.clip(np.asarray(scores, dtype=float), self.clip, 1 - self.clip)
        return np.log(p / (1 - p)).reshape(-1, 1)

    def fit(self, scores: np.ndarray, y: np.ndarray) -> SigmoidCalibrator:
        if len(np.unique(y)) < 2:
            raise ValueError("Calibration block needs both classes")
        self.model_ = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000)
        self.model_.fit(self._logit(scores), np.asarray(y))
        self.slope_ = float(self.model_.coef_[0, 0])
        self.intercept_ = float(self.model_.intercept_[0])
        return self

    def transform(self, scores: np.ndarray) -> np.ndarray:
        return self.model_.predict_proba(self._logit(scores))[:, 1]


def positive_scores(model: Any, X: pd.DataFrame) -> np.ndarray:
    return np.asarray(model.predict_proba(X))[:, 1]
