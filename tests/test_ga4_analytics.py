import pytest

import app as dashboard
import modules.ga4_api as ga4_api


@pytest.fixture(autouse=True)
def _local_auth_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")


def test_ga4_property_normalization():
    assert ga4_api.normalize_property("123456") == "properties/123456"
    assert ga4_api.normalize_property("properties/789") == "properties/789"
    assert ga4_api.normalize_property("") == ""


def test_analytics_page_shows_onboarding_without_ga4_property(monkeypatch):
    monkeypatch.setattr(dashboard, "get_ga4_property", lambda: "")

    response = dashboard.app.test_client().get("/analytics")

    assert response.status_code == 200
    assert "Analytics ainda nao configurado" in response.get_data(as_text=True)


def test_analytics_data_uses_ga4_module(monkeypatch):
    calls = {}
    monkeypatch.setattr(dashboard, "get_ga4_property", lambda: "properties/123")
    monkeypatch.setattr(dashboard, "get_gsc_token_json", lambda: '{"token":"ok"}')

    def fake_get_analytics_data(period_days=28, force=False, channel=None):
        calls["period_days"] = period_days
        calls["force"] = force
        calls["channel"] = channel
        return {
            "kpis": {"revenue": {"value": 100, "prev": 80, "delta": 25}},
            "time_series": [],
            "previous_time_series": [],
            "funnel": [],
            "channels": [],
            "landing_pages": [],
            "products": [],
            "seo_funnel": {},
            "seo_landing_pages": [],
        }

    monkeypatch.setattr(ga4_api, "get_analytics_data", fake_get_analytics_data)

    response = dashboard.app.test_client().get("/analytics/data?period=7&force=1&channel=organic")

    assert response.status_code == 200
    assert response.get_json()["kpis"]["revenue"]["value"] == 100
    assert calls == {"period_days": 7, "force": True, "channel": "organic"}


def test_analytics_data_error_returns_service_status(monkeypatch):
    monkeypatch.setattr(dashboard, "get_ga4_property", lambda: "properties/123")
    monkeypatch.setattr(dashboard, "get_gsc_token_json", lambda: '{"token":"ok"}')
    monkeypatch.setattr(ga4_api, "get_analytics_data", lambda period_days=28, force=False, channel=None: {"error": "ga4 down"})

    response = dashboard.app.test_client().get("/analytics/data?period=28")

    assert response.status_code == 503
    assert response.get_json()["error"] == "ga4 down"


def test_revenue_summary_uses_organic_filter_and_previous_period(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ga4_api, "get_ga4_property", lambda: "properties/123")
    monkeypatch.setattr(
        ga4_api,
        "_cache_file",
        lambda kind, period_days, property_id, channel=None: tmp_path / "revenue.json",
    )

    def fake_run_report(
        property_id,
        start_date,
        end_date,
        metrics,
        dimensions=None,
        limit=100,
        dimension_filter=None,
        timeout=18,
    ):
        calls.append(dimension_filter)
        values = ["1000", "10", "100"] if len(calls) == 1 else ["800", "5", "90"]
        return {"rows": [{"metricValues": [{"value": value} for value in values]}]}

    monkeypatch.setattr(ga4_api, "_run_report", fake_run_report)

    data = ga4_api.get_revenue_summary(period_days=28, force=True, channel="organic")

    assert data["kpis"]["revenue"] == {"value": 1000.0, "prev": 800.0, "delta": 25.0}
    assert data["kpis"]["purchases"]["value"] == 10.0
    assert data["kpis"]["avg_purchase_revenue"]["value"] == 100.0
    assert calls[0]["filter"]["fieldName"] == "sessionSourceMedium"
    assert calls[0]["filter"]["stringFilter"]["value"] == "/ organic"


def test_dashboard_revenue_uses_lightweight_organic_summary(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        dashboard,
        "_analytics_setup_status",
        lambda: (True, "", {"gsc_token_json": '{"token":"ok"}'}),
    )
    monkeypatch.setattr(dashboard, "_persist_dashboard_refreshed_gsc_token", lambda initial="": None)

    def fake_summary(period_days=28, force=False, channel=None):
        calls.update(period_days=period_days, force=force, channel=channel)
        return {"kpis": {"revenue": {"value": 500, "prev": 400, "delta": 25}}}

    monkeypatch.setattr(ga4_api, "get_revenue_summary", fake_summary)

    response = dashboard.app.test_client().get("/dashboard/revenue?period=7&force=1")

    assert response.status_code == 200
    assert response.get_json()["kpis"]["revenue"]["value"] == 500
    assert calls == {"period_days": 7, "force": True, "channel": "organic"}


def test_dashboard_revenue_accepts_custom_date_range(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        dashboard,
        "_analytics_setup_status",
        lambda: (True, "", {"gsc_token_json": '{"token":"ok"}'}),
    )
    monkeypatch.setattr(dashboard, "_persist_dashboard_refreshed_gsc_token", lambda initial="": None)

    def fake_summary(**kwargs):
        calls.update(kwargs)
        return {"period": "2026-06-01 -> 2026-06-10", "kpis": {}}

    monkeypatch.setattr(ga4_api, "get_revenue_summary", fake_summary)

    response = dashboard.app.test_client().get(
        "/dashboard/revenue?start_date=2026-06-01&end_date=2026-06-10"
    )

    assert response.status_code == 200
    assert calls["period_days"] == 10
    assert calls["start_date"] == "2026-06-01"
    assert calls["end_date"] == "2026-06-10"
    assert calls["channel"] == "organic"


