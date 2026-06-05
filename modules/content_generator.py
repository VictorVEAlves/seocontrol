"""
Gerador de conteúdo SEO com suporte a múltiplos provedores de IA.

Provedores suportados (todos com tier gratuito):
  - openrouter: roteador com modelos gratuitos como Qwen, GLM, DeepSeek, Kimi
                Chave em: openrouter.ai
  - gemini   : Google Gemini Flash — recomendado (grátis, excelente PT-BR)
               Chave em: aistudio.google.com
  - groq     : Groq + Llama 3.3 — muito rápido (grátis)
               Chave em: console.groq.com
  - mistral  : Mistral Small (grátis)
               Chave em: console.mistral.ai
  - anthropic: Claude Sonnet (pago, melhor qualidade)
               Chave em: console.anthropic.com

Uso via run.py:
  python run.py --module generate --urls /categoria --provider gemini --api-key AIza...
  python run.py --module generate --urls /categoria --provider groq   --api-key gsk_...
  python run.py --module generate --urls /categoria --provider openrouter --api-key sk-or-...
  python run.py --module generate --urls /categoria --provider mistral --api-key ...
"""

import json
import os
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import requests

from config import (
    GROQ_MODEL, OLLAMA_MODEL, OPENROUTER_MODEL, OPENROUTER_FALLBACK_MODELS,
    get_business_context, get_content_guidelines, get_default_provider,
    get_provider_api_key, get_provider_sequence, get_scoped_runtime_file,
    get_site_name, get_site_url
)

PENDING_FILE = get_scoped_runtime_file("pending_changes.json", "publishing")
SYSTEM_PROMPT_OVERRIDE: str | None = None
DEFAULT_MAX_OUTPUT_TOKENS = 4000


