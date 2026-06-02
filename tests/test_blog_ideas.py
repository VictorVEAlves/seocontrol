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


def test_blog_ideas_ai_strategic_studies_queries_without_brand_product(monkeypatch):
    import json

    captured = {}

    def fake_call_json(**kwargs):
        payload = json.loads(kwargs["prompt"])
        captured["payload"] = payload
        return {
            "_ai_enhanced": True,
            "_ai_provider": "gemini",
            "_ai_generated_at": "2026-06-02T10:00:00",
            "ideas": [
                {
                    "h1": "Como escolher o tamanho certo de tenis online",
                    "meta_title": "Como escolher o tamanho certo de tenis online",
                    "meta_description": "Veja como comparar medidas, formas e sinais de conforto antes de comprar tenis online com mais seguranca.",
                    "primary_query": "como saber tamanho certo tenis",
                    "source_queries": ["como saber tamanho certo tenis", "tenis forma grande"],
                    "search_intent": "informational",
                    "audience_question": "Como evitar comprar tenis no tamanho errado?",
                    "angle": "duvida pratica do publico",
                    "content_type": "guia",
                    "seasonality": "evergreen",
                    "recommended_publish_month": "evergreen",
                    "opportunity_reason": "Consulta de duvida com intencao pre-compra.",
                    "content_gap": "Nao ha conteudo salvo sobre tamanho de tenis.",
                    "priority": 76,
                    "sections": ["Como medir o pe", "Como comparar formas", "Quando trocar o tamanho"],
                    "faq": ["Tenis laceia com o uso?"],
                }
            ],
        }

    monkeypatch.setattr("modules.ai_layer.call_json", fake_call_json)
    monkeypatch.setattr(blog_ideas, "_load_existing_content", lambda limit=80: [
        {"title": "Ideia antiga sobre looks de inverno", "url": "/looks-inverno"}
    ])
    monkeypatch.setattr(blog_ideas, "save_ideas", lambda ideas: None)

    result = blog_ideas.run({
        "top_queries": [
            {"query": "como saber tamanho certo tenis", "impressions": 1200, "clicks": 20, "position": 7.5},
            {"query": "looks inverno masculino", "impressions": 800, "clicks": 8, "position": 11.0},
        ]
    }, top=5, use_ai=True, provider="gemini", api_key="fake-key")

    assert result
    assert result[0]["h1"] == "Como escolher o tamanho certo de tenis online"
    assert result[0]["provider"] == "query_suggester+gemini"
    assert result[0]["queries"] == ["como saber tamanho certo tenis", "tenis forma grande"]
    assert captured["payload"]["existing_content"][0]["title"] == "Ideia antiga sobre looks de inverno"
    assert "duvida" in captured["payload"]["queries"][0]["intent_tags"]


def test_blog_ideas_falls_back_to_query_clusters_when_ai_fails(monkeypatch):
    def fake_call_json(**_kwargs):
        return {
            "_ai_enhanced": False,
            "_ai_error": "JSON truncado pelo provider",
            "_ai_provider": "gemini",
        }

    monkeypatch.setattr("modules.ai_layer.call_json", fake_call_json)
    monkeypatch.setattr(blog_ideas, "save_ideas", lambda ideas: None)

    result = blog_ideas.run({
        "top_queries": [
            {"query": "como saber tamanho certo tenis", "impressions": 1200, "clicks": 20, "position": 7.5},
            {"query": "tenis forma grande", "impressions": 700, "clicks": 8, "position": 9.0},
        ]
    }, top=3, use_ai=True, provider="gemini", api_key="fake-key")

    assert result
    assert result[0]["provider"] == "query_suggester+fallback"
    assert result[0]["primary_query"] == "como saber tamanho certo tenis"
    assert result[0]["_ai_error"] == "JSON truncado pelo provider"
    assert "Como saber tamanho certo tenis" in result[0]["h1"]


def test_blog_ideas_fallback_ignores_broad_navigation_queries(monkeypatch):
    monkeypatch.setattr("modules.blog_ideas.get_product_terms", lambda: {"tenis", "camisa"})
    monkeypatch.setattr(blog_ideas, "save_ideas", lambda ideas: None)

    result = blog_ideas.suggest_strategic_from_gsc({
        "top_queries": [
            {"query": "reserva", "impressions": 40000, "clicks": 1000, "position": 1.0},
            {"query": "lacoste", "impressions": 15000, "clicks": 600, "position": 1.0},
            {"query": "tenis reserva masculino", "impressions": 6000, "clicks": 100, "position": 5.0},
            {"query": "como saber tamanho certo tenis", "impressions": 1200, "clicks": 20, "position": 7.5},
        ]
    }, top=5)

    assert result
    titles = [item["h1"].lower() for item in result]
    assert not any(title.startswith("reserva:") for title in titles)
    assert not any(title.startswith("lacoste:") for title in titles)
    assert any("tenis" in title for title in titles)


def test_blog_ideas_save_creates_runtime_directory(monkeypatch, tmp_path):
    ideas_file = tmp_path / "runtime" / "content" / "site-key" / "blog_ideas.json"
    monkeypatch.setattr(blog_ideas, "IDEAS_FILE", ideas_file)

    blog_ideas.save_ideas([
        {
            "url_slug": "melhores-tenis-lacoste",
            "h1": "Melhores tenis Lacoste",
            "primary_query": "tenis lacoste",
        }
    ])

    assert ideas_file.exists()
    assert "melhores-tenis-lacoste" in ideas_file.read_text(encoding="utf-8")


def test_blog_ideas_save_does_not_fail_on_read_only_runtime(monkeypatch):
    class BadParent:
        def mkdir(self, **_kwargs):
            raise OSError("read-only file system")

    class BadPath:
        parent = BadParent()

        def exists(self):
            return False

    monkeypatch.setattr(blog_ideas, "IDEAS_FILE", BadPath())

    blog_ideas.save_ideas([{"url_slug": "ideia"}])
