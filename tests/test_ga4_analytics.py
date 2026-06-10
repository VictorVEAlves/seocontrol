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
