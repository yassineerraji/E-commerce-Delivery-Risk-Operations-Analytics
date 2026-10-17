"""Point-in-time feature tests: expected values, strict boundaries and mutations of the future."""

from datetime import timedelta

import pytest
from helpers import copy_fixtures, query, rebuild_fixture_db, rewrite

pytestmark = pytest.mark.integration

HISTORY_SQL = """SELECT order_id, seller_id, known_outcomes_total, known_outcomes_90d, late_outcomes_90d,
                        late_rate_90d::float, mean_positive_delay_90d::float, approvals_30d
                 FROM feature.order_seller_history ORDER BY 1, 2"""


def features_snapshot() -> dict[str, tuple]:
    rows = query("test", "SELECT f.* FROM feature.order_features f ORDER BY order_id")
    return {r[0]: r for r in rows}


def history(order_id: str, seller_id: str) -> tuple:
    return query(
        "test",
        HISTORY_SQL.replace("ORDER BY 1, 2", "WHERE order_id = %s AND seller_id = %s"),
        (order_id, seller_id),
    )[0][2:]


@pytest.fixture(scope="module")
def baseline(fixture_db):
    return features_snapshot()


def test_expected_history_for_target_order(baseline):
    # o05 approved at t = 2018-01-20 10:00. Known before t: o01 (late, +5 days) and o02 (on time, delivered
    # on the promised day). o03 is delivered after t, o04 exactly at t: both excluded.
    assert history("o05", "s_a") == (2, 2, 1, 0.5, 2.5, 4)


def test_same_second_approval_is_not_counted(baseline):
    # o06 is approved at exactly the same second as o05: neither counts the other in approvals_30d.
    assert history("o06", "s_a")[-1] == 4
    assert history("o05", "s_a")[-1] == 4


def test_seller_without_history_and_equal_weight_mean(baseline):
    assert history("o07", "s_b") == (1, 1, 0, 0.0, 0.0, 1)
    assert history("o07", "s_c") == (0, 0, 0, None, None, 0)
    row = query(
        "test",
        """SELECT sh_known_outcomes_total_min, sh_late_rate_90d_mean, sh_approvals_30d_mean,
                                  sh_approvals_30d_max, sh_any_seller_no_history FROM feature.order_features WHERE order_id = 'o07'""",
    )[0]
    assert row == (0, 0.0, 0.5, 1, 1)


def test_scoring_population_ignores_final_status(baseline):
    # canceled (o06), not observed (o09), delivered without date (o15) and delivered-before-approval (o12)
    # orders are scored; orders without approval or with approval before purchase are not.
    assert {"o06", "o09", "o12", "o15"} <= set(baseline)
    assert not {"o10", "o11", "o14"} & set(baseline)


def _compare_before(new: dict, old: dict, cutoff_sql: str) -> None:
    """Features of every order approved before the cutoff must be byte-identical."""
    earlier = {
        r[0]
        for r in query(
            "test",
            f"SELECT order_id FROM evaluation.order_labels WHERE prediction_at < {cutoff_sql}",
        )
    }
    assert earlier, "nothing to compare"
    for order_id in earlier & set(old):
        assert new[order_id] == old[order_id], order_id


def test_mutating_future_delivery_and_reviews_does_not_change_past_features(baseline, tmp_path):
    raw = copy_fixtures(tmp_path / "future_mutation")
    # o03 was delivered 2018-01-25 18:00; move it far later. Change reviews of o01/o07 too.
    rewrite(
        raw / "olist_orders_dataset.csv",
        "2018-01-25 18:00:00,2018-01-20",
        "2018-03-28 18:00:00,2018-01-20",
    )
    rewrite(raw / "olist_order_reviews_dataset.csv", "r01,o01,2,", "r01,o01,5,")
    rewrite(raw / "olist_order_reviews_dataset.csv", "r07a,o07,4,", "r07a,o07,1,")
    try:
        rebuild_fixture_db(raw)
        _compare_before(features_snapshot(), baseline, "'2018-01-25 18:00:00'")
    finally:
        rebuild_fixture_db()


