"""
Blog post suggester baseado em dados do GSC.

Fluxo:
  1. Carrega queries e páginas do GSC
  2. Agrupa queries por tema/intenção
  3. Detecta gaps (queries sem página correspondente)
  4. Pontua oportunidades por tráfego potencial
  5. Gera brief completo do post via Groq/Gemini

Uso:
  python run.py --module suggest
  python run.py --module suggest --top 5
"""

import json
import re
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from config import (BASE_DIR, get_brand_aliases, get_business_context,
                    get_default_provider, get_product_terms, get_provider_api_key,
                    get_site_name, get_site_url)

BRIEFS_FILE = BASE_DIR / "blog_briefs.json"

# ── Stopwords PT-BR para limpeza de queries ───────────────────────────────────
STOPWORDS = {
    "de", "do", "da", "dos", "das", "para", "com", "sem", "em", "no", "na",
    "nos", "nas", "o", "a", "os", "as", "um", "uma", "uns", "umas", "e", "ou",
    "que", "se", "por", "como", "qual", "quais", "onde", "quando", "é", "são",
    "foi", "ser", "ter", "mais", "mas", "não", "sim", "já", "muito", "todo",
    "melhor", "melhores", "novo", "novos", "grande", "pequeno", "tipo",
}

BRAND_KEYWORDS = set()
PRODUCT_KEYWORDS = set()

# Intenções informacionais → oportunidade de blog
INFORMATIONAL_SIGNALS = {
    "como", "qual", "quais", "onde", "quando", "por que", "porque",
    "melhor", "melhores", "diferenca", "diferença", "guia", "dica",
    "dicas", "review", "vale a pena", "tamanho", "tamanhos", "original",
    "legitimo", "falso", "desconto",
}

def _brand_keywords() -> set[str]:
    values = set()
    for brand, aliases in get_brand_aliases().items():
        values.add(brand)
        values.update(aliases)
        for alias in aliases:
            values.update(alias.split())
    return {v for v in values if v}


def _product_keywords() -> set[str]:
    return set(get_product_terms())


def _site_terms() -> set[str]:
    host = get_site_name().lower().replace(".", " ").replace("-", " ")
    return {token for token in host.split() if len(token) > 2}


# ── Clustering ────────────────────────────────────────────────────────────────

def _clean_query(q: str) -> list:
    """Tokenize and clean a query."""
    tokens = re.sub(r"[^a-záéíóúâêîôûãõàüç\s]", " ", q.lower()).split()
    return [t for t in tokens if t not in STOPWORDS and len(t) > 2]


def _detect_brand(tokens: list) -> str:
    for brand in _brand_keywords():
        brand_tokens = brand.split()
        if all(bt in tokens for bt in brand_tokens):
            return brand.replace(" ", "-")
    return ""


def _detect_intent(tokens: list) -> str:
    if any(t in INFORMATIONAL_SIGNALS for t in tokens):
        return "informational"
    if any(t in _product_keywords() for t in tokens):
        return "commercial"
    return "navigational"


def _cluster_key(tokens: list, brand: str) -> str:
    """Generate a cluster key from query tokens."""
    if brand:
        brand_simple = brand.replace("-", " ")
        # Find a product word that isn't the same word as the brand itself
        product = next(
            (t for t in tokens if t in _product_keywords() and t not in brand_simple.split()),
            ""
        )
        return f"{brand}-{product}" if product else brand
    # No brand: use first 2 meaningful tokens
    meaningful = [t for t in tokens if t not in _brand_keywords()][:2]
    return "-".join(meaningful) if meaningful else tokens[0] if tokens else "outros"


def _is_site_query(tokens: list) -> bool:
    """Return True if the query is about the site itself (navigational, skip it)."""
    if any(t in _site_terms() for t in tokens):
        return True
    return False


