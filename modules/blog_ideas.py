import json
import re
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from config import (get_brand_aliases, get_business_context, get_product_terms,
                    get_scoped_runtime_file, get_site_id, get_site_name, get_site_url)

IDEAS_FILE = get_scoped_runtime_file("blog_ideas.json", "content")
AI_BATCH_SIZE = 3

QUESTION_TERMS = {
    "como", "qual", "quais", "quando", "onde", "porque", "por que",
    "vale", "diferenca", "diferença", "tamanho", "medida", "original",
    "combinar", "usar", "lavar", "cuidar",
}

SEASONALITY_BY_MONTH = {
    1: ["verao", "ferias", "volta as aulas", "ano novo"],
    2: ["carnaval", "verao", "volta as aulas"],
    3: ["outono", "meia-estacao"],
    4: ["outono", "dia das maes"],
    5: ["dia das maes", "frio chegando", "namorados"],
    6: ["dia dos namorados", "inverno", "festa junina"],
    7: ["inverno", "ferias", "liquidacao"],
    8: ["dia dos pais", "inverno", "meia-estacao"],
    9: ["primavera", "meia-estacao"],
    10: ["dia das criancas", "primavera", "pre-black friday"],
    11: ["black friday", "cyber monday", "presentes"],
    12: ["natal", "reveillon", "presentes", "verao"],
}

TOPIC_STOPWORDS = {
    "a", "as", "o", "os", "de", "da", "das", "do", "dos", "e", "em", "no", "na",
    "nos", "nas", "para", "por", "com", "sem", "um", "uma", "uns", "umas", "ao",
    "aos", "ou", "que", "qual", "quais", "como", "quando", "onde", "porque",
    "por", "que", "melhor", "melhores", "comprar", "preco", "preço", "valor",
    "desconto", "promocao", "promoção", "barato", "barata", "original", "loja",
    "online", "site", "brasil",
}

def _brand_keywords() -> set[str]:
    values = set()
    for brand, aliases in get_brand_aliases().items():
        values.add(brand)
        values.update(aliases)
    return {value for value in values if value}


def _blog_idea_system() -> str:
    return f"""Voce e um estrategista senior de SEO e conteudo.

Contexto:
- Site: {get_site_name()} ({get_site_url() or "site configurado pelo cliente"}).
- Negocio: {get_business_context()}.
- Objetivo: transformar queries do Google Search Console em pautas de blog que geram trafego qualificado e apoiam venda de categorias/produtos.

Regras:
- Nao invente dados de volume, preco, estoque ou promocoes.
- Preserve a marca e o produto detectados.
- Priorize conteudo util, comercialmente acionavel e alinhado a busca em PT-BR.
- Nao use formulas genericas como "Dicas e Modelos", "Guia Completo" ou "em Destaque" quando houver alternativa mais especifica.
- Se receber titulos anteriores, crie um H1 com angulo diferente deles.
- Seja conciso: no maximo 5 sections, 3 faq, 8 entities e 3 internal_links.
- Retorne APENAS JSON valido, sem markdown e sem texto fora do JSON.

Formato:
{{
  "h1": "...",
  "meta_title": "...",
  "meta_description": "...",
  "search_intent": "...",
  "angle": "...",
  "content_type": "...",
  "sections": ["...", "..."],
  "faq": ["...", "..."],
  "entities": ["...", "..."],
  "internal_links": [{{"anchor": "...", "url": "...", "reason": "..."}}],
  "recommended_products_or_categories": ["...", "..."],
  "brief_notes": ["...", "..."]
}}"""

def _blog_ideas_batch_system() -> str:
    return f"""Voce e um estrategista senior de SEO e editor de conteudo.

Contexto:
- Site: {get_site_name()} ({get_site_url() or "site configurado pelo cliente"}).
- Negocio: {get_business_context()}.
- Objetivo: transformar queries do Google Search Console em pautas de blog COMPLETAMENTE DIFERENTES entre si, com potencial comercial real.

Regras editoriais obrigatorias:
- DIVERSIDADE E OBRIGATORIA: cada pauta deve ter H1, meta_description, angle, sections e content_type diferentes das outras. Nao repita a mesma formula em nenhum campo.
- Angulos disponiveis (use um diferente por pauta): comparativo, custo-beneficio, materiais/durabilidade, passo a passo, erros comuns, tendencias, duvidas frequentes, checklist, casos de uso, mitos e verdades.
- H1: responde a query principal diretamente. Sem "Guia de escolha e", "Descubra os", "Encontre o" no inicio.
- meta_description: cada uma deve ter um foco diferente — uma fala de tamanho, outra de preco, outra de autenticidade, outra de looks, etc. Nunca use "Dicas de material, caimento e como montar looks versateis" — essa frase e proibida.
- sections: cada pauta deve ter uma estrutura de topicos diferente. Nao use "Melhores modelos" e "Como combinar no dia a dia" em todas as pautas.
- Preserve marca, produto, queries, impressoes e URL da pauta base.
- Nao invente dados de volume, preco, estoque ou promocoes.
- Use PT-BR natural e orientado a compra, mas sem exagero publicitario.
- Seja conciso: no maximo 5 sections, 3 faq, 8 entities e 3 internal_links por pauta.
- Retorne APENAS JSON valido, sem markdown e sem texto fora do JSON.

Formato:
{{
  "ideas": [
    {{
      "url_slug": "mesmo url_slug recebido",
      "h1": "...",
      "meta_title": "...",
      "meta_description": "...",
      "search_intent": "...",
      "angle": "...",
      "content_type": "...",
      "sections": ["...", "..."],
      "faq": ["...", "..."],
      "entities": ["...", "..."],
      "internal_links": [{{"anchor": "...", "url": "...", "reason": "..."}}],
      "recommended_products_or_categories": ["...", "..."],
      "brief_notes": ["...", "..."]
    }}
  ]
}}"""


