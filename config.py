from pathlib import Path
import os

# Pasta raiz do projeto (onde este arquivo está)
BASE_DIR = Path(__file__).parent.resolve()

# Carrega variáveis do .env automaticamente
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file)
    except ImportError:
        # Fallback manual se dotenv não estiver instalado
        for line in _env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                if val.strip() and not os.environ.get(key.strip()):
                    os.environ[key.strip()] = val.strip()

# ── Chaves de API (lidas do .env) ─────────────────────────────────────────────
def disable_broken_local_proxy() -> None:
    """Remove stale local proxy values that block external APIs on this machine."""
    proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                  "ALL_PROXY", "all_proxy", "GIT_HTTP_PROXY", "GIT_HTTPS_PROXY"]
    for key in proxy_keys:
        value = os.environ.get(key, "")
        # Remove any proxy pointing to a local port (SOCKS/HTTP forwarders that may be dead)
        if "127.0.0.1" in value or "localhost" in value or "::1" in value:
            os.environ.pop(key, None)


disable_broken_local_proxy()

GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY      = os.environ.get("GROQ_API_KEY", "")
MISTRAL_API_KEY   = os.environ.get("MISTRAL_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
OPENROUTER_FALLBACK_MODELS = [
    model.strip()
    for model in os.environ.get(
        "OPENROUTER_FALLBACK_MODELS",
        "openai/gpt-oss-20b:free,deepseek/deepseek-v4-flash:free,"
        "qwen/qwen3-next-80b-a3b-instruct:free,z-ai/glm-4.5-air:free,"
        "meta-llama/llama-3.3-70b-instruct:free,openrouter/free",
    ).split(",")
    if model.strip()
]
AI_PROVIDER_ORDER = [
    provider.strip()
    for provider in os.environ.get(
        "AI_PROVIDER_ORDER",
        "openrouter,groq,gemini,mistral,anthropic",
    ).split(",")
    if provider.strip()
]

PROVIDER_API_KEYS = {
    "openrouter": OPENROUTER_API_KEY,
    "groq": GROQ_API_KEY,
    "mistral": MISTRAL_API_KEY,
    "gemini": GEMINI_API_KEY,
    "anthropic": ANTHROPIC_API_KEY,
}


def get_provider_api_key(provider: str = "") -> str:
    """Return the configured API key for a specific provider."""
    return PROVIDER_API_KEYS.get(provider or "", "")


def get_provider_sequence(preferred: str = "") -> list[tuple[str, str]]:
    """Return configured providers in fallback order."""
    ordered = []
    seen = set()
    names = []
    if preferred:
        names.append(preferred)
    names.extend(AI_PROVIDER_ORDER)
    names.extend(PROVIDER_API_KEYS.keys())

    for name in names:
        if name in seen:
            continue
        seen.add(name)
        key = get_provider_api_key(name)
        if key:
            ordered.append((name, key))
    return ordered

# Provider padrão para geração de conteúdo
# Usa automaticamente o primeiro com chave configurada
def get_default_provider() -> tuple:
    """Returns (provider_name, api_key) for the first configured provider.
    Priority: openrouter > groq > mistral > gemini > anthropic.
    OpenRouter is first because it gives one API key for many free model variants.
    """
    for name, key in get_provider_sequence():
        if key:
            return name, key
    return "", ""

# ── Login Bagy ────────────────────────────────────────────────────────────────
BAGY_EMAIL    = os.environ.get("BAGY_EMAIL", "")
BAGY_PASSWORD = os.environ.get("BAGY_PASSWORD", "")

SITE_URL = "https://www.secretoutlet.com.br"

# ── Dynamic site configuration (overrides the hardcoded SITE_URL above) ─────────
_SITE_CONFIG_FILE = BASE_DIR / ".site_config.json"


def _load_site_config() -> dict:
    try:
        import json as _j
        if _SITE_CONFIG_FILE.exists():
            return _j.loads(_SITE_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def get_site_url() -> str:
    """Return the configured site URL, falling back to SITE_URL."""
    cfg = _load_site_config()
    url = cfg.get("site_url") or os.environ.get("SITE_URL") or SITE_URL
    return url.rstrip("/")


def get_gsc_property() -> str:
    """Return the GSC property URL (always ends with '/')."""
    cfg = _load_site_config()
    prop = (cfg.get("gsc_property")
            or os.environ.get("GSC_PROPERTY_URL")
            or (get_site_url() + "/"))
    return prop if prop.endswith("/") else prop + "/"


def get_site_name() -> str:
    """Short display name extracted from the configured site URL."""
    from urllib.parse import urlparse as _up
    host = _up(get_site_url()).netloc or get_site_url()
    # Strip www. prefix for display
    return host[4:] if host.startswith("www.") else host


def save_site_config(**kwargs) -> None:
    """Persist site configuration to .site_config.json."""
    import json as _j
    cfg = _load_site_config()
    for k, v in kwargs.items():
        if v is not None:
            cfg[k] = v
    _SITE_CONFIG_FILE.write_text(
        _j.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── Brand tiers ────────────────────────────────────────────────────────────────
# top: highest commercial priority — full cluster monitoring
# good: important brands — pillar + main categories monitored
BRAND_TIERS = {
    "top":  ["tommy_hilfiger", "tommy_jeans", "lacoste", "calvin_klein"],
    "good": ["aramis", "reserva", "john_john", "levis", "dudalina",
             "armani_exchange", "emporio_armani", "columbia"],
}

# ── Priority pages (derived from BRAND_CLUSTERS below) ────────────────────────
# Top Marcas — full subcategory coverage
_TOP_PAGES = [
    # Tommy Hilfiger
    "/tommy-hilfiger",
    "/tenis-tommy-hilfiger",
    "/camisas-sociais-tommy-hilfiger",
    "/camisetas-tommy-hilfiger",
    "/bones-tommy-hilfiger",
    "/blusas-jaquetas-e-moletons-tommy-hilfiger",
    "/calcas-tommy-hilfiger",
    "/bermudas-tommy-hilfiger",
    "/carteiras-tommy-hilfiger",
    "/chinelos-tommy-hilfiger",
    # Tommy Jeans
    "/tommy-jeans",
    # Lacoste
    "/lacoste",
    "/tenis-lacoste",
    "/polos-lacoste",
    "/camisetas-lacoste",
    "/bones-lacoste",
    "/blusas-jaquetas-e-moletons-lacoste",
    "/camisas-sociais-lacoste",
    "/calcas-lacoste",
    "/bermudas-lacoste",
    # Calvin Klein
    "/calvin-klein",
    "/tenis-calvin-klein",
    "/blusas-jaquetas-e-moletons-calvin-klein",
]

# Good Marcas — pillar + main categories
_GOOD_PAGES = [
    # Aramis
    "/aramis",
    # Reserva
    "/reserva",
    "/tenis-reserva",
    "/polos-reserva",
    "/camisas-sociais-reserva",
    "/camisetas-reserva",
    "/blusas-jaquetas-e-moletons-reserva",
    "/chinelos-reserva",
    # John John
    "/john-john",
    # Levis
    "/levis",
    "/calcas-levis",
    "/camisetas-levis",
    # Dudalina
    "/dudalina",
    "/camisas-sociais-dudalina",
    # Armani Exchange
    "/armani-exchange",
    # Emporio Armani
    "/marca-emporio-armani",
    # Columbia
    "/columbia",
    "/blusas-jaquetas-e-moletons-columbia",
]

# High-traffic pages that must always be monitored regardless of tier
_HIGH_TRAFFIC_PAGES = [
    "/diesel",           # 211k impressões
    "/osklen",           # 157k impressões
    "/colcci",           # 39k impressões
    "/sergio-k",         # 43k impressões
    "/polo-ralph-lauren",
    "/crocs",
    "/guia-de-tamanhos", # 334k impressões — maior página do site
]

PRIORITY_PAGES = _TOP_PAGES + _GOOD_PAGES + _HIGH_TRAFFIC_PAGES

BRAND_CLUSTERS = {
    # ── Top Marcas ────────────────────────────────────────────────────────────
    "tommy_hilfiger": {
        "tier": "top",
        "pillar": "/tommy-hilfiger",
        "pages": [
            "/tenis-tommy-hilfiger",
            "/camisas-sociais-tommy-hilfiger",
            "/camisetas-tommy-hilfiger",
            "/bones-tommy-hilfiger",
            "/blusas-jaquetas-e-moletons-tommy-hilfiger",
            "/calcas-tommy-hilfiger",
            "/bermudas-tommy-hilfiger",
            "/carteiras-tommy-hilfiger",
            "/chinelos-tommy-hilfiger",
        ],
        "blog": ["/estilos-de-camisas-tommy-hilfiger"],
    },
    "tommy_jeans": {
        "tier": "top",
        "pillar": "/tommy-jeans",
        "pages": [],
        "blog": [],
    },
    "lacoste": {
        "tier": "top",
        "pillar": "/lacoste",
        "pages": [
            "/tenis-lacoste",
            "/polos-lacoste",
            "/camisetas-lacoste",
            "/bones-lacoste",
            "/blusas-jaquetas-e-moletons-lacoste",
            "/camisas-sociais-lacoste",
            "/calcas-lacoste",
            "/bermudas-lacoste",
        ],
        "blog": ["/melhores-tenis-lacoste"],
    },
    "calvin_klein": {
        "tier": "top",
        "pillar": "/calvin-klein",
        "pages": [
            "/tenis-calvin-klein",
            "/blusas-jaquetas-e-moletons-calvin-klein",
        ],
        "blog": [],
    },
    # ── Good Marcas ───────────────────────────────────────────────────────────
    "aramis": {
        "tier": "good",
        "pillar": "/aramis",
        "pages": [],
        "blog": [],
    },
    "reserva": {
        "tier": "good",
        "pillar": "/reserva",
        "pages": [
            "/tenis-reserva",
            "/polos-reserva",
            "/camisas-sociais-reserva",
            "/camisetas-reserva",
            "/blusas-jaquetas-e-moletons-reserva",
            "/chinelos-reserva",
        ],
        "blog": [],
    },
    "john_john": {
        "tier": "good",
        "pillar": "/john-john",
        "pages": [],
        "blog": [],
    },
    "levis": {
        "tier": "good",
        "pillar": "/levis",
        "pages": ["/calcas-levis", "/camisetas-levis"],
        "blog": ["/levis-501-511-512-514-517-diferenca"],
    },
    "dudalina": {
        "tier": "good",
        "pillar": "/dudalina",
        "pages": ["/camisas-sociais-dudalina"],
        "blog": [],
    },
    "armani_exchange": {
        "tier": "good",
        "pillar": "/armani-exchange",
        "pages": [],
        "blog": [],
    },
    "emporio_armani": {
        "tier": "good",
        "pillar": "/marca-emporio-armani",
        "pages": [],
        "blog": [],
    },
    "columbia": {
        "tier": "good",
        "pillar": "/columbia",
        "pages": ["/blusas-jaquetas-e-moletons-columbia"],
        "blog": ["/melhores-jaquetas-columbia-masculinas"],
    },
}

# PageSpeed Insights API key (opcional — sem chave funciona mas com rate limit menor)
# Obtenha grátis em: https://developers.google.com/speed/docs/insights/v5/get-started
PAGESPEED_API_KEY = os.environ.get("PAGESPEED_API_KEY", "")

# Pasta com os exports CSV do Google Search Console
# Coloque os CSVs exportados do GSC diretamente aqui
GSC_EXPORT_FOLDER = str(BASE_DIR / "gsc_exports")

# Pasta de saída dos relatórios
REPORTS_FOLDER = str(BASE_DIR / "reports")

# Rastreamento
MAX_CRAWL_PAGES = 1000
CRAWL_DELAY = 1.0  # segundos entre requisições
REQUEST_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; SEOAudit/1.0)"


def get_brand_clusters() -> dict:
    """Return BRAND_CLUSTERS only if the configured site matches the original."""
    if get_site_url().rstrip("/") == SITE_URL.rstrip("/"):
        return BRAND_CLUSTERS
    return {}


def get_priority_pages() -> list:
    """Return PRIORITY_PAGES only if the configured site matches the original."""
    if get_site_url().rstrip("/") == SITE_URL.rstrip("/"):
        return PRIORITY_PAGES
    return []