def test_adding_future_orders_does_not_change_past_features(baseline, tmp_path):
    raw = copy_fixtures(tmp_path / "future_orders")
    rewrite(
        raw / "olist_orders_dataset.csv",
        "o15,c15,",
        "o20,c15,delivered,2018-03-28 08:00:00,2018-03-28 09:00:00,2018-03-29 09:00:00,2018-03-30 10:00:00,2018-04-02 00:00:00\no15,c15,",
    )
    rewrite(
        raw / "olist_order_items_dataset.csv",
        "o15,1,",
        "o20,1,p1,s_a,2018-03-30 09:00:00,10.00,2.00\no20,2,p2,s_b,2018-03-30 09:00:00,10.00,2.00\no15,1,",
    )
    try:
        rebuild_fixture_db(raw)
        new = features_snapshot()
        assert "o20" in new
        _compare_before(new, baseline, "'2018-03-28 09:00:00'")
    finally:
        rebuild_fixture_db()


def test_strict_boundary_one_second_before_t_is_included(baseline, tmp_path):
    raw = copy_fixtures(tmp_path / "boundary")
    # o04 delivered exactly at t(o05) -> excluded. One second earlier -> included.
    rewrite(
        raw / "olist_orders_dataset.csv",
        "2018-01-20 10:00:00,2018-01-18",
        "2018-01-20 09:59:59,2018-01-18",
    )
    try:
        rebuild_fixture_db(raw)
        assert history("o05", "s_a")[:3] == (3, 3, 2)
    finally:
        rebuild_fixture_db()


def test_duplicated_item_does_not_change_seller_features(baseline, tmp_path):
    raw = copy_fixtures(tmp_path / "dup_item")
    rewrite(
        raw / "olist_order_items_dataset.csv",
        "o07,3,p2,s_c",
        "o07,4,p1,s_b,2018-02-04 10:10:00,50.00,10.00\no07,3,p2,s_c",
    )
    sh_cols = (
        "sh_known_outcomes_total_min, sh_known_outcomes_90d_min, sh_late_rate_90d_mean, sh_late_rate_90d_max, "
        "sh_mean_positive_delay_90d_mean, sh_approvals_30d_mean, sh_approvals_30d_max, sh_any_seller_no_history, n_distinct_sellers"
    )
    before = query("test", f"SELECT {sh_cols} FROM feature.order_features WHERE order_id = 'o07'")
    try:
        rebuild_fixture_db(raw)
        assert (
            query("test", f"SELECT {sh_cols} FROM feature.order_features WHERE order_id = 'o07'")
            == before
        )
        assert query(
            "test", "SELECT n_items FROM feature.order_features WHERE order_id = 'o07'"
        ) == [(4,)]
    finally:
        rebuild_fixture_db()


# ------------------------------------------------------------------ reference implementation
def reference_history(mode: str) -> dict[tuple[str, str], tuple]:
    """Plain-Python as-of computation, written independently from the SQL."""
    labels = {
        r[0]: r
        for r in query(
            mode,
            "SELECT order_id, prediction_at, scoring_eligible, label_eligible, "
            "label_available_at, is_late, delay_days FROM evaluation.order_labels",
        )
    }
    approved = dict(query(mode, "SELECT order_id, approved_at FROM core.orders"))
    by_seller: dict[str, list[str]] = {}
    pairs = query(mode, "SELECT order_id, seller_id FROM core.order_sellers")
    for o, s in pairs:
        by_seller.setdefault(s, []).append(o)
    out = {}
    for o, s in pairs:
        _, t, scorable, *_ = labels[o]
        if not scorable:
            continue
        known = [
            labels[o2] for o2 in by_seller[s] if o2 != o and labels[o2][3] and labels[o2][4] < t
        ]
        recent = [k for k in known if k[4] >= t - timedelta(days=90)]
        late = sum(1 for k in recent if k[5])
        approvals = sum(
            1
            for o2 in by_seller[s]
            if o2 != o and approved[o2] is not None and t - timedelta(days=30) <= approved[o2] < t
        )
        out[(o, s)] = (
            len(known),
            len(recent),
            late,
            late / len(recent) if recent else None,
            sum(max(k[6], 0) for k in recent) / len(recent) if recent else None,
            approvals,
        )
    return out


def _assert_matches_reference(mode: str) -> None:
    ref = reference_history(mode)
    sql_rows = {(r[0], r[1]): r[2:] for r in query(mode, HISTORY_SQL)}
    assert sql_rows.keys() == ref.keys()
    for key, expected in ref.items():
        got = sql_rows[key]
        assert got[:3] == expected[:3] and got[5] == expected[5], key
        for g, e in zip(got[3:5], expected[3:5], strict=True):
            assert (g is None and e is None) or g == pytest.approx(e), key


def test_sql_history_matches_reference_on_fixtures(baseline):
    _assert_matches_reference("test")


@pytest.mark.slow
def test_sql_history_matches_reference_on_synthetic(e2e_db):
    _assert_matches_reference("e2e")
