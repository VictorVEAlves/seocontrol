import time
import requests
from config import PAGESPEED_API_KEY, SITE_URL, disable_broken_local_proxy

disable_broken_local_proxy()

API_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

# Proxy-free session — avoids Windows system proxy blocking googleapis.com
_session = requests.Session()
_session.trust_env = False


def _audit_url(url: str, strategy: str) -> dict:
    params = {
        "url": url,
        "strategy": strategy,
        "category": ["performance", "seo", "best-practices", "accessibility"],
    }
    if PAGESPEED_API_KEY:
        params["key"] = PAGESPEED_API_KEY

    try:
        resp = _session.get(API_URL, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"url": url, "strategy": strategy, "error": str(e),
                "performance_score": None, "seo_score": None}

    lhr        = data.get("lighthouseResult", {})
    categories = lhr.get("categories", {})
    audits     = lhr.get("audits", {})

    def score(cat_id):
        v = categories.get(cat_id, {}).get("score")
        return int(v * 100) if v is not None else None

    def metric(audit_id):
        return audits.get(audit_id, {}).get("displayValue", "—")

    def numeric(audit_id):
        return audits.get(audit_id, {}).get("numericValue")

    cls_val = numeric("cumulative-layout-shift")
    lcp_val = numeric("largest-contentful-paint")

    issues = []
    perf = score("performance")
    if perf is not None and perf < 50:
        issues.append(f"Performance crítica ({perf}/100)")
    if cls_val and cls_val > 0.1:
        issues.append(f"CLS alto: {metric('cumulative-layout-shift')} (deve ser < 0.1)")
    if lcp_val and lcp_val > 4000:
        issues.append(f"LCP lento: {metric('largest-contentful-paint')} (deve ser < 2.5s)")

    return {
        "url":                  url,
        "strategy":             strategy,
        "performance_score":    perf,
        "seo_score":            score("seo"),
        "best_practices_score": score("best-practices"),
        "accessibility_score":  score("accessibility"),
        "lcp":                  metric("largest-contentful-paint"),
        "lcp_ms":               lcp_val,
        "fcp":                  metric("first-contentful-paint"),
        "cls":                  metric("cumulative-layout-shift"),
        "cls_value":            cls_val,
        "tbt":                  metric("total-blocking-time"),
        "speed_index":          metric("speed-index"),
        "issues":               issues,
    }


def run(urls: list, strategies: list = None) -> list:
    """Audit a list of URLs via PageSpeed Insights API."""
    if not PAGESPEED_API_KEY:
        print("  Aviso: PAGESPEED_API_KEY nao configurada — PageSpeed sera pulado.")
        print("  Chave gratuita (necessaria): https://developers.google.com/speed/docs/insights/v5/get-started")
        print("  Adicione PAGESPEED_API_KEY=sua_chave no arquivo .env e rode novamente.")
        return []

    if strategies is None:
        strategies = ["mobile", "desktop"]

    full_urls = [u if u.startswith("http") else SITE_URL + u for u in urls]
    results   = []

    try:
        from rich.progress import track
        iterator = track(full_urls, description="Auditando PageSpeed...")
    except ImportError:
        iterator = full_urls

    for url in iterator:
        for strategy in strategies:
            for attempt in range(3):
                result = _audit_url(url, strategy)
                if "429" in result.get("error", ""):
                    time.sleep(2.0 * (2 ** attempt))
                    continue
                results.append(result)
                time.sleep(2.0)
                break
            else:
                results.append(result)

    return results
