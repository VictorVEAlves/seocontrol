import pandas as pd

from collectors import gsc


def test_gsc_loads_and_normalizes_export_folder(tmp_path):
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


def test_pure_brand_queries_are_not_ctr_tasks(tmp_path):
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