def _strategic_blog_ideas_system() -> str:
    return f"""Voce e um estrategista senior de SEO editorial para e-commerce.

Contexto:
- Site: {get_site_name()} ({get_site_url() or "site configurado pelo cliente"}).
- Negocio: {get_business_context()}.
- Objetivo: estudar consultas reais do Google Search Console e propor pautas de blog com potencial organico e comercial.

Como decidir as pautas:
- Nao escolha apenas as queries com mais impressoes. Agrupe consultas em temas, intencoes e duvidas do publico.
- Considere: duvidas frequentes, sazonalidade, queda/baixa CTR, posicao media, oportunidades comerciais e lacunas de conteudo.
- Use conteudos existentes para evitar duplicar pauta; se o tema ja existir, proponha um angulo complementar.
- Dê preferencia a pautas que possam criar ponte para categorias/produtos, mas tambem aceite temas educativos com boa intencao.
- Nao invente volume, preco, estoque, promocao ou dados externos.
- Use PT-BR natural.

Formato obrigatorio:
{{
  "ideas": [
    {{
      "h1": "...",
      "meta_title": "...",
      "meta_description": "...",
      "primary_query": "...",
      "source_queries": ["...", "..."],
      "search_intent": "informational | commercial investigation | transactional support | seasonal",
      "audience_question": "...",
      "angle": "...",
      "content_type": "guia | comparativo | checklist | faq | tendencia | sazonal | evergreen",
      "seasonality": "evergreen ou janela sazonal",
      "recommended_publish_month": "YYYY-MM ou evergreen",
      "opportunity_reason": "...",
      "content_gap": "...",
      "priority": 0,
      "sections": ["...", "..."],
      "faq": ["...", "..."],
      "entities": ["...", "..."],
      "internal_links": [{{"anchor": "...", "url": "...", "reason": "..."}}],
      "recommended_products_or_categories": ["...", "..."]
    }}
  ]
}}

Retorne APENAS JSON valido, sem markdown e sem texto fora do JSON."""

MODIFIERS = {
    "melhor",
    "melhores",
    "como",
    "qual",
    "quais",
    "diferença",
    "diferenca",
    "guia",
    "dica",
    "dicas",
    "tamanho",
    "original",
    "vale",
}

PLURAL_NORMALIZATION = {
    "moletom": "moletons",
    "moletons": "moletons",
    "tenis": "tenis",
    "tênis": "tenis",
    "camiseta": "camisetas",
    "camisetas": "camisetas",
    "polo": "polos",
    "polos": "polos",
    "jaqueta": "jaquetas",
    "jaquetas": "jaquetas",
    "calca": "calcas",
    "calça": "calcas",
    "calcas": "calcas",
    "calças": "calcas",
    "camisa": "camisas",
    "camisas": "camisas",
    "bone": "bones",
    "boné": "bones",
    "bones": "bones",
    "chinelo": "chinelos",
    "chinelos": "chinelos",
}

DISPLAY_PRODUCT = {
    "moletons": "moletons",
    "tenis": "tenis",
    "camisetas": "camisetas",
    "polos": "polos",
    "jaquetas": "jaquetas",
    "calcas": "calcas",
    "camisas": "camisas",
    "bones": "bones",
    "chinelos": "chinelos",
}

GENERIC_TITLE_PATTERNS = [
    "dicas e modelos",
    "guia completo",
    "em destaque",
    "para o seu estilo",
    "escolha perfeita",
    "guia de compras",
    "guia definitivo",
    "descubra os melhores",
    "como escolher o melhor",
    "escolhendo o",
    "escolhendo a",
    "escolhendo as",
    "escolhendo os",
    "guia para escolher",
]

TITLE_ANGLES = [
    "{product_title} {brand_title} original: como identificar antes de comprar",
    "{product_title} {brand_title}: opções para usar no dia a dia",
    "{product_title} {brand_title}: o que comparar antes de escolher",
    "{product_title} {brand_title}: critérios para decidir melhor",
    "{product_title} {brand_title} com desconto: sinais de uma boa compra",
    "{product_title} {brand_title}: combinações e usos práticos",
    "{product_title} {brand_title}: materiais, acabamento e durabilidade",
    "{product_title} {brand_title}: quando vale investir",
    "{product_title} {brand_title}: como escolher sem erro",
    "{product_title} {brand_title}: perguntas antes de comprar online",
]


def _tokens(query: str) -> list[str]:
    return re.sub(r"[^a-zA-ZÀ-ÿ0-9\s-]", " ", query.lower()).replace("-", " ").split()


