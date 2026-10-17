"""Load feature matrices and labels from PostgreSQL.

Features and labels live in different schemas (feature.order_features vs
evaluation.order_labels). They are joined here, by explicit column list (no SELECT *), and the
label columns are kept apart from the model input.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import psycopg
from psycopg import sql

from delivery_risk.config import Splits
from delivery_risk.features.catalog import FEATURES

LABEL_COLUMNS = ("label_eligible", "is_late", "label_available_at", "outcome_population")


def feature_sha256(conn: psycopg.Connection, feature_version: str) -> str:
    """Content hash of the whole materialised matrix for one feature version."""
    row = conn.execute(
        "SELECT encode(sha256(convert_to(coalesce(string_agg(f::text, E'\\n' ORDER BY f.order_id), ''), 'UTF8')), 'hex')"
        " FROM feature.order_features AS f WHERE f.feature_version = %s",
        (feature_version,),
    ).fetchone()
    return row[0]


def load_frame(
    conn: psycopg.Connection, feature_version: str, start: datetime, end: datetime
) -> pd.DataFrame:
    """Scorable orders with prediction_at in [start, end), features + label columns."""
    feature_cols = [sql.Identifier("f", f.name) for f in FEATURES]
    query = sql.SQL(
        "SELECT f.order_id, f.prediction_at, {features}, "
        "l.label_eligible, l.is_late, l.label_available_at, l.outcome_population "
        "FROM feature.order_features AS f "
        "JOIN evaluation.order_labels AS l USING (order_id) "
        "WHERE f.feature_version = %s AND f.prediction_at >= %s AND f.prediction_at < %s "
        "ORDER BY f.prediction_at, f.order_id"
    ).format(features=sql.SQL(", ").join(feature_cols))
    with conn.cursor() as cur:
        cur.execute(query, (feature_version, start, end))
        columns = [d.name for d in cur.description]
        frame = pd.DataFrame(cur.fetchall(), columns=columns)
    return coerce_types(frame)


def coerce_types(frame: pd.DataFrame) -> pd.DataFrame:
    for f in FEATURES:
        if f.name not in frame:
            continue
        if f.kind == "numeric":
            frame[f.name] = pd.to_numeric(frame[f.name], errors="raise").astype("float64")
        else:
            frame[f.name] = frame[f.name].astype(object).where(frame[f.name].notna(), np.nan)
    frame["prediction_at"] = pd.to_datetime(frame["prediction_at"])
    if "label_available_at" in frame:
        frame["label_available_at"] = pd.to_datetime(frame["label_available_at"])
        frame["label_eligible"] = frame["label_eligible"].astype(bool)
        frame["y"] = frame["is_late"].map({True: 1, False: 0}).astype("Int64")
    frame["order_id"] = frame["order_id"].astype(str)
    return frame


def assign_blocks(frame: pd.DataFrame, splits: Splits) -> pd.Series:
    block = pd.Series(pd.NA, index=frame.index, dtype=object)
    for b in splits.blocks:
        block[(frame["prediction_at"] >= b.start) & (frame["prediction_at"] < b.end)] = b.name
    return block


def fit_mask(frame: pd.DataFrame, blocks: tuple[str, ...], label_cutoff: datetime) -> pd.Series:
    """Rows allowed for fitting: in the given blocks, labelled, and label known before the cutoff.

    The label of an order becomes usable only once its customer delivery has happened
    (label_available_at). Orders still in transit at the cutoff are NOT used, even if their
    final outcome is in the historical export.
    """
    return (
        frame["block"].isin(blocks)
        & frame["label_eligible"]
        & (frame["label_available_at"] < label_cutoff)
    )
