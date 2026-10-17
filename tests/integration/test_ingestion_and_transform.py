"""Integration tests on real PostgreSQL with the hand-written edge-case fixtures."""

from decimal import Decimal

import pytest
from helpers import FIXTURES, copy_fixtures, query, rebuild_fixture_db, rewrite, scalar

from delivery_risk.ingestion.load_raw import ingest_directory
from delivery_risk.pipeline import PublicationBlocked, run_features, run_transform

pytestmark = pytest.mark.integration


# ----------------------------------------------------------------- ingestion & manifest
def test_manifest_records_every_file(fixture_db):
    rows = query(
        "test",
        "SELECT dataset, file_size_bytes, length(sha256), row_count, status, acquired_at_basis FROM meta.load_manifest",
    )
    assert len(rows) == 9
    assert all(r[1] > 0 and r[2] == 64 and r[4] == "loaded" and r[5] == "file_mtime" for r in rows)
    counts = dict((r[0], r[3]) for r in rows)
    assert counts["orders"] == 16 and counts["reviews"] == 12  # multi-line review counted once


def test_reingesting_the_same_batch_is_a_no_op(fixture_db):
    before = scalar("test", "SELECT count(*) FROM raw.orders")
    results = ingest_directory("test", FIXTURES)
    assert {r.status for r in results} == {"skipped_already_loaded"}
    assert scalar("test", "SELECT count(*) FROM raw.orders") == before
    assert scalar("test", "SELECT count(*) FROM meta.load_manifest") == 9


def test_raw_keeps_batch_file_and_line(fixture_db):
    line, file_name = query(
        "test", "SELECT source_line, source_file FROM raw.reviews WHERE review_id = 'r08'"
    )[0]
    assert (line, file_name) == (10, "olist_order_reviews_dataset.csv")


# ----------------------------------------------------------------- grains and keys
@pytest.mark.parametrize(
    "table, key",
    [
        ("core.orders", "order_id"),
        ("core.order_items", "order_id, order_item_id"),
        ("core.payments", "order_id, payment_sequential"),
        ("core.reviews", "review_id, order_id"),
        ("core.order_sellers", "order_id, seller_id"),
        ("core.customers", "customer_id"),
        ("mart.order_summary", "order_id"),
        ("mart.seller_order_exposure", "order_id, seller_id"),
        ("mart.order_categories", "order_id, category"),
        ("evaluation.order_labels", "order_id"),
        ("feature.order_features", "order_id, feature_version"),
        ("feature.order_seller_history", "order_id, seller_id"),
    ],
)
def test_published_grain_is_unique(fixture_db, table, key):
    assert scalar("test", f"SELECT count(*) - count(DISTINCT ({key})) FROM {table}") == 0


def test_foreign_keys_exist(fixture_db):
    fks = scalar(
        "test",
        "SELECT count(*) FROM information_schema.table_constraints WHERE constraint_schema = 'core' AND constraint_type = 'FOREIGN KEY'",
    )
    assert fks >= 8


def test_staging_counts_and_quarantine_reasons(fixture_db):
    assert (
        scalar("test", "SELECT count(*) FROM core.orders") == 14
    )  # 16 raw - 1 strict duplicate - 1 invalid timestamp
    reasons = dict(
        ((d, r), n)
        for d, r, n in query(
            "test", "SELECT dataset, reason, count(*) FROM quality.quarantine GROUP BY 1, 2"
        )
    )
    assert reasons == {
        ("orders", "invalid_purchase_timestamp"): 1,
        ("reviews", "invalid_review_score"): 1,
        ("order_items", "parent_order_quarantined"): 1,
        ("payments", "parent_order_quarantined"): 1,
    }
    dups = query("test", "SELECT dataset, key_value, kind FROM quality.duplicate_log ORDER BY 1")
    assert dups == [
        ("orders", "o02", "strict_duplicate"),
        ("payments", "o05#1", "strict_duplicate"),
    ]