def cluster_queries(queries: list) -> dict:
    """Group queries by topic cluster."""
    clusters = defaultdict(lambda: {
        "queries": [], "total_impressions": 0, "total_clicks": 0,
        "avg_position": 0.0, "brand": "", "intent": "", "key": "",
    })

    for q in queries:
        text    = q.get("query", "")
        tokens  = _clean_query(text)
        if not tokens:
            continue
        if _is_site_query(tokens):
            continue
        brand   = _detect_brand(tokens)
        intent  = _detect_intent(tokens)
        key     = _cluster_key(tokens, brand)

        c = clusters[key]
        c["queries"].append(q)
        c["total_impressions"] += q.get("impressions", 0)
        c["total_clicks"]      += q.get("clicks", 0)
        c["brand"]              = brand
        c["intent"]             = intent
        c["key"]                = key

    # Calculate avg position per cluster
    for key, c in clusters.items():
        positions = [q.get("position", 10) for q in c["queries"] if q.get("position")]
        c["avg_position"] = round(sum(positions) / len(positions), 1) if positions else 10.0
        c["ctr"] = round(c["total_clicks"] / c["total_impressions"] * 100, 2) if c["total_impressions"] else 0

    return dict(clusters)


# ── Gap detection ─────────────────────────────────────────────────────────────

def detect_gaps(clusters: dict, pages: list) -> list:
    """Find clusters with no matching page on the site."""
    # Build set of known page slugs
    known_slugs = set()
    for p in pages:
        url = p.get("page", "")
        path = url.replace(get_site_url(), "").strip("/")
        parts = path.split("/")
        for part in parts:
            known_slugs.add(part.lower().replace("-", " "))
            known_slugs.add(part.lower())

    gaps = []
    for key, cluster in clusters.items():
        cluster_str = key.replace("-", " ")
        # A cluster is only "covered" if there's a slug that matches the FULL
        # cluster string (both brand + product), not just one token of it.
        # This prevents a broad entity page from marking entity+product gaps as covered.
        covered = any(
            cluster_str == slug or cluster_str in slug
            for slug in known_slugs
            if len(slug) > 3
        )
        if not covered and cluster["intent"] in ("informational", "commercial"):
            gaps.append({**cluster, "cluster_key": key, "has_page": False})

    return gaps


# ── Opportunity scoring ───────────────────────────────────────────────────────

def score_opportunities(clusters: dict, pages: list) -> list:
    """Score all clusters by content opportunity potential."""
    gaps = detect_gaps(clusters, pages)

    scored = []
    for gap in gaps:
        imp  = gap["total_impressions"]
        pos  = gap["avg_position"]
        ctr  = gap["ctr"]

        # Opportunity score: high impressions + borderline position + low CTR
        pos_score = max(0, 20 - pos) / 20        # 0–1, best at pos 1
        vol_score = min(1, imp / 10000)           # 0–1, capped at 10k
        ctr_gap   = max(0, 5 - ctr) / 5          # 0–1, low CTR = high gap

        score = round((vol_score * 0.5 + pos_score * 0.3 + ctr_gap * 0.2) * 100, 1)

        # Estimate potential clicks if we get to position 5 (7% CTR)
        potential_clicks = int(imp * 0.07)

        scored.append({
            **gap,
            "opportunity_score": score,
            "potential_clicks":  potential_clicks,
            "top_queries":       sorted(gap["queries"],
                                        key=lambda x: x.get("impressions", 0),
                                        reverse=True)[:5],
        })

    return sorted(scored, key=lambda x: x["opportunity_score"], reverse=True)


# ── Brief generation ──────────────────────────────────────────────────────────

