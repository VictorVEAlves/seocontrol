"""
Shopify-only SEO automation.

This module is intentionally isolated from the existing Bagy publisher and
content queue. It reads products/collections from Shopify Admin GraphQL, creates
SEO proposals, stores them in a Shopify-specific review queue, and only writes
changes back after explicit approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Iterable

import requests

from config import (
    _load_site_config,
    clear_runtime_site_config,
    get_default_provider,
    get_provider_api_key,
    get_scoped_runtime_file,
    get_site_url,
    set_runtime_site_config,
    using_runtime_site_config,
)


TITLE_MIN = 45
TITLE_MAX = 60
DESC_MIN = 140
DESC_MAX = 170

DEFAULT_API_VERSION = "2026-04"
QUEUE_FILE = get_scoped_runtime_file("shopify_seo_changes.json", "shopify")
LOG_FILE = get_scoped_runtime_file("shopify_seo_publish_log.json", "shopify")
TOKEN_CACHE_FILE = get_scoped_runtime_file("shopify_admin_token.json", "shopify")
TOKEN_REFRESH_SKEW_SECONDS = 300
PROVIDER_DELAY_DEFAULTS = {
    "openrouter": 3.2,
    "groq": 2.2,
    "gemini": 1.0,
    "mistral": 1.0,
    "anthropic": 0.5,
    "ollama": 0.0,
}


def _now() -> str:
    return datetime.now().isoformat()


def _provider_delay_seconds(provider: str) -> float:
    provider = str(provider or "").strip().lower()
    specific = f"{provider.upper()}_REQUEST_DELAY_SECONDS" if provider else ""
    candidates = [
        os.environ.get(specific, "") if specific else "",
        os.environ.get("AI_REQUEST_DELAY_SECONDS", ""),
    ]
    for value in candidates:
        if str(value or "").strip():
            try:
                return max(0.0, min(60.0, float(value)))
            except Exception:
                pass
    return PROVIDER_DELAY_DEFAULTS.get(provider, 0.0)


def _clean_domain(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r"^https?://", "", value)
    return value.strip("/")


def _default_site_name(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"^https?://", "", value).strip("/")
    host = value.split("/", 1)[0]
    return host[4:] if host.startswith("www.") else host


def _shopify_setting(key: str, default: str = "") -> str:
    cfg = _load_site_config()
    if key in cfg:
        return str(cfg.get(key) or "").strip()
    return os.environ.get(key, default).strip()


def _shopify_generation_context() -> dict:
    public_base = _shopify_setting("SHOPIFY_PUBLIC_BASE_URL").rstrip("/")
    store_domain = _shopify_setting("SHOPIFY_STORE_DOMAIN")
    site_name = _shopify_setting("SHOPIFY_SITE_NAME") or _default_site_name(public_base or store_domain)
    business_context = _shopify_setting("SHOPIFY_BUSINESS_CONTEXT")
    if not business_context:
        business_context = (
            f"Loja virtual Shopify {site_name}. Gere SEO apenas com base na loja, "
            "na categoria/produto e nas informacoes disponiveis na Shopify. "
            "Nao cite marcas, ofertas ou nomes de outras lojas se eles nao aparecerem no item."
        )
    content_guidelines = _shopify_setting("SHOPIFY_CONTENT_GUIDELINES")
    if not content_guidelines:
        content_guidelines = (
            "Escreva em PT-BR, com tom comercial claro e natural. "
            "Nao invente promocoes, frete gratis, autenticidade ou marcas nao informadas. "
            "Evite repetir o nome de outra loja."
        )
    return {
        "site_url": public_base,
        "site_name": site_name,
        "business_context": business_context,
        "content_guidelines": content_guidelines,
    }


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _char_status(value: str, minimum: int, maximum: int) -> str:
    length = len(value or "")
    if length == 0:
        return "missing"
    if length < minimum:
        return "short"
    if length > maximum:
        return "long"
    return "ok"


def _resource_path(resource: dict) -> str:
    handle = resource.get("handle") or ""
    if resource.get("resource_type") == "collection":
        return f"/collections/{handle}"
    return f"/products/{handle}"


def _public_base_url() -> str:
    return (
        _shopify_setting("SHOPIFY_PUBLIC_BASE_URL").rstrip("/")
        or get_site_url().rstrip("/")
    )


def _public_url(resource: dict) -> str:
    if resource.get("onlineStoreUrl"):
        return str(resource["onlineStoreUrl"])
    base = _public_base_url()
    if base:
        return base + _resource_path(resource)
    return _resource_path(resource)


def _change_key(item: dict) -> str:
    return f"{item.get('resource_type')}:{item.get('id')}"


def _matches_urls(item: dict, urls_filter: Iterable[str] | None) -> bool:
    urls = [str(u).rstrip("/") for u in urls_filter or [] if str(u).strip()]
    if not urls:
        return True
    candidates = {
        str(item.get("url") or "").rstrip("/"),
        str(item.get("path") or "").rstrip("/"),
        _resource_path(item).rstrip("/"),
    }
    return any(
        candidate.endswith(url) or url.endswith(candidate)
        for candidate in candidates
        for url in urls
        if candidate
    )


@dataclass
class ShopifyCredentials:
    store_domain: str
    admin_token: str
    client_id: str = ""
    client_secret: str = ""
    api_version: str = DEFAULT_API_VERSION

    @classmethod
    def from_env(cls) -> "ShopifyCredentials":
        return cls(
            store_domain=_clean_domain(_shopify_setting("SHOPIFY_STORE_DOMAIN")),
            admin_token=_shopify_setting("SHOPIFY_ADMIN_TOKEN"),
            client_id=_shopify_setting("SHOPIFY_CLIENT_ID"),
            client_secret=_shopify_setting("SHOPIFY_CLIENT_SECRET"),
            api_version=_shopify_setting("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
            or DEFAULT_API_VERSION,
        )

    def validate(self) -> None:
        if not self.store_domain:
            raise RuntimeError("SHOPIFY_STORE_DOMAIN nao configurado.")
        if not self.admin_token and not (self.client_id and self.client_secret):
            raise RuntimeError(
                "Configure SHOPIFY_CLIENT_ID e SHOPIFY_CLIENT_SECRET "
                "ou informe SHOPIFY_ADMIN_TOKEN."
            )

    def can_refresh_token(self) -> bool:
        return bool(self.client_id and self.client_secret)


def _client_fingerprint(credentials: ShopifyCredentials) -> str:
    text = f"{credentials.store_domain}:{credentials.client_id}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_cached_token(credentials: ShopifyCredentials) -> str:
    if not TOKEN_CACHE_FILE.exists():
        return ""
    try:
        cached = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if cached.get("fingerprint") != _client_fingerprint(credentials):
        return ""
    expires_at = float(cached.get("expires_at") or 0)
    if expires_at <= time.time() + TOKEN_REFRESH_SKEW_SECONDS:
        return ""
    return str(cached.get("access_token") or "")


def _load_cached_scopes(credentials: ShopifyCredentials) -> set[str]:
    if not TOKEN_CACHE_FILE.exists():
        return set()
    try:
        cached = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if cached.get("fingerprint") != _client_fingerprint(credentials):
        return set()
    return {
        scope.strip()
        for scope in str(cached.get("scope") or "").replace(" ", ",").split(",")
        if scope.strip()
    }


def _save_cached_scopes(credentials: ShopifyCredentials, scopes: Iterable[str]) -> None:
    if not TOKEN_CACHE_FILE.exists():
        return
    try:
        cached = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    if cached.get("fingerprint") != _client_fingerprint(credentials):
        return
    cached["scope"] = ",".join(sorted({scope.strip() for scope in scopes if scope.strip()}))
    cached["scope_checked_at"] = _now()
    TOKEN_CACHE_FILE.write_text(json.dumps(cached, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_cached_token(credentials: ShopifyCredentials, payload: dict) -> str:
    access_token = str(payload.get("access_token") or "")
    if not access_token:
        raise RuntimeError("Shopify nao retornou access_token no client_credentials.")
    expires_in = int(payload.get("expires_in") or 86399)
    data = {
        "access_token": access_token,
        "scope": payload.get("scope") or "",
        "expires_at": time.time() + max(60, expires_in),
        "store_domain": credentials.store_domain,
        "fingerprint": _client_fingerprint(credentials),
        "created_at": _now(),
    }
    TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return access_token


def request_admin_token(credentials: ShopifyCredentials) -> str:
    """Exchange Dev Dashboard client credentials for a short-lived Admin API token."""
    if not credentials.can_refresh_token():
        return credentials.admin_token

    cached = _load_cached_token(credentials)
    if cached:
        return cached

    url = f"https://{credentials.store_domain}/admin/oauth/access_token"
    response = requests.post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        preview = response.text[:500].replace("\n", " ")
        if credentials.admin_token:
            return credentials.admin_token
        raise RuntimeError(f"Shopify token HTTP {response.status_code}: {preview}") from exc

    return _save_cached_token(credentials, response.json())


class ShopifyGraphQLClient:
    def __init__(self, credentials: ShopifyCredentials):
        credentials.validate()
        self.credentials = credentials
        self.access_token = request_admin_token(credentials)
        self.granted_scopes = _load_cached_scopes(credentials)
        self.api_version = credentials.api_version
        self.api_versions = self._candidate_api_versions(credentials.api_version)
        self.endpoint = self._endpoint_for(self.api_version)

    def _endpoint_for(self, api_version: str) -> str:
        return (
            f"https://{self.credentials.store_domain}/admin/api/"
            f"{api_version}/graphql.json"
        )

    @staticmethod
    def _candidate_api_versions(preferred: str) -> list[str]:
        configured = os.environ.get(
            "SHOPIFY_API_FALLBACK_VERSIONS",
            "2026-01,2025-10,2025-07,2025-04",
        )
        versions = [preferred or DEFAULT_API_VERSION]
        versions.extend(v.strip() for v in configured.split(",") if v.strip())
        result = []
        for version in versions:
            if version not in result:
                result.append(version)
        return result

    def _post_graphql(self, query: str, variables: dict | None = None) -> requests.Response:
        return requests.post(
            self.endpoint,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": self.access_token,
            },
            json={"query": query, "variables": variables or {}},
            timeout=45,
        )

    def _refresh_access_token(self) -> bool:
        if not self.credentials.can_refresh_token():
            return False
        try:
            if TOKEN_CACHE_FILE.exists():
                TOKEN_CACHE_FILE.unlink()
        except Exception:
            pass
        self.access_token = request_admin_token(self.credentials)
        self.granted_scopes = _load_cached_scopes(self.credentials)
        return bool(self.access_token)

    def _fetch_access_scopes(self) -> set[str]:
        url = f"https://{self.credentials.store_domain}/admin/oauth/access_scopes.json"
        response = requests.get(
            url,
            headers={"X-Shopify-Access-Token": self.access_token},
            timeout=30,
        )
        if response.status_code == 401 and self._refresh_access_token():
            response = requests.get(
                url,
                headers={"X-Shopify-Access-Token": self.access_token},
                timeout=30,
            )
        try:
            response.raise_for_status()
        except requests.HTTPError:
            return set()
        payload = response.json()
        scopes = {
            str(item.get("handle") or item.get("scope") or "").strip()
            for item in payload.get("access_scopes", [])
            if str(item.get("handle") or item.get("scope") or "").strip()
        }
        if scopes:
            self.granted_scopes = scopes
            _save_cached_scopes(self.credentials, scopes)
        return scopes

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        response = self._post_graphql(query, variables)
        if response.status_code == 401 and self._refresh_access_token():
            response = self._post_graphql(query, variables)
        if response.status_code == 404:
            for version in self.api_versions:
                if version == self.api_version:
                    continue
                previous_endpoint = self.endpoint
                self.api_version = version
                self.endpoint = self._endpoint_for(version)
                retry = self._post_graphql(query, variables)
                if retry.status_code != 404:
                    response = retry
                    break
                self.api_version = self.credentials.api_version
                self.endpoint = previous_endpoint
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            preview = response.text[:500].replace("\n", " ")
            if response.status_code == 404:
                raise RuntimeError(
                    "Shopify retornou 404 ao chamar a Admin API. "
                    "Verifique se o app esta instalado na loja, se o dominio "
                    "SHOPIFY_STORE_DOMAIN esta correto e se os escopos Admin API "
                    "incluem read_products/write_products para produtos e colecoes. "
                    f"Resposta: {preview}"
                ) from exc
            raise RuntimeError(f"Shopify HTTP {response.status_code}: {preview}") from exc

        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"Shopify GraphQL errors: {payload['errors']}")

        self._respect_throttle(payload.get("extensions") or {})
        return payload.get("data") or {}

    @staticmethod
    def _respect_throttle(extensions: dict) -> None:
        throttle = ((extensions.get("cost") or {}).get("throttleStatus") or {})
        available = float(throttle.get("currentlyAvailable") or 1000)
        restore_rate = float(throttle.get("restoreRate") or 50)
        if available < 50 and restore_rate > 0:
            time.sleep(min(2.0, (50 - available) / restore_rate))

    def fetch_products(self, limit: int | None = None, query: str | None = None) -> list[dict]:
        self._require_read_products_scope()
        gql = """
        query ShopifySeoProducts($first: Int!, $after: String, $query: String) {
          products(first: $first, after: $after, query: $query) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              title
              handle
              status
              vendor
              productType
              descriptionHtml
              onlineStoreUrl
              seo { title description }
            }
          }
        }
        """
        return self._fetch_connection("products", gql, limit=limit, query=query)

    def fetch_collections(self, limit: int | None = None, query: str | None = None) -> list[dict]:
        self._require_read_products_scope()
        gql = """
        query ShopifySeoCollections($first: Int!, $after: String) {
          collections(first: $first, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              title
              handle
              descriptionHtml
              seo { title description }
            }
          }
        }
        """
        items = self._fetch_connection("collections", gql, limit=limit, query=None, use_query=False)
        if query:
            lowered = query.lower().strip()
            if lowered and ":" not in lowered:
                items = [
                    item for item in items
                    if lowered in str(item.get("title") or "").lower()
                    or lowered in str(item.get("handle") or "").lower()
                ]
        return items

    def _require_read_products_scope(self) -> None:
        if "read_products" in self.granted_scopes:
            return
        self._fetch_access_scopes()
        if "read_products" in self.granted_scopes:
            return
        if self.credentials.can_refresh_token():
            self._refresh_access_token()
            self.granted_scopes = _load_cached_scopes(self.credentials)
            if "read_products" not in self.granted_scopes:
                self._fetch_access_scopes()
            if "read_products" in self.granted_scopes:
                return
        raise RuntimeError(
            "Permissao Shopify ausente: read_products. "
            "Abra o app no painel da Shopify, adicione o escopo Admin API "
            "read_products, mantenha write_products, salve e instale o app novamente. "
            f"Escopos atuais: {', '.join(sorted(self.granted_scopes)) or 'nenhum'}."
        )

    def _fetch_connection(
        self,
        key: str,
        gql: str,
        limit: int | None = None,
        query: str | None = None,
        use_query: bool = True,
    ) -> list[dict]:
        items: list[dict] = []
        after = None
        page_size = min(100, max(1, limit or 100))
        while True:
            variables = {"first": page_size}
            if after is not None:
                variables["after"] = after
            if use_query and query:
                variables["query"] = query
            data = self.graphql(gql, variables)
            conn = data.get(key) or {}
            items.extend(conn.get("nodes") or [])
            if limit and len(items) >= limit:
                return items[:limit]
            page_info = conn.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return items
            after = page_info.get("endCursor")

    def update_product_seo(
        self,
        product_id: str,
        seo_title: str,
        seo_description: str,
        description_html: str = "",
    ) -> dict:
        mutation = """
        mutation ShopifySeoProductUpdate($product: ProductUpdateInput!) {
          productUpdate(product: $product) {
            product { id title handle descriptionHtml seo { title description } }
            userErrors { field message }
          }
        }
        """
        product_input = {
            "id": product_id,
            "seo": {"title": seo_title, "description": seo_description},
        }
        if str(description_html or "").strip():
            product_input["descriptionHtml"] = description_html
        data = self.graphql(
            mutation,
            {"product": product_input},
        )
        return _extract_mutation_result(data, "productUpdate", "product")

    def update_collection_seo(
        self,
        collection_id: str,
        seo_title: str,
        seo_description: str,
        description_html: str = "",
    ) -> dict:
        mutation = """
        mutation ShopifySeoCollectionUpdate($input: CollectionInput!) {
          collectionUpdate(input: $input) {
            collection { id title handle descriptionHtml seo { title description } }
            userErrors { field message }
          }
        }
        """
        collection_input = {
            "id": collection_id,
            "seo": {"title": seo_title, "description": seo_description},
        }
        if str(description_html or "").strip():
            collection_input["descriptionHtml"] = description_html
        data = self.graphql(
            mutation,
            {"input": collection_input},
        )
        return _extract_mutation_result(data, "collectionUpdate", "collection")

    def update_seo(self, change: dict) -> dict:
        proposal = change.get("proposal") or {}
        seo_title = proposal.get("seo_title") or proposal.get("meta_title") or ""
        seo_description = (
            proposal.get("seo_description")
            or proposal.get("meta_description")
            or ""
        )
        description_html = (
            proposal.get("description_html")
            if proposal.get("update_description_html")
            else ""
        )
        if change.get("resource_type") == "collection":
            return self.update_collection_seo(
                change["id"],
                seo_title,
                seo_description,
                description_html or "",
            )
        return self.update_product_seo(
            change["id"],
            seo_title,
            seo_description,
            description_html or "",
        )


def _extract_mutation_result(data: dict, mutation_key: str, object_key: str) -> dict:
    result = data.get(mutation_key) or {}
    errors = result.get("userErrors") or []
    if errors:
        raise RuntimeError("; ".join(
            f"{'.'.join(err.get('field') or [])}: {err.get('message')}"
            for err in errors
        ))
    return result.get(object_key) or {}


def normalize_product(raw: dict) -> dict:
    seo = raw.get("seo") or {}
    return {
        "resource_type": "product",
        "id": raw.get("id", ""),
        "title": raw.get("title", ""),
        "handle": raw.get("handle", ""),
        "status": raw.get("status", ""),
        "vendor": raw.get("vendor", ""),
        "product_type": raw.get("productType", ""),
        "description_html": raw.get("descriptionHtml", "") or "",
        "description_text": _strip_html(raw.get("descriptionHtml", "") or ""),
        "seo_title": seo.get("title") or "",
        "seo_description": seo.get("description") or "",
        "onlineStoreUrl": raw.get("onlineStoreUrl") or "",
    }


def normalize_collection(raw: dict) -> dict:
    seo = raw.get("seo") or {}
    return {
        "resource_type": "collection",
        "id": raw.get("id", ""),
        "title": raw.get("title", ""),
        "handle": raw.get("handle", ""),
        "description_html": raw.get("descriptionHtml", "") or "",
        "description_text": _strip_html(raw.get("descriptionHtml", "") or ""),
        "seo_title": seo.get("title") or "",
        "seo_description": seo.get("description") or "",
        "onlineStoreUrl": raw.get("onlineStoreUrl") or "",
    }


def audit_resource(resource: dict) -> dict:
    title = resource.get("seo_title") or ""
    desc = resource.get("seo_description") or ""
    issues: list[str] = []
    warnings: list[str] = []

    title_status = _char_status(title, TITLE_MIN, TITLE_MAX)
    desc_status = _char_status(desc, DESC_MIN, DESC_MAX)

    if title_status == "missing":
        issues.append("SEO title missing in Shopify")
    elif title_status == "short":
        warnings.append(f"SEO title short ({len(title)} chars)")
    elif title_status == "long":
        warnings.append(f"SEO title long ({len(title)} chars)")

    if desc_status == "missing":
        issues.append("SEO description missing in Shopify")
    elif desc_status == "short":
        warnings.append(f"SEO description short ({len(desc)} chars)")
    elif desc_status == "long":
        warnings.append(f"SEO description long ({len(desc)} chars)")
    if desc.rstrip().endswith(("...", "…")):
        warnings.append("SEO description ends with ellipsis in Shopify")

    text_words = [word for word in resource.get("description_text", "").split() if len(word) > 2]
    if len(text_words) < 40:
        warnings.append(f"Description content thin ({len(text_words)} words)")

    audited = dict(resource)
    audited["url"] = _public_url(resource)
    audited["path"] = _resource_path(resource)
    audited["title_status"] = title_status
    audited["description_status"] = desc_status
    audited["issues"] = issues
    audited["warnings"] = warnings
    audited["needs_optimization"] = bool(issues or warnings)
    return audited


def audit_resources(resources: list[dict]) -> list[dict]:
    audited = [audit_resource(resource) for resource in resources]

    title_map: dict[str, list[int]] = {}
    desc_map: dict[str, list[int]] = {}
    for idx, item in enumerate(audited):
        title = (item.get("seo_title") or "").strip().casefold()
        desc = (item.get("seo_description") or "").strip().casefold()
        if title:
            title_map.setdefault(title, []).append(idx)
        if desc:
            desc_map.setdefault(desc, []).append(idx)

    for idxs in title_map.values():
        if len(idxs) > 1:
            for idx in idxs:
                audited[idx]["warnings"].append("SEO title duplicated in Shopify")
                audited[idx]["needs_optimization"] = True

    for idxs in desc_map.values():
        if len(idxs) > 1:
            for idx in idxs:
                audited[idx]["warnings"].append("SEO description duplicated in Shopify")
                audited[idx]["needs_optimization"] = True

    return audited


def fetch_resources(
    client: ShopifyGraphQLClient,
    resource: str = "all",
    limit: int | None = None,
    query: str | None = None,
) -> list[dict]:
    resources: list[dict] = []
    if resource in ("all", "products"):
        resources.extend(normalize_product(item) for item in client.fetch_products(limit, query))
    if resource in ("all", "collections"):
        resources.extend(normalize_collection(item) for item in client.fetch_collections(limit, query))
    return resources


def _page_for_generation(resource: dict) -> dict:
    description = resource.get("seo_description") or ""
    title = resource.get("seo_title") or resource.get("title") or ""
    words = len([word for word in resource.get("description_text", "").split() if len(word) > 2])
    return {
        "url": resource.get("url") or _public_url(resource),
        "title": title,
        "description": description,
        "h1_texts": [resource.get("title") or ""],
        "meta_keywords": "",
        "word_count": words,
        "issues": resource.get("issues") or [],
        "warnings": resource.get("warnings") or [],
    }


def generate_changes(
    resources: list[dict],
    provider: str | None = None,
    api_key: str | None = None,
    auto_approve: bool = False,
    only_needs: bool = True,
    progress_callback=None,
    change_callback=None,
) -> list[dict]:
    from modules import content_generator

    if provider and not api_key:
        api_key = get_provider_api_key(provider)
    if not provider or not api_key:
        auto_provider, auto_key = get_default_provider()
        provider = provider or auto_provider
        api_key = api_key or auto_key

    if not provider or not api_key:
        raise RuntimeError(
            "Nenhuma chave de IA configurada. Defina GEMINI_API_KEY, "
            "OPENROUTER_API_KEY, GROQ_API_KEY, MISTRAL_API_KEY, ANTHROPIC_API_KEY "
            "ou habilite OLLAMA_ENABLED=1."
        )

    should_clear_context = False
    if not using_runtime_site_config():
        set_runtime_site_config(_shopify_generation_context())
        should_clear_context = True
    try:
        audited = audit_resources(resources)
        targets = [item for item in audited if item.get("needs_optimization") or not only_needs]
        changes: list[dict] = []
        total = len(targets)
        for index, resource in enumerate(targets, start=1):
            if progress_callback:
                progress_callback(index, total, resource)
            description_words = len([
                word for word in str(resource.get("description_text") or "").split()
                if len(word) > 2
            ])
            proposal = content_generator.generate_for_page(
                _page_for_generation(resource),
                gsc_data=None,
                provider=provider,
                api_key=api_key,
            )
            proposal_description_html = proposal.get("description_html") or ""
            update_description_html = bool(
                proposal_description_html.strip()
                and description_words < 40
            )
            change = {
                "id": resource.get("id"),
                "resource_type": resource.get("resource_type"),
                "handle": resource.get("handle"),
                "title": resource.get("title"),
                "url": resource.get("url"),
                "path": resource.get("path"),
                "current": {
                    "seo_title": resource.get("seo_title") or "",
                    "seo_description": resource.get("seo_description") or "",
                    "description_html": resource.get("description_html") or "",
                    "description_words": description_words,
                },
                "proposal": {
                    "seo_title": proposal.get("meta_title") or "",
                    "seo_description": proposal.get("meta_description") or "",
                    "h1": proposal.get("h1") or "",
                    "description_html": proposal_description_html,
                    "update_description_html": update_description_html,
                },
                "provider": proposal.get("_provider") or provider,
                "issues": resource.get("issues") or [],
                "warnings": resource.get("warnings") or [],
                "status": "approved" if auto_approve else "pending_review",
                "generated_at": _now(),
            }
            changes.append(change)
            if change_callback:
                change_callback(index, total, change)
            delay = _provider_delay_seconds(proposal.get("_provider") or provider)
            if delay and index < total:
                time.sleep(delay)
        return changes
    finally:
        if should_clear_context:
            clear_runtime_site_config()


def load_queue(path: Path = QUEUE_FILE) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def save_queue(changes: list[dict], path: Path = QUEUE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_queue(new_changes: list[dict], path: Path = QUEUE_FILE) -> list[dict]:
    existing = load_queue(path)
    index = {_change_key(item): pos for pos, item in enumerate(existing)}
    for change in new_changes:
        key = _change_key(change)
        if key in index:
            old = existing[index[key]]
            if old.get("status") == "published":
                change["previous_status"] = "published"
            existing[index[key]] = change
        else:
            existing.append(change)
    save_queue(existing, path)
    return existing


def approve_queue(
    urls_filter: Iterable[str] | None = None,
    approve_all: bool = False,
    path: Path = QUEUE_FILE,
) -> tuple[int, list[dict]]:
    changes = load_queue(path)
    count = 0
    for item in changes:
        if item.get("status") != "pending_review":
            continue
        if approve_all or _matches_urls(item, urls_filter):
            item["status"] = "approved"
            item["approved_at"] = _now()
            count += 1
    save_queue(changes, path)
    return count, changes


def _selected_approved_changes(changes: list[dict], urls_filter: Iterable[str] | None) -> list[dict]:
    return [
        item
        for item in changes
        if item.get("status") == "approved" and _matches_urls(item, urls_filter)
    ]


def apply_approved_changes(
    client: ShopifyGraphQLClient | None,
    changes: list[dict],
    urls_filter: Iterable[str] | None = None,
    apply: bool = False,
) -> tuple[list[dict], list[dict]]:
    log: list[dict] = []
    selected_keys = {_change_key(item) for item in _selected_approved_changes(changes, urls_filter)}
    updated: list[dict] = []

    for item in changes:
        key = _change_key(item)
        if key not in selected_keys:
            updated.append(item)
            continue

        proposal = item.get("proposal") or {}
        event = {
            "id": item.get("id"),
            "resource_type": item.get("resource_type"),
            "handle": item.get("handle"),
            "url": item.get("url"),
            "seo_title": proposal.get("seo_title"),
            "seo_description": proposal.get("seo_description"),
            "description_html_updated": bool(proposal.get("update_description_html")),
            "at": _now(),
            "dry_run": not apply,
        }

        if not apply:
            log.append({**event, "status": "dry_run"})
            updated.append(item)
            continue

        if client is None:
            raise RuntimeError("Cliente Shopify obrigatorio para publicar com apply=True.")

        try:
            result = client.update_seo(item)
            item = dict(item)
            item["status"] = "published"
            item["published_at"] = _now()
            item["shopify_result"] = result
            log.append({**event, "status": "published"})
        except Exception as exc:
            item = dict(item)
            item["status"] = "error"
            item["error"] = str(exc)
            log.append({**event, "status": "error", "error": str(exc)})
        updated.append(item)

    return updated, log


def append_log(events: list[dict], path: Path = LOG_FILE) -> None:
    if not events:
        return
    existing = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(existing + events, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def print_audit(items: list[dict], top: int = 30) -> None:
    needs = [item for item in items if item.get("needs_optimization")]
    print(f"Shopify SEO audit: {len(items)} item(ns), {len(needs)} precisa(m) de ajuste")
    for item in needs[:top]:
        problems = "; ".join((item.get("issues") or []) + (item.get("warnings") or []))
        print(f"- [{item['resource_type']}] {item.get('path')} | {problems}")
    if len(needs) > top:
        print(f"... {len(needs) - top} item(ns) ocultos. Use --limit/--query para fatiar.")


def print_review(changes: list[dict], top: int = 50) -> None:
    shown = 0
    for item in changes:
        if item.get("status") not in {"pending_review", "approved", "error"}:
            continue
        proposal = item.get("proposal") or {}
        current = item.get("current") or {}
        shown += 1
        print(f"\n[{item.get('status')}] {item.get('resource_type')} {item.get('path')}")
        print(f"  atual title : {current.get('seo_title') or '(vazio)'}")
        print(f"  novo title  : {proposal.get('seo_title') or '(vazio)'}")
        print(f"  atual desc  : {(current.get('seo_description') or '(vazio)')[:180]}")
        print(f"  nova desc   : {(proposal.get('seo_description') or '(vazio)')[:180]}")
        if proposal.get("update_description_html"):
            content_preview = _strip_html(proposal.get("description_html") or "")
            print(f"  nova descricao Shopify: {content_preview[:180]}")
        if shown >= top:
            break
    if shown == 0:
        print("Nenhum item na fila Shopify.")


def _build_client() -> ShopifyGraphQLClient:
    return ShopifyGraphQLClient(ShopifyCredentials.from_env())


def _cmd_audit(args) -> int:
    client = _build_client()
    resources = fetch_resources(client, args.resource, args.limit, args.query)
    audited = audit_resources(resources)
    if args.urls:
        audited = [item for item in audited if _matches_urls(item, args.urls)]
    print_audit(audited, top=args.top)
    return 0


def _cmd_generate(args) -> int:
    client = _build_client()
    resources = fetch_resources(client, args.resource, args.limit, args.query)
    if args.urls:
        resources = [item for item in audit_resources(resources) if _matches_urls(item, args.urls)]
    audited = audit_resources(resources)
    targets = [item for item in audited if item.get("needs_optimization") or args.force]
    print(
        f"Encontrados {len(resources)} recurso(s) Shopify; "
        f"{len(targets)} precisa(m) de sugestao.",
        flush=True,
    )

    saved_queue: list[dict] = []

    def progress(index: int, total: int, resource: dict) -> None:
        print(
            f"Gerando {index}/{total}: [{resource.get('resource_type')}] "
            f"{resource.get('path') or resource.get('handle')}",
            flush=True,
        )

    def save_each(index: int, total: int, change: dict) -> None:
        nonlocal saved_queue
        saved_queue = upsert_queue([change])
        print(
            f"  ok {index}/{total} salvo na fila Shopify: "
            f"{change.get('path') or change.get('handle')}",
            flush=True,
        )

    changes = generate_changes(
        resources,
        provider=args.provider,
        api_key=args.api_key,
        auto_approve=args.auto_approve,
        only_needs=not args.force,
        progress_callback=progress,
        change_callback=save_each,
    )
    queue = saved_queue or (upsert_queue(changes) if changes else load_queue())
    print(f"Geradas {len(changes)} sugestao(oes) Shopify.")
    print(f"Fila Shopify: {QUEUE_FILE}")
    print(f"Pendentes de revisao: {len([i for i in queue if i.get('status') == 'pending_review'])}")
    print(f"Aprovadas: {len([i for i in queue if i.get('status') == 'approved'])}")
    return 0


def _cmd_review(args) -> int:
    changes = load_queue()
    print_review(changes, top=args.top)
    print(f"\nFila Shopify: {QUEUE_FILE}")
    return 0


def _cmd_approve(args) -> int:
    if not args.all and not args.urls:
        print("Use --all ou --urls para aprovar itens explicitamente.")
        return 2
    count, _ = approve_queue(args.urls, approve_all=args.all)
    print(f"Aprovados {count} item(ns) na fila Shopify.")
    return 0


def _cmd_apply(args) -> int:
    changes = load_queue()
    selected = _selected_approved_changes(changes, args.urls)
    if not selected:
        print("Nenhum item aprovado para publicar.")
        return 0
    if not args.apply:
        print("DRY RUN Shopify. Nada sera publicado. Use --apply para publicar.")
    client = _build_client() if args.apply else None
    updated, events = apply_approved_changes(client, changes, args.urls, apply=args.apply)
    if args.apply:
        save_queue(updated)
        append_log(events)
    for event in events:
        print(
            f"- {event['status']} [{event['resource_type']}] "
            f"{event.get('url') or event.get('handle')}: {event.get('seo_title')}"
        )
    if args.apply:
        ok = len([event for event in events if event.get("status") == "published"])
        err = len([event for event in events if event.get("status") == "error"])
        print(f"Publicado: {ok}; erros: {err}. Log: {LOG_FILE}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shopify SEO automation")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_fetch_flags(p):
        p.add_argument("--resource", choices=["all", "products", "collections"], default="all")
        p.add_argument("--limit", type=int, default=100, help="Limite por tipo de recurso")
        p.add_argument("--query", default=None, help="Query Shopify Admin, ex: status:active")
        p.add_argument("--urls", nargs="+", default=None, help="Filtra por paths/URLs especificos")
        p.add_argument("--top", type=int, default=30)

    audit = sub.add_parser("audit", help="Ler Shopify e listar problemas de SEO")
    add_fetch_flags(audit)
    audit.set_defaults(func=_cmd_audit)

    generate = sub.add_parser("generate", help="Gerar sugestoes de SEO e salvar fila Shopify")
    add_fetch_flags(generate)
    generate.add_argument("--provider", choices=["openrouter", "gemini", "groq", "mistral", "anthropic"], default=None)
    generate.add_argument("--api-key", default=None)
    generate.add_argument("--force", action="store_true", help="Gera mesmo para itens sem alerta")
    generate.add_argument("--auto-approve", action="store_true", help="Marca sugestoes como aprovadas, mas ainda exige apply")
    generate.set_defaults(func=_cmd_generate)

    review = sub.add_parser("review", help="Mostrar fila Shopify para revisao")
    review.add_argument("--top", type=int, default=50)
    review.set_defaults(func=_cmd_review)

    approve = sub.add_parser("approve", help="Aprovar itens pendentes da fila Shopify")
    approve.add_argument("--all", action="store_true")
    approve.add_argument("--urls", nargs="+", default=None)
    approve.set_defaults(func=_cmd_approve)

    apply_cmd = sub.add_parser("apply", help="Publicar itens aprovados na Shopify")
    apply_cmd.add_argument("--urls", nargs="+", default=None)
    apply_cmd.add_argument("--apply", action="store_true", help="Confirma escrita real na Shopify")
    apply_cmd.set_defaults(func=_cmd_apply)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"Erro Shopify SEO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
