"""Dashboard smoke and behaviour tests against the e2e database (read-only role)."""

import json

import pytest

from delivery_risk.dashboard.app import create_app
from delivery_risk.dashboard.data import DashboardData
from delivery_risk.dashboard.pages import model_quality, overview, risk_review

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def text_of(component) -> str:
    return json.dumps(component.to_plotly_json(), default=str)


@pytest.fixture(scope="module")
def data(e2e_db):
    return DashboardData("e2e")


@pytest.fixture(scope="module")
def run(data):
    r = data.latest_run()
    assert r is not None
    day = data.review_days(r["run_id"], r["selected_model"]).prediction_date.iloc[3]
    return r, day


def test_three_pages_render_without_error(data):
    for module in (overview, risk_review, model_quality):
        out = text_of(module.layout(data, "e2e"))
        assert "message-error" not in out
        assert "SYNTHETIC TEST DATA" in out  # banner on every page in synthetic modes
    assert "Historical simulation" in text_of(risk_review.layout(data, "e2e"))


def test_main_callbacks_render(data, run):
    r, day = run
    assert "message-error" not in text_of(overview.render_overview(data, None, None, None, None))
    assert "message-error" not in text_of(
        overview.render_segments(data, "main_category", None, None, None, None)
    )
    assert "message-error" not in text_of(
        risk_review.render_review(
            data, r["selected_model"], day, 10, "approval", "risk_rank", "asc", 0
        )
    )
    assert "message-error" not in text_of(model_quality.group_table(data, "customer_region"))
    assert len(model_quality.calibration_figure(data, "lightgbm").data) == 3


def test_filter_reduces_population_and_updates_title_and_denominators(data):
    full = data.overview_kpis("2017-01-01", "2018-08-31")
    north = data.overview_kpis("2017-01-01", "2018-08-31", regions=["North"])
    assert 0 < north["orders"] < full["orders"]
    assert north["eligible_delivered"] < full["eligible_delivered"]
    out = text_of(overview.render_overview(data, "2017-01-01", "2018-08-31", ["North"], None))
    assert "regions: North" in out and f"{north['eligible_delivered']:,} eligible delivered" in out


def test_category_filter_counts_each_order_once(data):
    cats = data.filter_options()["categories"][:3]
    one_by_one = sum(
        data.overview_kpis("2017-01-01", "2018-08-31", categories=[c])["orders"] for c in cats
    )
    together = data.overview_kpis("2017-01-01", "2018-08-31", categories=cats)["orders"]
    assert (
        together <= one_by_one
    )  # orders containing several selected categories are not double counted


def test_at_approval_mode_does_not_reveal_outcomes(data, run):
    r, day = run
    approval = text_of(
        risk_review.render_review(
            data, r["selected_model"], day, 10, "approval", "risk_rank", "asc", 0
        )
    )
    retro = text_of(
        risk_review.render_review(
            data, r["selected_model"], day, 10, "retro", "risk_rank", "asc", 0
        )
    )
    for marker in (
        "Outcome",
        "Delivered",
        "Late?",
        "Delay (days)",
        "precision among known",
        "retro-warning",
    ):
        assert marker not in approval, marker
        assert marker in retro, marker
    table = data.review_table(r["run_id"], r["selected_model"], day, 0.1, retrospective=False)
    assert not {"is_late", "delivered_date", "outcome_population", "delay_days"} & set(
        table.columns
    )


def test_sort_column_comes_from_allow_list(data, run):
    r, day = run
    hostile = data.review_table(
        r["run_id"], r["selected_model"], day, 0.1, False, sort="order_id; DROP TABLE x --"
    )
    default = data.review_table(r["run_id"], r["selected_model"], day, 0.1, False, sort="risk_rank")
    assert hostile.equals(default)
    with pytest.raises(ValueError):
        data.overview_segments("customer_id", "2017-01-01", "2018-08-31")


def test_page_size_is_bounded(data, run):
    r, day = run
    assert (
        len(data.review_table(r["run_id"], r["selected_model"], day, 0.1, False, page_size=10_000))
        <= 50
    )


def test_empty_filter_state(data):
    out = text_of(
        overview.render_overview(
            data, "2017-01-01", "2018-08-31", None, ["category_that_does_not_exist"]
        )
    )
    assert "No orders match these filters" in out


def test_no_model_state(db):
    from helpers import rebuild_fixture_db

    rebuild_fixture_db()
    out = text_of(risk_review.layout(DashboardData("test"), "test"))
    assert "No evaluated model run" in out


def test_database_error_is_readable_without_trace_or_secret(monkeypatch, data):
    monkeypatch.setenv("DR_READER_PASSWORD", "wrong-password-value")
    broken = DashboardData("e2e")
    out = text_of(overview.render_overview(broken, None, None, None, None))
    assert "Database unavailable" in out
    assert "Traceback" not in out and "wrong-password-value" not in out


def test_app_serves_layout(e2e_db):
    app = create_app("e2e")
    client = app.server.test_client()
    assert client.get("/").status_code == 200
    assert client.get("/_dash-layout").status_code == 200
    deps = client.get("/_dash-dependencies")
    assert deps.status_code == 200 and "rv-content" in deps.get_data(as_text=True)
