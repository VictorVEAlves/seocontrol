"""Small shared AI helper for SEO analysis modules.

The project already has provider clients in ``modules.content_generator``.
This wrapper reuses them, keeps prompts JSON-only, and degrades gracefully when
no API key is configured.
"""

from __future__ import annotations

import copy
import threading
from datetime import datetime
from typing import Any

from config import get_default_provider, get_provider_api_key, get_provider_sequence
from modules import content_generator

_prompt_lock = threading.Lock()


def call_json(
    prompt: str,
    system_prompt: str,
    provider: str | None = None,
    api_key: str | None = None,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the configured AI provider and return parsed JSON.

    The return value always contains ``_ai_enhanced``. If the call cannot run,
    the provided fallback is returned with ``_ai_error`` instead of raising.
    """
    result = copy.deepcopy(fallback or {})
    if provider and not api_key:
        api_key = get_provider_api_key(provider)

    if not provider or not api_key:
        auto_provider, auto_key = get_default_provider()
        provider = provider or auto_provider
        api_key = api_key or auto_key

    if not provider or not api_key:
        result["_ai_enhanced"] = False
        result["_ai_error"] = "Nenhuma chave de IA configurada no .env."
        return result

    errors = []
    with _prompt_lock:
        prompt_attr = (
            "SYSTEM_PROMPT_OVERRIDE"
            if hasattr(content_generator, "SYSTEM_PROMPT_OVERRIDE")
            else "SYSTEM_PROMPT"
        )
        previous_prompt = getattr(content_generator, prompt_attr, None)
        setattr(content_generator, prompt_attr, system_prompt)
        try:
            sequence = get_provider_sequence(provider)
            if provider and api_key and (not sequence or sequence[0] != (provider, api_key)):
                sequence.insert(0, (provider, api_key))
            for provider_name, provider_key in sequence:
                caller = content_generator.PROVIDERS.get(provider_name)
                if not caller:
                    errors.append(f"{provider_name}: provider desconhecido")
                    continue
                try:
                    raw = caller(prompt, provider_key)
                    data = content_generator._parse_json(raw)
                    if not isinstance(data, dict):
                        raise ValueError("A IA retornou JSON, mas nao um objeto.")
                    data["_ai_enhanced"] = True
                    data["_ai_provider"] = provider_name
                    data["_ai_generated_at"] = datetime.now().isoformat()
                    if errors:
                        data["_ai_fallback_errors"] = errors
                    return data
                except Exception as exc:
                    errors.append(f"{provider_name}: {exc}")
                    continue

            result["_ai_enhanced"] = False
            result["_ai_error"] = " | ".join(errors) if errors else "Nenhum provider configurado respondeu."
            result["_ai_provider"] = provider
            return result
        finally:
            setattr(content_generator, prompt_attr, previous_prompt)
