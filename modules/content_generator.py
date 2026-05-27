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
  python run.py --module generate --urls /lacoste --provider gemini --api-key AIza...
  python run.py --module generate --urls /lacoste --provider groq   --api-key gsk_...
  python run.py --module generate --urls /lacoste --provider openrouter --api-key sk-or-...
  python run.py --module generate --urls /lacoste --provider mistral --api-key ...
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import requests

from config import (
    BASE_DIR, SITE_URL, OPENROUTER_MODEL, OPENROUTER_FALLBACK_MODELS,
    get_default_provider, get_provider_api_key, get_provider_sequence
)

PENDING_FILE = BASE_DIR / "pending_changes.json"

# ── System prompt (igual para todos os provedores) ────────────────────────────

SYSTEM_PROMPT = """Você é um especialista em SEO para e-commerce de moda masculina premium no Brasil.

Site: secretoutlet.com.br — outlet oficial e revendedor autorizado de marcas premium
(Lacoste, Tommy Hilfiger, Columbia, Reserva, Calvin Klein, Levi's, Aramis, Dudalina e outras)

Regras fixas:
- Meta title: 50–60 caracteres. Incluir marca + benefício. Nunca usar "!" ou emojis.
- Meta description: 145–160 caracteres. CTA implícito. Mencionar "revendedor autorizado" quando relevante.
- Meta keywords: 8–12 termos separados por vírgula.
- H1: diferente do meta title. Máximo 65 caracteres.
- description_html: 3–5 parágrafos em HTML. Incluir 1 H2, 2–3 <p> e 1 lista <ul> com benefícios.
  Tom consultivo. Sem markdown — apenas HTML limpo.

IMPORTANTE — comprimentos obrigatórios (conte os caracteres antes de responder):
- meta_title: MÍNIMO 50, MÁXIMO 60 caracteres. Exemplo com 54 chars: "Lacoste Outlet | Roupas e Tênis com até 50% OFF"
- meta_description: MÍNIMO 145, MÁXIMO 160 caracteres. Preencha até o limite.
- h1: MÍNIMO 40, MÁXIMO 65 caracteres.

Retorne APENAS um JSON válido, sem texto antes ou depois, sem blocos de código. Formato exato:
{
  "meta_title": "...",
  "meta_description": "...",
  "meta_keywords": "...",
  "h1": "...",
  "description_html": "..."
}"""


# ── Provider clients ──────────────────────────────────────────────────────────

