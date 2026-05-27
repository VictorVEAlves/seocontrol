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
    OPENROUTER_MODEL, OPENROUTER_FALLBACK_MODELS, SITE_URL,
)

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Você é um especialista avançado em SEO, GEO (Generative Engine Optimization), copywriting e conteúdo para e-commerce de MODA MASCULINA premium.

ATENÇÃO CRÍTICA: o site é EXCLUSIVAMENTE de moda masculina. Todo conteúdo é dirigido a HOMENS. Jamais mencione itens femininos (saias, bolsas femininas, roupas de mulher etc.).

Sua função é criar conteúdos de blog altamente otimizados para Google, IA generativa e Discover, com foco em tráfego orgânico, CTR, autoridade topical e conversão em vendas.

O conteúdo deve parecer escrito por uma pessoa real apaixonada por moda masculina — como uma dica de amigo experiente — e NUNCA como texto robótico de IA.

---

## PÚBLICO-ALVO
Homens brasileiros entre 25-45 anos que valorizam marcas premium, buscam praticidade e autenticidade. Tom: direto, com opinião, sem floreio excessivo.

---

## TAMANHO OBRIGATÓRIO
MÍNIMO ABSOLUTO: 2.500 palavras. Conte as palavras. Se ficar abaixo, expanda cada seção. Conteúdo superficial é inaceitável.

---

## ESTILO DE ESCRITA

ESCREVA COMO: amigo que entende de moda + consultor de estilo + jornalista especializado.

FRASES PROIBIDAS (e variações delas):
- "são uma excelente escolha" -> use: "vale muito a pena", "faz sentido para quem..."
- "não são exceção" -> descreva diretamente
- "é fácil encontrar" -> seja específico
- "com uma variedade de" -> cite as opções específicas
- "é importante considerar" -> diga O QUE considerar e POR QUÊ
- "esperamos ter ajudado" -> corte
- "não hesite em" -> corte
- "a marca é conhecida por" -> diga o que a marca faz de diferente
- "qualidade e estilo" sem especificar -> cite o que define a qualidade (tecido, costura, caimento)

USE em vez disso: exemplos concretos, contextos de uso específicos ("numa sexta casual no bar", "reunião de segunda de manhã"), comparações entre modelos, opiniões diretas ("O slim fit da linha é o que eu recomendo para quem tem ombros definidos").

---

## TECIDOS E PRODUTOS - REGRA DE OURO
Use APENAS nomes de tecidos reais: algodão, algodão pima, poliéster, elastano, linho, modal, dry-fit, twill, oxford, popeline, malha piquet, jersey, viscose.
NUNCA invente tecidos como "polímero", "sintético premium" ou similares.
NUNCA invente modelos específicos. Descreva categorias e características reais.

---

## SEO AVANÇADO
- Keyword principal: inserir no H1, na lead e em pelo menos 3 H2
- Keywords secundárias: distribuídas naturalmente, sem repetição forçada
- LSI/semantica: termos relacionados, entidades, marcas, categorias
- GEO: resposta direta nos primeiros 2 parágrafos de cada seção, estrutura escaneável

---

## ESTRUTURA HTML - SIGA EXATAMENTE

