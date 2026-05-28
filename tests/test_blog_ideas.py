from modules import blog_ideas


def _configure_blog_terms(monkeypatch):
    monkeypatch.setattr("modules.blog_ideas.get_brand_aliases", lambda: {"lacoste": ["lacoste"]})
    monkeypatch.setattr("modules.blog_ideas.get_product_terms", lambda: {"moletom", "tenis"})


def test_blog_ideas_generates_best_product_brand_title(monkeypatch):
    _configure_blog_terms(monkeypatch)
    result = blog_ideas.suggest_from_gsc({
        "top_queries": [
            {
                "query": "moletom lacoste",
                "impressions": 1200,
                "clicks": 20,
                "ctr": 1.6,
                "position": 8.0,
            }
        ]
    })

    assert result
    assert result[0]["h1"] == "Melhores moletons Lacoste"
    assert result[0]["primary_query"] == "moletom lacoste"
    assert "O que avaliar" in result[0]["sections"][0]


def test_blog_ideas_ai_enrichment_merges_brief(monkeypatch):
    _configure_blog_terms(monkeypatch)
    def fake_call_json(**kwargs):
        return {
            "_ai_enhanced": True,
            "_ai_provider": "gemini",
            "_ai_generated_at": "2026-05-15T10:00:00",
            "h1": "Melhores moletons Lacoste masculinos",
            "search_intent": "commercial investigation",
            "angle": "guia de compra com foco em autenticidade",
            "entities": ["Lacoste", "moletom masculino"],
        }

    monkeypatch.setattr("modules.ai_layer.call_json", fake_call_json)

    idea = blog_ideas.suggest_from_gsc({
        "top_queries": [{"query": "moletom lacoste", "impressions": 1200}]
    })[0]
    result = blog_ideas.enhance_idea_with_ai(idea)

    assert result["ai_enhanced"] is True
    assert result["provider"] == "query_suggester+gemini"
    assert result["h1"] == "Melhores moletons Lacoste masculinos"
    assert result["entities"] == ["Lacoste", "moletom masculino"]


def test_blog_ideas_batch_ai_enrichment_keeps_titles_distinct(monkeypatch):
    _configure_blog_terms(monkeypatch)
    import json

    def fake_call_json(**kwargs):
        prompt = json.loads(kwargs["prompt"])
        slug = prompt["base_idea"]["url_slug"]
        if slug == "melhores-moletons-lacoste":
            return {
                "_ai_enhanced": True,
                "_ai_provider": "groq",
                "_ai_generated_at": "2026-05-15T10:00:00",
                "h1": "Moletom Lacoste original: como escolher sem erro",
                "angle": "autenticidade",
            }
        return {
            "_ai_enhanced": True,
            "_ai_provider": "groq",
            "_ai_generated_at": "2026-05-15T10:00:00",
            "h1": "Tenis Lacoste masculino: quais modelos combinam com seu estilo",
            "angle": "ocasiao de uso",
        }

    monkeypatch.setattr("modules.ai_layer.call_json", fake_call_json)

    ideas = [
        {
            "url_slug": "melhores-moletons-lacoste",
            "h1": "Melhores moletons Lacoste",
            "primary_query": "moletom lacoste",
            "queries": ["moletom lacoste"],
        },
        {
            "url_slug": "melhores-tenis-lacoste",
            "h1": "Melhores tenis Lacoste",
            "primary_query": "tenis lacoste",
            "queries": ["tenis lacoste"],
        },
    ]

    result = blog_ideas.enhance_ideas_with_ai(ideas)

    assert result[0]["ai_enhanced"] is True
    assert result[0]["h1"] != result[1]["h1"]
    assert result[0]["angle"] == "autenticidade"
    assert result[1]["provider"] == "query_suggester+groq"