def _query_sources(gsc_data: dict) -> list[dict]:
    sources = (
        gsc_data.get("top_queries", [])
        + gsc_data.get("content_opps", [])
        + gsc_data.get("quick_wins", [])
        + gsc_data.get("pos_opps", [])
        + gsc_data.get("ctr_opps", [])
    )
    seen = set()
    rows = []
    for row in sources:
        query = str(row.get("query", "")).strip()
        if not query:
            continue
        key = query.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def _query_intent_tags(query: str) -> list[str]:
    tokens = _tokens(query)
    tags = []
    if set(tokens) & QUESTION_TERMS:
        tags.append("duvida")
    if any(token in tokens for token in ("melhor", "melhores", "comparar", "versus", "vs")):
        tags.append("comparacao")
    if any(token in tokens for token in ("comprar", "preco", "preço", "desconto", "cupom", "outlet")):
        tags.append("comercial")
    if any(token in tokens for token in ("inverno", "verao", "verão", "natal", "black", "presente", "namorados")):
        tags.append("sazonal")
    return tags or ["exploracao"]


def _compact_queries(gsc_data: dict, limit: int = 160) -> list[dict]:
    rows = sorted(
        _query_sources(gsc_data),
        key=lambda row: (
            int(row.get("impressions", 0) or 0),
            int(row.get("clicks", 0) or 0),
        ),
        reverse=True,
    )
    compact = []
    for row in rows[:limit]:
        query = str(row.get("query", "")).strip()
        compact.append({
            "query": query,
            "impressions": int(row.get("impressions", 0) or 0),
            "clicks": int(row.get("clicks", 0) or 0),
            "ctr": row.get("ctr", 0),
            "position": row.get("position", 0),
            "intent_tags": _query_intent_tags(query),
        })
    return compact


