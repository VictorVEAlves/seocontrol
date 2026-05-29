"""
Publicador automático para o painel da Bagy via Playwright.

Lê a fila de mudanças pendentes (pending_changes.json),
abre o Chrome, faz login na Bagy e aplica cada mudança.

Uso:
  python run.py --module publish               # publica tudo que está pendente
  python run.py --module publish --urls /categoria  # publica só essa URL
  python run.py --module publish --dry-run     # mostra o que faria, sem publicar
"""

import json
import time
from pathlib import Path
from urllib.parse import urlparse

from config import get_scoped_runtime_file

PENDING_FILE = get_scoped_runtime_file("pending_changes.json", "publishing")
LOG_FILE     = get_scoped_runtime_file("publish_log.json", "publishing")

# URL base do painel da Bagy — ajuste se necessário
BAGY_ADMIN_URL  = "https://app.bagy.com.br"
BAGY_LOGIN_URL  = f"{BAGY_ADMIN_URL}/login"
BAGY_CATS_URL   = f"{BAGY_ADMIN_URL}/categories"

# Seletores do painel Bagy (mapeados pelo print)
SEL = {
    "login_email":    'input[type="email"], input[name="email"], input[placeholder*="e-mail" i]',
    "login_password": 'input[type="password"]',
    "login_button":   'button[type="submit"]',

    # Listagem de categorias — campo de busca
    "cat_search":     'input[placeholder*="buscar" i], input[placeholder*="pesquisar" i], input[type="search"]',
    "cat_edit_link":  'a[href*="/categories/"][href*="/edit"], a[href*="categoria"]',

    # Formulário de edição
    "meta_title":     'input[name="meta_title"], input[id*="meta_title"], input[placeholder*="meta title" i]',
    "meta_desc":      'input[name="meta_description"], textarea[name="meta_description"], '
                      'input[id*="meta_desc"], textarea[id*="meta_desc"]',
    "meta_keywords":  'input[name="meta_keywords"], input[id*="meta_keywords"]',

    # Editor rich text — botão HTML (<>)
    "rte_html_btn":   'button[title="HTML"], button[data-name="source"], .ql-code-block, '
                      'button:has-text("<>"), [class*="source"]',
    "rte_textarea":   'textarea.ql-source, textarea[class*="source"], .ProseMirror, [contenteditable="true"]',

    # Salvar
    "save_btn":       'button[type="submit"]:has-text("Salvar"), button:has-text("Salvar")',
}


