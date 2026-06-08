import pandas as pd

from collectors import gsc
from modules import gsc_analyzer


def _configure_terms(monkeypatch):
    monkeypatch.setattr("modules.gsc_analyzer.get_brand_aliases", lambda: {"lacoste": ["lacoste"]})
    monkeypatch.setattr("modules.gsc_analyzer.get_product_terms", lambda: {"tenis"})
    monkeypatch.setattr("modules.gsc_analyzer.get_commercial_terms", lambda: {"comprar", "preco"})


def test_gsc_loads_and_normalizes_export_folder(tmp_path, monkeypatch):
    _configure_terms(monkeypatch)
    pd.DataFrame([
        {
            "Query": "tenis lacoste",
            "Clicks": 10,
            "Impressions": 1000,
            "CTR": "0,1%",
            "Position": "8,2",
        }
    ]).to_csv(tmp_path / "Consultas.csv", index=False, encoding="utf-8-sig")

    result = gsc.run(str(tmp_path))

    assert result["total_queries"] == 1
    assert result["top_queries"][0]["query"] == "tenis lacoste"
    assert result["top_queries"][0]["ctr"] == 0.1
    assert result["top_queries"][0]["position"] == 8.2
    assert result["ctr_opps"] == []
    assert result["content_opps"][0]["query_type"] == "product_brand"
    assert "guia de compra" in result["content_opps"][0]["content_action"]
    assert result["benchmarks"]["avg_position_target"] == 6.0


def test_pure_brand_queries_are_not_ctr_tasks(tmp_path, monkeypatch):
    _configure_terms(monkeypatch)
    pd.DataFrame([
        {
            "Query": "lacoste",
            "Clicks": 7,
            "Impressions": 10000,
            "CTR": "0,07%",
            "Position": "4,2",
        },
        {
            "Query": "tenis lacoste",
            "Clicks": 7,
            "Impressions": 10000,
            "CTR": "0,07%",
            "Position": "9,3",
        },
    ]).to_csv(tmp_path / "Consultas.csv", index=False, encoding="utf-8-sig")

    result = gsc.run(str(tmp_path))
    ctr_queries = [row["query"] for row in result["ctr_opps"]]
    content_queries = [row["query"] for row in result["content_opps"]]

    assert "lacoste" not in ctr_queries
    assert "tenis lacoste" not in ctr_queries
    assert "lacoste" not in content_queries
    assert "tenis lacoste" in content_queries


def test_run_from_api_respects_top_limit(monkeypatch):
    _configure_terms(monkeypatch)
    queries = [
        {
            "query": f"tenis lacoste {i}",
            "clicks": i,
            "impressions": 1000 - i,
            "ctr": 1.0,
            "position": 5.0,
        }
        for i in range(30)
    ]
    pages = [
        {
            "page": f"/produto-{i}",
            "clicks": i,
            "impressions": 1000 - i,
            "ctr": 1.0,
            "position": 5.0,
        }
        for i in range(30)
    ]

    result = gsc_analyzer.run_from_api(queries, pages, top_limit=25)

    assert result["top_limit"] == 25
    assert len(result["top_queries"]) == 25
    assert len(result["top_pages"]) == 25


def test_full_audit_gsc_limit_follows_selected_scope():
    import app as dashboard

    assert dashboard._full_audit_gsc_limit("priority") == 100
    assert dashboard._full_audit_gsc_limit("500") == 500
    assert dashboard._full_audit_gsc_limit("2000") == 2000
