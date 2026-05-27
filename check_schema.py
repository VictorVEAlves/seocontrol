import sys, json, requests
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from bs4 import BeautifulSoup

BASE = "https://www.secretoutlet.com.br"
pages = [
    "/",
    "/lacoste",
    "/tenis-lacoste",
    "/blusas-jaquetas-e-moletons-lacoste",
    "/perguntas-frequentes",
    "/guia-de-tamanhos",
]

headers = {"User-Agent": "Mozilla/5.0 (compatible; SEOAudit/1.0)"}

for path in pages:
    url = BASE + path
    try:
        r = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        scripts = soup.find_all("script", type="application/ld+json")
        print(f"\n{'='*60}")
        print(f"  PAGE : {path}")
        print(f"  STATUS: {r.status_code}")
        if not scripts:
            print("  SCHEMA: nenhum encontrado")
            continue
        for s in scripts:
            try:
                data = json.loads(s.string or "{}")
                schema_type = data.get("@type", "unknown")
                print(f"  SCHEMA: {schema_type}")
                # Print key fields depending on type
                if schema_type == "Organization":
                    print(f"    name: {data.get('name')}")
                    print(f"    url: {data.get('url')}")
                    print(f"    logo: {data.get('logo')}")
                    print(f"    sameAs: {data.get('sameAs')}")
                elif schema_type == "WebSite":
                    print(f"    name: {data.get('name')}")
                    print(f"    url: {data.get('url')}")
                    print(f"    potentialAction: {bool(data.get('potentialAction'))}")
                elif schema_type in ("ItemList", "CollectionPage"):
                    items = data.get("itemListElement", [])
                    print(f"    items: {len(items)}")
                    for i in items[:3]:
                        print(f"      - {i.get('name','?')} | {i.get('@type','?')}")
                elif schema_type == "BreadcrumbList":
                    items = data.get("itemListElement", [])
                    print(f"    breadcrumbs: {' > '.join(i.get('name','?') for i in items)}")
                elif schema_type == "FAQPage":
                    faqs = data.get("mainEntity", [])
                    print(f"    FAQs: {len(faqs)}")
                    for f in faqs[:2]:
                        print(f"      Q: {f.get('name','')[:60]}")
                elif schema_type == "Product":
                    print(f"    name: {data.get('name')}")
                    print(f"    offers: {bool(data.get('offers'))}")
                    print(f"    aggregateRating: {bool(data.get('aggregateRating'))}")
                else:
                    print(f"    raw: {json.dumps(data, ensure_ascii=False)[:200]}")
            except Exception as e:
                print(f"  ERRO parse: {e}")
    except Exception as e:
        print(f"  ERRO request: {e}")

print("\n" + "="*60)
print("DONE")
