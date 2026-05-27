from modules import ai_analysis


def test_ai_analysis_compacts_results():
    compact = ai_analysis.compact_results({
        "_urls": ["/lacoste"],
        "gsc": {
            "top_queries": [{"query": "moletom lacoste", "impressions": 1000}],
            "ctr_opps": [],
        },
        "onpage": [{"url": "/lacoste", "grade": "B", "issues": ["title curto"]}],
        "backlog": [{"action": "Otimizar title", "priority": 80}],
        "content_gap": {"gaps": [{"entity": "lacoste"}]},
    })

    assert compact["scope"] == ["/lacoste"]
    assert compact["gsc"]["top_queries"][0]["query"] == "moletom lacoste"
    assert compact["onpage"][0]["issues"] == ["title curto"]
    assert compact["content_gap"][0]["entity"] == "lacoste"