def _max_output_tokens() -> int:
    try:
        return max(1200, int(os.environ.get("AI_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS)))
    except Exception:
        return DEFAULT_MAX_OUTPUT_TOKENS


def _trim_without_ellipsis(
    value: str,
    maximum: int,
    min_last_space: int | None = None,
    end_sentence: bool = True,
) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= maximum and not end_sentence:
        return text
    cutoff = text if len(text) <= maximum else text[:maximum].rstrip()
    if len(text) > maximum:
        last_space = cutoff.rfind(" ")
        if last_space > (min_last_space or int(maximum * 0.7)):
            cutoff = cutoff[:last_space].rstrip()
    cutoff = cutoff.rstrip(" ,;:-.!?")
    bad_endings = {
        "a", "o", "as", "os", "e", "ou", "de", "do", "da", "dos", "das",
        "com", "para", "por", "que", "não", "nao", "n?o", "no", "na", "nos", "nas",
        "em", "ao", "aos", "à", "às", "seu", "sua", "seus", "suas",
    }
    min_len = min_last_space or int(maximum * 0.7)
    while " " in cutoff and len(cutoff) > min_len:
        last_word = cutoff.rsplit(" ", 1)[-1].strip(" ,;:-.!?").lower()
        last_word_ascii = "".join(
            ch for ch in unicodedata.normalize("NFKD", last_word)
            if not unicodedata.combining(ch)
        )
        if last_word not in bad_endings and last_word_ascii not in bad_endings:
            break
        cutoff = cutoff.rsplit(" ", 1)[0].rstrip(" ,;:-.!?")
    if not end_sentence:
        return cutoff[:maximum].rstrip()
    if cutoff.endswith((".", "!", "?")):
        return cutoff[:maximum].rstrip()
    if len(cutoff) < maximum:
        return cutoff + "."
    return cutoff[: max(0, maximum - 1)].rstrip(" ,;:-.!?") + "."

# ── System prompt (igual para todos os provedores) ────────────────────────────

def _system_prompt() -> str:
    if SYSTEM_PROMPT_OVERRIDE:
        return SYSTEM_PROMPT_OVERRIDE
    site = get_site_url() or "site configurado pelo cliente"
    return f"""Você é um especialista em SEO para o negócio abaixo.

Site: {get_site_name()} — {site}
Contexto do negócio: {get_business_context()}
Diretrizes do cliente: {get_content_guidelines()}

Regras fixas:
- Meta title: 50–60 caracteres. Incluir entidade principal + benefício real. Nunca usar "!" ou emojis.
- Meta description: idealmente 145–160 caracteres. CTA implícito e compatível com o contexto do cliente.
  Priorize frase completa e natural; se passar um pouco de 160 caracteres, tudo bem.
  Nunca use reticências ("..." ou "…") e nunca termine com frase cortada.
- Meta keywords: 8–12 termos separados por vírgula.
- H1: diferente do meta title. Máximo 65 caracteres.
- description_html: 3–5 parágrafos em HTML. Incluir 1 H2, 2–3 <p> e 1 lista <ul> com benefícios.
  Tom consultivo. Sem markdown — apenas HTML limpo.

IMPORTANTE — comprimentos recomendados (conte os caracteres antes de responder):
- meta_title: MÍNIMO 50, MÁXIMO 60 caracteres. Exemplo genérico: "Categoria Principal | Benefício Claro para Comprar"
- meta_description: ALVO 145–160 caracteres. Pode passar um pouco quando necessário para manter a frase completa. Não corte com reticências.
- h1: MÍNIMO 40, MÁXIMO 65 caracteres.

Retorne APENAS um JSON válido, sem texto antes ou depois, sem blocos de código. Formato exato:
{{
  "meta_title": "...",
  "meta_description": "...",
  "meta_keywords": "...",
  "h1": "...",
  "description_html": "..."
}}"""


# ── Provider clients ──────────────────────────────────────────────────────────

def _response_preview(response, limit: int = 500) -> str:
    return str(getattr(response, "text", "") or "")[:limit].replace("\n", " ").strip()


def _retry_after_seconds(response, default: float = 4.0) -> float:
    value = getattr(response, "headers", {}).get("Retry-After")
    try:
        if value:
            return max(0.0, min(30.0, float(value)))
    except Exception:
        pass
    try:
        payload = response.json()
        meta = ((payload.get("error") or {}).get("metadata") or {})
        for key in ("retry_after_seconds", "retry_after_seconds_raw"):
            if meta.get(key) is not None:
                return max(0.0, min(30.0, float(meta.get(key))))
    except Exception:
        pass
    return max(0.0, min(30.0, float(default)))


def _quota_is_exhausted(text: str) -> bool:
    lower = str(text or "").lower()
    return any(token in lower for token in [
        "free-models-per-day",
        "rate limit-remaining\":\"0",
        "ratelimit-remaining\":\"0",
        "quota",
        "limit: 0",
        "requests per day",
        "per day",
    ])


def _provider_retry_message(provider: str, response) -> str:
    preview = _response_preview(response)
    return f"{provider} HTTP {response.status_code}: {preview}"


def _friendly_provider_error(provider: str, error: str) -> str:
    text = str(error or "")
    lower = text.lower()
    if provider == "groq" and "429" in lower:
        return "Groq atingiu limite de uso agora (429). Reduza o lote ou aguarde alguns minutos."
    if provider == "openrouter" and "free-models-per-day" in lower:
        return "OpenRouter atingiu o limite diario dos modelos gratuitos."
    if provider == "openrouter" and "no healthy upstream" in lower:
        return "OpenRouter gratuito ficou sem modelo saudavel no momento."
    if provider == "openrouter" and ("no endpoints found" in lower or " 404" in lower):
        return "OpenRouter tem modelo gratuito configurado que nao existe mais; use openrouter/free."
    if provider == "openrouter" and "429" in lower:
        return "OpenRouter gratuito esta limitado temporariamente (429)."
    if provider == "gemini" and ("quota" in lower or "sem quota" in lower):
        return "Gemini esta sem quota disponivel no momento."
    if provider == "ollama" and ("connection" in lower or "max retries" in lower or "refused" in lower):
        return "Ollama local nao respondeu. Abra o Ollama ou configure OLLAMA_BASE_URL."
    if provider == "ollama" and "modelo ollama nao encontrado" in lower:
        return text
    return f"{provider}: {text}"


def _format_provider_failures(errors: list[tuple[str, str]]) -> str:
    friendly = []
    seen = set()
    for provider, error in errors:
        message = _friendly_provider_error(provider, error)
        if message not in seen:
            seen.add(message)
            friendly.append(message)
    if not friendly:
        return "Todos os providers de IA falharam."
    detail = "\n- " + "\n- ".join(friendly)
    return (
        "Todos os providers de IA falharam." + detail +
        "\n\nSugestao: gere em lotes menores (5 a 10), aguarde a quota renovar, "
        "adicione creditos/chave paga, ou use provider ollama local."
    )


def _call_gemini(prompt: str, api_key: str) -> str:
    # Try gemini-2.0-flash first, fallback to gemini-1.5-flash-8b
    models = ["gemini-2.0-flash", "gemini-1.5-flash-8b", "gemini-2.0-flash-lite"]
    body = {
        "contents": [{"parts": [{"text": _system_prompt() + "\n\n" + prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": _max_output_tokens()},
    }
    last_error = None
    for model in models:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={api_key}")
        for attempt, wait in enumerate([0, 10, 20]):
            if wait:
                time.sleep(wait)
            try:
                r = requests.post(url, json=body, timeout=45)
                if r.status_code == 429:
                    body_preview = r.text[:500].replace("\n", " ")
                    last_error = f"429 rate limit/quota (model: {model}): {body_preview}"
                    if "quota" in body_preview.lower() or "limit: 0" in body_preview.lower():
                        raise RuntimeError(
                            "Gemini sem quota disponivel no momento. "
                            "Use o provider auto, openrouter ou groq para continuar."
                        )
                    continue
                r.raise_for_status()
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            except requests.exceptions.HTTPError as e:
                if r.status_code == 404:
                    break  # try next model
                last_error = str(e)
            except Exception as e:
                if "Gemini sem quota" in str(e):
                    raise
                last_error = str(e)
    raise RuntimeError(f"Gemini falhou apos todas as tentativas: {last_error}")


def _call_groq(prompt: str, api_key: str) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": _max_output_tokens(),
    }
    last_error = ""
    for attempt, fallback_wait in enumerate([3, 8, 15]):
        r = requests.post(url, json=body, headers=headers, timeout=45)
        if r.status_code in {429, 500, 502, 503, 504}:
            preview = _response_preview(r)
            last_error = _provider_retry_message("Groq", r)
            if _quota_is_exhausted(preview):
                raise RuntimeError(last_error)
            if attempt < 2:
                time.sleep(_retry_after_seconds(r, fallback_wait))
                continue
        try:
            r.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            raise RuntimeError(_provider_retry_message("Groq", r)) from exc
        return r.json()["choices"][0]["message"]["content"]
    raise RuntimeError(last_error or "Groq falhou apos retries")


def _call_openrouter(prompt: str, api_key: str) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": get_site_url() or "http://localhost",
        "X-Title": "SEO Control Center",
    }
    models = []
    for model in [OPENROUTER_MODEL, *OPENROUTER_FALLBACK_MODELS]:
        if model and model not in models:
            models.append(model)

    base_body = {
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.35,
        "max_tokens": _max_output_tokens(),
    }

    def request_once(model: str, use_json_mode: bool) -> str:
        payload = dict(base_body)
        payload["model"] = model
        if use_json_mode:
            payload["response_format"] = {"type": "json_object"}

        r = None
        for attempt, fallback_wait in enumerate([3, 8]):
            r = requests.post(url, json=payload, headers=headers, timeout=60)
            if r.status_code in {429, 500, 502, 503, 504}:
                body_preview = _response_preview(r)
                if _quota_is_exhausted(body_preview):
                    raise RuntimeError(f"OpenRouter HTTP {r.status_code}: {body_preview}")
                if attempt == 0:
                    time.sleep(_retry_after_seconds(r, fallback_wait))
                    continue
            try:
                r.raise_for_status()
            except requests.exceptions.HTTPError as exc:
                body_preview = _response_preview(r)
                raise RuntimeError(f"OpenRouter HTTP {r.status_code}: {body_preview}") from exc
            break

        data = r.json()
        if "choices" not in data:
            body_preview = json.dumps(data, ensure_ascii=False)[:500]
            raise RuntimeError(f"OpenRouter sem choices: {body_preview}")

        message = data["choices"][0].get("message", {})
        content = message.get("content")
        if not content:
            body_preview = json.dumps(data, ensure_ascii=False)[:500]
            raise RuntimeError(f"OpenRouter sem content: {body_preview}")
        return content

    errors: list[tuple[str, str]] = []
    for model in models:
        try:
            return request_once(model, use_json_mode=True)
        except RuntimeError as exc:
            text = str(exc).lower()
            errors.append(f"{model}: {exc}")
            if "response_format" in text or "sem content" in text:
                try:
                    return request_once(model, use_json_mode=False)
                except RuntimeError as retry_exc:
                    errors.append(f"{model} sem json_mode: {retry_exc}")

            retryable = any(token in text for token in [
                " 404", " 429", "rate", "limit", "sem choices", "sem content",
                "temporarily", "upstream", "not found",
            ])
            if not retryable:
                raise

    raise RuntimeError("OpenRouter falhou em todos os modelos: " + " | ".join(errors[-5:]))


def _call_ollama(prompt: str, base_url: str) -> str:
    url = (base_url or "http://localhost:11434").rstrip("/") + "/api/chat"
    body = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": prompt},
        ],
        "options": {
            "temperature": 0.3,
            "num_predict": _max_output_tokens(),
        },
    }
    try:
        r = requests.post(url, json=body, timeout=180)
    except requests.RequestException as exc:
        raise RuntimeError(f"Ollama local nao respondeu em {url}: {exc}") from exc
    if r.status_code == 404:
        raise RuntimeError(
            f"Modelo Ollama nao encontrado: {OLLAMA_MODEL}. "
            f"Rode: ollama pull {OLLAMA_MODEL}"
        )
    try:
        r.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(f"Ollama HTTP {r.status_code}: {_response_preview(r)}") from exc
    data = r.json()
    content = (data.get("message") or {}).get("content")
    if not content:
        raise RuntimeError(f"Ollama respondeu sem conteudo: {json.dumps(data, ensure_ascii=False)[:500]}")
    return content


def _call_mistral(prompt: str, api_key: str) -> str:
    url = "https://api.mistral.ai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": "mistral-small-latest",
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": _max_output_tokens(),
    }
    r = requests.post(url, json=body, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _call_anthropic(prompt: str, api_key: str) -> str:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("pip install anthropic")
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=_max_output_tokens(),
        system=_system_prompt(),
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


PROVIDERS = {
    "openrouter": _call_openrouter,
    "gemini":    _call_gemini,
    "groq":      _call_groq,
    "mistral":   _call_mistral,
    "anthropic": _call_anthropic,
    "ollama":    _call_ollama,
}


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(page: dict, gsc_data: dict = None) -> str:
    url  = page.get("url", "")
    slug = url.strip("/").split("/")[0]

    current = {
        "title":      page.get("title", ""),
        "desc":       page.get("description", ""),
        "h1":         (page.get("h1_texts") or [""])[0],
        "keywords":   page.get("meta_keywords", ""),
        "words":      page.get("word_count", 0),
        "issues":     page.get("issues", []),
        "warnings":   page.get("warnings", []),
    }

    gsc_block = ""
    if gsc_data:
        kw = slug.replace("-", " ").lower()
        top_q = [r for r in gsc_data.get("top_queries", [])
                 if kw in r.get("query", "").lower()
                 or slug.split("-")[0] in r.get("query", "").lower()][:5]
        if top_q:
            gsc_block = "\n\nDados GSC (últimos 28 dias):\n"
            for q in top_q:
                gsc_block += (f"  query: \"{q['query']}\" | "
                              f"impressões: {q.get('impressions',0):,} | "
                              f"CTR: {q.get('ctr',0):.2f}% | "
                              f"posição: {q.get('position',0):.1f}\n")

    issues_block = ""
    if current["issues"]:
        issues_block += f"\nProblemas: {', '.join(current['issues'])}"
    if current["warnings"]:
        issues_block += f"\nAvisos: {', '.join(current['warnings'])}"

    site_url = get_site_url()
    full_url = url if url.startswith("http") else (site_url + url if site_url else url)

    return f"""Otimize os campos SEO da seguinte página:

URL: {full_url}
Marca/slug: {slug}
Contexto do negócio: {get_business_context()}

Dados atuais:
- Meta title ({len(current['title'])} chars): "{current['title']}"
- Meta description ({len(current['desc'])} chars): "{current['desc']}"
- H1: "{current['h1']}"
- Keywords: "{current['keywords']}"
- Palavras no conteúdo: {current['words']}{issues_block}{gsc_block}

Gere versões otimizadas. Na description_html crie conteúdo relevante para o contexto do cliente
e respeite as diretrizes comerciais configuradas."""


# ── Core generation ───────────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    # Remove markdown code blocks if present
    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else parts[0]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    # Some models prepend reasoning or append notes despite JSON-only prompts.
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start:end + 1]

    # Strip non-printable control chars (except tab \x09, LF \x0a, CR \x0d)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fallback: LLM put raw newlines inside JSON string values — collapse them
        aggressive = re.sub(r"[\x00-\x1f\x7f]", " ", raw)
        return json.loads(aggressive)


def generate_for_page(page: dict, gsc_data: dict = None,
                      provider: str = None, api_key: str = None) -> dict:
    # Auto-detect provider if not specified. Keep provider/key pairs aligned:
    # a requested provider without its own key may fall back to another provider,
    # but must never be called with a different provider's key.
    if provider and not api_key:
        api_key = get_provider_api_key(provider)

    sequence = get_provider_sequence(provider or "")
    if provider and api_key:
        requested_pair = (provider, api_key)
        if requested_pair in sequence:
            sequence.remove(requested_pair)
        sequence.insert(0, requested_pair)

    if not sequence:
        raise RuntimeError(
            "Nenhum provider de IA configurado.\n"
            "Adicione uma chave GEMINI_API_KEY, OPENROUTER_API_KEY ou GROQ_API_KEY no .env, "
            "ou use Ollama local com OLLAMA_ENABLED=1.")

    provider, api_key = sequence[0]
    prompt = _build_prompt(page, gsc_data)
    errors = []
    result = None
    used_provider = provider
    for provider_name, provider_key in sequence:
        caller = PROVIDERS.get(provider_name)
        if not caller:
            errors.append((provider_name, "provider desconhecido"))
            continue
        try:
            raw = caller(prompt, provider_key)
            result = _parse_json(raw)
            used_provider = provider_name
            break
        except Exception as exc:
            errors.append((provider_name, str(exc)))
            continue

    if result is None:
        raise RuntimeError(_format_provider_failures(errors))

    result = _enforce_lengths(result)

    result["_url"]              = page.get("url", "")
    result["_provider"]         = used_provider
    if errors:
        result["_fallback_errors"] = [f"{name}: {error}" for name, error in errors]
    result["_generated_at"]     = datetime.now().isoformat()
    result["_meta_title_len"]   = len(result.get("meta_title", ""))
    result["_meta_desc_len"]    = len(result.get("meta_description", ""))

    return result


def _enforce_lengths(result: dict) -> dict:
    """Normalize generated fields without truncating meta descriptions."""
    title = result.get("meta_title", "")
    desc  = re.sub(r"\s+", " ", str(result.get("meta_description", "") or "")).strip()
    if desc.endswith(("...", "…")):
        desc = desc.rstrip(".… ").strip()

    # Keep title bounded because Shopify titles are short UI fields.
    if len(title) > 60:
        title = _trim_without_ellipsis(title, 60, 45, end_sentence=False)

    result["meta_title"]       = title
    result["meta_description"] = desc

    # Warn on short fields
    result["_warnings"] = []
    if len(title) < 45:
        result["_warnings"].append(f"title curto ({len(title)} chars)")
    if len(desc) < 140:
        result["_warnings"].append(f"desc curta ({len(desc)} chars)")
    if len(desc) > 170:
        result["_warnings"].append(f"desc longa ({len(desc)} chars)")
    if len(result.get("h1", "")) < 30:
        result["_warnings"].append(f"h1 curto ({len(result.get('h1',''))} chars)")

    return result


def generate_for_pages(pages: list, gsc_data: dict = None,
                       provider: str = "gemini", api_key: str = None) -> list:
    results = []

    try:
        from rich.progress import track
        iterator = track(pages, description=f"Gerando com {provider}...")
    except ImportError:
        iterator = pages

    for page in iterator:
        url = page.get("url", page.get("final_url", ""))
        base = get_site_url()
        try:
            content          = generate_for_page(page, gsc_data, provider, api_key)
            content["status"] = "pending"
            results.append(content)
            tl = content["_meta_title_len"]
            dl = content["_meta_desc_len"]
            print(f"   ok {url.replace(base,'')[:50]:50s}  "
                  f"title:{tl}ch desc:{dl}ch")
        except Exception as e:
            print(f"   x  {url.replace(base,'')[:50]} — {e}")

    _save_to_queue(results)
    return results


# ── Queue management ──────────────────────────────────────────────────────────

def _save_to_queue(new_items: list):
    existing = []
    if PENDING_FILE.exists():
        try:
            existing = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    url_to_idx = {item["_url"]: i for i, item in enumerate(existing)}
    for item in new_items:
        idx = url_to_idx.get(item["_url"])
        if idx is not None:
            existing[idx] = item
        else:
            existing.append(item)

    PENDING_FILE.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n   Fila salva: {PENDING_FILE}")
    print(f"   {len([i for i in existing if i.get('status')=='pending'])} "
          f"item(ns) aguardando publicacao.")


def list_pending() -> list:
    if not PENDING_FILE.exists():
        return []
    return [i for i in json.loads(PENDING_FILE.read_text(encoding="utf-8"))
            if i.get("status") == "pending"]


def mark_published(url: str):
    if not PENDING_FILE.exists():
        return
    items = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    for item in items:
        if item["_url"] == url:
            item["status"]        = "published"
            item["_published_at"] = datetime.now().isoformat()
    PENDING_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Main entry point ──────────────────────────────────────────────────────────

def run(onpage_results: list, gsc_data: dict = None,
        provider: str = "gemini", api_key: str = None) -> list:
    needs = [p for p in onpage_results
             if p.get("grade") in ("B", "C", "D", "F") or p.get("issues")]

    if not needs:
        print("   Todas as paginas estao otimizadas.")
        return []

    print(f"   {len(needs)} pagina(s) para otimizar via {provider}")
    return generate_for_pages(needs, gsc_data, provider, api_key)
