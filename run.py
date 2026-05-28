"""
SEO Audit Tool

Auditoria:
  python run.py --all --urls /categoria /produto
  python run.py --module gsc
  python run.py --module onpage --urls /categoria
  python run.py --module broken-links --urls /categoria
  python run.py --module pagespeed --urls /categoria

Geração de conteúdo:
  python run.py --module generate --urls /categoria
  python run.py --module generate --urls /categoria --provider gemini --api-key ...

Publicação na Bagy (Playwright):
  python run.py --module publish --bagy-email seu@email.com --bagy-password suasenha
  python run.py --module publish --urls /categoria --bagy-email ... --bagy-password ...
  python run.py --module publish --dry-run   (mostra o que faria sem publicar)

Quando --urls é passado, TODOS os módulos focam apenas nessas páginas.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# Fix Windows terminal encoding
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from config import (BASE_DIR, GSC_EXPORT_FOLDER, REPORTS_FOLDER,
                    BAGY_EMAIL, BAGY_PASSWORD, get_default_provider,
                    get_provider_api_key, get_site_url, get_site_name,
                    get_priority_pages, get_brand_clusters)


def parse_args():
    parser = argparse.ArgumentParser(description="SEO Audit")
    parser.add_argument("--all", action="store_true", help="Rodar todos os módulos")
    parser.add_argument("--module", choices=["gsc", "gsc-api", "onpage", "duplicates",
                                              "broken-links", "clusters",
                                              "sitemap", "indexability", "snippets",
                                              "content-gap", "products", "link-suggestions",
                                              "regression", "monitor", "content-sync", "blog-ideas",
                                              "ai-analysis", "ai-insights", "doctor",
                                              "generate", "publish", "suggest", "review",
                                              "geo", "backlog", "change-memory",
                                              "keyword-tracker", "schema-check", "cannibalization"],
                        help="Módulo específico")
    parser.add_argument("--gsc", default=GSC_EXPORT_FOLDER,
                        help="Pasta com os CSVs do Google Search Console")
    parser.add_argument("--urls", nargs="+", default=None,
                        help="Páginas para focar (ex: /categoria /produto). "
                             "Quando passado, todos os módulos se limitam a essas páginas.")
    parser.add_argument("--max-pages", type=int, default=200,
                        help="Máximo de páginas para rastrear no broken-links (padrão: 200). "
                             "Ignorado quando --urls é passado.")
    parser.add_argument("--no-report", action="store_true",
                        help="Não gerar relatório HTML")
    parser.add_argument("--save-db", action="store_true",
                        help="Salvar resultados da auditoria no Supabase")
    parser.add_argument("--export-backlog", action="store_true",
                        help="Exportar backlog priorizado em CSV e HTML")
    parser.add_argument("--provider", default=None,
                        choices=["openrouter", "gemini", "groq", "mistral", "anthropic"],
                        help="Provedor de IA (padrão: detectado automaticamente pelo .env)")
    parser.add_argument("--api-key", default=None,
                        help="Chave da API do provedor escolhido")
    parser.add_argument("--ai", action="store_true",
                        help="Usar IA para enriquecer ideias e analises quando aplicavel")
    parser.add_argument("--bagy-email", default=None,
                        help="E-mail de login no painel Bagy")
    parser.add_argument("--bagy-password", default=None,
                        help="Senha do painel Bagy")
    parser.add_argument("--top", type=int, default=5,
                        help="Número de oportunidades para gerar briefs no --module suggest (padrão: 5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Mostra o que o publish faria sem publicar de verdade")
    parser.add_argument("--headless", action="store_true",
                        help="Roda o Playwright em modo headless (sem abrir janela)")
    parser.add_argument("--changes-log", default=None,
                        help="CSV de controle SEO para evitar tarefas ja implementadas")
    parser.add_argument("--comparison", default="week",
                        choices=["week", "month", "year"],
                        help="Modo de comparação para gsc-api: week (padrão), month (28 dias), year (YoY)")
    return parser.parse_args()


def print_banner():
    site = get_site_url() or "site não configurado"
    print(f"""
===========================================
  SEO Audit -- {get_site_name()}
  {site}