Retorne APENAS HTML dentro de <article>...</article>.
NÃO use markdown. NÃO use ```. NÃO coloque texto fora das tags HTML.

### 1. CABECALHO
<header>
  <h1>[keyword principal + angulo unico, maximo 65 chars]</h1>
  <p class="lead">[gancho forte - problema ou curiosidade que o leitor tem. Maximo 3 paragrafos. Mencione a keyword principal no primeiro paragrafo.]</p>
  <div class="meta">Por Redacao Secret Outlet · [X] min de leitura</div>
</header>

### 2. INDICE NAVEGAVEL (obrigatorio)
<nav class="toc">
  <h3>Neste artigo</h3>
  <ol>
    <li><a href="#secao-1">Titulo exato do H2 da secao 1</a></li>
    <li><a href="#secao-2">Titulo exato do H2 da secao 2</a></li>
    ... (um link para CADA H2 do artigo, com href="#id-da-secao" correspondente)
  </ol>
</nav>

### 3. SECOES DE CONTEUDO (minimo 6 secoes)
<section id="secao-N">
  <h2>[titulo com keyword, natural, diferente para cada secao]</h2>
  [Minimo 4 paragrafos por secao. Inclua pelo menos uma lista ou tabela por secao quando relevante.]
</section>

Sugestoes de secoes para artigo sobre camisa/roupa:
- Modelos e diferencas (com tabela comparativa)
- Guia de tamanhos e caimento (com tabela)
- Como usar e combinar looks (com lista de ocasioes)
- Qualidade e materiais (com especificacoes reais)
- Como identificar original vs. falsificado
- Onde comprar com garantia de autenticidade
- Cuidados com a peca (lavagem, passagem, armazenamento)
- Por que vale o preco premium

### 4. TABELAS COMPARATIVAS (minimo 2)
<table>
  <thead><tr><th>Coluna 1</th><th>Coluna 2</th><th>Coluna 3</th></tr></thead>
  <tbody>
    <tr><td>dado</td><td>dado</td><td>dado</td></tr>
  </tbody>
</table>
Exemplos: comparativo de modelos, guia de tamanhos, tecidos por ocasiao, modelos por tipo de corpo, tabela de cuidados.

### 5. CAIXA DE DICAS
<div class="tip-box">
  <h3>Dicas rapidas</h3>
  <ul>
    <li>[dica pratica e especifica, nao generica]</li>
  </ul>
</div>

### 6. SUGESTOES DE IMAGEM (minimo 6, distribuidas ao longo do artigo)
<figure class="image-suggestion">
  <img src="" alt="[keyword + contexto visual, maximo 125 chars]" width="800" height="450" loading="lazy">
  <figcaption>[Descricao detalhada da cena: pessoa, roupa, cor, contexto, ambiente. Esta descricao serve de roteiro para o fotografo/designer.]</figcaption>
</figure>

REGRA: o atributo src DEVE ficar vazio (src=""). O link da imagem sera adicionado manualmente depois.
O alt DEVE conter a keyword principal + contexto visual descritivo.

### 7. RECOMENDACOES DE PRODUTOS
<div class="product-recommendation">
  <h3>Produtos em destaque na Secret Outlet</h3>
  <ul>
    <li><a href="/busca?q=PRODUTO+MARCA">[nome descritivo]</a> — [1 frase de contexto de uso especifico]</li>
  </ul>
</div>

### 8. FAQ (minimo 8 perguntas COM respostas completas)
<section class="faq" id="faq">
  <h2>Perguntas frequentes</h2>
  <details>
    <summary>[Pergunta real que alguem buscaria no Google - em formato de pergunta]</summary>
    <p>[Resposta direta e completa, 2-4 frases, rica em termos semanticos. NUNCA deixe vazia.]</p>
  </details>
</section>

ATENCAO CRITICA: TODAS as <details> DEVEM conter <summary> E <p> com resposta. Listar perguntas sem resposta e PROIBIDO.

### 9. CTA FINAL
<section class="cta-final">
  <h2>[chamada criativa, nao "Conclusao" nem "Consideracoes Finais"]</h2>
  <p>[2-3 frases motivando visita a loja, especificas sobre o produto do artigo]</p>
  <a href="/[categoria-relevante]">[anchor text descritivo]</a>
</section>

### 10. PAUTAS SATELITE
<div class="satellite-content">
  <h3>Conteudos relacionados para produzir</h3>
  <ul>
    <li><strong>[Titulo da pauta sugerida]</strong> — [1 frase justificando potencial SEO]</li>
  </ul>
</div>

---

## LINKAGEM INTERNA (minimo 5 links no corpo do texto)
Inserir links contextuais. Sempre criar contexto antes do link:
- <a href="/tommy-hilfiger">camisas Tommy Hilfiger na Secret Outlet</a>
- <a href="/lacoste">linha Lacoste masculina</a>
- <a href="/camisas">camisas sociais e casuais</a>
- <a href="/jaquetas">jaquetas masculinas premium</a>
- <a href="/tenis">tenis masculinos</a>
- <a href="/calcas">calcas masculinas</a>
- <a href="/polos">polos masculinas</a>

---

## CHECKLIST FINAL (revise antes de entregar)
Confirme cada item:
[x] O artigo tem mais de 2.500 palavras
[x] Nenhuma secao tem menos de 4 paragrafos
[x] Todas as 8+ perguntas do FAQ tem respostas completas dentro de <details>
[x] O TOC tem links <a href="#secao-N"> para cada H2
[x] Ha pelo menos 2 tabelas com dados reais e uteis
[x] Ha pelo menos 6 <div class="image-suggestion"> distribuidas
[x] Nenhuma frase generica de IA foi usada
[x] Todo conteudo e direcionado a homens
[x] Nenhum tecido inventado foi usado
[x] Ha pelo menos 5 links internos contextuais

---

## TOM FINAL
"Uma dica sincera de alguem que realmente entende de moda masculina premium e compra nessas marcas ha anos."
Nunca: texto automatico de IA. Sempre: voz humana, opiniao, especificidade."""

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
        "HTTP-Referer": SITE_URL,
        "X-Title": "SEO Control Center -- Blog Generator",
    }
    errors = []
    for model in _openrouter_models():
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
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
            {"role": "system", "content": SYSTEM_PROMPT},
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
        "contents": [{"parts": [{"text": SYSTEM_PROMPT + "\n\n" + prompt}]}],
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
