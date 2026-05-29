"""
GEO Analyzer — Generative Engine Optimization

Analisa como o site configurado aparece nas IAs de busca.

Componentes:
  1. GEO Readiness Score  — analisa páginas do site para otimização em LLMs
  2. Perplexity Probe      — testa queries reais na API do Perplexity (requer chave)
  3. Citation Tracker      — registra quem está sendo citado vs o site
  4. Recomendações         — o que fazer para melhorar

Uso:
  python run.py --module geo                        (só readiness score)
  python run.py --module geo --api-key pplx-...    (readiness + perplexity)

Chave Perplexity (plano básico ~$5/mês):
  console.perplexity.ai → API → Generate Key
"""

import json
import time
import re
from datetime import datetime
from pathlib import Path

import requests

from config import get_business_context, get_priority_pages, get_scoped_runtime_file, get_site_name, get_site_url
from modules.crawler import get_page

GEO_RESULTS_FILE = get_scoped_runtime_file("geo_results.json", "geo")

# ── Queries de teste ──────────────────────────────────────────────────────────
# Representam como usuários reais buscam nas IAs

def _default_queries() -> list[str]:
    site = get_site_name()
    context = get_business_context()
    return [
        f"{site} e confiavel?",
        f"{site} vale a pena?",
        f"Melhores opcoes para {context}",
        f"Onde encontrar {context} no Brasil?",
    ]

# ── GEO Readiness — fatores que LLMs valorizam ───────────────────────────────

GEO_FACTORS = {
    "faq_schema":        {"weight": 20, "label": "FAQPage schema (JSON-LD)"},
    "organization":      {"weight": 15, "label": "Organization schema com nome, logo, contato"},
    "breadcrumb":        {"weight": 8,  "label": "BreadcrumbList schema"},
    "h1_clear":          {"weight": 10, "label": "H1 descritivo e específico"},
    "meta_desc_answer":  {"weight": 10, "label": "Meta description responde uma pergunta"},
    "has_faq_content":   {"weight": 12, "label": "Conteúdo de FAQ na página"},
    "word_count":        {"weight": 8,  "label": "Conteúdo suficiente (>300 palavras)"},
    "trust_signals":     {"weight": 10, "label": "Sinais de confiança (original, autorizado, nota fiscal)"},
    "entity_clarity":    {"weight": 7,  "label": "Entidade clara (nome da empresa, localização)"},
}

TRUST_WORDS = {
    "original", "autorizado", "oficial", "nota fiscal", "garantia",
    "revendedor", "autêntico", "confiável", "certificado",
}


def score_page_geo(url: str) -> dict:
    """Score a single page for GEO readiness (0-100)."""
    status, soup, headers, final_url = get_page(url)
    if not soup:
        return {"url": url, "score": 0, "factors": {}, "error": f"status {status}"}

    factors = {}
    details = {}

    # --- FAQ Schema ---
    ld_jsons = []
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            ld_jsons.append(json.loads(s.string or "{}"))
        except Exception:
            pass

    schema_types = [d.get("@type", "") for d in ld_jsons]
    factors["faq_schema"]    = "FAQPage" in schema_types
    factors["organization"]  = "Organization" in schema_types
    factors["breadcrumb"]    = "BreadcrumbList" in schema_types

    # --- H1 clarity ---
    h1 = soup.find("h1")
    h1_text = h1.get_text(strip=True) if h1 else ""
    factors["h1_clear"] = len(h1_text) >= 20 and len(h1_text) <= 80

    # --- Meta description answers a question ---
    meta = soup.find("meta", attrs={"name": "description"})
    desc = meta.get("content", "").strip() if meta else ""
    factors["meta_desc_answer"] = len(desc) >= 100

    # --- FAQ content in page ---
    faq_content = bool(
        soup.find(class_=lambda c: c and "faq" in str(c).lower()) or
        soup.find_all("details") or
        (soup.find("dl") and len(soup.find_all("dt")) > 2)
    )
    factors["has_faq_content"] = faq_content

    # --- Word count ---
    body = soup.find("body")
    words = len(body.get_text(separator=" ", strip=True).split()) if body else 0
    factors["word_count"] = words >= 300

    # --- Trust signals ---
    page_text = (soup.get_text(separator=" ", strip=True)).lower()
    trust_found = [w for w in TRUST_WORDS if w in page_text]
    factors["trust_signals"] = len(trust_found) >= 2
    details["trust_words_found"] = trust_found

    # --- Entity clarity ---
    has_address = bool(
        any(d.get("@type") == "Organization" and d.get("address") for d in ld_jsons) or
        "curitiba" in page_text
    )
    factors["entity_clarity"] = has_address

    # --- Score ---
    score = sum(
        GEO_FACTORS[f]["weight"] for f, v in factors.items() if v
    )

    return {
        "url":        url,
        "score":      score,
        "factors":    factors,
        "details":    details,
        "h1":         h1_text[:80],
        "desc_len":   len(desc),
        "word_count": words,
    }