def _seasonality_context(today: date | None = None) -> dict:
    today = today or date.today()
    months = []
    for offset in range(4):
        month = ((today.month - 1 + offset) % 12) + 1
        year = today.year + ((today.month - 1 + offset) // 12)
        months.append({
            "month": f"{year:04d}-{month:02d}",
            "themes": SEASONALITY_BY_MONTH.get(month, []),
        })
    return {"today": today.isoformat(), "next_months": months}


def _load_existing_content(limit: int = 80) -> list[dict]:
    items = []
    try:
        site_id = get_site_id()
        if site_id:
            from modules import supabase_store
            rows = (
                supabase_store._client()
                .table("content_changes")
                .select("url, provider, status, meta_title, h1, raw, created_at")
                .eq("site_id", site_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute().data
                or []
            )
            for row in rows:
                raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
                title = row.get("h1") or row.get("meta_title") or raw.get("h1") or raw.get("title")
                if title:
                    items.append({
                        "title": title,
                        "url": row.get("url") or raw.get("url"),
                        "status": row.get("status"),
                        "provider": row.get("provider"),
                    })
            return items[:limit]
    except Exception:
        pass

    try:
        if IDEAS_FILE.exists():
            existing = json.loads(IDEAS_FILE.read_text(encoding="utf-8"))
            for item in existing[:limit]:
                items.append({
                    "title": item.get("h1") or item.get("meta_title"),
                    "url": item.get("url"),
                    "status": item.get("status"),
                    "provider": item.get("provider"),
                })
    except Exception:
        pass
    return [item for item in items if item.get("title")]


def _detect_brand(tokens: list[str]) -> str:
    joined = " ".join(tokens)
    for brand in sorted(_brand_keywords(), key=len, reverse=True):
        if brand in joined:
            return brand.replace(" ", "-")
    return ""


def _detect_product(tokens: list[str]) -> str:
    configured = get_product_terms()
    for token in tokens:
        if token in configured:
            return PLURAL_NORMALIZATION.get(token, token)
    for token in tokens:
        singular = token.rstrip("s")
        if singular in configured:
            return PLURAL_NORMALIZATION.get(singular, singular)
    return ""


def _has_blog_intent(tokens: list[str]) -> bool:
    return bool(set(tokens) & MODIFIERS)


def _title_for(product: str, brand: str, intent: str) -> str:
    brand_label = brand.replace("-", " ").title()
    product_label = DISPLAY_PRODUCT.get(product, product).lower()
    if intent == "guide":
        return f"Como escolher {product_label} {brand_label}"
    if intent == "comparison":
        return f"{brand_label}: guia de {product_label}"
    return f"Melhores {product_label} {brand_label}"


def _intent(tokens: list[str]) -> str:
    if "como" in tokens or "tamanho" in tokens:
        return "guide"
    if "diferença" in tokens or "diferenca" in tokens or "qual" in tokens or "quais" in tokens:
        return "comparison"
    return "best"


def _slug(text: str) -> str:
    value = text.lower()
    replacements = {
        "ç": "c", "ã": "a", "á": "a", "à": "a", "â": "a",
        "é": "e", "ê": "e", "í": "i", "ó": "o", "ô": "o",
        "õ": "o", "ú": "u",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value


_INTENT_SECTIONS = {
    "guide": lambda p, b: [
        f"Como escolher o tamanho certo de {p} {b}",
        f"Materiais e acabamento: o que avaliar em {p} {b}",
        f"Diferenca entre modelos basicos e avancados",
        f"Como cuidar e conservar {p} {b} por mais tempo",
        f"Onde comprar {p} {b} original com seguranca",
    ],
    "comparison": lambda p, b: [
        f"Principais linhas de {p} {b}: o que muda entre elas",
        f"Comparativo de modelos por ocasiao de uso",
        f"Custo-beneficio: quando vale pagar mais",
        f"Como identificar {p} {b} original",
        f"Qual modelo comprar primeiro?",
    ],
    "best": lambda p, b: [
        f"O que avaliar nos modelos de {p} {b} mais buscados",
        f"Diferenciais que fazem {b} se destacar nessa categoria",
        f"Como combinar {p} {b} em looks casuais e semi-formais",
        f"Sinais de autenticidade no produto {b}",
        f"Onde encontrar {p} {b} com melhor preco garantido",
    ],
}

_INTENT_FAQ = {
    "guide": lambda p, b: [
        f"Qual tamanho de {p} {b} devo escolher?",
        f"{p.title()} {b} encolhe na lavagem?",
        f"Como saber se o {p} {b} e original?",
    ],
    "comparison": lambda p, b: [
        f"Qual linha de {p} {b} e melhor para uso diario?",
        f"Vale a pena pagar mais caro em {p} {b}?",
        f"Qual a diferenca entre os modelos de {p} {b}?",
    ],
    "best": lambda p, b: [
        f"{b} tem {p} original disponivel?",
        f"Quais os {p} {b} mais vendidos?",
        f"Como comprar {p} {b} com desconto e seguranca?",
    ],
}

_INTENT_DESC = {
    "guide": lambda p, b: (
        f"Guia completo para escolher {p} {b}: tamanhos, materiais, modelos e cuidados. "
        f"Tudo que voce precisa saber antes de comprar."
    ),
    "comparison": lambda p, b: (
        f"Compare as principais linhas de {p} {b} e descubra qual modelo vale mais para o seu estilo e orcamento."
    ),
    "best": lambda p, b: (
        f"Conheca os {p} {b} mais buscados, seus diferenciais e como identificar o produto original antes de comprar."
    ),
}


def _brief(title: str, product: str, brand: str, queries: list[dict], intent: str = "best") -> dict:
    brand_label = brand.replace("-", " ").title()
    product_label = DISPLAY_PRODUCT.get(product, product)
    top_queries = [q["query"] for q in queries[:8]]
    slug = _slug(title)
    desc_fn = _INTENT_DESC.get(intent, _INTENT_DESC["best"])
    sec_fn  = _INTENT_SECTIONS.get(intent, _INTENT_SECTIONS["best"])
    faq_fn  = _INTENT_FAQ.get(intent, _INTENT_FAQ["best"])
    return {
        "url_slug": slug,
        "url": f"/{slug}",
        "meta_title": title[:60],
        "meta_description": desc_fn(product_label, brand_label)[:160],
        "h1": title,
        "primary_query": top_queries[0] if top_queries else title.lower(),
        "queries": top_queries,
        "sections": sec_fn(product_label, brand_label),
        "faq": faq_fn(product_label, brand_label),
        "internal_links": [
            {"anchor": brand_label, "url": f"/{brand}"},
            {"anchor": product_label.title(), "url": f"/{product}-{brand}"},
            {"anchor": f"{product_label.title()} {brand_label}", "url": f"/blusas-jaquetas-e-moletons-{brand}"},
        ],
        "status": "idea",
        "provider": "query_suggester",
        "ai_enhanced": False,
        "brand": brand,
        "product": product,
        "intent": intent,
        "generated_at": datetime.now().isoformat(),
    }


def suggest_from_gsc(gsc_data: dict, min_impressions: int = 50) -> list[dict]:
    grouped = defaultdict(lambda: {"queries": [], "impressions": 0, "clicks": 0})
    query_sources = (
        gsc_data.get("top_queries", [])
        + gsc_data.get("content_opps", [])
        + gsc_data.get("quick_wins", [])
        + gsc_data.get("pos_opps", [])
        + gsc_data.get("ctr_opps", [])
    )

    seen = set()
    for row in query_sources:
        query = str(row.get("query", "")).strip()
        if not query or query in seen:
            continue
        seen.add(query)
        tokens = _tokens(query)
        brand = _detect_brand(tokens)
        product = _detect_product(tokens)
        if not brand or not product:
            continue
        intent = _intent(tokens)
        if not _has_blog_intent(tokens) and int(row.get("impressions", 0) or 0) < min_impressions:
            continue
        key = f"{brand}:{product}:{intent}"
        grouped[key]["brand"] = brand
        grouped[key]["product"] = product
        grouped[key]["intent"] = intent
        grouped[key]["queries"].append(row)
        grouped[key]["impressions"] += int(row.get("impressions", 0) or 0)
        grouped[key]["clicks"] += int(row.get("clicks", 0) or 0)

    ideas = []
    for data in grouped.values():
        queries = sorted(data["queries"], key=lambda q: q.get("impressions", 0), reverse=True)
        title = _title_for(data["product"], data["brand"], data["intent"])
        brief = _brief(title, data["product"], data["brand"], queries, intent=data["intent"])
        brief["impressions"] = data["impressions"]
        brief["clicks"] = data["clicks"]
        brief["opportunity_score"] = round(min(100, 30 + data["impressions"] / 200), 1)
        ideas.append(brief)

    return sorted(ideas, key=lambda item: item["opportunity_score"], reverse=True)


def _query_metrics(source_queries: list[str], query_index: dict[str, dict]) -> dict:
    impressions = 0
    clicks = 0
    positions = []
    for query in source_queries:
        row = query_index.get(str(query).casefold())
        if not row:
            continue
        impressions += int(row.get("impressions", 0) or 0)
        clicks += int(row.get("clicks", 0) or 0)
        try:
            pos = float(row.get("position", 0) or 0)
            if pos:
                positions.append(pos)
        except Exception:
            pass
    avg_position = round(sum(positions) / len(positions), 1) if positions else 0
    return {"impressions": impressions, "clicks": clicks, "avg_position": avg_position}


def _normalize_ai_ideas(ai_data: dict, query_rows: list[dict], top: int) -> list[dict]:
    query_index = {str(row.get("query", "")).casefold(): row for row in query_rows}
    ideas = []
    used_slugs = set()
    for item in ai_data.get("ideas", []) or []:
        if not isinstance(item, dict):
            continue
        h1 = str(item.get("h1") or item.get("title") or "").strip()
        primary = str(item.get("primary_query") or "").strip()
        source_queries = [
            str(query).strip()
            for query in (item.get("source_queries") or item.get("queries") or ([primary] if primary else []))
            if str(query).strip()
        ]
        if not h1:
            continue
        slug = _slug(item.get("url_slug") or h1)
        if not slug or slug in used_slugs:
            continue
        used_slugs.add(slug)
        metrics = _query_metrics(source_queries, query_index)
        priority = item.get("priority", item.get("opportunity_score", 0))
        try:
            priority = float(priority)
        except Exception:
            priority = 0
        if not priority:
            priority = min(100, 30 + metrics["impressions"] / 250 + max(0, 12 - metrics["avg_position"]))
        ideas.append({
            "url_slug": slug,
            "url": f"/{slug}",
            "meta_title": str(item.get("meta_title") or h1)[:60],
            "meta_description": str(item.get("meta_description") or item.get("opportunity_reason") or "")[:160],
            "h1": h1,
            "primary_query": primary or (source_queries[0] if source_queries else h1.lower()),
            "queries": source_queries[:10],
            "sections": list(item.get("sections") or [])[:6],
            "faq": list(item.get("faq") or [])[:5],
            "internal_links": list(item.get("internal_links") or [])[:4],
            "entities": list(item.get("entities") or [])[:10],
            "recommended_products_or_categories": list(item.get("recommended_products_or_categories") or [])[:6],
            "status": "idea",
            "provider": f"query_suggester+{ai_data.get('_ai_provider', 'ai')}",
            "ai_enhanced": True,
            "search_intent": item.get("search_intent"),
            "audience_question": item.get("audience_question"),
            "angle": item.get("angle"),
            "content_type": item.get("content_type"),
            "seasonality": item.get("seasonality"),
            "recommended_publish_month": item.get("recommended_publish_month"),
            "opportunity_reason": item.get("opportunity_reason"),
            "content_gap": item.get("content_gap"),
            "opportunity_score": round(min(100, max(0, priority)), 1),
            "impressions": metrics["impressions"],
            "clicks": metrics["clicks"],
            "avg_position": metrics["avg_position"],
            "_ai_provider": ai_data.get("_ai_provider"),
            "_ai_generated_at": ai_data.get("_ai_generated_at"),
            "generated_at": datetime.now().isoformat(),
        })
        if len(ideas) >= top:
            break
    return ideas


def _idea_sort_key(item: dict) -> tuple:
    return (
        float(item.get("opportunity_score", 0) or 0),
        int(item.get("impressions", 0) or 0),
        int(item.get("clicks", 0) or 0),
    )


def _dedupe_ideas(ideas: list[dict], limit: int) -> list[dict]:
    deduped = []
    seen = set()
    for idea in sorted(ideas, key=_idea_sort_key, reverse=True):
        slug = idea.get("url_slug") or _slug(idea.get("h1", ""))
        primary = str(idea.get("primary_query", "")).casefold()
        key = slug or primary
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(idea)
        if len(deduped) >= limit:
            break
    return deduped


def _topic_terms(query: str) -> list[str]:
    brand_terms = set()
    for brand in _brand_keywords():
        brand_terms.update(_tokens(brand))
    terms = []
    for token in _tokens(query):
        if len(token) < 3 or token in TOPIC_STOPWORDS or token in brand_terms:
            continue
        if token not in terms:
            terms.append(token)
    if not terms:
        for token in _tokens(query):
            if len(token) >= 3 and token not in terms:
                terms.append(token)
    return terms[:5]


def _cluster_queries_for_blog(query_rows: list[dict]) -> list[dict]:
    grouped = {}
    for row in query_rows:
        query = str(row.get("query", "")).strip()
        if not query:
            continue
        terms = _topic_terms(query)
        if not terms:
            continue
        tags = row.get("intent_tags") or _query_intent_tags(query)
        key = ":".join([tags[0], *terms[:3]])
        group = grouped.setdefault(key, {
            "terms": terms,
            "tags": set(),
            "queries": [],
            "impressions": 0,
            "clicks": 0,
            "positions": [],
        })
        group["tags"].update(tags)
        group["queries"].append(row)
        group["impressions"] += int(row.get("impressions", 0) or 0)
        group["clicks"] += int(row.get("clicks", 0) or 0)
        try:
            pos = float(row.get("position", 0) or 0)
            if pos:
                group["positions"].append(pos)
        except Exception:
            pass
    return sorted(grouped.values(), key=lambda item: (item["impressions"], item["clicks"]), reverse=True)


def _topic_label(terms: list[str]) -> str:
    topic = " ".join(terms[:4]).strip() or "tema pesquisado"
    return topic[:1].upper() + topic[1:]


def _fallback_title(topic: str, primary_query: str, tags: set[str]) -> str:
    query = primary_query.strip()
    if "duvida" in tags and query:
        lowered = query.lower()
        if lowered.startswith(("como ", "qual ", "quais ", "quando ", "onde ")):
            return f"{query[:1].upper() + query[1:]}: guia pratico"
        return f"Como escolher {topic.lower()} sem erro"
    if "comparacao" in tags:
        return f"{topic}: comparativo para escolher melhor"
    if "sazonal" in tags:
        return f"{topic}: guia sazonal para comprar melhor"
    if "comercial" in tags:
        return f"{topic}: o que avaliar antes de comprar"
    return f"{topic}: guia completo para escolher melhor"


def _fallback_sections(topic: str, tags: set[str]) -> list[str]:
    sections = [
        f"O que o publico quer saber sobre {topic.lower()}",
        f"Como escolher {topic.lower()} com mais seguranca",
        "Principais criterios antes da compra",
        "Erros comuns e como evitar",
        "Perguntas frequentes dos usuarios",
    ]
    if "sazonal" in tags:
        sections.insert(1, "Quando publicar e como aproveitar a sazonalidade")
    if "comparacao" in tags:
        sections.insert(1, "Comparativo entre opcoes e criterios de decisao")
    return sections[:6]


def _fallback_faq(topic: str, queries: list[dict]) -> list[str]:
    faq = []
    for row in queries:
        query = str(row.get("query", "")).strip()
        tokens = _tokens(query)
        if set(tokens) & QUESTION_TERMS:
            question = query.rstrip("?")
            faq.append(question[:1].upper() + question[1:] + "?")
        if len(faq) >= 3:
            break
    if faq:
        return faq
    return [
        f"Como escolher {topic.lower()}?",
        f"O que comparar antes de comprar {topic.lower()}?",
        f"Quando vale investir em {topic.lower()}?",
    ]


def suggest_strategic_from_gsc(gsc_data: dict, top: int = 20, ai_error: str | None = None) -> list[dict]:
    query_rows = _compact_queries(gsc_data, limit=220)
    ideas = []
    for group in _cluster_queries_for_blog(query_rows):
        queries = sorted(group["queries"], key=lambda row: int(row.get("impressions", 0) or 0), reverse=True)
        primary_query = str(queries[0].get("query", "")).strip()
        topic = _topic_label(group["terms"])
        tags = set(group["tags"])
        avg_position = round(sum(group["positions"]) / len(group["positions"]), 1) if group["positions"] else 0
        title = _fallback_title(topic, primary_query, tags)
        slug = _slug(title)
        score = min(100, 28 + group["impressions"] / 300 + max(0, 12 - avg_position) + len(queries) * 1.5)
        idea = {
            "url_slug": slug,
            "url": f"/{slug}",
            "meta_title": title[:60],
            "meta_description": (
                f"Guia para responder as principais duvidas sobre {topic.lower()} e ajudar o usuario a decidir melhor antes da compra."
            )[:160],
            "h1": title,
            "primary_query": primary_query,
            "queries": [str(row.get("query", "")).strip() for row in queries[:10] if row.get("query")],
            "sections": _fallback_sections(topic, tags),
            "faq": _fallback_faq(topic, queries),
            "internal_links": [],
            "entities": group["terms"][:10],
            "recommended_products_or_categories": group["terms"][:6],
            "status": "idea",
            "provider": "query_suggester+fallback",
            "ai_enhanced": False,
            "search_intent": ", ".join(sorted(tags)),
            "audience_question": primary_query if "duvida" in tags else "",
            "angle": "pauta criada por cluster de consultas do GSC",
            "content_type": "guia",
            "seasonality": "sazonal" if "sazonal" in tags else "evergreen",
            "recommended_publish_month": "evergreen",
            "opportunity_reason": "Cluster de consultas reais do GSC com potencial editorial.",
            "content_gap": "Validar se ja existe conteudo equivalente antes de publicar.",
            "opportunity_score": round(max(0, score), 1),
            "impressions": group["impressions"],
            "clicks": group["clicks"],
            "avg_position": avg_position,
            "generated_at": datetime.now().isoformat(),
        }
        if ai_error:
            idea["_ai_error"] = ai_error
        ideas.append(idea)
        if len(ideas) >= top * 2:
            break
    return _dedupe_ideas(ideas, top)


def generate_strategic_ideas_with_ai(
    gsc_data: dict,
    top: int = 20,
    provider: str | None = None,
    api_key: str | None = None,
) -> tuple[list[dict], str | None]:
    from modules.ai_layer import call_json

    query_rows = _compact_queries(gsc_data, limit=180)
    if not query_rows:
        return [], "Nenhuma query disponivel para a IA analisar."
    payload = {
        "requested_ideas": top,
        "queries": query_rows,
        "seasonality": _seasonality_context(),
        "existing_content": _load_existing_content(limit=80),
        "configured_brands": sorted(_brand_keywords())[:80],
        "configured_product_terms": sorted(get_product_terms())[:80],
        "instructions": (
            "Estude as consultas como um conjunto. Crie clusters e ideias editoriais que cubram duvidas, "
            "sazonalidade e lacunas, evitando repetir conteudos existentes."
        ),
    }
    ai_data = call_json(
        prompt=json.dumps(payload, ensure_ascii=False, indent=2),
        system_prompt=_strategic_blog_ideas_system(),
        provider=provider,
        api_key=api_key,
        fallback={},
    )
    if not ai_data.get("_ai_enhanced"):
        return [], ai_data.get("_ai_error") or "IA nao retornou uma resposta valida."
    ideas = _normalize_ai_ideas(ai_data, query_rows, top)
    if not ideas:
        return [], "IA respondeu, mas nao retornou ideias validas no JSON."
    return ideas, None


def enhance_idea_with_ai(
    idea: dict,
    provider: str | None = None,
    api_key: str | None = None,
    previous_titles: list[str] | None = None,
    used_angles: list[str] | None = None,
) -> dict:
    """Use AI to turn a heuristic idea into a richer editorial brief."""
    from modules.ai_layer import call_json

    prompt = json.dumps({
        "base_idea": idea,
        "avoid_titles": previous_titles or [],
        "avoid_angles": used_angles or [],
        "expected_output": "Enriqueca a pauta sem perder a intencao original. Use PT-BR.",
    }, ensure_ascii=False, indent=2)
    ai_data = call_json(
        prompt=prompt,
        system_prompt=_blog_idea_system(),
        provider=provider,
        api_key=api_key,
        fallback={},
    )

    merged = dict(idea)
    if not ai_data.get("_ai_enhanced"):
        merged["_ai_error"] = ai_data.get("_ai_error")
        merged["_ai_provider"] = ai_data.get("_ai_provider")
        return merged

    allowed_fields = [
        "h1",
        "meta_title",
        "meta_description",
        "search_intent",
        "angle",
        "content_type",
        "sections",
        "faq",
        "entities",
        "internal_links",
        "recommended_products_or_categories",
        "brief_notes",
    ]
    for field in allowed_fields:
        value = ai_data.get(field)
        if value:
            merged[field] = value

    merged["ai_enhanced"] = True
    merged["provider"] = f"query_suggester+{ai_data.get('_ai_provider', 'ai')}"
    merged["_ai_provider"] = ai_data.get("_ai_provider")
    merged["_ai_generated_at"] = ai_data.get("_ai_generated_at")
    if ai_data.get("_ai_fallback_errors"):
        merged["_ai_fallback_errors"] = ai_data.get("_ai_fallback_errors")
    return merged


def _merge_ai_fields(idea: dict, ai_data: dict) -> dict:
    merged = dict(idea)
    allowed_fields = [
        "h1",
        "meta_title",
        "meta_description",
        "search_intent",
        "angle",
        "content_type",
        "sections",
        "faq",
        "entities",
        "internal_links",
        "recommended_products_or_categories",
        "brief_notes",
    ]
    for field in allowed_fields:
        value = ai_data.get(field)
        if value:
            merged[field] = value
    return merged


def _idea_brand_product(idea: dict) -> tuple[str, str]:
    brand = idea.get("brand", "")
    product = idea.get("product", "")
    if brand and product:
        return brand, product
    tokens = _tokens(idea.get("primary_query", ""))
    return brand or _detect_brand(tokens), product or _detect_product(tokens)


def _is_generic_title(title: str, previous_titles: list[str]) -> bool:
    normalized = str(title or "").strip().lower()
    if not normalized:
        return True
    if normalized in {str(t).strip().lower() for t in previous_titles}:
        return True
    return any(pattern in normalized for pattern in GENERIC_TITLE_PATTERNS)


def _curated_title(idea: dict, index: int) -> str:
    brand, product = _idea_brand_product(idea)
    brand_title = brand.replace("-", " ").title() if brand else ""
    product_title = DISPLAY_PRODUCT.get(product, product).title() if product else "Produto"
    template = TITLE_ANGLES[index % len(TITLE_ANGLES)]
    return template.format(product_title=product_title, brand_title=brand_title).strip()


def _polish_title(idea: dict, candidate: dict, index: int, previous_titles: list[str]) -> dict:
    title = candidate.get("h1") or idea.get("h1")
    if _is_generic_title(title, previous_titles):
        title = _curated_title(idea, index)
        notes = candidate.setdefault("brief_notes", [])
        if isinstance(notes, list):
            notes.append("H1 curado automaticamente porque a IA retornou titulo generico.")
    candidate["h1"] = title
    if not candidate.get("meta_title") or _is_generic_title(candidate.get("meta_title"), previous_titles):
        candidate["meta_title"] = title[:60]
    return candidate


def enhance_ideas_with_ai(
    ideas: list[dict],
    provider: str | None = None,
    api_key: str | None = None,
) -> list[dict]:
    """Enhance ideas one by one with context to avoid repeated AI patterns."""
    if not ideas:
        return []

    enhanced = []
    previous_titles = []
    used_angles = []
    for idx, idea in enumerate(ideas):
        item = enhance_idea_with_ai(
            idea,
            provider=provider,
            api_key=api_key,
            previous_titles=previous_titles[-8:],
            used_angles=used_angles[-8:],
        )
        item = _polish_title(idea, item, idx, previous_titles)
        enhanced.append(item)
        previous_titles.append(str(item.get("h1") or idea.get("h1") or ""))
        if item.get("angle"):
            used_angles.append(str(item.get("angle")))
        if idx < len(ideas) - 1:
            time.sleep(1.8)
    return enhanced


def _enhance_ideas_chunk(
    ideas: list[dict],
    provider: str | None = None,
    api_key: str | None = None,
) -> list[dict]:
    """Enhance a small group of ideas in one AI call."""

    from modules.ai_layer import call_json

    compact_ideas = []
    for idx, idea in enumerate(ideas, start=1):
        compact_ideas.append({
            "position": idx,
            "url_slug": idea.get("url_slug"),
            "base_h1": idea.get("h1"),
            "primary_query": idea.get("primary_query"),
            "queries": idea.get("queries", [])[:8],
            "impressions": idea.get("impressions"),
            "clicks": idea.get("clicks"),
            "opportunity_score": idea.get("opportunity_score"),
            "current_sections": idea.get("sections", [])[:5],
        })

    angles_used = [idea.get("intent", "best") for idea in ideas]
    prompt = json.dumps({
        "ideas": compact_ideas,
        "intents_per_idea": angles_used,
        "task": (
            "Reescreva e enriqueca cada pauta com angulo unico. "
            "H1, meta_description, sections e angle devem ser DIFERENTES entre todas as pautas do lote. "
            "Proibido repetir a mesma estrutura de sections ou a mesma abertura de meta_description."
        ),
    }, ensure_ascii=False, indent=2)

    ai_data = call_json(
        prompt=prompt,
        system_prompt=_blog_ideas_batch_system(),
        provider=provider,
        api_key=api_key,
        fallback={},
    )

    if not ai_data.get("_ai_enhanced"):
        return [
            {
                **idea,
                "_ai_error": ai_data.get("_ai_error"),
                "_ai_provider": ai_data.get("_ai_provider"),
            }
            for idea in ideas
        ]

    ai_by_slug = {
        item.get("url_slug"): item
        for item in ai_data.get("ideas", [])
        if isinstance(item, dict)
    }

    enhanced = []
    used_h1 = set()
    for idea in ideas:
        item = ai_by_slug.get(idea.get("url_slug"), {})
        merged = _merge_ai_fields(idea, item)
        h1_key = str(merged.get("h1", "")).strip().lower()
        if h1_key and h1_key in used_h1:
            merged["h1"] = idea.get("h1")
            merged.setdefault("brief_notes", []).append("Titulo IA repetido; mantido titulo base.")
        used_h1.add(str(merged.get("h1", "")).strip().lower())
        merged["ai_enhanced"] = True
        merged["provider"] = f"query_suggester+{ai_data.get('_ai_provider', 'ai')}"
        merged["_ai_provider"] = ai_data.get("_ai_provider")
        merged["_ai_generated_at"] = ai_data.get("_ai_generated_at")
        enhanced.append(merged)

    return enhanced


def save_ideas(ideas: list[dict]) -> None:
    try:
        IDEAS_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if IDEAS_FILE.exists():
            try:
                existing = json.loads(IDEAS_FILE.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        by_slug = {item.get("url_slug"): idx for idx, item in enumerate(existing)}
        for idea in ideas:
            idx = by_slug.get(idea.get("url_slug"))
            if idx is None:
                existing.append(idea)
            else:
                existing[idx] = idea
        IDEAS_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        # Cache local/legado. Em producao multiusuario, a fonte persistente e o Supabase.
        return


def run(
    gsc_data: dict,
    top: int = 20,
    use_ai: bool = False,
    provider: str | None = None,
    api_key: str | None = None,
) -> list[dict]:
    ai_error = None
    if use_ai:
        ideas, ai_error = generate_strategic_ideas_with_ai(
            gsc_data or {},
            top=top,
            provider=provider,
            api_key=api_key,
        )
    else:
        ideas = []
    strategic_ai_used = bool(ideas)
    if not ideas:
        ideas = _dedupe_ideas([
            *suggest_from_gsc(gsc_data),
            *suggest_strategic_from_gsc(gsc_data, top=top, ai_error=ai_error),
        ], top)
    if use_ai and ideas and not strategic_ai_used:
        # Single batch call — much faster than one call per idea
        ideas = _enhance_ideas_chunk(ideas, provider=provider, api_key=api_key)
    save_ideas(ideas)
    return ideas