class BagyPublisher:
    def __init__(self, email: str, password: str, headless: bool = False):
        self.email    = email
        self.password = password
        self.headless = headless
        self.page     = None
        self.browser  = None
        self.log      = []

    def start(self):
        from playwright.sync_api import sync_playwright
        self._pw      = sync_playwright().__enter__()
        self.browser  = self._pw.chromium.launch(headless=self.headless, slow_mo=300)
        self.page     = self.browser.new_page()
        self.page.set_viewport_size({"width": 1440, "height": 900})

    def stop(self):
        if self.browser:
            self.browser.close()
        if hasattr(self, "_pw"):
            self._pw.__exit__(None, None, None)

    def login(self):
        print("  Abrindo painel Bagy...")
        self.page.goto(BAGY_LOGIN_URL, wait_until="networkidle")
        self.page.fill(SEL["login_email"],    self.email)
        self.page.fill(SEL["login_password"], self.password)
        self.page.click(SEL["login_button"])
        self.page.wait_for_url(f"{BAGY_ADMIN_URL}/**", timeout=15000)
        print("  Login realizado.")

    def _find_category_edit_url(self, slug: str) -> str:
        """Navigate to categories list and find the edit URL for a slug."""
        self.page.goto(BAGY_CATS_URL, wait_until="networkidle")
        time.sleep(1)

        # Try searching
        search = self.page.query_selector(SEL["cat_search"])
        if search:
            search.fill(slug.replace("-", " "))
            time.sleep(1.5)

        # Find edit link matching the slug
        links = self.page.query_selector_all("a[href*='/edit']")
        for link in links:
            href = link.get_attribute("href") or ""
            text = link.inner_text().lower()
            if slug.replace("-", " ") in text or slug in href:
                return BAGY_ADMIN_URL + href if href.startswith("/") else href

        # Fallback: click first edit link on page
        if links:
            href = links[0].get_attribute("href") or ""
            return BAGY_ADMIN_URL + href if href.startswith("/") else href

        return ""

    def _fill_seo_fields(self, change: dict):
        """Fill meta title, description and keywords inputs."""
        p = self.page

        if change.get("meta_title"):
            field = p.query_selector(SEL["meta_title"])
            if field:
                field.triple_click()
                field.fill(change["meta_title"])

        if change.get("meta_description"):
            field = p.query_selector(SEL["meta_desc"])
            if field:
                field.triple_click()
                field.fill(change["meta_description"])

        if change.get("meta_keywords"):
            field = p.query_selector(SEL["meta_keywords"])
            if field:
                field.triple_click()
                field.fill(change["meta_keywords"])

    def _fill_description(self, html: str):
        """Paste HTML into the rich text editor via the <> source button."""
        p = self.page

        # Try clicking the HTML source button
        btn = p.query_selector(SEL["rte_html_btn"])
        if btn:
            btn.click()
            time.sleep(0.5)
            # Find the raw textarea that appears
            ta = p.query_selector(SEL["rte_textarea"])
            if ta:
                p.evaluate("el => { el.value = ''; }", ta)
                ta.fill(html)
                btn.click()  # switch back to visual mode
                time.sleep(0.5)
                return

        # Fallback: contenteditable div
        editor = p.query_selector('[contenteditable="true"]')
        if editor:
            editor.click()
            p.keyboard.press("Control+A")
            p.keyboard.type(html)

    def publish_change(self, change: dict, dry_run: bool = False) -> bool:
        url_path = change.get("_url", "")
        slug     = url_path.strip("/").split("/")[0]

        print(f"\n  Publicando: {url_path}")

        if dry_run:
            print(f"     [DRY RUN] meta_title    : {change.get('meta_title','')}")
            print(f"     [DRY RUN] meta_desc      : {change.get('meta_description','')[:60]}...")
            print(f"     [DRY RUN] meta_keywords  : {change.get('meta_keywords','')[:60]}...")
            return True

        try:
            edit_url = self._find_category_edit_url(slug)
            if not edit_url:
                print(f"   x  Categoria '{slug}' nao encontrada no painel.")
                return False

            self.page.goto(edit_url, wait_until="networkidle")
            time.sleep(1)

            # Fill fields
            self._fill_seo_fields(change)

            if change.get("description_html"):
                self._fill_description(change["description_html"])

            # Save
            save = self.page.query_selector(SEL["save_btn"])
            if save:
                save.click()
                self.page.wait_for_load_state("networkidle")
                print(f"   ok Salvo: {url_path}")
                self._log(url_path, "success")
                return True
            else:
                print(f"   x  Botao Salvar nao encontrado.")
                return False

        except Exception as e:
            print(f"   x  Erro ao publicar {url_path}: {e}")
            self._log(url_path, "error", str(e))
            return False

    def _log(self, url: str, status: str, error: str = ""):
        from datetime import datetime
        self.log.append({
            "url": url, "status": status,
            "error": error, "at": datetime.now().isoformat()
        })

    def save_log(self):
        existing = []
        if LOG_FILE.exists():
            try:
                existing = json.loads(LOG_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing.extend(self.log)
        LOG_FILE.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def run(email: str, password: str, urls_filter: list = None,
        dry_run: bool = False, headless: bool = False):
    """
    Main entry point. Reads pending queue and publishes to Bagy.
    """
    if not PENDING_FILE.exists():
        print("  Nenhuma mudanca pendente. Rode --module generate primeiro.")
        return

    pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    to_publish = [p for p in pending if p.get("status") == "pending"]

    if urls_filter:
        to_publish = [p for p in to_publish
                      if any(u in p.get("_url", "") for u in urls_filter)]

    if not to_publish:
        print("  Nenhum item pendente para publicar.")
        return

    print(f"  {len(to_publish)} item(ns) para publicar{'  [DRY RUN]' if dry_run else ''}:")
    for p in to_publish:
        print(f"     - {p['_url']}")

    if dry_run:
        pub = BagyPublisher(email, password, headless)
        for change in to_publish:
            pub.publish_change(change, dry_run=True)
        return

    pub = BagyPublisher(email, password, headless=headless)
    pub.start()
    try:
        pub.login()
        for change in to_publish:
            ok = pub.publish_change(change)
            if ok:
                # Mark as published in queue
                from modules.content_generator import mark_published
                mark_published(change["_url"])
    finally:
        pub.save_log()
        pub.stop()

    ok_count  = len([l for l in pub.log if l["status"] == "success"])
    err_count = len([l for l in pub.log if l["status"] == "error"])
    print(f"\n  Concluido: {ok_count} publicados, {err_count} erros.")
    print(f"  Log salvo em: {LOG_FILE}")
