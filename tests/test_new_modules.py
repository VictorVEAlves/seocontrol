from modules import content_gap, link_suggestions, serp_snippets


def test_snippets_flags_short_title_and_description():
    result = serp_snippets.run([
        {
            "url": "https://www.secretoutlet.com.br/lacoste",
            "title": "Lacoste",
            "description": "Curta",
        }
    ])

    assert result["total"] == 1
    assert result["issues"][0]["title_status"] == "short"
    assert result["issues"][0]["description_status"] == "short"


def test_content_gap_groups_queries_by_entity_and_intent():
    result = content_gap.run({
        "top_queries": [
            {"query": "como usar lacoste", "impressions": 500, "clicks": 10},
            {"query": "lacoste outlet", "impressions": 700, "clicks": 20},
        ]
    })

    assert result["gaps"]
    assert result["gaps"][0]["impressions"] >= 500


def test_link_suggestions_from_cluster_missing_links():
    result = link_suggestions.run({
        "cluster_analysis": [
            {
                "brand": "lacoste",
                "pillar": "/lacoste",
                "missing_from_pillar": ["/tenis-lacoste"],
                "missing_to_pillar": ["/polos-lacoste"],
            }
        ]
    })

    assert result["total"] == 2
    assert result["suggestions"][0]["source"] == "/lacoste"
