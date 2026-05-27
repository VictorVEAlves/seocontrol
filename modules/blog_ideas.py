import json
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from config import BASE_DIR
from modules.blog_suggester import BRAND_KEYWORDS, PRODUCT_KEYWORDS

IDEAS_FILE = BASE_DIR / "blog_ideas.json"
AI_BATCH_SIZE = 3

AI_BLOG_IDEA_SYSTEM = """Voce e um estrategista senior de SEO e conteudo para e-commerce de moda masculina premium no Brasil.

Contexto:
- Site: Secret Outlet, revendedor autorizado de marcas premium.
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
{
  "h1": "...",
  "meta_title": "...",
  "meta_description": "...",
  "search_intent": "...",
  "angle": "...",
  "content_type": "...",
  "sections": ["...", "..."],
  "faq": ["...", "..."],
  "entities": ["...", "..."],
  "internal_links": [{"anchor": "...", "url": "...", "reason": "..."}],
  "recommended_products_or_categories": ["...", "..."],
  "brief_notes": ["...", "..."]
}"""

AI_BLOG_IDEAS_BATCH_SYSTEM = """Voce e um estrategista senior de SEO e editor de conteudo para e-commerce de moda masculina premium no Brasil.

Contexto:
- Site: Secret Outlet, revendedor autorizado de marcas premium.
- Objetivo: transformar queries do Google Search Console em pautas de blog COMPLETAMENTE DIFERENTES entre si, com potencial comercial real.

Regras editoriais obrigatorias:
- DIVERSIDADE E OBRIGATORIA: cada pauta deve ter H1, meta_description, angle, sections e content_type diferentes das outras. Nao repita a mesma formula em nenhum campo.
- Angulos disponiveis (use um diferente por pauta): autenticidade, tamanho/caimento, comparativo de modelos, ocasiao de uso (trabalho/casual/esporte), custo-beneficio, materiais e durabilidade, presente masculino, estilo minimalista, produto original vs falsificado, como montar looks, tendencias da marca, historia da marca.
- H1: responde a query principal diretamente. Sem "Guia de escolha e", "Descubra os", "Encontre o" no inicio.
- meta_description: cada uma deve ter um foco diferente — uma fala de tamanho, outra de preco, outra de autenticidade, outra de looks, etc. Nunca use "Dicas de material, caimento e como montar looks versateis" — essa frase e proibida.
- sections: cada pauta deve ter uma estrutura de topicos diferente. Nao use "Melhores modelos" e "Como combinar no dia a dia" em todas as pautas.
- Preserve marca, produto, queries, impressoes e URL da pauta base.
- Nao invente dados de volume, preco, estoque ou promocoes.
- Use PT-BR natural e orientado a compra, mas sem exagero publicitario.
- Seja conciso: no maximo 5 sections, 3 faq, 8 entities e 3 internal_links por pauta.
- Retorne APENAS JSON valido, sem markdown e sem texto fora do JSON.

Formato:
{
  "ideas": [
    {
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
      "internal_links": [{"anchor": "...", "url": "...", "reason": "..."}],
      "recommended_products_or_categories": ["...", "..."],
      "brief_notes": ["...", "..."]
    }
  ]
}"""

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
    "{product_title} {brand_title} masculino: modelos para usar no dia a dia",
    "{product_title} {brand_title}: o que comparar antes de escolher",
    "{product_title} {brand_title}: tamanho, caimento e conforto",
    "{product_title} {brand_title} com desconto: sinais de uma boa compra",
    "{product_title} {brand_title}: combinacoes para um visual casual premium",
    "{product_title} {brand_title}: materiais, acabamento e durabilidade",
    "{product_title} {brand_title}: quando vale investir na marca",
    "{product_title} {brand_title}: como montar looks sem erro",
    "{product_title} {brand_title}: perguntas antes de comprar online",
]


def _tokens(query: str) -> list[str]:
    return re.sub(r"[^a-zA-ZÀ-ÿ0-9\s-]", " ", query.lower()).replace("-", " ").split()


def _detect_brand(tokens: list[str]) -> str:
    joined = " ".join(tokens)
    for brand in sorted(BRAND_KEYWORDS, key=len, reverse=True):
        if brand in joined:
            return brand.replace(" ", "-")
    return ""


def _detect_product(tokens: list[str]) -> str:
    for token in tokens:
        normalized = PLURAL_NORMALIZATION.get(token)
        if normalized:
            return normalized
    for token in tokens:
        if token in PRODUCT_KEYWORDS:
            return PLURAL_NORMALIZATION.get(token, token)
    return ""


def _has_blog_intent(tokens: list[str]) -> bool:
    return bool(set(tokens) & MODIFIERS)


def _title_for(product: str, brand: str, intent: str) -> str:
    brand_label = brand.replace("-", " ").title()
    product_label = DISPLAY_PRODUCT.get(product, product).lower()
    if intent == "guide":
        return f"Como escolher {product_label} {brand_label}"
    if intent == "comparison":
        return f"{brand_label}: guia de {product_label} masculinos"
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
        f"Diferenca entre modelos basicos e premium",
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
        f"Os modelos de {p} {b} mais buscados em 2025",
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
        f"{b} tem {p} masculino original disponivel?",
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
        system_prompt=AI_BLOG_IDEA_SYSTEM,
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
        system_prompt=AI_BLOG_IDEAS_BATCH_SYSTEM,
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


def run(
    gsc_data: dict,
    top: int = 20,
    use_ai: bool = False,
    provider: str | None = None,
    api_key: str | None = None,
) -> list[dict]:
    ideas = suggest_from_gsc(gsc_data)[:top]
    if use_ai and ideas:
        # Single batch call — much faster than one call per idea
        ideas = _enhance_ideas_chunk(ideas, provider=provider, api_key=api_key)
    save_ideas(ideas)
    return ideas