def test_zip_prefix_normalised_and_raw_kept(fixture_db):
    assert query(
        "test", "SELECT zip_prefix_raw, zip_prefix FROM core.customers WHERE customer_id = 'c01'"
    ) == [("1037", "01037")]
    assert query("test", "SELECT zip_prefix FROM core.customers WHERE customer_id = 'c10'") == [
        (None,)
    ]


def test_geo_prefix_aggregated_before_join(fixture_db):
    lat, lng, n_raw, n_points, n_out = query(
        "test",
        "SELECT lat, lng, n_raw_rows, n_distinct_points, n_outlier_points FROM core.geo_prefix WHERE zip_prefix = '01037'",
    )[0]
    assert (n_raw, n_points, n_out) == (4, 2, 1)
    assert lat == pytest.approx(-23.5458) and lng == pytest.approx(-46.63965)


def test_categories_missing_and_untranslated(fixture_db):
    rows = dict(query("test", "SELECT product_id, category FROM core.products"))
    assert (
        rows["p3"] == "missing_category"
        and rows["p4"] == "pc_gamer"
        and rows["p1"] == "bed_bath_table"
    )


# ----------------------------------------------------------------- fan-out prevention
def test_multi_item_multi_payment_order_is_not_multiplied(fixture_db):
    row = query(
        "test",
        "SELECT n_items, item_value_brl, freight_value_brl, n_payments, paid_value_brl, n_reviews, review_score_mean,"
        " n_distinct_sellers FROM mart.order_summary WHERE order_id = 'o07'",
    )[0]
    assert row == (
        3,
        Decimal("130.00"),
        Decimal("25.00"),
        3,
        Decimal("155.00"),
        2,
        Decimal("3.00"),
        2,
    )


def test_naive_join_would_multiply_and_mart_does_not(fixture_db):
    naive = scalar(
        "test",
        "SELECT sum(i.price_brl) FROM core.order_items i JOIN core.payments p USING (order_id) "
        "JOIN core.reviews r USING (order_id) WHERE order_id = 'o07'",
    )
    assert naive == Decimal(
        "780.00"
    )  # 3 items x 3 payments x 2 reviews -> item value counted 6 times
    assert scalar(
        "test", "SELECT item_value_brl FROM mart.order_summary WHERE order_id = 'o07'"
    ) == Decimal("130.00")


def test_totals_invariant_across_marts(fixture_db):
    assert scalar("test", "SELECT sum(item_value_brl) FROM mart.order_summary") == scalar(
        "test", "SELECT sum(price_brl) FROM core.order_items"
    )
    assert scalar("test", "SELECT sum(paid_value_brl) FROM mart.order_summary") == Decimal(
        "1915.50"
    )
    assert scalar("test", "SELECT sum(item_value_brl) FROM mart.order_categories") == scalar(
        "test", "SELECT sum(price_brl) FROM core.order_items"
    )


def test_value_gap_reported_not_forced(fixture_db):
    assert query(
        "test",
        "SELECT items_freight_minus_paid_brl, flag_value_gap FROM mart.order_summary WHERE order_id = 'o05'",
    ) == [(Decimal("-5.00"), True)]


def test_order_counted_once_per_seller(fixture_db):
    rows = query(
        "test",
        "SELECT seller_id, seller_items FROM mart.seller_order_exposure WHERE order_id = 'o07' ORDER BY 1",
    )
    assert rows == [("s_b", 2), ("s_c", 1)]  # s_b has two items but one exposure row


def test_monthly_operations_reconcile(fixture_db):
    assert scalar("test", "SELECT sum(orders) FROM mart.monthly_operations") == 14
    assert scalar("test", "SELECT sum(late_orders) FROM mart.monthly_operations") == scalar(
        "test", "SELECT count(*) FROM evaluation.order_labels WHERE is_late"
    )


