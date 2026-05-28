"""Generate full HTML blog post (2500+ words) from a blog idea.

Uses dedicated high-token callers: OpenRouter -> Groq -> Gemini.
The standard content_generator callers are capped at 1500 tokens -- too short for blog posts.
"""
from __future__ import annotations

import json
import re
import time

import requests

from config import (
    GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY,
    OPENROUTER_MODEL, OPENROUTER_FALLBACK_MODELS, get_site_url,
)

# ── System prompt ─────────────────────────────────────────────────────────────

def _system_prompt() -> str:
    from config import get_business_context, get_content_guidelines, get_site_name, get_site_url
    return f"""Voce e um especialista avancado em SEO, GEO, copywriting e conteudo.

Site: {get_site_name()} - {get_site_url() or "site configurado pelo cliente"}
Contexto do negocio: {get_business_context()}
Diretrizes do cliente: {get_content_guidelines()}

Sua funcao e criar conteudos de blog altamente otimizados para Google, IA generativa e Discover, com foco em trafego organico, CTR, autoridade topical e conversao.

Regras:
- Nao invente dados de preco, estoque, garantia, certificacoes ou promessas comerciais.
- Use apenas o contexto do cliente e as informacoes fornecidas na pauta.
- Escreva em PT-BR natural, com tom consultivo e especifico.
- Retorne APENAS HTML dentro de <article>...</article>. Nao use markdown.
- MINIMO ABSOLUTO: 2.000 palavras, salvo se a pauta pedir algo menor.
- Inclua H1, lead, indice navegavel, H2s claros, FAQ em <details>, links internos contextuais e CTA final compativel com o negocio.
- Adapte exemplos, entidades e argumentos ao segmento configurado pelo cliente.

Evite frases genericas de IA. Prefira exemplos concretos, criterios de decisao, comparacoes uteis e recomendacoes acionaveis.
"""

# ── Provider callers (high token limits for blog posts) ───────────────────────

_OR_BLOG_MODELS = []  # built lazily from config


def _openrouter_models() -> list[str]:
    if not _OR_BLOG_MODELS:
        for m in [OPENROUTER_MODEL, *OPENROUTER_FALLBACK_MODELS]:
            if m and m not in _OR_BLOG_MODELS:
                _OR_BLOG_MODELS.append(m)
    return _OR_BLOG_MODELS


def _call_openrouter(prompt: str) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY nao configurada")
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": get_site_url() or "http://localhost",
        "X-Title": "SEO Control Center -- Blog Generator",
    }
    errors = []
    for model in _openrouter_models():
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {"role": "user",   "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 8000,
        }
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=body, headers=headers, timeout=120,
            )
            if r.status_code in (429, 503):
                errors.append(f"{model}: {r.status_code}")
                continue
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            if content and len(content) > 500:
                return content
            errors.append(f"{model}: resposta curta ({len(content or '')} chars)")
        except Exception as exc:
            errors.append(f"{model}: {exc}")
    raise RuntimeError("OpenRouter: " + " | ".join(errors[-4:]))