def run_readiness(urls: list) -> list:
    """Score multiple pages and print results."""
    results = []
    for url in urls:
        full = url if url.startswith("http") else get_site_url() + url
        r = score_page_geo(full)
        score = r["score"]
        grade = "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D"
        results.append({**r, "grade": grade})

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def print_readiness(results: list):
    print(f"\n  {'#':>2}  {'URL':<45} {'Score':>6}  {'Grade'}")
    print("  " + "-" * 65)
    for i, r in enumerate(results, 1):
        path = r["url"].replace(get_site_url(), "") or "/"
        print(f"  {i:>2}  {path[:45]:<45} {r['score']:>5}/100  {r['grade']}")

    # Mostrar fatores faltando para o pior
    worst = [r for r in results if r["score"] < 60]
    if worst:
        print(f"\n  Fatores ausentes nas paginas com score < 60:")
        for r in worst[:5]:
            missing = [
                GEO_FACTORS[f]["label"]
                for f, v in r["factors"].items() if not v
            ]
            path = r["url"].replace(get_site_url(), "") or "/"
            print(f"\n  {path}")
            for m in missing:
                print(f"    x {m}")


# ── Perplexity Probe ──────────────────────────────────────────────────────────

PPLX_URL = "https://api.perplexity.ai/chat/completions"


