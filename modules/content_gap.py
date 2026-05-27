from collections import defaultdict
from urllib.parse import urlparse


QUESTION_PREFIXES = ("como", "qual", "quais", "onde", "quando", "por que", "porque", "o que")
COMMERCIAL_TERMS = ("comprar", "preco", "preço", "outlet", "promoção", "promocao", "desconto", "original")


def _intent(query: str) -> str:
    q = query.lower().strip()
    if q.startswith(QUESTION_PREFIXES):
        return "informational"
    if any(term in q for term in COMMERCIAL_TERMS):
        return "transactional"
    return "commercial"


def _entity(query: str) -> str:
    words = [w for w in query.lower().replace("-", " ").split() if len(w) > 3]
    return words[0] if words else query[:30].lower()


def run(gsc_data: dict) -> dict:
    clusters = defaultdict(lambda: {"queries": [], "impressions": 0, "clicks": 0, "intents": defaultdict(int)})
    for row in (
        gsc_data.get("top_queries", [])
        + gsc_data.get("content_opps", [])
        + gsc_data.get("quick_wins", [])
        + gsc_data.get("pos_opps", [])
    ):
        query = str(row.get("query", ""))
        entity = _entity(query)
        intent = _intent(query)
        clusters[entity]["queries"].append(query)
        clusters[entity]["impressions"] += int(row.get("impressions", 0) or 0)
        clusters[entity]["clicks"] += int(row.get("clicks", 0) or 0)
        clusters[entity]["intents"][intent] += 1

    gaps = []
    for entity, data in clusters.items():
        if data["impressions"] < 200:
            continue
        dominant_intent = max(data["intents"].items(), key=lambda item: item[1])[0]
        action = "Criar/otimizar guia ou FAQ" if dominant_intent == "informational" else "Reforcar categoria/produtos e snippet comercial"
        gaps.append({
            "entity": entity,
            "intent": dominant_intent,
            "queries": sorted(set(data["queries"]))[:8],
            "impressions": data["impressions"],
            "clicks": data["clicks"],
            "suggested_action": action,
        })

    cannibalization = []
    for row in gsc_data.get("cannibalization", []):
        cannibalization.append({
            "root": row.get("root"),
            "page": urlparse(str(row.get("page", ""))).path,
            "impressions": row.get("impressions", 0),
            "position": row.get("position", 0),
        })

    return {
        "gaps": sorted(gaps, key=lambda item: item["impressions"], reverse=True),
        "cannibalization": cannibalization,
    }