===========================================
""")


def configure_change_memory(path: str | None) -> None:
    if path:
        os.environ["SEO_CHANGELOG_CSV"] = path


def resolve_ai_credentials(provider_arg: str = None, api_key_arg: str = None) -> tuple[str, str]:
    """Resolve AI provider/key without mixing keys between providers."""
    if provider_arg:
        return provider_arg, api_key_arg or get_provider_api_key(provider_arg)
    provider, auto_key = get_default_provider()
    return provider, api_key_arg or auto_key


def _has_gsc_credentials() -> bool:
    from config import BASE_DIR
    return (BASE_DIR / "gsc_credentials.json").exists()


def _slugs_from_urls(urls: list) -> list:
    """Extract path slugs from URL list for keyword matching."""
    slugs = []
    for u in urls:
        path = urlparse(u if u.startswith("http") else _full_url(u)).path
        slug = path.strip("/").split("/")[0]
        if slug:
            slugs.append(slug)
    return slugs


def _filter_gsc_to_urls(gsc_result: dict, urls: list) -> dict:
    """Filter GSC results to only show data relevant to the specified URLs."""
    if not gsc_result or not urls:
        return gsc_result

    slugs = _slugs_from_urls(urls)

    # Build keywords from slugs (e.g. "categoria-produto" -> "categoria produto")
    keywords = set()
    for s in slugs:
        keywords.add(s.lower())
        keywords.update(s.lower().replace("-", " ").split())

    def query_matches(query: str) -> bool:
        q = str(query).lower()
        return any(kw in q for kw in keywords if len(kw) > 3)

    def page_matches(page: str) -> bool:
        p = str(page).lower()
        return any(slug in p for slug in slugs)

    result = dict(gsc_result)

    # Filter queries
    for key in ("top_queries", "ctr_opps", "content_opps", "pos_opps", "quick_wins"):
        if key in result:
            result[key] = [r for r in result[key] if query_matches(r.get("query", ""))]

    # Filter pages
    for key in ("top_pages", "low_ctr_pages", "cannibalization"):
        if key in result:
            result[key] = [r for r in result[key]
                           if page_matches(r.get("page", ""))]

    return result


def _brands_from_urls(urls: list) -> list:
    """Return brand cluster keys whose pillar is in the specified URLs."""
    slugs = set(_slugs_from_urls(urls))
    matched = []
    clusters = get_brand_clusters()
    for brand, cluster in clusters.items():
        pillar_slug = cluster["pillar"].strip("/")
        if pillar_slug in slugs:
            matched.append(brand)
    return matched or list(clusters.keys())


def _full_url(url: str) -> str:
    if url.startswith("http"):
        return url
    base = get_site_url()
    if not base:
        raise RuntimeError("Configure SITE_URL no .env ou a URL do site em /settings.")
    return base + url


# ── Module runners ────────────────────────────────────────────────────────────

def run_gsc(folder: str = None, urls: list = None) -> dict:
    focused = f" (filtrado para {len(urls)} URLs)" if urls else ""
    print(f"  Analisando GSC{focused}...")

    result = None

    # Try GSC API first (live data, no CSV needed)
    if _has_gsc_credentials():
        try:
            from modules import gsc_api as _gsc_api
            from modules.gsc_analyzer import run_from_api
            print("   via API GSC (ao vivo)...")
            raw = _gsc_api.fetch_raw(limit=500)
            if "error" not in raw:
                result = run_from_api(raw["queries"], raw["pages"], raw.get("time_series"))
                print(f"   ok {len(raw['queries'])} queries | {len(raw['pages'])} paginas carregadas via API")
            else:
                print(f"   ! API GSC: {raw['error']} — usando CSV como fallback")
        except Exception as exc:
            print(f"   ! API GSC falhou ({exc}) — usando CSV como fallback")

    # Fallback: CSV exports
    if result is None:
        _folder = folder or GSC_EXPORT_FOLDER
        from collectors import gsc
        result = gsc.run(_folder)
        if "error" in result:
            print(f"   x {result['error']}")
            return {}
        print("   via CSV exports")

    if urls:
        result = _filter_gsc_to_urls(result, urls)

    print(f"   ok {len(result.get('top_queries',[]))} queries relevantes | "
          f"{len(result.get('top_pages',[]))} paginas relevantes")
    print(f"   ok {len(result.get('low_ctr_pages',[]))} paginas com CTR abaixo do benchmark")
    print(f"   ok {len(result.get('content_opps',[]))} oportunidades de conteudo por query")
    print(f"   ok {len(result.get('quick_wins',[]))} quick wins")
    benchmarks = result.get("benchmarks", {})
    if benchmarks:
        print(
            f"   benchmark CTR: {benchmarks.get('avg_ctr', 0):.2f}% "
            f"(benchmark apenas de paginas {benchmarks.get('page_ctr_desired_range')}) | "
            f"posicao media: {benchmarks.get('avg_position', 0):.2f} "
            f"(meta < {benchmarks.get('avg_position_target', 6):.2f})"
        )
    reach = result.get("brand_reachability", [])[:5]
    if reach:
        top = ", ".join(
            f"{row['brand']}:{row['reachable_priority']}"
            for row in reach
        )
        print(f"   marcas mais atingiveis: {top}")
    return result


def run_onpage(urls: list) -> list:
    print(f"  Auditando on-page de {len(urls)} paginas...")
    from analyzers import onpage
    base = get_site_url()
    full_urls = [_full_url(u) for u in urls]
    results = onpage.audit_pages(full_urls, verbose=False)
    for r in results:
        grade   = r.get("grade", "?")
        issues  = len(r.get("issues", []))
        warns   = len(r.get("warnings", []))
        path    = r.get("url", "").replace(base, "") or "/"
        symbol  = "x" if grade in ("D", "F") else "!" if grade == "C" else "ok"
        print(f"   {symbol} [{grade}] {path[:55]:55s}  {issues} issues, {warns} avisos")
    return results


def run_duplicates(urls: list) -> dict:
    print(f"  Verificando conteudo duplicado em {len(urls)} paginas...")
    from analyzers import duplicates
    result = duplicates.run(urls)
    print(f"   ok {result.get('total_issues', 0)} issues encontrados")
    print(f"      {len(result.get('missing_titles',[]))} titles ausentes")
    print(f"      {len(result.get('duplicate_titles',[]))} titles duplicados")
    print(f"      {len(result.get('duplicate_descs',[]))} descriptions duplicadas")
    return result


def run_broken_links(urls: list = None, max_pages: int = 200) -> dict:
    from analyzers import broken_links

    if urls:
        # Targeted mode: fetch only specified pages + check their outgoing links
        print(f"  Verificando links em {len(urls)} paginas especificas...")
        full_urls = [_full_url(u) for u in urls]

        pages_data = {}
        incoming   = {}

        from collectors.crawler import get_page, extract_links, normalize_url
        import time

        for url in full_urls:
            status, soup, headers, final_url = get_page(url)
            links = extract_links(soup, url) if soup else []
            pages_data[url] = {"status": status, "final_url": final_url,
                               "redirect": url != final_url, "links": links}

            for lk in links:
                if lk["is_internal"]:
                    target = normalize_url(lk["url"])
                    incoming.setdefault(target, []).append(
                        {"source": url, "anchor": lk["anchor"], "nofollow": lk["nofollow"]})

        # Check status of all outgoing links (deduplicated)
        outgoing_targets = {normalize_url(lk["url"])
                            for data in pages_data.values()
                            for lk in data["links"]
                            if lk["is_internal"]}

        for target in outgoing_targets:
            if target not in pages_data:
                status, _, _, final_url = get_page(target)
                pages_data[target] = {"status": status, "final_url": final_url,
                                      "redirect": target != final_url, "links": []}

        crawl_data = {"pages": pages_data, "incoming_links": incoming}

    else:
        # Full crawl mode
        print(f"  Rastreando site (max {max_pages} paginas)...")
        crawl_data = broken_links.crawl(max_pages=max_pages)

    scope = urls or get_priority_pages()

    result = {
        "pages_crawled":     len(crawl_data["pages"]),
        "broken":            broken_links.find_broken(crawl_data),
        "redirect_chains":   broken_links.find_redirect_chains(crawl_data),
        "orphans":           broken_links.find_orphans(crawl_data, scope),
        "nofollow_internal": broken_links.find_nofollow_internal(crawl_data),
        "crawl_data":        crawl_data,
    }

    print(f"   ok {result['pages_crawled']} paginas verificadas")
    print(f"   x  {len(result['broken'])} links quebrados")
    print(f"   !  {len(result['redirect_chains'])} cadeias de redirect")
    print(f"   !  {len(result['orphans'])} paginas sem links internos suficientes")
    return result


def run_clusters(crawl_data=None, filter_brands: list = None) -> dict:
    print("  Analisando clusters e links internos...")
    from analyzers import internal_links

    result = internal_links.run(crawl_data=crawl_data)

    # Filter cluster analysis to only requested brands
    if filter_brands:
        result["cluster_analysis"] = [
            c for c in result.get("cluster_analysis", [])
            if c["brand"] in filter_brands
        ]

    analysis = result.get("cluster_analysis", [])
    critical = [c for c in analysis if c["health_score"] < 60]
    print(f"   ok {len(analysis)} clusters analisados")
    if critical:
        for c in critical:
            print(f"   x  {c['brand']}: {c['health_score']}/100")
    return result


def run_pagespeed(urls: list) -> list:
    print(f"  Auditando PageSpeed de {len(urls)} URLs...")
    from collectors import pagespeed
    results = pagespeed.run(urls)
    if not results:
        return results
    for r in results:
        if r.get("strategy") != "mobile":
            continue
        path  = r["url"].replace(get_site_url(), "") or "/"
        perf  = r.get("performance_score")
        score = f"{perf}/100" if perf is not None else "ERRO"
        sym   = "x" if (perf or 100) < 50 else "!" if (perf or 100) < 75 else "ok"
        print(f"   {sym} {path[:55]:55s}  Perf: {score}  CLS: {r.get('cls', '-')}")
    return results


def run_sitemap(crawl_data=None, priority_pages=None) -> dict:
    print("  Analisando sitemap.xml e robots.txt...")
    from modules import sitemap_robots
    result = sitemap_robots.run(crawl_data=crawl_data, priority_pages=priority_pages)
    print(f"   ok {result.get('sitemap_urls_count', 0)} URLs no sitemap")
    print(f"   !  {len(result.get('missing_priority', []))} paginas prioritarias fora do sitemap")
    print(f"   !  {len(result.get('blocked_priority', []))} paginas prioritarias bloqueadas")
    print(f"   !  {len(result.get('canonical_issues', []))} canonicals inconsistentes")
    return result


def run_indexability(urls: list) -> dict:
    print(f"  Auditando indexabilidade de {len(urls)} URLs...")
    from modules import indexability
    result = indexability.run(urls)
    print(f"   ok {result.get('indexable', 0)} indexaveis")
    print(f"   !  {result.get('non_indexable', 0)} com risco de indexacao")
    return result


def run_snippets(onpage_results: list) -> dict:
    print("  Analisando SERP snippets...")
    from modules import serp_snippets
    import json
    pending = []
    pending_file = Path("pending_changes.json")
    if pending_file.exists():
        try:
            pending = json.loads(pending_file.read_text(encoding="utf-8"))
        except Exception:
            pending = []
    result = serp_snippets.run(onpage_results, pending_changes=pending)
    print(f"   ok {result.get('total', 0)} snippets analisados")
    print(f"   !  {len(result.get('issues', []))} snippets fora do ideal")
    return result


def run_content_gap(gsc_data: dict) -> dict:
    print("  Analisando gaps de conteudo e intencao...")
    from modules import content_gap
    result = content_gap.run(gsc_data or {})
    print(f"   ok {len(result.get('gaps', []))} clusters de conteudo")
    print(f"   !  {len(result.get('cannibalization', []))} pistas de canibalizacao")
    return result


def run_products(urls: list) -> dict:
    print(f"  Auditando produtos/categorias em {len(urls)} URLs...")
    from modules import product_category
    result = product_category.run(urls)
    print(f"   ok {result.get('total', 0)} paginas analisadas")
    print(f"   !  {len(result.get('issues', []))} paginas com alertas")
    return result


def run_link_suggestions(internal_links: dict) -> dict:
    print("  Gerando sugestoes de links internos...")
    from modules import link_suggestions
    result = link_suggestions.run(internal_links or {})
    print(f"   ok {result.get('total', 0)} sugestoes")
    return result


def run_regression() -> dict:
    print("  Comparando regressao com runs anteriores...")
    from modules import regression
    result = regression.run()
    if result.get("status") != "ok":
        print("   !  dados insuficientes para comparar")
    else:
        print(f"   !  {len(result.get('regressions', []))} metricas pioraram")
    return result


def run_blog_ideas(
    gsc_data: dict,
    top: int = 20,
    use_ai: bool = False,
    provider: str = None,
    api_key: str = None,
) -> list:
    suffix = " com IA" if use_ai else ""
    print(f"  Gerando ideias de blog a partir das queries{suffix}...")
    from modules import blog_ideas
    ideas = blog_ideas.run(
        gsc_data or {},
        top=top,
        use_ai=use_ai,
        provider=provider,
        api_key=api_key,
    )
    if not ideas:
        print("   !  nenhuma ideia encontrada com marca + produto")
        return []
    for idx, idea in enumerate(ideas[:top], start=1):
        mode = "IA" if idea.get("ai_enhanced") else "base"
        print(f"   {idx:02d}. [{mode}] {idea['h1']} | {idea.get('impressions', 0):,} impressoes")
        print(f"       query: {idea.get('primary_query', '')}")
        if idea.get("_ai_error"):
            print(f"       IA indisponivel: {idea.get('_ai_error')}")
    print(f"   ok ideias salvas em blog_ideas.json")
    return ideas


def run_ai_analysis(results: dict, provider: str = None, api_key: str = None) -> dict:
    print("  Gerando analise executiva com IA...")
    from modules import ai_analysis
    analysis = ai_analysis.run(results, provider=provider, api_key=api_key)
    if not analysis.get("_ai_enhanced"):
        print(f"   !  IA indisponivel: {analysis.get('_ai_error', 'sem detalhes')}")
    print(f"   resumo: {analysis.get('summary', '')[:180]}")
    for action in analysis.get("next_actions", [])[:5]:
        print(f"   - {action}")
    print("   ok analise salva em reports/ai_analysis_latest.json")
    return analysis


def run_ai_insights(results: dict, provider: str = None, api_key: str = None) -> dict:
    print("  Analisando auditoria com Gemini (insights estratégicos)...")
    from modules import gemini_insights
    insights = gemini_insights.run(results, provider=provider, api_key=api_key)

    if not insights.get("_ai_enhanced"):
        print(f"   !  Gemini indisponivel: {insights.get('_ai_error', 'sem detalhes')}")
        return insights

    summary = insights.get("executive_summary", "")
    if summary:
        print(f"   resumo: {summary[:200]}")

    alerts = insights.get("critical_alerts", [])
    if alerts:
        print(f"   ok {len(alerts)} alertas criticos:")
        for a in alerts[:3]:
            urgency = a.get("urgency", "")
            print(f"      [{urgency.upper()}] {a.get('title', '')}")

    tasks = insights.get("tasks", [])
    print(f"   ok {len(tasks)} tarefas IA geradas")

    content_gaps = insights.get("content_gaps", [])
    snippets = insights.get("snippet_rewrites", [])
    print(f"   ok {len(content_gaps)} gaps de conteudo | {len(snippets)} rewrites de snippet sugeridos")
    print("   ok insights salvos em reports/ai_insights_latest.json")
    return insights


def save_report(all_results: dict, urls_label: str = "") -> str:
    """Save audit snapshot as JSON for internal dashboard display.
    HTML reports are no longer generated — data lives in the dashboard at /report.
    """
    import math

    def _clean(obj):
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean(v) for v in obj]
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        return obj

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    suffix    = "_" + "_".join(_slugs_from_urls(all_results.get("_urls", [])))[:40] if urls_label else ""
    latest    = os.path.join(REPORTS_FOLDER, "latest_report.json")
    archived  = os.path.join(REPORTS_FOLDER, f"report_{timestamp}{suffix}.json")

    payload = _clean({**all_results, "_generated_at": timestamp, "_label": urls_label or ""})
    os.makedirs(REPORTS_FOLDER, exist_ok=True)
    with open(latest, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    with open(archived, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    return latest


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print_banner()
    args = parse_args()
    configure_change_memory(args.changes_log)

    if not args.all and not args.module:
        print("Use --all para rodar tudo, ou --module <nome> para um modulo especifico.")
        print("Exemplos:")
        print("  python run.py --all --gsc ./gsc_exports")
        print("  python run.py --all --gsc ./gsc_exports --urls /categoria /produto")
        sys.exit(0)

    # Determine scope
    urls     = args.urls
    scope    = urls or get_priority_pages()
    run_all  = args.all
    mod      = args.module
    focused  = bool(urls)

    if focused:
        print(f"  Foco em {len(urls)} pagina(s): {', '.join(urls)}\n")

    results           = {"_urls": urls or []}
    crawl_data_cache  = None
    brands_in_scope   = _brands_from_urls(scope) if focused else None

    # GSC
    if run_all or mod == "gsc":
        results["gsc"] = run_gsc(args.gsc, urls=urls if focused else None)
        print()

    # GSC API — live trend detection
    if mod == "gsc-api" or (run_all and _has_gsc_credentials()):
        print("  Detectando quedas via GSC API...")
        from modules import gsc_api as _gsc_api
        results["gsc_api"] = _gsc_api.run(results, scope_urls=urls if focused else None,
                                           comparison=args.comparison)
        drops = results["gsc_api"].get("drops", [])
        crit  = sum(1 for d in drops if d.get("severity") == "critical")
        print(f"   ok {len(drops)} quedas detectadas ({crit} críticas)")

        # Auto-save tag suggestions to Kanban (no --save-db flag needed)
        tag_sug = results["gsc_api"].get("tag_suggestions", [])
        if tag_sug:
            try:
                from modules import supabase_store as _ss

                def _sug_to_item(s):
                    pri     = s.get("priority", "media")
                    impact  = 75 if pri == "alta" else 55
                    conf    = 80
                    effort  = 2
                    ice     = round(impact * conf / 100 / effort, 1)
                    parts   = []
                    if s.get("suggested_title"):
                        parts.append(f"Title: {s['suggested_title'][:60]}")
                    if s.get("suggested_h1"):
                        parts.append(f"H1: {s['suggested_h1'][:50]}")
                    if s.get("main_issue"):
                        parts.append(s["main_issue"])
                    return {
                        "source":     "gsc_tags",
                        "action":     "Otimizar tags SEO (title/H1/description)",
                        "target":     str(s.get("page") or ""),
                        "reason":     " | ".join(parts),
                        "impact":     impact,
                        "confidence": conf,
                        "effort":     effort,
                        "priority":   ice,
                        "owner":      "SEO",
                        "evidence":   s,
                    }

                saved = _ss.save_tag_suggestions([_sug_to_item(s) for s in tag_sug])
                print(f"   ok {saved} tarefas de otimização de tags salvas no Kanban")
            except Exception as e:
                print(f"   ! Falha ao salvar tarefas no Kanban: {e}")
        print()

    # On-page
    if run_all or mod == "onpage":
        results["onpage"] = run_onpage(scope)
        print()

    # Duplicates
    if run_all or mod == "duplicates":
        results["duplicates"] = run_duplicates(scope)
        print()

    # Broken links
    if run_all or mod == "broken-links":
        bl = run_broken_links(urls=urls if focused else None,
                              max_pages=args.max_pages)
        results["broken_links"] = bl
        crawl_data_cache = bl.get("crawl_data")
        print()

    # Clusters
    if run_all or mod == "clusters":
        results["internal_links"] = run_clusters(
            crawl_data=crawl_data_cache,
            filter_brands=brands_in_scope
        )
        print()


    # Sitemap and robots
    if run_all or mod == "sitemap":
        results["sitemap"] = run_sitemap(crawl_data=crawl_data_cache, priority_pages=scope)
        print()

    # Indexability
    if run_all or mod == "indexability":
        results["indexability"] = run_indexability(scope)
        print()

    # SERP snippets
    if run_all or mod == "snippets":
        if "onpage" not in results:
            results["onpage"] = run_onpage(scope)
            print()
        results["snippets"] = run_snippets(results.get("onpage", []))
        print()

    # Content gaps
    if run_all or mod == "content-gap":
        if "gsc" not in results:
            results["gsc"] = run_gsc(args.gsc, urls=urls if focused else None)
            print()
        results["content_gap"] = run_content_gap(results.get("gsc", {}))
        print()

    # Product/category audit
    if run_all or mod == "products":
        results["products"] = run_products(scope)
        print()

    # Internal link suggestions
    if run_all or mod == "link-suggestions":
        if "internal_links" not in results:
            results["internal_links"] = run_clusters(
                crawl_data=crawl_data_cache,
                filter_brands=brands_in_scope
            )
            print()
        results["link_suggestions"] = run_link_suggestions(results.get("internal_links", {}))
        print()

    # Regression
    if mod == "regression":
        results["regression"] = run_regression()
        print()

    # Monitor recorrente
    if mod == "monitor":
        try:
            from modules import monitor
            print("  Rodando monitor operacional...")
            results = monitor.run(scope=scope, gsc_folder=args.gsc, max_pages=args.max_pages)
            print(f"   ok Run monitor salvo: {results.get('run_id')}")
        except Exception as e:
            print(f"   x Falha no monitor operacional: {e}")
        print()

    # Diagnostico
    if mod == "doctor":
        from modules import doctor
        result = doctor.run(check_remote=True)
        doctor.print_result(result)
        print()

    # Generate (IA gratuita ou paga)
    if mod == "generate":
        from modules import content_generator
        # Resolve provider e chave: argumento > .env > erro
        provider, api_key = resolve_ai_credentials(args.provider, args.api_key)

        if not api_key:
            print("  Nenhuma chave de API configurada.")
            print("  Edite {BASE_DIR / '.env'} e adicione:")
            print("  GEMINI_API_KEY=sua_chave_aqui")
            print("  Chave gratuita em: https://aistudio.google.com/apikey")
        else:
            print(f"  Provedor: {provider}")
            onpage_res = run_onpage(scope)
            gsc_res    = run_gsc(args.gsc, urls=urls if focused else None)
            print(f"\n  Gerando conteudo otimizado...")
            generated = content_generator.run(onpage_res, gsc_data=gsc_res,
                                              provider=provider, api_key=api_key)
            if args.save_db and generated:
                from actions import content_queue
                try:
                    saved = content_queue.sync_pending_to_supabase()
                    print(f"   ok {saved} item(ns) de conteudo sincronizados no Supabase")
                except Exception as e:
                    print(f"   x Falha ao sincronizar conteudo no Supabase: {e}")
        print()

    # Sincronizar fila local de conteudo com Supabase
    if mod == "content-sync":
        from actions import content_queue
        try:
            saved = content_queue.sync_pending_to_supabase()
            print(f"  ok {saved} item(ns) de conteudo sincronizados no Supabase\n")
        except Exception as e:
            print(f"  x Falha ao sincronizar conteudo no Supabase: {e}\n")

    # Ideias de blog a partir do GSC API (ao vivo) + Gemini
    if mod == "blog-ideas":
        print("  Buscando queries ao vivo do GSC API...")
        from modules.gsc_api import get_top_queries
        gsc_query_data = get_top_queries(comparison=args.comparison, limit=500)
        if gsc_query_data.get("error"):
            print(f"  ! Falha na API GSC: {gsc_query_data['error']}")
            gsc_query_data = {}
        else:
            print(f"  ok {len(gsc_query_data.get('top_queries', []))} queries carregadas")
        _, api_key = resolve_ai_credentials("gemini", None)
        results["blog_ideas"] = run_blog_ideas(
            gsc_query_data,
            top=args.top,
            use_ai=True,
            provider="gemini",
            api_key=api_key,
        )
        if args.save_db and results["blog_ideas"]:
            try:
                from modules import supabase_store
                saved = supabase_store.save_blog_ideas(results["blog_ideas"])
                print(f"   ok {saved} ideias sincronizadas no Supabase\n")
            except Exception as e:
                print(f"   x Falha ao salvar ideias no Supabase: {e}\n")

    # Analise executiva com IA
    if mod == "ai-analysis":
        provider, api_key = resolve_ai_credentials(args.provider, args.api_key)

        if "gsc" not in results:
            results["gsc"] = run_gsc(args.gsc, urls=urls if focused else None)
            print()
        if "onpage" not in results:
            results["onpage"] = run_onpage(scope)
            print()
        if "content_gap" not in results:
            results["content_gap"] = run_content_gap(results.get("gsc", {}))
            print()
        if "backlog" not in results:
            from actions import backlog
            print("  Priorizando backlog SEO...")
            results["backlog"] = backlog.run(results, limit=max(args.top, 20))
            print()

        results["ai_analysis"] = run_ai_analysis(results, provider=provider, api_key=api_key)
        print()

    # AI Insights — Gemini strategic analysis (standalone ou integrado ao --all --ai)
    if mod == "ai-insights" or (run_all and args.ai):
        provider, api_key = resolve_ai_credentials(args.provider, args.api_key)

        # Fonte primária: GSC API (ao vivo). Fallback: CSV exports.
        if "gsc_api" not in results and _has_gsc_credentials():
            print("  Buscando dados do GSC API para base do AI Insights...")
            from modules import gsc_api as _gsc_api
            results["gsc_api"] = _gsc_api.run(
                results,
                scope_urls=urls if focused else None,
                use_ai=False,  # sem análise Gemini aqui — AI Insights fará isso
                comparison=args.comparison,
            )
            print()
        elif "gsc_api" not in results and "gsc" not in results:
            # Último recurso: tenta CSV
            results["gsc"] = run_gsc(args.gsc, urls=urls if focused else None)
            print()

        if "backlog" not in results:
            from actions import backlog as _backlog_mod
            print("  Priorizando backlog SEO...")
            results["backlog"] = _backlog_mod.run(results, limit=max(args.top, 20))
            print()

        results["ai_insights"] = run_ai_insights(results, provider=provider, api_key=api_key)

        # Reintegra tarefas de IA no backlog se já foi gerado
        if results["ai_insights"].get("_ai_enhanced") and "backlog" in results:
            from modules import gemini_insights as _gi
            ai_items = _gi.insights_to_backlog_items(results["ai_insights"])
            if ai_items:
                combined = results["backlog"] + ai_items
                results["backlog"] = sorted(combined, key=lambda x: x["priority"], reverse=True)
                print(f"   ok {len(ai_items)} tarefas IA adicionadas ao backlog")
        print()

    # GEO — Generative Engine Optimization
    if mod == "geo":
        from modules import geo_analyzer
        geo_key = args.api_key  # pplx-... key
        geo_analyzer.run(api_key=geo_key)
        print()

    # Backlog priorizado
    if mod == "backlog":
        from actions import backlog
        if "gsc" not in results:
            results["gsc"] = run_gsc(args.gsc, urls=urls if focused else None)
            print()
        if "onpage" not in results:
            results["onpage"] = run_onpage(scope)
            print()
        print("  Priorizando backlog SEO...")
        results["backlog"] = backlog.run(results, limit=args.top)
        backlog.print_backlog(results["backlog"])
        memory = results.get("change_memory", {})
        if memory:
            print(
                f"   memoria SEO: {memory.get('done_records', 0)} mudancas implementadas; "
                f"{memory.get('suppressed_count', 0)} tarefas antigas filtradas."
            )
        print()

    if mod == "change-memory":
        from modules import change_memory
        info = change_memory.summary(args.changes_log)
        print("  Memoria SEO do controle de mudancas")
        print(f"   arquivo: {info.get('path') or 'nao encontrado'}")
        print(f"   registros: {info.get('total_records', 0)}")
        print(f"   implementados: {info.get('done_records', 0)}")
        for row in info.get("latest_done", [])[:10]:
            print(
                f"   - {row.get('date')} {row.get('path')} | "
                f"{row.get('change_type')} | {row.get('element')}"
            )
        print()

    # Review (HTML para aplicação manual)
    if mod == "review":
        from modules import review
        review.run()
        print()

    # Blog suggestions
    if mod == "suggest":
        from modules import blog_suggester
        provider, api_key_val = resolve_ai_credentials(args.provider, args.api_key)

        if not api_key_val:
            print("  Nenhuma chave de API configurada.")
            print("  Edite {BASE_DIR / '.env'} e adicione:")
            print("  OPENROUTER_API_KEY=sua_chave_aqui  (modelos free em openrouter.ai)")
        else:
            print(f"  Provedor: {provider}")
            gsc_res = run_gsc(args.gsc, urls=urls if focused else None)
            blog_suggester.run(gsc_res, top=args.top, generate=True,
                               provider=provider, api_key=api_key_val)
        print()

    # Publish (Playwright)
    if mod == "publish":
        from modules import bagy_publisher
        # Credenciais: argumento > .env
        email    = args.bagy_email    or BAGY_EMAIL
        password = args.bagy_password or BAGY_PASSWORD
        if not args.dry_run and (not email or not password):
            print("  Credenciais Bagy nao encontradas.")
            print("  Edite {BASE_DIR / '.env'} e adicione:")
            print("  BAGY_EMAIL=seu@email.com")
            print("  BAGY_PASSWORD=suasenha")
        else:
            bagy_publisher.run(
                email=email or "",
                password=password or "",
                urls_filter=urls if focused else None,
                dry_run=args.dry_run,
                headless=args.headless,
            )
        print()

    # Keyword Tracker
    if mod == "keyword-tracker":
        print("  Rastreando posições de keywords por página...")
        from modules import keyword_tracker as _kt
        results["keyword_tracker"] = _kt.run(scope_urls=urls if focused else None)
        r = results["keyword_tracker"]
        print(f"   ok {r.get('pages_checked', 0)} páginas | "
              f"{r.get('total_dropped', 0)} quedas | "
              f"{r.get('pages_critical', 0)} páginas críticas")
        print()

    # Schema Check
    if mod == "schema-check":
        print("  Auditando schema markup (structured data)...")
        from modules import schema_check as _sc
        results["schema_check"] = _sc.run(urls=urls if focused else None)
        r = results["schema_check"]
        print(f"   ok {r.get('total', 0)} páginas | "
              f"{r.get('pages_with_issues', 0)} com problemas | "
              f"score médio {r.get('avg_score', 0)}/100")
        print()

    # Cannibalization
    if mod == "cannibalization":
        print("  Detectando canibalização de keywords via GSC API...")
        from modules import cannibalization as _can
        results["cannibalization"] = _can.run(scope_urls=urls if focused else None)
        r = results["cannibalization"]
        print(f"   ok {r.get('total', 0)} queries canibalizadas | "
              f"{r.get('high', 0)} críticas | "
              f"{r.get('medium', 0)} médias")
        print()

    # Persistencia no Supabase
    if args.save_db:
        try:
            if "backlog" not in results and any(k in results for k in ("gsc", "onpage", "broken_links", "internal_links")):
                from actions import backlog
                results["backlog"] = backlog.run(results, limit=max(args.top, 20))

            from actions import persist
            run_type = "all" if run_all else (mod or "audit")
            print("  Salvando resultados no Supabase...")
            run_id = persist.save_audit_results(results, run_type=run_type, scope=scope)
            print(f"   ok Run salvo: {run_id}\n")
        except Exception as e:
            print(f"   x Falha ao salvar no Supabase: {e}\n")

    if args.export_backlog:
        try:
            if "backlog" not in results and any(k in results for k in ("gsc", "onpage", "broken_links", "internal_links")):
                from actions import backlog
                results["backlog"] = backlog.run(results, limit=max(args.top, 20))

            from actions import export_backlog
            print("  Exportando backlog...")
            exported = export_backlog.export_all(results.get("backlog", []))
            print(f"   ok CSV:  {os.path.abspath(exported['csv'])}")
            print(f"   ok HTML: {os.path.abspath(exported['html'])}\n")
        except Exception as e:
            print(f"   x Falha ao exportar backlog: {e}\n")

    # Save JSON snapshot for dashboard /report view
    AUDIT_MODULES = {"gsc", "gsc-api", "onpage", "duplicates", "broken-links", "clusters",
                     "sitemap", "indexability", "snippets", "content-gap", "products",
                     "link-suggestions", "regression", "monitor", "blog-ideas", "ai-analysis", "backlog",
                     "change-memory", "ai-insights", "keyword-tracker", "schema-check", "cannibalization"}
    should_report = run_all or mod in AUDIT_MODULES
    if should_report and results:
        out = save_report(results, urls_label=focused)
        if out:
            print(f"  ok snapshot salvo — veja em /report no dashboard")


if __name__ == "__main__":
    main()
