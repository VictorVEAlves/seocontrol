from app import _health_score, _scoreable_onpage_warnings


def _page(issues=None, warnings=None):
    return {
        "url": "https://www.secretoutlet.com.br/lacoste",
        "issues": issues or [],
        "warnings": warnings or [],
    }


def test_health_score_ignores_traffic_drops_and_performance_context():
    baseline = {"onpage": [_page()]}
    context_only = {
        "onpage": [_page()],
        "gsc": {
            "top_pages": [{"clicks": 0, "impressions": 100000}],
            "top_queries": [{"position": 80}],
            "low_ctr_pages": [{} for _ in range(20)],
            "cannibalization": [{} for _ in range(500)],
        },
        "gsc_api": {"drops": [{"severity": "critical"} for _ in range(123)]},
    }

    assert _health_score(baseline) == 100
    assert _health_score(context_only) == _health_score(baseline)


def test_health_score_uses_onpage_findings_and_ignores_legacy_meta_keywords():
    healthy = {"onpage": [_page(warnings=["Meta keywords ausente"])]}
    with_seo_finding = {"onpage": [_page(warnings=["Tag canonical ausente"])]}

    assert _health_score(healthy) == 100
    assert _health_score(with_seo_finding) == 95
    assert _scoreable_onpage_warnings(healthy["onpage"][0]) == []