def _query_perplexity(query: str, api_key: str) -> dict:
    """Send a query to Perplexity and return response + citations."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "sonar",
        "messages": [{"role": "user", "content": query}],
        "max_tokens": 800,
        "return_citations": True,
        "return_related_questions": False,
        "search_recency_filter": "month",
    }
    try:
        r = requests.post(PPLX_URL, json=body, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        content   = data["choices"][0]["message"]["content"]
        citations = data.get("citations", [])
        return {"content": content, "citations": citations, "error": None}
    except Exception as e:
        return {"content": "", "citations": [], "error": str(e)}


def _analyze_response(query: str, response: dict) -> dict:
    """Analyze if the site is mentioned and who the competitors are."""
    content   = response.get("content", "").lower()
    citations = response.get("citations", [])

    site_domain = get_site_url().replace("https://", "").replace("http://", "").strip("/")
    site_cited  = any(site_domain in c for c in citations)
    site_mentioned = site_domain in content or get_site_name().lower() in content

    competitor_domains = []
    for c in citations:
        domain = re.sub(r"https?://(www\.)?", "", c).split("/")[0]
        if domain and domain != site_domain:
            competitor_domains.append(domain)

    return {
        "query":              query,
        "site_cited":         site_cited,
        "site_mentioned":     site_mentioned,
        "citations":          citations,
        "competitors_cited":  competitor_domains,
        "response_snippet":   response.get("content", "")[:300],
        "error":              response.get("error"),
        "tested_at":          datetime.now().isoformat(),
    }


def run_perplexity_probe(api_key: str, queries: list = None) -> list:
    """Test all queries against Perplexity and record results."""
    if queries is None:
        queries = _default_queries()

    results = []
    print(f"\n  Testando {len(queries)} queries no Perplexity...")
    print(f"  {'Query':<55} {'Citado':>7} {'Mencionado':>11}")
    print("  " + "-" * 75)

    for q in queries:
        resp   = _query_perplexity(q, api_key)
        result = _analyze_response(q, resp)
        results.append(result)

        cited = "SIM" if result["site_cited"]     else "nao"
        ment  = "SIM" if result["site_mentioned"] else "nao"
        err   = f" [ERRO: {result['error'][:30]}]" if result["error"] else ""
        print(f"  {q[:55]:<55} {cited:>7} {ment:>11}{err}")

        time.sleep(1.5)  # rate limit

    return results


def print_probe_summary(results: list):
    cited    = sum(1 for r in results if r["site_cited"])
    mentioned = sum(1 for r in results if r["site_mentioned"])
    total    = len(results)

    print(f"\n  Visibilidade no Perplexity:")
    print(f"  Citado como fonte  : {cited}/{total} queries ({cited/total*100:.0f}%)")
    print(f"  Mencionado no texto: {mentioned}/{total} queries ({mentioned/total*100:.0f}%)")

    # Competitors
    all_competitors = []
    for r in results:
        all_competitors.extend(r.get("competitors_cited", []))

    from collections import Counter
    top_comp = Counter(all_competitors).most_common(8)
    if top_comp:
        print(f"\n  Concorrentes mais citados:")
        for domain, count in top_comp:
            print(f"    {count:>3}x  {domain}")

    # Queries where site was NOT cited
    not_cited = [r for r in results if not r["site_cited"]]
    if not_cited:
        print(f"\n  Queries onde o site NAO apareceu ({len(not_cited)}):")
        for r in not_cited:
            print(f"    - {r['query'][:70]}")


# ── Save / Load ───────────────────────────────────────────────────────────────

def save_results(readiness: list, probe: list):
    existing = {}
    if GEO_RESULTS_FILE.exists():
        try:
            existing = json.loads(GEO_RESULTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    today = datetime.now().strftime("%Y-%m-%d")
    existing[today] = {
        "readiness": readiness,
        "probe":     probe,
        "summary": {
            "avg_readiness_score": round(
                sum(r["score"] for r in readiness) / len(readiness), 1
            ) if readiness else 0,
            "pplx_cited_rate": round(
                sum(1 for r in probe if r["site_cited"]) / len(probe) * 100, 1
            ) if probe else None,
        }
    }

    GEO_RESULTS_FILE.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n  Resultados salvos em: {GEO_RESULTS_FILE}")


def print_geo_recommendations(readiness: list, probe: list):
    print("\n" + "=" * 60)
    print("  RECOMENDACOES GEO")
    print("=" * 60)

    # Check schema injection
    pages_without_faq = [
        r["url"].replace(get_site_url(), "")
        for r in readiness
        if not r["factors"].get("faq_schema")
    ]
    if pages_without_faq:
        print(f"\n  1. Adicionar schema FAQPage/BreadcrumbList nas paginas")
        print(f"     {len(pages_without_faq)} paginas sem FAQPage/BreadcrumbList schema")

    # Low trust signals
    low_trust = [
        r["url"].replace(get_site_url(), "")
        for r in readiness
        if not r["factors"].get("trust_signals") and r["score"] < 60
    ]
    if low_trust:
        print(f"\n  2. Adicionar sinais de confianca nas paginas:")
        for p in low_trust[:4]:
            print(f"     - {p}")
        print(f"     Use sinais de confianca reais do cliente: garantia, certificacoes, reviews, endereco ou politicas.")

    # Low word count
    thin = [
        r for r in readiness if not r["factors"].get("word_count")
    ]
    if thin:
        print(f"\n  3. Conteudo escasso em {len(thin)} paginas (< 300 palavras)")
        print(f"     LLMs preferem paginas com respostas completas")
        print(f"     Use: py run.py --module generate para gerar descricoes HTML")

    # Probe recommendations
    if probe:
        not_cited = [r for r in probe if not r["site_cited"]]
        if not_cited:
            print(f"\n  4. Criar conteudo especifico para queries nao respondidas:")
            for r in not_cited[:4]:
                print(f"     - '{r['query'][:60]}'")

    print(f"\n  5. Acoes de autoridade para IAs:")
    print(f"     - Cadastrar no Google Business Profile (cita local businesses)")
    print(f"     - Publicar press releases em portais (UOL, Metrópoles, TudoCelular)")
    print(f"     - Conseguir mencoes em sites relevantes do segmento")
    print(f"     - Responder reviews e manter perfis publicos atualizados")
    print(f"     - Manter paginas institucionais e politicas claras")
    print("=" * 60)


# ── Main entry point ──────────────────────────────────────────────────────────

def run(api_key: str = None, queries: list = None) -> dict:
    GEO_PAGES = get_priority_pages() or ["/"]
    print(f"  Analisando GEO Readiness de {len(GEO_PAGES)} paginas...")
    readiness = run_readiness(GEO_PAGES)
    print_readiness(readiness)

    probe = []
    if api_key:
        probe = run_perplexity_probe(api_key, queries or _default_queries())
        print_probe_summary(probe)
    else:
        print("\n  Perplexity nao configurado — rodando apenas readiness score.")
        print("  Para testar queries reais: --api-key pplx-...")
        print("  Chave em: console.perplexity.ai (plano basico ~$5/mes)")

    print_geo_recommendations(readiness, probe)

    if readiness or probe:
        save_results(readiness, probe)

    return {"readiness": readiness, "probe": probe}