def _call_groq(prompt: str) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY nao configurada")
    body = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 8000,
    }
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _call_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY nao configurada")
    models = ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash-latest", "gemini-1.5-flash-8b"]
    body = {
        "contents": [{"parts": [{"text": _system_prompt() + "\n\n" + prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 8192},
    }
    all_429 = True
    errors = []
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            r = requests.post(url, json=body, timeout=120)
            if r.status_code == 429:
                errors.append(f"{model}: 429")
                continue
            if r.status_code == 404:
                errors.append(f"{model}: 404")
                continue
            all_429 = False
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as exc:
            all_429 = False
            errors.append(f"{model}: {exc}")
    if all_429:
        raise _RateLimitError("Gemini quota atingida")
    raise RuntimeError("Gemini: " + " | ".join(errors))


class _RateLimitError(Exception):
    pass


# ── HTML extraction & cleanup ─────────────────────────────────────────────────

def _clean_html(html: str) -> str:
    # Remove inline style noise (models sometimes dump CSS as inline styles)
    html = re.sub(r'\s+style="[^"]*"', '', html)
    html = re.sub(r"\s+style='[^']*'", '', html)
    # Fix double-nested <article> tags
    html = re.sub(r'(<article[^>]*>)\s*<article[^>]*>', r'\1', html, flags=re.IGNORECASE)
    html = re.sub(r'</article>\s*</article>', '</article>', html, flags=re.IGNORECASE)
    # Remove injected <style> blocks
    html = re.sub(r'<style[\s\S]*?</style>', '', html, flags=re.IGNORECASE)
    return html.strip()


def _extract_article(raw: str) -> str:
    clean = raw.strip()
    # Strip markdown code fences
    if clean.startswith("```"):
        clean = re.sub(r"^```[a-zA-Z]*\s*", "", clean)
        clean = re.sub(r"\s*```\s*$", "", clean)
        clean = clean.strip()
    # Try to find <article>...</article>
    m = re.search(r"(<article[\s\S]*</article>)", clean, re.IGNORECASE)
    if m and len(m.group(1)) > 200:
        return _clean_html(m.group(1))
    # Fall back: wrap any substantial HTML content
    if re.search(r"<(h1|h2|header|section|div|p)\b", clean, re.IGNORECASE) and len(clean) > 200:
        return _clean_html(f"<article>{clean}</article>")
    return ""


# ── Public API ────────────────────────────────────────────────────────────────

def _build_prompt(idea: dict) -> str:
    queries = idea.get("queries", [])[:10]
    sections = idea.get("sections", [])[:6]
    faq = idea.get("faq", [])[:5]
    entities = idea.get("entities", [])[:8]
    links = idea.get("internal_links", [])[:5]

    return json.dumps({
        "pauta": {
            "h1_sugerido":           idea.get("h1") or idea.get("meta_title", ""),
            "marca":                 idea.get("brand", ""),
            "produto":               idea.get("product", ""),
            "angulo_editorial":      idea.get("angle", ""),
            "intencao_de_busca":     idea.get("search_intent", ""),
            "meta_description_base": idea.get("meta_description", ""),
            "keyword_principal":     queries[0] if queries else "",
            "keywords_secundarias":  queries[1:],
            "secoes_sugeridas":      sections,
            "perguntas_faq_base":    faq,
            "entidades_semanticas":  entities,
            "links_internos_base":   links,
            "impressoes_mensais":    idea.get("impressions", 0),
        },
        "instrucao": (
            "Crie o artigo completo conforme a estrutura obrigatoria do sistema. "
            "MINIMO 2.500 palavras - conte e expanda se necessario. "
            "Inclua TODAS as secoes: indice com links ancora, minimo 6 secoes de conteudo, "
            "minimo 2 tabelas, tip-box, minimo 6 figure.image-suggestion, produtos, "
            "FAQ com 8+ perguntas E respostas completas, CTA final e pautas satelite. "
            "Imagens: use <figure class='image-suggestion'><img src='' alt='...'><figcaption>...</figcaption></figure> com src VAZIO. "
            "Links de produtos: /busca?q=PRODUTO+MARCA. "
            "Publico: homens brasileiros. NUNCA mencione itens femininos. "
            "NÃO invente produtos ou URLs."
        ),
    }, ensure_ascii=False, indent=2)


PROVIDERS = [
    ("openrouter", _call_openrouter),
    ("groq",       _call_groq),
    ("gemini",     _call_gemini),
]


def generate(idea: dict) -> dict:
    """Generate full HTML blog post (2500+ words). Returns {html, error, rate_limited, provider}."""
    prompt = _build_prompt(idea)
    errors = []

    for provider_name, caller in PROVIDERS:
        try:
            raw = caller(prompt)
            print(f"[blog_content] {provider_name} OK -- {len(raw)} chars", flush=True)
            html = _extract_article(raw)
            if html:
                return {"html": html, "error": None, "rate_limited": False, "provider": provider_name}
            errors.append(f"{provider_name}: HTML nao extraido ({len(raw)} chars, preview: {raw[:80]!r})")
        except _RateLimitError:
            errors.append(f"{provider_name}: rate-limit")
            continue
        except Exception as exc:
            print(f"[blog_content] {provider_name} erro: {str(exc)[:120]}", flush=True)
            errors.append(f"{provider_name}: {str(exc)[:120]}")
            continue

    return {
        "html": None,
        "error": "Todos os provedores falharam: " + " | ".join(errors),
        "rate_limited": False,
    }
