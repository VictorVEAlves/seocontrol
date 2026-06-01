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
