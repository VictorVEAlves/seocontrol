from actions import backlog
from modules import change_memory
from modules.backlog import _priority


def test_priority_combines_impact_confidence_and_effort():
    assert _priority(100, 80, 4) == 20.0
    assert _priority(50, 100, 0) == 50.0


def test_backlog_is_sorted_by_priority(monkeypatch):
    monkeypatch.setattr(change_memory, "DEFAULT_CHANGELOG_CANDIDATES", [])
    results = {
        "gsc": {
            "content_opps": [
                {
                    "query": "tenis lacoste",
                    "clicks": 10,
                    "impressions": 5000,
                    "ctr": 0.5,
                    "position": 8,
                    "query_type": "product_brand",
                    "content_action": "Criar ou otimizar guia de compra para produto + marca",
                    "opportunity_score": 70,
                }
            ],
            "quick_wins": [],
            "low_ctr_pages": [],
        },
        "onpage": [
            {
                "url": "https://www.secretoutlet.com.br/lacoste",
                "issues": ["H1 ausente"],
                "warnings": [],
                "grade": "D",
                "score": 40,
            }
        ],
    }

    items = backlog.run(results, limit=10)

    assert len(items) == 2
    assert items[0]["priority"] >= items[1]["priority"]
    assert items[0]["source"] == "gsc"
