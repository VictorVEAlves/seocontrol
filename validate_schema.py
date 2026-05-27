import sys, json, re
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with open("schema_injection.html", encoding="utf-8") as f:
    content = f.read()

pattern = r'<script type="application/ld\+json">(.*?)</script>'
scripts = re.findall(pattern, content, re.DOTALL)

print(f"Scripts encontrados: {len(scripts)}")
for s in scripts:
    try:
        data = json.loads(s.strip())
        t = data.get("@type", "?")
        print(f"\nSchema @type={t} - JSON VALIDO")
        if t == "Organization":
            print(f"  name       : {data.get('name')}")
            print(f"  logo       : {data.get('logo', {}).get('url', 'AUSENTE')}")
            print(f"  sameAs     : {data.get('sameAs')}")
            print(f"  description: {data.get('description','')[:80]}")
        elif t == "FAQPage":
            qs = data.get("mainEntity", [])
            print(f"  questions  : {len(qs)}")
            print(f"  primeiro Q : {qs[0]['name'][:70] if qs else '-'}")
            print(f"  ultima  Q  : {qs[-1]['name'][:70] if qs else '-'}")
    except Exception as e:
        print(f"\nJSON INVALIDO: {e}")
        print(s[:200])

print(f"\nTamanho total do arquivo: {len(content):,} chars")