def _brief_system() -> str:
    return f"""Você é um especialista em SEO e content marketing.

Site: {get_site_name()} — {get_site_url() or "site configurado pelo cliente"}.
Contexto do negócio: {get_business_context()}

Gere um brief completo para um post de blog focado em SEO. O post deve:
- Ranquear para as queries indicadas
- Ser informativo e útil, não apenas comercial
- Mencionar naturalmente produtos, serviços ou categorias do cliente quando fizer sentido
- Ter tom consultivo e alinhado ao contexto do negócio

Retorne APENAS um JSON válido. Formato exato:
{
  "url_slug": "slug-da-url-sem-barra",
  "meta_title": "Título SEO (50-60 chars)",
  "meta_description": "Description (145-160 chars)",
  "h1": "Título do post (40-70 chars)",
  "introduction": "Introdução de 2 parágrafos em texto puro",
  "sections": [
    {"h2": "Título da seção", "summary": "O que cobrir nessa seção (2-3 linhas)"}
  ],
  "faq": [
    {"question": "Pergunta frequente?", "answer": "Resposta direta (2-3 linhas)"}
  ],
  "internal_links": [
    {"anchor": "texto do link", "url": "/pagina-destino", "where": "onde inserir no texto"}
  ],
  "cta_section": "Parágrafo de CTA final apontando para produtos, serviços ou categorias do cliente",
  "estimated_words": 1200
}"""


def generate_brief(opportunity: dict, provider: str = None,
                   api_key: str = None) -> dict:
    """Generate a full blog post brief for a content opportunity."""
    import requests as req

    if provider and not api_key:
        api_key = get_provider_api_key(provider)

    if not provider or not api_key:
        provider, api_key = get_default_provider()
    if not provider:
        raise RuntimeError("Nenhuma API configurada. Adicione uma chave no .env")

    top_q = opportunity.get("top_queries", [])
    queries_text = "\n".join(
        f"  - \"{q['query']}\" | {q.get('impressions',0):,} impressões | "
        f"pos {q.get('position',0):.1f} | CTR {q.get('ctr',0):.2f}%"
        for q in top_q
    )

    brand   = opportunity.get("brand", "")
    intent  = opportunity.get("intent", "")
    cluster = opportunity.get("cluster_key", "")

    prompt = f"""Crie um brief de post de blog para {get_site_name()} com base nessas queries do Google:

Cluster: "{cluster}"
Marca relacionada: {brand or "sem marca específica"}
Intenção: {intent}
Impressões totais no cluster: {opportunity.get('total_impressions', 0):,}
Potencial de cliques (posição 5): ~{opportunity.get('potential_clicks', 0):,}/mês

Queries principais:
{queries_text}

O post deve ranquear primariamente para: "{top_q[0]['query'] if top_q else cluster}"

Links internos disponíveis (use os mais relevantes):
- páginas prioritárias configuradas no sistema
- URLs presentes nos dados do Search Console

Gere o brief completo seguindo o formato JSON especificado."""

    from modules.content_generator import (
        _call_openrouter, _call_groq, _call_gemini, _call_mistral, _call_anthropic, _parse_json
    )
    callers = {"openrouter": _call_openrouter, "groq": _call_groq, "gemini": _call_gemini,
               "mistral": _call_mistral, "anthropic": _call_anthropic}

    caller = callers.get(provider)
    if not caller:
        raise ValueError(f"Provider '{provider}' desconhecido")

    # Temporarily override system prompt
    import modules.content_generator as cg
    original = cg.SYSTEM_PROMPT_OVERRIDE
    cg.SYSTEM_PROMPT_OVERRIDE = _brief_system()

    try:
        raw    = caller(prompt, api_key)
        result = _parse_json(raw)
    finally:
        cg.SYSTEM_PROMPT_OVERRIDE = original

    result["_cluster"]      = cluster
    result["_brand"]        = brand
    result["_generated_at"] = datetime.now().isoformat()
    result["_opportunity"]  = {
        "impressions":       opportunity.get("total_impressions", 0),
        "potential_clicks":  opportunity.get("potential_clicks", 0),
        "score":             opportunity.get("opportunity_score", 0),
        "top_queries":       [q["query"] for q in top_q],
    }

    return result


