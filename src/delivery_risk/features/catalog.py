"""Allow-list of predictive features (single source of truth).

The SQL matrix (sql/features/030_order_features.sql) must expose exactly these columns plus the
technical keys. Tests compare both. docs/feature_catalog.md documents each feature's source,
availability time, missing-value handling and leakage risk.
"""

from __future__ import annotations

from dataclasses import dataclass

KEY_COLUMNS = ("order_id", "feature_version", "prediction_at")


@dataclass(frozen=True)
class Feature:
    name: str
    group: str  # order | geo | catalog | seller_history
    kind: str  # numeric | categorical


FEATURES: tuple[Feature, ...] = (
    # Order attributes known at approval (static snapshot; assumption documented)
    Feature("n_items", "order", "numeric"),
    Feature("n_distinct_products", "order", "numeric"),
    Feature("n_distinct_sellers", "order", "numeric"),
    Feature("item_value_brl", "order", "numeric"),
    Feature("freight_value_brl", "order", "numeric"),
    Feature("freight_to_item_value_ratio", "order", "numeric"),
    Feature("promised_lead_days", "order", "numeric"),
    Feature("approval_delay_hours", "order", "numeric"),
    Feature("shipping_limit_days_after_approval", "order", "numeric"),
    Feature("approval_weekday", "order", "categorical"),
    Feature("approval_hour", "order", "numeric"),
    # Geography (customer/seller location and prefix-centroid distance)
    Feature("customer_state", "geo", "categorical"),
    Feature("primary_seller_state", "geo", "categorical"),
    Feature("any_seller_same_state", "geo", "numeric"),
    Feature("max_seller_distance_km", "geo", "numeric"),
    # Product catalog snapshot (ablated in the sensitivity analysis)
    Feature("main_category", "catalog", "categorical"),
    Feature("n_distinct_categories", "catalog", "numeric"),
    Feature("total_weight_g", "catalog", "numeric"),
    Feature("max_product_volume_cm3", "catalog", "numeric"),
    # As-of seller history (outcomes known strictly before approval)
    Feature("sh_known_outcomes_total_min", "seller_history", "numeric"),
    Feature("sh_known_outcomes_90d_min", "seller_history", "numeric"),
    Feature("sh_late_rate_90d_mean", "seller_history", "numeric"),
    Feature("sh_late_rate_90d_max", "seller_history", "numeric"),
    Feature("sh_mean_positive_delay_90d_mean", "seller_history", "numeric"),
    Feature("sh_approvals_30d_mean", "seller_history", "numeric"),
    Feature("sh_approvals_30d_max", "seller_history", "numeric"),
    Feature("sh_any_seller_no_history", "seller_history", "numeric"),
)

FEATURE_NAMES = tuple(f.name for f in FEATURES)
GROUPS = tuple(dict.fromkeys(f.group for f in FEATURES))

# Columns that must never enter the matrix (targets, post-approval events, identifiers).
FORBIDDEN_COLUMNS = frozenset(
    {
        "order_id",
        "customer_id",
        "customer_unique_id",
        "seller_id",
        "product_id",
        "order_status",
        "outcome_population",
        "delivered_customer_at",
        "delivered_carrier_at",
        "delivered_date",
        "delivery_duration_days",
        "is_late",
        "delay_days",
        "positive_delay_days",
        "label_available_at",
        "label_eligible",
        "review_score",
        "review_score_mean",
        "n_reviews",
        "paid_value_brl",
        "n_payments",
        "payment_types",
        "max_installments",
    }
)


def select_features(drop_groups: tuple[str, ...] = ()) -> tuple[Feature, ...]:
    unknown = set(drop_groups) - set(GROUPS)
    if unknown:
        raise ValueError(f"Unknown feature groups: {sorted(unknown)}")
    return tuple(f for f in FEATURES if f.group not in drop_groups)


def numeric_names(features: tuple[Feature, ...]) -> list[str]:
    return [f.name for f in features if f.kind == "numeric"]


def categorical_names(features: tuple[Feature, ...]) -> list[str]:
    return [f.name for f in features if f.kind == "categorical"]