def test_all_critical_assertions_pass_and_warnings_are_counted(fixture_db):
    rows = dict(
        ((n, s), v)
        for n, s, v in query(
            "test", "SELECT check_name, severity, violation_count FROM quality.assertion_results"
        )
    )
    assert all(v == 0 for (n, s), v in rows.items() if s == "critical")
    assert rows[("strict_duplicate_keys", "warning")] == 2
    assert rows[("orders_missing_approval", "warning")] == 1


# ----------------------------------------------------------------- labels and eligibility
EXPECTED_LABELS = {
    "o01": ("delivered", True, True, True, 5),
    "o02": (
        "delivered",
        True,
        True,
        False,
        0,
    ),  # delivered on the promised day (afternoon) -> on time
    "o05": ("delivered", True, True, False, -4),
    "o06": ("canceled", True, False, None, None),
    "o09": ("not_observed", True, False, None, None),
    "o10": (
        "delivered",
        False,
        False,
        None,
        None,
    ),  # missing approval, never replaced by purchase time
    "o11": ("delivered", False, False, None, None),
    "o12": ("delivered", True, False, None, None),
    "o15": ("delivered_missing_date", True, False, None, None),
}


@pytest.mark.parametrize("order_id", sorted(EXPECTED_LABELS))
def test_label_population_and_target(fixture_db, order_id):
    row = query(
        "test",
        "SELECT outcome_population, scoring_eligible, label_eligible, is_late, delay_days FROM evaluation.order_labels WHERE order_id = %s",
        (order_id,),
    )[0]
    assert row == EXPECTED_LABELS[order_id]


def test_exclusion_reasons(fixture_db):
    reasons = dict(
        query(
            "test",
            "SELECT order_id, label_exclusion_reason FROM evaluation.order_labels WHERE NOT label_eligible",
        )
    )
    assert reasons == {
        "o06": "outcome_canceled",
        "o09": "outcome_not_observed",
        "o10": "missing_approval",
        "o11": "approval_before_purchase",
        "o12": "delivery_before_approval",
        "o15": "outcome_delivered_missing_date",
    }


def test_transform_is_idempotent(fixture_db):
    snapshot = "SELECT md5(string_agg(t::text, '|' ORDER BY order_id)) FROM mart.order_summary t"
    before = scalar("test", snapshot)
    run_transform("test")
    run_features("test")
    assert scalar("test", snapshot) == before
    assert scalar("test", "SELECT count(*) FROM quality.quarantine") == 4


# ----------------------------------------------------------------- blocked publication is atomic
def test_key_conflict_blocks_publication_and_keeps_previous_tables(fixture_db, tmp_path):
    before = scalar(
        "test", "SELECT md5(string_agg(t::text, '|' ORDER BY order_id)) FROM core.orders t"
    )
    raw = copy_fixtures(tmp_path / "conflict")
    rewrite(
        raw / "olist_orders_dataset.csv",
        "o15,c15,delivered,",
        "o01,c01,canceled,2018-01-02 10:00:00,,,,2018-01-10 00:00:00\no15,c15,delivered,",
    )
    try:
        ingest_directory("test", raw)
        with pytest.raises(PublicationBlocked, match="key_conflicts"):
            run_transform("test")
        assert (
            scalar(
                "test", "SELECT md5(string_agg(t::text, '|' ORDER BY order_id)) FROM core.orders t"
            )
            == before
        )
        assert (
            scalar("test", "SELECT count(*) FROM quality.quarantine") == 4
        )  # quality tables rolled back too
    finally:
        rebuild_fixture_db()


def test_orphan_reference_blocks_publication(fixture_db, tmp_path):
    raw = copy_fixtures(tmp_path / "orphan")
    rewrite(
        raw / "olist_order_items_dataset.csv",
        "o15,1,p2,s_d",
        "o99,1,p2,s_d,2018-03-23 10:00:00,1.00,1.00\no15,1,p2,s_d",
    )
    try:
        ingest_directory("test", raw)
        with pytest.raises(PublicationBlocked, match="orphan_references"):
            run_transform("test")
    finally:
        rebuild_fixture_db()