def _call_gemini(prompt: str, api_key: str) -> str:
    # Try gemini-2.0-flash first, fallback to gemini-1.5-flash-8b
    models = ["gemini-2.0-flash", "gemini-1.5-flash-8b", "gemini-2.0-flash-lite"]
    body = {
        "contents": [{"parts": [{"text": SYSTEM_PROMPT + "\n\n" + prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1500},
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
                    last_error = f"429 rate limit (model: {model})"
                    continue
                r.raise_for_status()
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            except requests.exceptions.HTTPError as e:
                if r.status_code == 404:
                    break  # try next model
                last_error = str(e)
            except Exception as e:
                last_error = str(e)
    raise RuntimeError(f"Gemini falhou apos todas as tentativas: {last_error}")


def _call_groq(prompt: str, api_key: str) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 1500,
    }
    r = requests.post(url, json=body, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _call_openrouter(prompt: str, api_key: str) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": SITE_URL,
        "X-Title": "SEO Control Center",
    }
    models = []
    for model in [OPENROUTER_MODEL, *OPENROUTER_FALLBACK_MODELS]:
        if model and model not in models:
            models.append(model)

    base_body = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.35,
        "max_tokens": 1600,
    }

    def request_once(model: str, use_json_mode: bool) -> str:
        payload = dict(base_body)
        payload["model"] = model
        if use_json_mode:
            payload["response_format"] = {"type": "json_object"}

        r = requests.post(url, json=payload, headers=headers, timeout=45)
        try:
            r.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            body_preview = r.text[:500].replace("\n", " ")
            raise RuntimeError(f"OpenRouter HTTP {r.status_code}: {body_preview}") from exc

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

    errors = []
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


def _call_mistral(prompt: str, api_key: str) -> str:
    url = "https://api.mistral.ai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": "mistral-small-latest",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 1500,
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
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


PROVIDERS = {
    "openrouter": _call_openrouter,
    "gemini":    _call_gemini,
    "groq":      _call_groq,
    "mistral":   _call_mistral,
    "anthropic": _call_anthropic,
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

    return f"""Otimize os campos SEO da seguinte página:

URL: {SITE_URL}{url}
Marca/slug: {slug}

Dados atuais:
- Meta title ({len(current['title'])} chars): "{current['title']}"
- Meta description ({len(current['desc'])} chars): "{current['desc']}"
- H1: "{current['h1']}"
- Keywords: "{current['keywords']}"
- Palavras no conteúdo: {current['words']}{issues_block}{gsc_block}

Gere versões otimizadas. Na description_html crie conteúdo relevante para a marca,
mencionando que somos revendedores autorizados, com desconto, nota fiscal e parcelamento."""


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
    # Auto-detect provider if not specified
    if provider and not api_key:
        api_key = get_provider_api_key(provider)

    if not provider or not api_key:
        auto_provider, auto_key = get_default_provider()
        provider = provider or auto_provider
        api_key  = api_key  or auto_key

    if not provider:
        raise RuntimeError(
            "Nenhuma chave de API configurada.\n"
            "Edite o arquivo .env e adicione sua chave GEMINI_API_KEY (gratuita).\n"
            "Obter em: https://aistudio.google.com/apikey")

    sequence = get_provider_sequence(provider)
    if provider and api_key and (not sequence or sequence[0] != (provider, api_key)):
        sequence.insert(0, (provider, api_key))
    prompt = _build_prompt(page, gsc_data)
    errors = []
    result = None
    used_provider = provider
    for provider_name, provider_key in sequence:
        caller = PROVIDERS.get(provider_name)
        if not caller:
            errors.append(f"{provider_name}: provider desconhecido")
            continue
        try:
            raw = caller(prompt, provider_key)
            result = _parse_json(raw)
            used_provider = provider_name
            break
        except Exception as exc:
            errors.append(f"{provider_name}: {exc}")
            continue

    if result is None:
        raise RuntimeError("Todos os providers de IA falharam: " + " | ".join(errors))

    result = _enforce_lengths(result)

    result["_url"]              = page.get("url", "")
    result["_provider"]         = used_provider
    if errors:
        result["_fallback_errors"] = errors
    result["_generated_at"]     = datetime.now().isoformat()
    result["_meta_title_len"]   = len(result.get("meta_title", ""))
    result["_meta_desc_len"]    = len(result.get("meta_description", ""))

    return result


def _enforce_lengths(result: dict) -> dict:
    """Trim fields to Google's display limits and warn if too short."""
    title = result.get("meta_title", "")
    desc  = result.get("meta_description", "")

    # Hard trim at Google limits
    if len(title) > 60:
        title = title[:57].rstrip() + "..."
    if len(desc) > 160:
        # Cut at last space before limit
        desc = desc[:157]
        last_space = desc.rfind(" ")
        desc = (desc[:last_space] if last_space > 120 else desc) + "..."

    result["meta_title"]       = title
    result["meta_description"] = desc

    # Warn on short fields
    result["_warnings"] = []
    if len(title) < 45:
        result["_warnings"].append(f"title curto ({len(title)} chars)")
    if len(desc) < 140:
        result["_warnings"].append(f"desc curta ({len(desc)} chars)")
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
        try:
            content          = generate_for_page(page, gsc_data, provider, api_key)
            content["status"] = "pending"
            results.append(content)
            tl = content["_meta_title_len"]
            dl = content["_meta_desc_len"]
            print(f"   ok {url.replace(SITE_URL,'')[:50]:50s}  "
                  f"title:{tl}ch desc:{dl}ch")
        except Exception as e:
            print(f"   x  {url.replace(SITE_URL,'')[:50]} — {e}")

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
