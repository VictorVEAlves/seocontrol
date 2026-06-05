from modules import ai_analysis, ai_layer, content_generator


def test_call_json_uses_system_prompt_override_and_restores_it(monkeypatch):
    captured = {}
    original_override = content_generator.SYSTEM_PROMPT_OVERRIDE

    def fake_provider(prompt, api_key):
        captured["prompt"] = prompt
        captured["api_key"] = api_key
        captured["system_prompt"] = content_generator._system_prompt()
        return '{"summary":"ok"}'

    monkeypatch.setattr(ai_layer, "get_provider_sequence", lambda provider: [(provider, "fake-key")])
    monkeypatch.setattr(content_generator, "PROVIDERS", {"gemini": fake_provider})

    result = ai_layer.call_json(
        prompt="dados da auditoria",
        system_prompt="prompt estrategico da auditoria",
        provider="gemini",
        api_key="fake-key",
        fallback={"summary": "fallback"},
    )

    assert result["summary"] == "ok"
    assert result["_ai_enhanced"] is True
    assert captured["prompt"] == "dados da auditoria"
    assert captured["api_key"] == "fake-key"
    assert captured["system_prompt"] == "prompt estrategico da auditoria"
    assert content_generator.SYSTEM_PROMPT_OVERRIDE == original_override


def test_ai_analysis_save_is_best_effort_on_read_only_filesystem(monkeypatch):
    class BadParent:
        def mkdir(self, **_kwargs):
            raise OSError("read-only file system")

    class BadPath:
        parent = BadParent()

        def write_text(self, *_args, **_kwargs):
            raise OSError("read-only file system")

    monkeypatch.setattr(ai_analysis, "_analysis_file", lambda: BadPath())

    ai_analysis.save_analysis({"summary": "ok"})


def test_content_generator_default_system_prompt_keeps_json_literal():
    prompt = content_generator._system_prompt()

    assert '"meta_title": "..."' in prompt
    assert '"description_html": "..."' in prompt


def test_content_generator_formats_provider_limit_errors():
    message = content_generator._format_provider_failures([
        ("groq", "Groq HTTP 429: Too Many Requests"),
        ("openrouter", "OpenRouter HTTP 429: free-models-per-day remaining 0"),
        ("gemini", "Gemini sem quota disponivel no momento."),
    ])

    assert "Groq atingiu limite" in message
    assert "OpenRouter atingiu o limite diario" in message
    assert "Gemini esta sem quota" in message
    assert "lotes menores" in message


def test_content_generator_call_ollama_reads_chat_content(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = ""
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": '{"summary":"ok"}'}}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(content_generator.requests, "post", fake_post)

    result = content_generator._call_ollama("dados", "http://localhost:11434")

    assert result == '{"summary":"ok"}'
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["json"]["stream"] is False


def test_content_generator_does_not_mix_provider_with_other_provider_key(monkeypatch):
    calls = []

    def fake_gemini(_prompt, _api_key):
        calls.append(("gemini", _api_key))
        raise AssertionError("Gemini should not be called without a Gemini key")

    def fake_openrouter(_prompt, _api_key):
        calls.append(("openrouter", _api_key))
        return (
            '{"meta_title":"Produto Premium Secret Shop com Estilo Casual",'
            '"meta_description":"Compre produto premium masculino com curadoria Secret Shop, qualidade original, condicoes especiais e envio para todo o Brasil.",'
            '"meta_keywords":"produto premium, moda masculina, secret shop",'
            '"h1":"Produto premium masculino com curadoria Secret Shop",'
            '"description_html":"<h2>Produto premium</h2><p>Descricao.</p>"}'
        )

    monkeypatch.setattr(content_generator, "get_provider_api_key", lambda provider: "")
    monkeypatch.setattr(content_generator, "get_provider_sequence", lambda provider: [("openrouter", "open-key")])
    monkeypatch.setattr(
        content_generator,
        "PROVIDERS",
        {"gemini": fake_gemini, "openrouter": fake_openrouter},
    )

    result = content_generator.generate_for_page(
        {
            "url": "/products/produto-premium",
            "title": "",
            "description": "",
            "h1_texts": ["Produto Premium"],
            "issues": ["SEO title missing"],
            "warnings": [],
        },
        provider="gemini",
    )

    assert result["_provider"] == "openrouter"
    assert calls == [("openrouter", "open-key")]


def test_content_generator_keeps_full_meta_description_without_ellipsis():
    result = content_generator._enforce_lengths({
        "meta_title": "Produto Premium Masculino com Curadoria Exclusiva e Condições Especiais para Comprar Online",
        "meta_description": "Encontre as melhores calças de sarja para homem e mulher, com descontos exclusivos e qualidade garantida, compre agora e aproveite os benefícios de nossas ofertas especiais para renovar seu guarda roupa com estilo.",
        "h1": "Produto premium masculino com curadoria Secret Shop",
    })

    assert len(result["meta_title"]) <= 60
    assert len(result["meta_description"]) > 160
    assert not result["meta_title"].endswith("...")
    assert not result["meta_description"].endswith("...")
    assert result["meta_description"].endswith(".")

    no_space = content_generator._enforce_lengths({
        "meta_title": "T" * 80,
        "meta_description": "D" * 190,
        "h1": "H" * 40,
    })
    assert len(no_space["meta_title"]) <= 60
    assert len(no_space["meta_description"]) == 190
    assert not no_space["meta_title"].endswith("...")
    assert not no_space["meta_description"].endswith("...")

    dangling = content_generator._enforce_lengths({
        "meta_title": "Produto Premium Masculino com Curadoria",
        "meta_description": "Encontre as melhores ofertas de marcas renomadas como Tommy Hilfiger, Diesel e Calvin Klein na Secret Outlet. Compre com confiança e aproveite preços que não comprometem o estilo.",
        "h1": "Produto premium masculino com curadoria Secret Shop",
    })
    assert not dangling["meta_description"].lower().endswith((" que.", " não.", "..."))

    ellipsis = content_generator._enforce_lengths({
        "meta_title": "Produto Premium Masculino com Curadoria",
        "meta_description": "Encontre descontos especiais na Secret Outlet...",
        "h1": "Produto premium masculino com curadoria Secret Shop",
    })
    assert not ellipsis["meta_description"].endswith("...")
