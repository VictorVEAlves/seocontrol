from pathlib import Path
import os
import hashlib
from contextvars import ContextVar

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

PROVIDER_ENV_KEYS = {
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def get_provider_api_key(provider: str = "") -> str:
    """Return the configured API key for a specific provider."""
    provider = provider or ""
    cfg = _load_site_config()
    site_keys = cfg.get("ai_api_keys") if isinstance(cfg.get("ai_api_keys"), dict) else {}
    if site_keys.get(provider):
        return str(site_keys[provider])
    env_key = PROVIDER_ENV_KEYS.get(provider)
    if env_key and cfg.get(env_key):
        return str(cfg[env_key])
    return PROVIDER_API_KEYS.get(provider, "")


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

# ── Optional publisher credentials ────────────────────────────────────────────
BAGY_EMAIL    = os.environ.get("BAGY_EMAIL", "")
BAGY_PASSWORD = os.environ.get("BAGY_PASSWORD", "")

# Public installs must not assume a client/site. Configure this in the dashboard
# settings screen or through SITE_URL in .env.
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")

# ── Dynamic site configuration ────────────────────────────────────────────────
_SITE_CONFIG_FILE = BASE_DIR / ".site_config.json"
_RUNTIME_SITE_CONFIG: ContextVar[dict | None] = ContextVar("runtime_site_config", default=None)


def set_runtime_site_config(config: dict | None) -> None:
    """Set the active site configuration for the current request/job context."""
    if config is None:
        _RUNTIME_SITE_CONFIG.set(None)
    else:
        _RUNTIME_SITE_CONFIG.set(config if isinstance(config, dict) else {})


def clear_runtime_site_config() -> None:
    """Clear request/job scoped site configuration and use local fallbacks again."""
    _RUNTIME_SITE_CONFIG.set(None)


def _load_site_config() -> dict:
    runtime_cfg = _RUNTIME_SITE_CONFIG.get()
    if runtime_cfg is not None:
        return runtime_cfg
    runtime_env = os.environ.get("SEO_RUNTIME_SITE_CONFIG", "")
    if runtime_env:
        try:
            import json as _j
            parsed = _j.loads(runtime_env)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    try:
        import json as _j
        if _SITE_CONFIG_FILE.exists():
            return _j.loads(_SITE_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _using_runtime_site_config() -> bool:
    if _RUNTIME_SITE_CONFIG.get() is not None:
        return True
    return bool(os.environ.get("SEO_RUNTIME_SITE_CONFIG", ""))


def using_runtime_site_config() -> bool:
    return _using_runtime_site_config()


def get_site_url() -> str:
    """Return the configured site URL. Empty means the user has not configured a client yet."""
    cfg = _load_site_config()
    if _using_runtime_site_config():
        return str(cfg.get("site_url") or "").rstrip("/")
    url = cfg.get("site_url") or os.environ.get("SITE_URL") or SITE_URL
    return url.rstrip("/")


def get_gsc_property() -> str:
    """Return the GSC property URL (always ends with '/')."""
    cfg = _load_site_config()
    if _using_runtime_site_config():
        site_url = get_site_url()
        prop = cfg.get("gsc_property") or ((site_url + "/") if site_url else "")
    else:
        prop = (cfg.get("gsc_property")
                or os.environ.get("GSC_PROPERTY_URL")
                or ((get_site_url() + "/") if get_site_url() else ""))
    if not prop:
        return ""
    return prop if prop.endswith("/") else prop + "/"


def _resolve_runtime_path(value: str, fallback: Path) -> Path:
    path = Path(str(value or fallback))
    return path if path.is_absolute() else BASE_DIR / path


def get_gsc_credentials_file() -> Path:
    """Return the OAuth client JSON path for the active user/site."""
    cfg = _load_site_config()
    if _using_runtime_site_config():
        return _resolve_runtime_path(
            cfg.get("gsc_credentials_file"),
            BASE_DIR / ".runtime" / "gsc" / "unconfigured_credentials.json",
        )
    return _resolve_runtime_path(
        cfg.get("gsc_credentials_file") or os.environ.get("GSC_CREDENTIALS_FILE"),
        BASE_DIR / "gsc_credentials.json",
    )


def get_gsc_token_file() -> Path:
    """Return the OAuth token JSON path for the active user/site."""
    cfg = _load_site_config()
    if _using_runtime_site_config():
        return _resolve_runtime_path(
            cfg.get("gsc_token_file"),
            BASE_DIR / ".runtime" / "gsc" / "unconfigured_token.json",
        )
    return _resolve_runtime_path(
        cfg.get("gsc_token_file") or os.environ.get("GSC_TOKEN_FILE"),
        BASE_DIR / ".gsc_token.json",
    )


def get_gsc_token_json() -> str:
    """Return the OAuth token JSON stored for the active user/site, when available."""
    cfg = _load_site_config()
    return str(cfg.get("gsc_token_json") or "")


def has_gsc_token() -> bool:
    """Return whether the active site has a usable GSC OAuth token."""
    return bool(get_gsc_token_json() or get_gsc_token_file().exists())


def get_site_name() -> str:
    """Short display name extracted from the configured site URL."""
    from urllib.parse import urlparse as _up
    cfg = _load_site_config()
    if cfg.get("site_name"):
        return str(cfg["site_name"]).strip()
    host = _up(get_site_url()).netloc or get_site_url()
    # Strip www. prefix for display
    name = host[4:] if host.startswith("www.") else host
    return name or "Site não configurado"


def get_site_id() -> str:
    """Return the active site id from request/job-scoped configuration."""
    cfg = _load_site_config()
    return str(cfg.get("site_id") or "")


def get_site_owner_user_id() -> str:
    """Return the authenticated owner id for the active site when available."""
    cfg = _load_site_config()
    return str(cfg.get("user_id") or "")


def get_scoped_runtime_file(filename: str, folder: str = "scoped") -> Path:
    """Return a per-user/site file path when running with request/job config."""
    cfg = _load_site_config()
    user_id = str(cfg.get("user_id") or "")
    site_id = str(cfg.get("site_id") or "")
    if user_id or site_id:
        raw_key = "|".join([user_id or "local", site_id or "", str(cfg.get("site_url") or "default")])
        key = hashlib.sha1(raw_key.encode("utf-8")).hexdigest()
        return BASE_DIR / ".runtime" / folder / key / filename
    return BASE_DIR / filename


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


# ── Client-controlled SEO scope ───────────────────────────────────────────────
# These constants intentionally start empty. Public users configure their own
# URLs, brands and market terms in /settings or .site_config.json.
PRIORITY_PAGES: list[str] = []
BRAND_TIERS = {"top": [], "good": []}
BRAND_CLUSTERS: dict = {}

DEFAULT_PRODUCT_TERMS = {
    "produto", "produtos", "categoria", "categorias", "servico", "serviço",
    "servicos", "serviços", "preco", "preço", "comprar", "loja", "oferta",
}
DEFAULT_COMMERCIAL_TERMS = {
    "comprar", "preco", "preço", "promocao", "promoção", "oferta", "desconto",
    "cupom", "loja", "online", "barato", "melhor", "comparar",
}


def _split_lines(value) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value or "").replace(",", "\n").splitlines()
    return [str(item).strip() for item in raw if str(item).strip()]


def _normalize_path(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if value.startswith("http"):
        from urllib.parse import urlparse as _up
        parsed = _up(value)
        value = parsed.path or "/"
    if not value.startswith("/"):
        value = "/" + value
    return value.rstrip("/") or "/"


def get_business_context() -> str:
    cfg = _load_site_config()
    return str(
        cfg.get("business_context")
        or os.environ.get("BUSINESS_CONTEXT")
        or "Negócio digital configurado pelo cliente."
    ).strip()


def get_content_guidelines() -> str:
    cfg = _load_site_config()
    return str(
        cfg.get("content_guidelines")
        or os.environ.get("CONTENT_GUIDELINES")
        or "Use tom consultivo, linguagem clara em PT-BR e recomendações acionáveis."
    ).strip()


def get_priority_pages() -> list:
    cfg = _load_site_config()
    pages = cfg.get("priority_pages")
    if pages is None:
        pages = os.environ.get("PRIORITY_PAGES", "")
    result = []
    seen = set()
    for page in _split_lines(pages):
        normalized = _normalize_path(page)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _parse_brand_aliases(value) -> dict[str, list[str]]:
    if isinstance(value, dict):
        return {
            str(brand).strip().lower(): [str(alias).strip().lower() for alias in aliases if str(alias).strip()]
            for brand, aliases in value.items()
            if str(brand).strip()
        }
    aliases: dict[str, list[str]] = {}
    for line in str(value or "").splitlines():
        if not line.strip():
            continue
        if ":" in line:
            brand, raw_aliases = line.split(":", 1)
            items = [brand.strip(), *[item.strip() for item in raw_aliases.split(",")]]
        else:
            brand = line.strip()
            items = [brand]
        key = brand.strip().lower()
        values = []
        for item in items:
            item = item.strip().lower()
            if item and item not in values:
                values.append(item)
        if key and values:
            aliases[key] = values
    return aliases


def get_brand_aliases() -> dict[str, list[str]]:
    cfg = _load_site_config()
    return _parse_brand_aliases(cfg.get("brand_aliases") or os.environ.get("BRAND_ALIASES", ""))


def get_product_terms() -> set[str]:
    cfg = _load_site_config()
    terms = _split_lines(cfg.get("product_terms") or os.environ.get("PRODUCT_TERMS", ""))
    return {term.lower() for term in terms} or set(DEFAULT_PRODUCT_TERMS)


def get_commercial_terms() -> set[str]:
    cfg = _load_site_config()
    terms = _split_lines(cfg.get("commercial_terms") or os.environ.get("COMMERCIAL_TERMS", ""))
    return {term.lower() for term in terms} or set(DEFAULT_COMMERCIAL_TERMS)


def get_brand_clusters() -> dict:
    cfg = _load_site_config()
    clusters = cfg.get("brand_clusters")
    if isinstance(clusters, dict):
        return clusters

    aliases = get_brand_aliases()
    pages = get_priority_pages()
    derived = {}
    for brand, names in aliases.items():
        tokens = {
            alias.strip().lower().replace(" ", "-")
            for alias in [brand, *names]
            if alias.strip()
        }
        matches = [
            page for page in pages
            if any(token and token in page.strip("/").lower() for token in tokens)
        ]
        if not matches:
            continue
        pillar = min(matches, key=lambda page: (page.count("/"), len(page)))
        derived[brand.replace(" ", "_")] = {
            "tier": "configured",
            "pillar": pillar,
            "pages": [page for page in matches if page != pillar],
            "blog": [],
        }
    return derived

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
CRAWL_DELAY = float(os.environ.get("SEO_CRAWL_DELAY", "1.0"))  # segundos entre requisicoes
REQUEST_TIMEOUT = float(os.environ.get("SEO_REQUEST_TIMEOUT", "15"))
CRAWL_RETRIES = int(os.environ.get("SEO_CRAWL_RETRIES", "2"))
USER_AGENT = os.environ.get(
    "SEO_CRAWLER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36",
)