def test_dashboard_revenue_reconnect_required_on_revoked_google_token(monkeypatch, tmp_path):
    saved = {}
    token_file = tmp_path / "stale-token.json"
    token_file.write_text("stale", encoding="utf-8")

    monkeypatch.setattr(
        dashboard,
        "_analytics_setup_status",
        lambda: (True, "", {"gsc_token_json": '{"token":"old"}'}),
    )
    monkeypatch.setattr(dashboard, "_is_authenticated", lambda: True)
    monkeypatch.setattr(dashboard, "_active_gsc_files", lambda: (tmp_path / "creds.json", token_file))
    monkeypatch.setattr(dashboard, "_update_active_user_site_config", lambda **kwargs: saved.update(kwargs))
    monkeypatch.setattr(
        ga4_api,
        "get_revenue_summary",
        lambda **kwargs: {
            "error": "Google Analytics nao autorizado para este token. Detalhe: Token has been expired or revoked."
        },
    )

    response = dashboard.app.test_client().get("/dashboard/revenue?period=7")

    payload = response.get_json()
    assert response.status_code == 401
    assert payload["google_reconnect_required"] is True
    assert saved["gsc_token_json"] is None
    assert saved["gsc_account_email"] is None
    assert not token_file.exists()


def test_custom_period_builds_equal_previous_period():
    current_start, current_end, previous_start, previous_end, days = ga4_api._resolved_periods(
        28,
        start_date="2026-06-01",
        end_date="2026-06-10",
    )

    assert str(current_start) == "2026-06-01"
    assert str(current_end) == "2026-06-10"
    assert str(previous_start) == "2026-05-22"
    assert str(previous_end) == "2026-05-31"
    assert days == 10


def test_ga4_funnel_rates_are_calculated():
    payload = {
        "rows": [
            {"dimensionValues": [{"value": "session_start"}], "metricValues": [{"value": "100"}]},
            {"dimensionValues": [{"value": "view_item"}], "metricValues": [{"value": "40"}]},
            {"dimensionValues": [{"value": "add_to_cart"}], "metricValues": [{"value": "10"}]},
            {"dimensionValues": [{"value": "begin_checkout"}], "metricValues": [{"value": "5"}]},
            {"dimensionValues": [{"value": "purchase"}], "metricValues": [{"value": "2"}]},
        ]
    }

    funnel = ga4_api._build_funnel(payload)

    assert funnel[0]["count"] == 100
    assert funnel[-1]["event"] == "purchase"
    assert funnel[-1]["from_start_rate"] == 2.0
    assert funnel[-1]["step_rate"] == 40.0


def test_ga4_channel_presets():
    assert ga4_api._channel_filter_values("all") == []
    assert ga4_api._channel_filter_values("organic") == ["sessionSourceMedium contains / organic"]
    assert ga4_api._channel_filter_values("Paid Search") == ["Paid Search"]


def test_ga4_organic_filter_matches_source_medium_report():
    dimension_filter = ga4_api._channel_dimension_filter("organic")

    assert dimension_filter["filter"]["fieldName"] == "sessionSourceMedium"
    assert dimension_filter["filter"]["stringFilter"]["matchType"] == "CONTAINS"
    assert dimension_filter["filter"]["stringFilter"]["value"] == "/ organic"


def test_seo_funnel_crosses_gsc_and_ga4():
    gsc = {"period": "2026-06-01 -> 2026-06-07", "impressions": 1000, "clicks": 100}
    summary = {"sessions": 80, "ecommercePurchases": 4, "totalRevenue": 1200}
    funnel = [
        {"event": "view_item", "count": 50},
        {"event": "add_to_cart", "count": 20},
        {"event": "begin_checkout", "count": 10},
    ]

    seo = ga4_api._build_seo_funnel(gsc, summary, funnel)

    assert seo["steps"][0]["value"] == 1000
    assert seo["steps"][1]["step_rate"] == 10.0
    assert seo["steps"][2]["step_rate"] == 80.0
    assert seo["steps"][-1]["value"] == 1200
    assert seo["metrics"]["session_to_purchase_rate"] == 5.0
    assert seo["metrics"]["revenue_per_click"] == 12.0


def test_seo_landing_pages_join_gsc_and_ga4_by_path():
    gsc_pages = [{"page": "https://example.com/lacoste", "impressions": 1000, "clicks": 80, "position": 3}]
    ga4_pages = [{"landing_page": "/lacoste?utm_source=x", "sessions": 70, "purchases": 3, "revenue": 900}]

    rows = ga4_api._build_seo_landing_pages(gsc_pages, ga4_pages)

    assert rows[0]["landing_page"] == "/lacoste"
    assert rows[0]["clicks"] == 80
    assert rows[0]["sessions"] == 70
    assert rows[0]["revenue_per_click"] == 11.25
