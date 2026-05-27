"""
Gera os schemas JSON-LD corrigidos para injetar no Bagy.
Salva em: seo-audit/schema_injection.html
"""
import sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from modules.crawler import get_page

BASE = "https://www.secretoutlet.com.br"

# ── 1. Extrair todas as FAQs ───────────────────────────────────────────────────
print("Extraindo FAQs...")
_, soup_faq, _, _ = get_page(BASE + "/perguntas-frequentes")
faq_items = soup_faq.find_all(class_="page-faq-item")
faqs = []
for item in faq_items:
    q_el = item.find("h3") or item.find(class_="page-faq-question")
    a_el = item.find(class_="page-faq-answer")
    if q_el and a_el:
        q = q_el.get_text(strip=True)
        a = a_el.get_text(separator=" ", strip=True)
        if q and a:
            faqs.append({"q": q, "a": a})
print(f"  {len(faqs)} FAQs extraídas")

# ── 2. Schemas ────────────────────────────────────────────────────────────────

faq_schema = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "mainEntity": [
        {
            "@type": "Question",
            "name": f["q"],
            "acceptedAnswer": {
                "@type": "Answer",
                "text": f["a"]
            }
        }
        for f in faqs
    ]
}

organization_schema = {
    "@context": "https://schema.org",
    "@type": "Organization",
    "@id": f"{BASE}#organization",
    "name": "Secret Outlet",
    "alternateName": "Secret Shop Comércio de Moda",
    "url": BASE,
    "logo": {
        "@type": "ImageObject",
        "url": "https://cdn.dooca.store/946/files/logo-secret-16-anos.png",
        "width": 300,
        "height": 60
    },
    "description": "Outlet oficial e revendedor autorizado de marcas premium de moda masculina no Brasil. Lacoste, Tommy Hilfiger, Columbia, Reserva, Calvin Klein, Levi's, Aramis e muito mais.",
    "address": {
        "@type": "PostalAddress",
        "streetAddress": "Al. Dr. Carlos de Carvalho, 603 - Centro",
        "addressLocality": "Curitiba",
        "addressRegion": "PR",
        "postalCode": "80430-180",
        "addressCountry": "BR"
    },
    "contactPoint": {
        "@type": "ContactPoint",
        "telephone": "+55-41-98889-1429",
        "email": "atendimento@secretoutlet.com.br",
        "contactType": "customer service",
        "availableLanguage": "Portuguese"
    },
    "sameAs": [
        "https://www.facebook.com/secretoutlet",
        "https://www.instagram.com/secretoutlet/"
    ]
}

# BreadcrumbList por página
def breadcrumb(path: str, label: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": BASE},
            {"@type": "ListItem", "position": 2, "name": label, "item": BASE + path}
        ]
    }

BRAND_BREADCRUMBS = {
    "/lacoste":                               "Lacoste",
    "/tommy-hilfiger":                        "Tommy Hilfiger",
    "/columbia":                              "Columbia",
    "/reserva":                               "Reserva",
    "/aramis":                                "Aramis",
    "/calvin-klein":                          "Calvin Klein",
    "/tommy-jeans":                           "Tommy Jeans",
    "/levis":                                 "Levi's",
    "/dudalina":                              "Dudalina",
    "/polo-ralph-lauren":                     "Polo Ralph Lauren",
    "/crocs":                                 "Crocs",
    "/blusas-jaquetas-e-moletons-lacoste":    "Jaquetas Lacoste",
    "/blusas-jaquetas-e-moletons-columbia":   "Jaquetas Columbia",
    "/blusas-jaquetas-e-moletons-tommy-hilfiger": "Jaquetas Tommy Hilfiger",
    "/blusas-jaquetas-e-moletons-calvin-klein":   "Jaquetas Calvin Klein",
    "/blusas-jaquetas-e-moletons-reserva":    "Jaquetas Reserva",
    "/tenis-lacoste":                         "Tênis Lacoste",
    "/tenis-tommy-hilfiger":                  "Tênis Tommy Hilfiger",
    "/tenis-calvin-klein":                    "Tênis Calvin Klein",
    "/tenis-reserva":                         "Tênis Reserva",
    "/polos-lacoste":                         "Polos Lacoste",
    "/polos-reserva":                         "Polos Reserva",
    "/camisetas-lacoste":                     "Camisetas Lacoste",
    "/camisetas-reserva":                     "Camisetas Reserva",
    "/calcas-levis":                          "Calças Levi's",
    "/camisetas-levis":                       "Camisetas Levi's",
    "/camisas-sociais-tommy-hilfiger":        "Camisas Tommy Hilfiger",
    "/camisas-sociais-reserva":               "Camisas Reserva",
    "/camisas-sociais-dudalina":              "Camisas Dudalina",
    "/bones-lacoste":                         "Bonés Lacoste",
    "/bones-tommy-hilfiger":                  "Bonés Tommy Hilfiger",
    "/masculino":                             "Masculino",
    "/guia-de-tamanhos":                      "Guia de Tamanhos",
    "/perguntas-frequentes":                  "Perguntas Frequentes",
}

# ── 3. Gerar HTML de injeção ──────────────────────────────────────────────────

faq_json     = json.dumps(faq_schema,          ensure_ascii=False, indent=2)
org_json     = json.dumps(organization_schema, ensure_ascii=False, indent=2)

breadcrumb_cases = "\n".join(
    f"  if (p === '{path}') bc = {json.dumps(breadcrumb(path, label), ensure_ascii=False)};"
    for path, label in BRAND_BREADCRUMBS.items()
)

html = f"""<!-- ═══════════════════════════════════════════════════════════════
     SECRET OUTLET — Schema JSON-LD Injection
     Cole este bloco no campo "Código personalizado > <head>" do Bagy
     ═══════════════════════════════════════════════════════════════ -->

<!-- 1. Organization: corrige nome, adiciona logo e redes sociais -->
<script type="application/ld+json">
{org_json}
</script>

<!-- 2. FAQPage: rich results nas SERPs (apenas /perguntas-frequentes) -->
<script>
(function() {{
  if (window.location.pathname !== '/perguntas-frequentes') return;
  var el = document.createElement('script');
  el.type = 'application/ld+json';
  el.text = {json.dumps(faq_json)};
  document.head.appendChild(el);
}})();
</script>

<!-- 3. BreadcrumbList: navegação estruturada em todas as categorias -->
<script>
(function() {{
  var p = window.location.pathname.replace(/\\/+$/, '') || '/';
  var bc = null;
{breadcrumb_cases}
  if (!bc) return;
  var el = document.createElement('script');
  el.type = 'application/ld+json';
  el.text = JSON.stringify(bc);
  document.head.appendChild(el);
}})();
</script>
"""

from pathlib import Path
out = Path(__file__).parent / "schema_injection.html"
out.write_text(html, encoding="utf-8")
print(f"\nArquivo gerado: {out}")
print(f"  Organization schema: OK")
print(f"  FAQPage schema: {len(faqs)} perguntas")
print(f"  BreadcrumbList: {len(BRAND_BREADCRUMBS)} paginas")
print(f"\nComo usar:")
print(f"  Bagy Admin > Configuracoes > Codigo personalizado > <head>")
print(f"  Cole o conteudo de schema_injection.html")