def save_briefs(briefs: list):
    existing = []
    if BRIEFS_FILE.exists():
        try:
            existing = json.loads(BRIEFS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    slugs = {b.get("url_slug"): i for i, b in enumerate(existing)}
    for brief in briefs:
        slug = brief.get("url_slug", "")
        if slug in slugs:
            existing[slugs[slug]] = brief
        else:
            existing.append(brief)

    BRIEFS_FILE.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def print_opportunities(opps: list, top: int = 10):
    """Print opportunity table to terminal."""
    print(f"\n  {'#':>2}  {'Cluster':<30} {'Impressoes':>10} {'Pos':>5} "
          f"{'Potencial':>9} {'Score':>6}  Intent")
    print("  " + "-" * 72)
    for i, o in enumerate(opps[:top], 1):
        print(f"  {i:>2}  {o['cluster_key'][:30]:<30} "
              f"{o['total_impressions']:>10,} "
              f"{o['avg_position']:>5.1f} "
              f"{o['potential_clicks']:>8,}  "
              f"{o['opportunity_score']:>5.1f}  "
              f"{o['intent']}")


# ── Main entry point ──────────────────────────────────────────────────────────

def run(gsc_data: dict, top: int = 5, generate: bool = True,
        provider: str = None, api_key: str = None) -> list:
    """
    Analyze GSC data, find blog opportunities, generate briefs.
    """
    queries = gsc_data.get("top_queries", [])
    pages   = gsc_data.get("top_pages", [])

    if not queries:
        print("  Sem dados de queries. Execute com --gsc apontando para a pasta correta.")
        return []

    print(f"  Analisando {len(queries)} queries...")

    clusters = cluster_queries(queries)
    print(f"  {len(clusters)} clusters identificados")

    opps = score_opportunities(clusters, pages)
    print(f"  {len(opps)} oportunidades de conteudo detectadas")

    print_opportunities(opps, top=15)

    if not generate or not opps:
        return opps

    # Generate briefs for top opportunities
    if provider and not api_key:
        api_key = get_provider_api_key(provider)

    if not provider or not api_key:
        provider, api_key = get_default_provider()

    print(f"\n  Gerando briefs para o top {top} via {provider}...")
    briefs = []
    for opp in opps[:top]:
        cluster = opp["cluster_key"]
        try:
            brief = generate_brief(opp, provider, api_key)
            briefs.append(brief)
            print(f"   ok {cluster[:45]:<45}  → /{brief.get('url_slug','?')}")
        except Exception as e:
            print(f"   x  {cluster[:45]} — {e}")

    if briefs:
        save_briefs(briefs)
        print(f"\n  {len(briefs)} brief(s) salvos em: {BRIEFS_FILE}")
        _print_best_brief(briefs[0])

    return briefs


def _print_best_brief(brief: dict):
    """Print the top brief to terminal for quick review."""
    print("\n" + "=" * 55)
    print("  MELHOR OPORTUNIDADE — BRIEF GERADO")
    print("=" * 55)
    print(f"  URL       : /{brief.get('url_slug','')}")
    print(f"  H1        : {brief.get('h1','')}")
    print(f"  Title     : {brief.get('meta_title','')} ({len(brief.get('meta_title',''))} chars)")
    print(f"  Desc      : {brief.get('meta_description','')} ({len(brief.get('meta_description',''))} chars)")
    print(f"  Palavras  : ~{brief.get('estimated_words', 1200)}")
    print(f"\n  Secoes:")
    for s in brief.get("sections", []):
        print(f"    H2: {s.get('h2','')}")
    print(f"\n  FAQ ({len(brief.get('faq', []))} perguntas):")
    for f in brief.get("faq", [])[:3]:
        print(f"    Q: {f.get('question','')}")
    print(f"\n  Links internos sugeridos:")
    for l in brief.get("internal_links", []):
        print(f"    [{l.get('anchor','')}] → {l.get('url','')}")
    print("=" * 55)
