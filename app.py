import html
import json as _json_mod
import os
import queue as _queue_mod
import subprocess
import sys
import threading
import uuid
from collections import OrderedDict
from urllib.parse import quote

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, request, session, stream_with_context, url_for
from supabase import Client, create_client

from config import (disable_broken_local_proxy, BASE_DIR,
                    get_site_url, get_gsc_property, get_site_name, save_site_config)

load_dotenv()
disable_broken_local_proxy()

# Force UTF-8 on stdout/stderr — prints with → á é ç must never raise on Windows
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "seo-audit-local-dev-2024")

# Allow OAuth over plain HTTP in local development
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

# ── Supabase singleton ────────────────────────────────────────────────────────

_supabase: Client | None = None


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
        _supabase = create_client(os.environ["SUPABASE_URL"], key)
    return _supabase


# ── Tool job registry (capped to avoid memory growth) ────────────────────────

MAX_TOOL_JOBS = 100
TOOL_JOBS: OrderedDict = OrderedDict()
_jobs_lock = threading.Lock()


def _register_job(job_id: str, data: dict) -> None:
    with _jobs_lock:
        TOOL_JOBS[job_id] = data
        while len(TOOL_JOBS) > MAX_TOOL_JOBS:
            TOOL_JOBS.popitem(last=False)


def _update_job(job_id: str, updates: dict) -> None:
    with _jobs_lock:
        if job_id in TOOL_JOBS:
            TOOL_JOBS[job_id].update(updates)


def _get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        return dict(TOOL_JOBS.get(job_id) or {}) or None


# ── Allowed modules ───────────────────────────────────────────────────────────

ALLOWED_TOOL_MODULES = {
    "blog-ideas":      "Ideias de blog por query",
    "ai-insights":     "Insights estrategicos com Gemini",
    "onpage":          "Auditoria on-page",
    "gsc":             "Analise GSC",
    "gsc-api":         "Tendencias GSC ao vivo (API)",
    "doctor":          "Diagnostico do ambiente",
    "monitor":         "Monitor operacional",
    "broken-links":    "Links quebrados",
    "sitemap":         "Sitemap e robots.txt",
    "duplicates":      "Conteudo duplicado",
    "indexability":    "Indexabilidade das paginas",
    "keyword-tracker": "Rastreamento de posicoes por keyword",
    "schema-check":    "Auditoria de schema markup",
    "cannibalization": "Canibalizacao de keywords",
}

AI_PROVIDERS = ["auto", "openrouter", "groq", "gemini", "mistral", "anthropic"]
AI_TOOL_MODULES = {"blog-ideas", "ai-analysis", "generate", "suggest"}
TOP_TOOL_MODULES = {"blog-ideas", "ai-analysis", "suggest"}
URL_TOOL_MODULES = {"onpage", "ai-analysis", "blog-ideas", "monitor"}
MAX_PAGES_TOOL_MODULES = {"monitor", "broken-links"}
GSC_FOLDER_MODULES = {"monitor", "ai-analysis", "generate", "suggest", "regression", "backlog", "gsc"}

PAGE_SIZE = 50


# ── Helpers ───────────────────────────────────────────────────────────────────

def esc(value) -> str:
    return html.escape(str(value or ""))


def fetch_count(supabase: Client, table: str, **filters) -> int:
    query = supabase.table(table).select("id", count="exact")
    for field, value in filters.items():
        query = query.eq(field, value)
    response = query.limit(1).execute()
    return response.count or 0


def _split_urls(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").replace(",", " ").split() if item.strip()]


def _clean_output(value: str) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def _mask_key(key: str) -> str:
    """Show first 5 + last 3 chars of an API key, rest masked."""
    if not key:
        return ""
    if len(key) <= 10:
        return "••••••••"
    return key[:5] + "••••••••••" + key[-3:]


def _update_env_file(key: str, value: str) -> None:
    """Update or add key=value in .env and refresh os.environ + config module."""
    env_path = BASE_DIR / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    updated = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Match uncommented KEY= lines only
        if stripped.startswith(f"{key}=") and not stripped.startswith("#"):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value
    # Refresh config module constants so the running process picks up new keys immediately
    try:
        import config as _cfg
        if hasattr(_cfg, key):
            setattr(_cfg, key, value)
        if hasattr(_cfg, "PROVIDER_API_KEYS"):
            _provider_map = {
                "GEMINI_API_KEY":    "gemini",
                "GROQ_API_KEY":      "groq",
                "MISTRAL_API_KEY":   "mistral",
                "ANTHROPIC_API_KEY": "anthropic",
                "OPENROUTER_API_KEY": "openrouter",
            }
            if key in _provider_map:
                _cfg.PROVIDER_API_KEYS[_provider_map[key]] = value
    except Exception:
        pass


def _pagination_html(page: int, total: int, page_size: int, base_url: str) -> str:
    if total <= page_size:
        return ""
    total_pages = (total + page_size - 1) // page_size
    sep = "&amp;" if "?" in base_url else "?"
    prev_url = f"{base_url}{sep}page={page - 1}" if page > 1 else ""
    next_url = f"{base_url}{sep}page={page + 1}" if page < total_pages else ""
    prev_btn = f'<a class="pg-btn" href="{prev_url}">&#8592; Anterior</a>' if prev_url else '<span class="pg-btn disabled">&#8592; Anterior</span>'
    next_btn = f'<a class="pg-btn" href="{next_url}">Próxima &#8594;</a>' if next_url else '<span class="pg-btn disabled">Próxima &#8594;</span>'
    return f'<div class="pagination">{prev_btn}<span class="pg-info">Página {page} de {total_pages} &middot; {total} itens</span>{next_btn}</div>'


# ── Design system ─────────────────────────────────────────────────────────────

def styles() -> str:
    return """<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    :root {
      --brand:       #8f1d2c;
      --brand-dark:  #6d1521;
      --brand-light: #fdf2f3;
      --brand-mid:   #f5d8db;
      --nav-bg:      #0f1723;
      --nav-border:  #1e2d3d;
      --ink:         #0f172a;
      --ink-mid:     #334155;
      --muted:       #64748b;
      --line:        #e2e8f0;
      --line-light:  #f1f5f9;
      --canvas:      #f8fafc;
      --panel:       #ffffff;
      --ok:          #16a34a;
      --ok-bg:       #dcfce7;
      --warn:        #d97706;
      --warn-bg:     #fef3c7;
      --bad:         #dc2626;
      --bad-bg:      #fee2e2;
      --info:        #2563eb;
      --info-bg:     #dbeafe;
      --shadow-sm:   0 1px 3px rgba(15,23,42,.06), 0 1px 2px rgba(15,23,42,.04);
      --shadow-md:   0 4px 12px rgba(15,23,42,.08), 0 2px 4px rgba(15,23,42,.04);
      --shadow-lg:   0 10px 30px rgba(15,23,42,.10), 0 4px 8px rgba(15,23,42,.06);
      --radius:      10px;
      --radius-sm:   6px;
      --radius-lg:   14px;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html { font-size: 14px; }
    body {
      font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
      color: var(--ink);
      background: var(--canvas);
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }

    /* ── Layout ── */
    .layout { display: flex; min-height: 100vh; }
    .sidebar {
      width: 232px;
      flex-shrink: 0;
      background: var(--nav-bg);
      border-right: 1px solid var(--nav-border);
      display: flex;
      flex-direction: column;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow-y: auto;
    }
    .sidebar-brand {
      padding: 18px 16px 14px;
      border-bottom: 1px solid var(--nav-border);
    }
    .sidebar-brand .logo {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .sidebar-brand .logo-icon {
      width: 34px; height: 34px;
      background: var(--brand);
      border-radius: 9px;
      display: flex; align-items: center; justify-content: center;
      font-weight: 800; color: #fff; font-size: 13px; flex-shrink: 0;
      box-shadow: 0 2px 8px rgba(143,29,44,.4);
    }
    .sidebar-brand .logo-text { color: #f1f5f9; font-weight: 700; font-size: 14px; line-height: 1.2; }
    .sidebar-brand .logo-sub { color: #64748b; font-size: 11px; margin-top: 1px; }
    .sidebar-env {
      margin: 10px 0 0;
      padding: 4px 8px;
      background: rgba(255,255,255,.05);
      border: 1px solid rgba(255,255,255,.07);
      border-radius: 6px;
      color: #64748b;
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: .06em;
      display: inline-block;
    }
    .sidebar-cta {
      margin: 12px 14px 4px;
    }
    .sidebar-cta a {
      display: flex;
      align-items: center;
      gap: 7px;
      background: var(--brand);
      color: #fff !important;
      text-decoration: none;
      padding: 8px 12px;
      border-radius: 8px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: .02em;
      transition: background .15s, opacity .15s;
      box-shadow: 0 2px 6px rgba(143,29,44,.35);
    }
    .sidebar-cta a:hover { background: #a82234; opacity: 1; text-decoration: none; }
    .sidebar-cta svg { flex-shrink: 0; opacity: 1; }
    .nav-section { padding: 10px 10px 4px; }
    .nav-section + .nav-section { border-top: 1px solid rgba(255,255,255,.05); margin-top: 2px; padding-top: 12px; }
    .nav-label { color: #374151; font-size: 9.5px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; padding: 0 8px; margin-bottom: 3px; }
    .nav a {
      display: flex;
      align-items: center;
      gap: 9px;
      color: #94a3b8;
      text-decoration: none;
      padding: 7px 10px;
      border-radius: 7px;
      font-size: 13px;
      font-weight: 500;
      transition: background .12s, color .12s;
      border-left: 2px solid transparent;
    }
    .nav a:hover { background: rgba(255,255,255,.06); color: #cbd5e1; text-decoration: none; border-left-color: rgba(255,255,255,.12); }
    .nav a.active { background: rgba(143,29,44,.2); color: #fca5a5; font-weight: 600; border-left-color: var(--brand); }
    .nav a.active .nav-icon { color: #fb7185; opacity: 1; }
    .nav-icon { width: 15px; height: 15px; flex-shrink: 0; opacity: .55; }
    .nav a.active .nav-icon { opacity: 1; }
    .nav a:hover .nav-icon { opacity: .8; }
    .nav-badge {
      margin-left: auto;
      font-size: 10px;
      font-weight: 700;
      padding: 1px 6px;
      border-radius: 10px;
      background: rgba(220,38,38,.25);
      color: #fca5a5;
    }
    .sidebar-footer {
      margin-top: auto;
      padding: 10px 14px 14px;
      border-top: 1px solid rgba(255,255,255,.05);
    }
    .main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
    .topbar {
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      padding: 14px 28px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      position: sticky;
      top: 0;
      z-index: 10;
      box-shadow: var(--shadow-sm);
    }
    .topbar-title { font-size: 16px; font-weight: 700; color: var(--ink); }
    .topbar-sub { font-size: 12px; color: var(--muted); margin-top: 1px; }
    .content { padding: 24px 28px 48px; max-width: 1380px; }

    /* ── Typography ── */
    h1 { font-size: 22px; font-weight: 800; }
    h2 { font-size: 17px; font-weight: 700; margin-bottom: 12px; }
    h3 { font-size: 14px; font-weight: 700; }
    p { color: var(--ink-mid); }
    a { color: var(--brand); text-decoration: none; font-weight: 600; }
    a:hover { text-decoration: underline; }
    code { background: var(--line-light); padding: 2px 6px; border-radius: 4px; font-size: 12px; font-family: 'Fira Code', monospace; color: var(--ink-mid); }

    /* ── KPI Cards ── */
    .kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin-bottom: 28px; }
    .kpi {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 18px 20px;
      box-shadow: var(--shadow-sm);
      display: flex;
      flex-direction: column;
      gap: 6px;
      transition: box-shadow .15s;
    }
    .kpi:hover { box-shadow: var(--shadow-md); }
    .kpi-top { display: flex; align-items: flex-start; justify-content: space-between; }
    .kpi-icon {
      width: 36px; height: 36px;
      border-radius: 8px;
      display: flex; align-items: center; justify-content: center;
      font-size: 16px;
      flex-shrink: 0;
    }
    .kpi-value { font-size: 32px; font-weight: 800; color: var(--ink); line-height: 1; }
    .kpi-label { font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .03em; }

    /* ── AI Insights ── */
    .insights-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
    .insight-card {
      background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
      padding: 18px 20px; box-shadow: var(--shadow-sm);
    }
    .insight-card h3 { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin-bottom: 12px; }
    .alert-item { display: flex; gap: 10px; padding: 10px 12px; border-radius: var(--radius-sm); margin-bottom: 8px; border: 1px solid var(--line); background: var(--line-light); }
    .alert-item.alta   { border-color: #fca5a5; background: #fff1f2; }
    .alert-item.media  { border-color: #fcd34d; background: #fffbeb; }
    .alert-item.baixa  { border-color: #86efac; background: #f0fdf4; }
    .alert-urgency { font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: .06em; white-space: nowrap; margin-top: 2px; }
    .alert-title   { font-size: 13px; font-weight: 700; color: var(--ink); margin-bottom: 3px; }
    .alert-desc    { font-size: 12px; color: var(--ink-mid); line-height: 1.4; }
    .summary-box   { background: linear-gradient(135deg, #0f1723 0%, #1e2d3d 100%); color: #e2e8f0; border-radius: var(--radius); padding: 20px 22px; margin-bottom: 20px; }
    .summary-box .ai-label { font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: #64748b; margin-bottom: 8px; }
    .summary-box p { color: #cbd5e1; font-size: 14px; line-height: 1.6; margin: 0; }
    .brand-score { display: flex; align-items: center; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--line); }
    .brand-score:last-child { border-bottom: none; }
    .score-bar-wrap { flex: 1; background: var(--line); border-radius: 4px; height: 6px; overflow: hidden; }
    .score-bar { height: 100%; border-radius: 4px; background: var(--brand); transition: width .3s; }
    .score-bar.high { background: var(--ok); }
    .score-bar.mid  { background: var(--warn); }
    .score-bar.low  { background: var(--bad); }
    .snippet-card { background: var(--line-light); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 14px; margin-bottom: 10px; }
    .snippet-url  { font-size: 12px; font-weight: 700; color: var(--brand); margin-bottom: 6px; }
    .snippet-problem { font-size: 12px; color: var(--bad); margin-bottom: 8px; }
    .snippet-sugg { background: #fff; border: 1px solid var(--line); border-radius: 4px; padding: 8px 10px; margin-bottom: 6px; }
    .snippet-sugg label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); display: block; margin-bottom: 3px; }
    .snippet-sugg span  { font-size: 13px; color: var(--ink); line-height: 1.4; }
    .ai-badge { display: inline-flex; align-items: center; gap: 5px; background: linear-gradient(90deg, #4f46e5, #7c3aed); color: #fff; border-radius: 20px; padding: 3px 10px; font-size: 11px; font-weight: 700; }
    .content-gap-item { padding: 10px 0; border-bottom: 1px solid var(--line-light); }
    .content-gap-item:last-child { border-bottom: none; }
    .gap-type { font-size: 10px; font-weight: 700; text-transform: uppercase; color: var(--muted); letter-spacing: .05em; }
    .gap-title { font-size: 13px; font-weight: 700; color: var(--ink); margin: 3px 0; }
    .gap-rationale { font-size: 12px; color: var(--ink-mid); }
    @media (max-width: 1000px) { .insights-grid { grid-template-columns: 1fr; } }

    /* ── Panels ── */
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 20px;
      box-shadow: var(--shadow-sm);
    }
    .panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; gap: 12px; }
    .panel-head h2 { margin-bottom: 0; }
    .notice {
      background: var(--panel);
      border: 1px solid var(--line);
      border-left: 4px solid var(--brand);
      border-radius: var(--radius);
      padding: 20px 22px;
      max-width: 720px;
    }

    /* ── Tables ── */
    .table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); box-shadow: var(--shadow-sm); }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    thead { position: sticky; top: 0; z-index: 1; }
    th {
      background: var(--line-light);
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .05em;
      padding: 10px 14px;
      text-align: left;
      border-bottom: 1px solid var(--line);
      white-space: nowrap;
    }
    td { padding: 11px 14px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; color: var(--ink-mid); }
    tr:last-child td { border-bottom: none; }
    tbody tr:hover td { background: #fafbfc; }

    /* ── Badges ── */
    .badge { display: inline-flex; align-items: center; gap: 4px; border-radius: 20px; padding: 3px 9px; font-size: 11px; font-weight: 700; white-space: nowrap; }
    .badge-high   { background: var(--bad-bg);  color: var(--bad);  }
    .badge-medium { background: var(--warn-bg); color: var(--warn); }
    .badge-low    { background: var(--ok-bg);   color: var(--ok);   }
    .badge-info   { background: var(--info-bg); color: var(--info); }
    .badge-gray   { background: var(--line-light); color: var(--muted); }
    .badge-brand  { background: var(--brand-light); color: var(--brand); }
    .status-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex-shrink: 0; }

    /* ── Buttons ── */
    .btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 7px 14px;
      border-radius: var(--radius-sm);
      font-size: 13px;
      font-weight: 600;
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink-mid);
      cursor: pointer;
      text-decoration: none;
      transition: all .12s;
      white-space: nowrap;
    }
    .btn:hover { border-color: var(--ink-mid); color: var(--ink); text-decoration: none; }
    .btn-primary { background: var(--brand); color: #fff; border-color: var(--brand); }
    .btn-primary:hover { background: var(--brand-dark); border-color: var(--brand-dark); color: #fff; }
    .btn-primary:disabled { opacity: .6; cursor: wait; }
    .btn-ghost { border-color: transparent; background: transparent; }
    .btn-ghost:hover { background: var(--line-light); border-color: var(--line); }
    .btn-sm { padding: 4px 10px; font-size: 12px; }
    .btn-danger { color: var(--bad); border-color: var(--bad-bg); }
    .btn-danger:hover { background: var(--bad-bg); border-color: var(--bad); }
    form.inline { display: inline; }

    /* ── Filters ── */
    .filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; align-items: center; }
    .filters input, .filters select {
      padding: 8px 12px;
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      font-size: 13px;
      background: var(--panel);
      color: var(--ink);
      outline: none;
      transition: border-color .12s;
    }
    .filters input:focus, .filters select:focus { border-color: var(--brand); box-shadow: 0 0 0 3px rgba(143,29,44,.1); }

    /* ── Tools panel ── */
    .tool-grid { display: grid; grid-template-columns: 360px 1fr; gap: 18px; align-items: start; }
    .field { display: grid; gap: 5px; margin-bottom: 14px; }
    .field label { font-size: 12px; font-weight: 700; color: var(--ink-mid); text-transform: uppercase; letter-spacing: .03em; }
    .field input, .field select {
      padding: 9px 12px;
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      font-size: 13px;
      background: var(--panel);
      color: var(--ink);
      outline: none;
      width: 100%;
      transition: border-color .12s;
    }
    .field input:focus, .field select:focus { border-color: var(--brand); box-shadow: 0 0 0 3px rgba(143,29,44,.1); }
    .checks { display: grid; gap: 10px; margin: 14px 0; }
    .checks label { display: flex; gap: 9px; align-items: center; font-size: 13px; color: var(--ink-mid); cursor: pointer; }
    .checks input[type=checkbox] { width: 15px; height: 15px; accent-color: var(--brand); cursor: pointer; }
    .output {
      background: #0d1117;
      color: #c9d1d9;
      border-radius: var(--radius);
      padding: 16px 18px;
      overflow: auto;
      min-height: 440px;
      white-space: pre-wrap;
      line-height: 1.5;
      font-family: 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
      font-size: 12.5px;
    }
    .job-banner {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
      padding: 11px 14px;
      border-radius: var(--radius-sm);
      border: 1px solid var(--line);
      background: var(--line-light);
      font-size: 13px;
    }
    .job-running  { border-color: #bfdbfe; background: #eff6ff; color: #1d4ed8; }
    .job-completed{ border-color: #bbf7d0; background: #f0fdf4; color: #15803d; }
    .job-failed   { border-color: #fecaca; background: #fef2f2; color: #b91c1c; }
    .spinner { width: 13px; height: 13px; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: spin .7s linear infinite; display: inline-block; vertical-align: middle; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .is-hidden { display: none !important; }

    /* ── Kanban ── */
    .kanban { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; align-items: start; margin-top: 4px; }
    .lane {
      background: var(--line-light);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      min-height: 500px;
      padding: 12px;
      transition: outline .1s, background .1s;
    }
    .lane.drag-over { outline: 2px solid var(--brand); background: var(--brand-light); }
    .lane-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; padding-bottom: 10px; border-bottom: 1px solid var(--line); }
    .lane-head h3 { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }
    .lane-count { background: var(--panel); border: 1px solid var(--line); border-radius: 20px; padding: 2px 9px; color: var(--muted); font-weight: 700; font-size: 11px; }
    .lane[data-status="open"]  .lane-head h3 { color: var(--muted); }
    .lane[data-status="todo"]  .lane-head h3 { color: var(--info); }
    .lane[data-status="doing"] .lane-head h3 { color: var(--warn); }
    .lane[data-status="done"]  .lane-head h3 { color: var(--ok); }
    .task-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 13px 14px;
      margin-bottom: 9px;
      cursor: grab;
      box-shadow: var(--shadow-sm);
      transition: box-shadow .12s, transform .12s;
      position: relative;
      overflow: hidden;
    }
    .task-card::before {
      content: '';
      position: absolute;
      left: 0; top: 0; bottom: 0;
      width: 3px;
      background: var(--brand);
      border-radius: 3px 0 0 3px;
    }
    .task-card:active { cursor: grabbing; box-shadow: var(--shadow-lg); transform: scale(1.01); }
    .task-card.dragging { opacity: .5; }
    .task-title { font-weight: 700; font-size: 13px; line-height: 1.4; margin-bottom: 8px; color: var(--ink); }
    .delete-btn { opacity:0; background:none; border:none; cursor:pointer; color:var(--muted); font-size:13px; padding:2px 4px; border-radius:4px; line-height:1; transition:opacity .15s, background .15s; flex-shrink:0; }
    .task-card:hover .delete-btn { opacity:1; }
    .delete-btn:hover { background:var(--bad-bg); color:var(--bad); }
    .task-meta { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 7px; }
    .tag { display: inline-flex; align-items: center; border-radius: 20px; background: var(--line-light); color: var(--ink-mid); padding: 2px 8px; font-size: 11px; font-weight: 600; border: 1px solid var(--line); }
    .tag-priority-high   { background: var(--bad-bg); color: var(--bad); border-color: #fca5a5; }
    .tag-priority-mid    { background: var(--warn-bg); color: var(--warn); border-color: #fcd34d; }
    .tag-priority-low    { background: var(--ok-bg); color: var(--ok); border-color: #86efac; }
    .task-target { color: var(--muted); font-size: 11px; margin-bottom: 6px; word-break: break-all; }
    .task-reason { color: var(--ink-mid); font-size: 12px; line-height: 1.4; }
    .empty-lane { text-align: center; padding: 32px 12px; color: var(--muted); font-size: 13px; }
    .empty-lane .empty-icon { font-size: 28px; margin-bottom: 8px; opacity: .4; }

    /* ── Toast ── */
    .toast {
      position: fixed; right: 20px; bottom: 20px;
      background: var(--ink); color: #f1f5f9;
      padding: 11px 16px; border-radius: var(--radius-sm);
      font-size: 13px; font-weight: 600;
      opacity: 0; transform: translateY(8px);
      transition: .18s ease;
      pointer-events: none;
      box-shadow: var(--shadow-md);
      z-index: 9999;
    }
    .toast.show { opacity: 1; transform: translateY(0); }
    .toast.toast-ok  { background: #15803d; }
    .toast.toast-err { background: #b91c1c; }

    /* ── Pagination ── */
    .pagination { display: flex; align-items: center; gap: 8px; margin-top: 16px; }
    .pg-btn { display: inline-flex; align-items: center; padding: 7px 14px; border: 1px solid var(--line); border-radius: var(--radius-sm); font-size: 13px; font-weight: 600; color: var(--ink-mid); text-decoration: none; background: var(--panel); transition: all .12s; }
    .pg-btn:hover { border-color: var(--brand); color: var(--brand); text-decoration: none; }
    .pg-btn.disabled { opacity: .4; pointer-events: none; }
    .pg-info { color: var(--muted); font-size: 12px; flex: 1; text-align: center; }

    /* ── Section header ── */
    .section-head { display: flex; justify-content: space-between; align-items: flex-end; margin: 0 0 14px; gap: 12px; }
    .section-head h2 { margin: 0; }
    .muted { color: var(--muted); font-size: 13px; }

    /* ── Error page ── */
    .error-box { max-width: 620px; }
    .error-box .output { min-height: 100px; }

    /* ── Content table ── */
    .content-status-pending   { color: var(--warn); }
    .content-status-approved  { color: var(--ok); }
    .content-status-published { color: var(--info); }
    .content-status-rejected  { color: var(--bad); }

    /* ── Runs ── */
    .run-status-completed { color: var(--ok); font-weight: 600; }
    .run-status-failed    { color: var(--bad); font-weight: 600; }
    .run-status-running   { color: var(--info); font-weight: 600; }

    /* ── AI Insights ── */
    .insights-header { display:flex; align-items:center; gap:10px; margin-bottom:20px; }
    .ai-badge {
      display:inline-flex; align-items:center; gap:5px;
      padding:3px 10px; border-radius:20px;
      background: linear-gradient(135deg,#6d1521,#1e2d3d);
      color:#fff; font-size:11px; font-weight:600; letter-spacing:.4px;
    }
    .summary-box {
      background: linear-gradient(135deg, #0f1723 0%, #1a2840 100%);
      border: 1px solid #1e2d3d;
      border-radius: var(--radius-lg);
      padding: 24px 28px;
      margin-bottom: 24px;
      color: #e2e8f0;
    }
    .summary-box h3 { color:#94a3b8; font-size:11px; text-transform:uppercase; letter-spacing:.8px; margin-bottom:10px; }
    .summary-box p  { font-size:14px; line-height:1.7; color:#cbd5e1; }
    .insights-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .insight-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 18px 20px;
      box-shadow: var(--shadow-sm);
    }
    .insight-card h4 { font-size:13px; font-weight:700; margin-bottom:6px; color:var(--ink); }
    .insight-card p  { font-size:12px; color:var(--ink-mid); line-height:1.55; }
    .alert-item {
      display: flex;
      gap: 14px;
      padding: 14px 16px;
      border-radius: var(--radius);
      border-left: 4px solid transparent;
      background: var(--panel);
      box-shadow: var(--shadow-sm);
      margin-bottom: 10px;
    }
    .alert-item.urgency-alta   { border-color: var(--bad);  background: #fff8f8; }
    .alert-item.urgency-media  { border-color: var(--warn); background: #fffdf4; }
    .alert-item.urgency-baixa  { border-color: var(--info); background: #f5f8ff; }
    .alert-dot {
      width:10px; height:10px; border-radius:50%; flex-shrink:0; margin-top:4px;
    }
    .urgency-alta  .alert-dot { background: var(--bad); }
    .urgency-media .alert-dot { background: var(--warn); }
    .urgency-baixa .alert-dot { background: var(--info); }
    .alert-title { font-weight:700; font-size:13px; margin-bottom:3px; }
    .alert-desc  { font-size:12px; color:var(--ink-mid); line-height:1.5; }
    .brand-score-item {
      display:flex; flex-direction:column; gap:6px;
      padding:14px 16px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow-sm);
    }
    .brand-score-header { display:flex; justify-content:space-between; align-items:center; }
    .brand-score-name   { font-weight:700; font-size:13px; }
    .brand-score-num    { font-size:18px; font-weight:800; color:var(--brand); }
    .brand-score-bar    { height:6px; border-radius:3px; background:var(--line); overflow:hidden; }
    .brand-score-fill   { height:100%; border-radius:3px; background: linear-gradient(90deg, var(--brand-dark), var(--brand)); transition: width .4s; }
    .brand-score-meta   { font-size:11px; color:var(--muted); }
    .snippet-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 16px 18px;
      box-shadow: var(--shadow-sm);
      margin-bottom: 12px;
    }
    .snippet-url    { font-size:12px; color:var(--brand); font-weight:600; margin-bottom:8px; word-break:break-all; }
    .snippet-problem{ font-size:11px; color:var(--bad); margin-bottom:10px; }
    .snippet-label  { font-size:11px; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:.5px; margin-bottom:3px; }
    .snippet-value  { font-size:13px; color:var(--ink); line-height:1.45; margin-bottom:10px; }
    .gap-item {
      padding: 14px 16px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow-sm);
      margin-bottom: 10px;
    }
    .gap-type    { display:inline-block; padding:2px 8px; border-radius:12px; font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.5px; background:var(--brand-mid); color:var(--brand-dark); margin-bottom:6px; }
    .gap-title   { font-weight:700; font-size:13px; margin-bottom:4px; }
    .gap-query   { font-size:12px; color:var(--info); margin-bottom:4px; }
    .gap-reason  { font-size:12px; color:var(--ink-mid); line-height:1.5; }
    .no-insights { text-align:center; padding:60px 20px; color:var(--muted); }
    .no-insights h3 { font-size:18px; margin-bottom:8px; }

    /* ── Responsive ── */
    @media (max-width: 1100px) {
      .kpis { grid-template-columns: repeat(3, 1fr); }
      .kanban { grid-template-columns: repeat(2, 1fr); }
      .sidebar { width: 210px; }
    }
    @media (max-width: 780px) {
      .layout { flex-direction: column; }
      .sidebar { width: 100%; height: auto; position: static; flex-direction: row; flex-wrap: wrap; }
      .tool-grid { grid-template-columns: 1fr; }
      .kpis { grid-template-columns: repeat(2, 1fr); }
      .kanban { grid-template-columns: 1fr; }
      .content { padding: 16px; }
    }
  </style>"""


# ── Nav SVG icons ─────────────────────────────────────────────────────────────

NAV_ICONS = {
    "dashboard": '<svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1" y="1" width="6" height="6" rx="1.5"/><rect x="9" y="1" width="6" height="6" rx="1.5"/><rect x="1" y="9" width="6" height="6" rx="1.5"/><rect x="9" y="9" width="6" height="6" rx="1.5"/></svg>',
    "issues":    '<svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6.5"/><line x1="8" y1="5" x2="8" y2="8.5"/><circle cx="8" cy="11" r=".6" fill="currentColor" stroke="none"/></svg>',
    "backlog":   '<svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><line x1="3" y1="4" x2="13" y2="4"/><line x1="3" y1="8" x2="13" y2="8"/><line x1="3" y1="12" x2="9" y2="12"/></svg>',
    "kanban":    '<svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1" y="2" width="4" height="12" rx="1.5"/><rect x="6" y="2" width="4" height="8" rx="1.5"/><rect x="11" y="2" width="4" height="10" rx="1.5"/></svg>',
    "content":   '<svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="12" height="12" rx="1.5"/><line x1="5" y1="7" x2="11" y2="7"/><line x1="5" y1="10" x2="9" y2="10"/></svg>',
    "blog":      '<svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M10 2l3 3-7 7H3v-3l7-7z"/></svg>',
    "tools":     '<svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M11.5 1.5a3 3 0 0 1 0 4.24L5 12.24 2 13l.76-3L9 3.5a3 3 0 0 1 2.5-2z"/></svg>',

    "insights":  '<svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 1v2M8 13v2M1 8h2M13 8h2"/><circle cx="8" cy="8" r="3"/><path d="M3.5 3.5l1.4 1.4M11.1 11.1l1.4 1.4M3.5 12.5l1.4-1.4M11.1 4.9l1.4-1.4"/></svg>',
    "reports":   '<svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 1.5h5.5L13 5v9.5H4z" rx="1"/><polyline points="9.5 1.5 9.5 5 13 5"/><line x1="5.5" y1="7.5" x2="10.5" y2="7.5"/><line x1="5.5" y1="10" x2="8.5" y2="10"/></svg>',
    "search":    '<svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="6.5" cy="6.5" r="4.5"/><line x1="10" y1="10" x2="14" y2="14"/></svg>',
    "audit":     '<svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 1.5a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13z"/><polyline points="5 8 7 10 11 6"/></svg>',
    "settings":  '<svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="2"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.2 3.2l1.4 1.4M11.4 11.4l1.4 1.4M3.2 12.8l1.4-1.4M11.4 4.6l1.4-1.4"/></svg>',
}


def page_shell(title: str, body: str, active: str = "") -> str:
    path = request.path

    def nav_link(href: str, label: str, icon_key: str) -> str:
        is_active = (href == "/" and path == "/") or (href != "/" and path.startswith(href))
        cls = " active" if is_active else ""
        return f'<a href="{href}" class="{cls.strip()}">{NAV_ICONS.get(icon_key, "")}{esc(label)}</a>'

    _site_name = get_site_name()
    _logo_init = (_site_name[:2].upper()) if _site_name else "SE"
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} — SEO Control Center</title>
  {styles()}
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-brand">
      <div class="logo">
        <div class="logo-icon">{esc(_logo_init)}</div>
        <div>
          <div class="logo-text">SEO Control</div>
          <div class="logo-sub" title="{esc(_site_name)}" style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{esc(_site_name)}</div>
        </div>
      </div>
      <div class="sidebar-env">&#9679; Local</div>
    </div>
    <div class="sidebar-cta">
      <a href="/tools">
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="8" y1="2" x2="8" y2="14"/><line x1="2" y1="8" x2="14" y2="8"/></svg>
        Nova Auditoria
      </a>
    </div>
    <nav class="nav-section">
      <div class="nav-label">Auditoria</div>
      <nav class="nav">
        {nav_link("/full-audit", "Auditoria Completa", "audit")}
        {nav_link("/tools", "Ferramentas", "tools")}
        {nav_link("/reports", "Relatórios", "reports")}
        {nav_link("/ai-insights", "AI Insights", "insights")}
      </nav>
    </nav>
    <nav class="nav-section">
      <div class="nav-label">Gestão</div>
      <nav class="nav">
        {nav_link("/", "Dashboard", "dashboard")}
        {nav_link("/kanban", "Kanban", "kanban")}
      </nav>
    </nav>
    <nav class="nav-section">
      <div class="nav-label">Conteúdo</div>
      <nav class="nav">
        {nav_link("/blog-ideas", "Ideias de blog", "blog")}
      </nav>
    </nav>
    <div class="sidebar-footer">
      {nav_link("/settings", "Configurações", "settings")}
    </div>
  </aside>
  <div class="main">
    <div class="content">
      {body}
    </div>
  </div>
</div>
<div id="toast" class="toast"></div>
<script>
  function showToast(text, type) {{
    const t = document.getElementById('toast');
    t.textContent = text;
    t.className = 'toast show' + (type ? ' toast-' + type : '');
    clearTimeout(t._timer);
    t._timer = setTimeout(() => t.classList.remove('show'), 2200);
  }}
</script>
</body>
</html>"""


def error_page(message: str) -> str:
    body = f"""
  <div class="error-box">
    <div class="notice">
      <h2 style="margin-bottom:10px">Conexão indisponível</h2>
      <p style="margin-bottom:14px">O dashboard não conseguiu acessar o Supabase agora.</p>
      <pre class="output">{esc(message)}</pre>
      <p style="margin-top:14px">Verifique as <a href="/settings">Configurações</a> e tente novamente.</p>
    </div>
  </div>"""
    return page_shell("Conexão indisponível", body)


# ── Tool command builder ───────────────────────────────────────────────────────

def build_tool_command(form) -> list[str]:
    module = form.get("module", "blog-ideas")
    if module not in ALLOWED_TOOL_MODULES:
        raise ValueError("Módulo não permitido.")

    cmd = [sys.executable, "run.py", "--module", module]

    gsc = form.get("gsc", "./gsc_exports").strip()
    if gsc and module in GSC_FOLDER_MODULES:
        cmd.extend(["--gsc", gsc])

    urls = _split_urls(form.get("urls", ""))
    if urls:
        cmd.append("--urls")
        cmd.extend(urls)

    top = str(form.get("top", "10")).strip()
    if module in TOP_TOOL_MODULES and top:
        cmd.extend(["--top", top])

    max_pages = str(form.get("max_pages", "200")).strip()
    if module in MAX_PAGES_TOOL_MODULES and max_pages:
        cmd.extend(["--max-pages", max_pages])

    changes_log = str(form.get("changes_log", "")).strip()
    if changes_log:
        cmd.extend(["--changes-log", changes_log])

    provider = form.get("provider", "auto")
    if module in AI_TOOL_MODULES and provider and provider != "auto":
        cmd.extend(["--provider", provider])

    comparison = form.get("comparison", "week").strip()
    if module in {"gsc-api", "keyword-tracker", "cannibalization", "blog-ideas"} and comparison in ("week", "month", "year"):
        cmd.extend(["--comparison", comparison])

    if module in AI_TOOL_MODULES and form.get("ai"):
        cmd.append("--ai")
    cmd.append("--save-db")
    cmd.append("--no-report")  # Reports are internal — never open external HTML files

    return cmd


def run_tool_from_form(form) -> dict:
    cmd = build_tool_command(form)
    env = os.environ.copy()
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "GIT_HTTP_PROXY", "GIT_HTTPS_PROXY"]:
        env.pop(key, None)

    completed = subprocess.run(
        cmd,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        capture_output=True,
        text=True,
        timeout=900,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "command": " ".join(cmd),
        "returncode": completed.returncode,
        "stdout": _clean_output(completed.stdout),
        "stderr": _clean_output(completed.stderr),
    }


def start_tool_job(form) -> str:
    job_id = uuid.uuid4().hex
    form_data = dict(form)
    try:
        command_preview = " ".join(build_tool_command(form_data))
    except Exception:
        command_preview = ""
    _register_job(job_id, {
        "status": "running",
        "command": command_preview,
        "returncode": None,
        "stdout": "Executando ferramenta…\nAguarde, a saída será atualizada automaticamente.",
        "stderr": "",
        "error": "",
    })

    def worker():
        try:
            result = run_tool_from_form(form_data)
            _update_job(job_id, {**result, "status": "completed" if result["returncode"] == 0 else "failed"})
        except subprocess.TimeoutExpired:
            _update_job(job_id, {"status": "failed", "error": "A ferramenta passou do limite de 15 minutos."})
        except Exception as exc:
            _update_job(job_id, {"status": "failed", "error": str(exc)})

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def format_job_output(job: dict) -> str:
    if not job:
        return "Job não encontrado."
    output = f"Status: {job.get('status', '?')}\n"
    if job.get("command"):
        output += f"Comando: {job['command']}\n"
    output += "\n"
    if job.get("error"):
        output += f"Erro: {job['error']}\n"
    output += job.get("stdout", "")
    if job.get("stderr"):
        output += f"\n\nSTDERR:\n{job['stderr']}"
    return output


def job_banner_html(job: dict | None) -> str:
    if not job:
        return '<div id="job-banner" class="job-banner"><strong>Pronto para executar</strong><span>Escolha uma ferramenta e clique em Executar.</span></div>'
    status = job.get("status", "running")
    if status == "running":
        label = '<span class="spinner"></span> <strong>Executando…</strong>'
        detail = "A saída será atualizada automaticamente."
        cls = "job-running"
    elif status == "completed":
        label = "<strong>✓ Concluído</strong>"
        detail = "A ferramenta terminou com sucesso."
        cls = "job-completed"
    else:
        label = "<strong>✗ Falhou</strong>"
        detail = "Veja a saída abaixo para detalhes."
        cls = "job-failed"
    return f'<div id="job-banner" class="job-banner {cls}"><span>{label}</span><span>{esc(detail)}</span></div>'


# ── Kanban helpers ─────────────────────────────────────────────────────────────

KANBAN_COLUMNS = [
    ("open",  "Backlog",     "—"),
    ("todo",  "A Fazer",     "→"),
    ("doing", "Em Execução", "⚡"),
    ("done",  "Concluído",   "✓"),
]


def normalize_recommendation_status(value: str) -> str:
    value = str(value or "open")
    aliases = {"pending": "open", "approved": "todo", "in_progress": "doing", "completed": "done"}
    return aliases.get(value, value if value in {c[0] for c in KANBAN_COLUMNS} else "open")


# ── POST actions ───────────────────────────────────────────────────────────────


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/dashboard/data")
def dashboard_data():
    from flask import jsonify
    try:
        period = max(7, min(90, int(request.args.get("period", 28))))
    except (ValueError, TypeError):
        period = 28
    if request.args.get("force") == "1":
        try:
            from config import BASE_DIR as _bd
            for _f in _bd.glob(".dashboard_cache_*.json"):
                _f.unlink()
        except Exception:
            pass
    try:
        from modules.gsc_api import get_dashboard_data
        return jsonify(get_dashboard_data(period_days=period))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/dashboard/ai")
def dashboard_ai():
    from flask import jsonify
    try:
        period = max(7, min(90, int(request.args.get("period", 28))))
    except (ValueError, TypeError):
        period = 28
    if request.args.get("force") == "1":
        try:
            from config import BASE_DIR as _bd
            for _f in _bd.glob(".dashboard_ai_*.json"):
                _f.unlink()
        except Exception:
            pass
    try:
        from modules.gsc_api import get_dashboard_ai
        return jsonify(get_dashboard_ai(period_days=period))
    except Exception as exc:
        return jsonify({"ai_summary": "", "ai_error": str(exc)})


@app.route("/")
def index():
    latest_runs = []
    try:
        sb = get_supabase()
        latest_runs = (
            sb.table("crawl_runs")
            .select("id, run_type, created_at, summary")
            .order("created_at", desc=True)
            .limit(5)
            .execute().data
        )
    except Exception:
        pass

    run_rows = "".join(
        f"<tr>"
        f"<td style='color:var(--muted);white-space:nowrap'>{esc(str(r.get('created_at',''))[:19].replace('T',' '))}</td>"
        f"<td><span class='badge badge-gray'>{esc(r.get('run_type',''))}</span></td>"
        f"<td><code style='font-size:11px'>{esc(str(r.get('id',''))[:8])}</code></td>"
        f"<td style='color:var(--ink-mid);font-size:12px'>{esc(str(r.get('summary','') or '')[:100])}</td>"
        f"</tr>"
        for r in latest_runs
    )
    no_runs = '<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:24px">Nenhuma auditoria. <a href="/tools">Execute uma →</a></td></tr>'

    body = f"""
<style>
  .period-pills {{display:flex;gap:6px;align-items:center}}
  .period-pill {{
    padding:5px 14px;border-radius:16px;border:1px solid var(--line);
    font-size:12px;font-weight:600;cursor:pointer;background:var(--surface);
    color:var(--ink-mid);transition:all .15s;
  }}
  .period-pill:hover {{background:var(--line-light)}}
  .period-pill.active {{background:var(--brand);color:#fff;border-color:var(--brand)}}

  .kpi-grid {{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}}
  .kpi-card {{
    background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
    padding:16px 18px;
  }}
  .kpi-label {{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:8px}}
  .kpi-value {{font-size:30px;font-weight:700;color:var(--ink);line-height:1;margin-bottom:6px}}
  .kpi-delta-wrap {{margin-bottom:4px;min-height:22px}}
  .kpi-delta {{display:inline-flex;align-items:center;gap:3px;font-size:12px;font-weight:600;padding:2px 8px;border-radius:10px}}
  .delta-up   {{background:#dcfce7;color:#16a34a}}
  .delta-down {{background:#fee2e2;color:#dc2626}}
  .delta-flat {{background:var(--line-light);color:var(--muted)}}
  .kpi-prev   {{font-size:11px;color:var(--muted)}}

  .chart-panel {{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:18px 20px;margin-bottom:16px}}
  .chart-wrap  {{position:relative;height:270px}}

  .dash-two-col {{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}}

  .sk {{
    background:linear-gradient(90deg,var(--line-light) 25%,var(--line) 50%,var(--line-light) 75%);
    background-size:200% 100%;animation:shimmer 1.4s infinite;
    border-radius:4px;display:inline-block;
  }}
  @keyframes shimmer {{0%{{background-position:200% 0}}100%{{background-position:-200% 0}}}}
  @keyframes spin {{to{{transform:rotate(360deg)}}}}
  #dash-refresh.spinning {{animation:spin .9s linear infinite;opacity:.7}}
  .period-pill:disabled,.period-pill[disabled] {{opacity:.45;pointer-events:none}}

  .gemini-dash {{background:#faf5ff;border:1px solid #e9d5ff;border-radius:var(--radius);padding:16px 20px;margin-bottom:16px}}
  .gemini-dash-head {{display:flex;align-items:center;gap:6px;font-size:11px;font-weight:700;color:#7c3aed;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px}}
  #gemini-dash-text strong {{color:var(--ink);font-weight:700}}
  #gemini-dash-text p {{margin:3px 0;color:var(--ink-mid);font-size:13px;line-height:1.65}}
  #gemini-dash-text ul {{margin:5px 0 5px 18px;padding:0;list-style:disc}}
  #gemini-dash-text li {{margin-bottom:3px;color:var(--ink-mid);font-size:13px;line-height:1.55}}

  .perf-tbl th {{font-size:11px;text-transform:uppercase;letter-spacing:.3px;cursor:pointer;user-select:none;white-space:nowrap}}
  .perf-tbl th:hover {{color:var(--ink)}}
  .perf-tbl th .sort-icon {{display:inline-block;margin-left:4px;opacity:.35;font-style:normal;font-size:10px}}
  .perf-tbl th.sort-asc .sort-icon,
  .perf-tbl th.sort-desc .sort-icon {{opacity:1;color:var(--brand)}}
  .perf-tbl td {{font-size:12px;vertical-align:middle}}

  @media(max-width:900px){{
    .kpi-grid {{grid-template-columns:repeat(2,1fr)}}
    .dash-two-col {{grid-template-columns:1fr}}
  }}
</style>

<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:18px">
  <h1 style="font-size:20px;font-weight:700;color:var(--ink);margin:0">Desempenho nos resultados da pesquisa</h1>
  <div style="display:flex;align-items:center;gap:10px">
    <div class="period-pills">
      <button class="period-pill" data-days="7">7 dias</button>
      <button class="period-pill active" data-days="28">28 dias</button>
      <button class="period-pill" data-days="90">3 meses</button>
    </div>
    <button class="btn btn-ghost btn-sm" id="dash-refresh" title="Forçar atualização" style="padding:5px 10px">↻</button>
  </div>
</div>

<div class="kpi-grid">
  <div class="kpi-card">
    <div class="kpi-label">Total de Cliques</div>
    <div class="kpi-value" id="kv-clicks"><span class="sk" style="width:80px;height:28px">&nbsp;</span></div>
    <div class="kpi-delta-wrap" id="kd-clicks"></div>
    <div class="kpi-prev" id="kp-clicks">&nbsp;</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Total de Impressões</div>
    <div class="kpi-value" id="kv-impressions"><span class="sk" style="width:90px;height:28px">&nbsp;</span></div>
    <div class="kpi-delta-wrap" id="kd-impressions"></div>
    <div class="kpi-prev" id="kp-impressions">&nbsp;</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">CTR médio</div>
    <div class="kpi-value" id="kv-ctr"><span class="sk" style="width:60px;height:28px">&nbsp;</span></div>
    <div class="kpi-delta-wrap" id="kd-ctr"></div>
    <div class="kpi-prev" id="kp-ctr">&nbsp;</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Posição média</div>
    <div class="kpi-value" id="kv-position"><span class="sk" style="width:60px;height:28px">&nbsp;</span></div>
    <div class="kpi-delta-wrap" id="kd-position"></div>
    <div class="kpi-prev" id="kp-position">&nbsp;</div>
  </div>
</div>

<div class="chart-panel">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
    <div style="font-size:13px;font-weight:600;color:var(--ink)" id="chart-period">Carregando...</div>
    <div style="display:flex;gap:16px;font-size:11px;color:var(--muted)">
      <span style="display:flex;align-items:center;gap:5px">
        <span style="width:14px;height:3px;background:#4285F4;border-radius:2px;display:inline-block"></span>Cliques
      </span>
      <span style="display:flex;align-items:center;gap:5px">
        <span style="width:14px;height:3px;background:#7C3AED;border-radius:2px;display:inline-block"></span>Impressões
      </span>
    </div>
  </div>
  <div class="chart-wrap"><canvas id="perf-chart"></canvas></div>
</div>

<div class="gemini-dash" id="gemini-dash">
  <div class="gemini-dash-head">&#10024; Análise Gemini</div>
  <div id="gemini-dash-text" style="font-size:13px;line-height:1.65;color:var(--ink-mid)">
    <span class="sk" style="width:100%;height:14px;display:block;margin-bottom:6px">&nbsp;</span>
    <span class="sk" style="width:75%;height:14px;display:block">&nbsp;</span>
  </div>
</div>

<div class="dash-two-col">
  <div class="panel">
    <div class="panel-head" style="margin-bottom:10px"><h2>Top consultas</h2></div>
    <div class="table-wrap">
      <table class="perf-tbl" id="tbl-queries-table">
        <thead id="thead-queries"><tr>
          <th data-field="query"       data-tbl="queries">Consulta <i class="sort-icon">⇅</i></th>
          <th data-field="clicks"      data-tbl="queries" style="text-align:right">Cliques <i class="sort-icon">⇅</i></th>
          <th data-field="impressions" data-tbl="queries" style="text-align:right">Impressões <i class="sort-icon">⇅</i></th>
          <th data-field="ctr"         data-tbl="queries" style="text-align:right">CTR <i class="sort-icon">⇅</i></th>
          <th data-field="position"    data-tbl="queries" style="text-align:right">Posição <i class="sort-icon">⇅</i></th>
        </tr></thead>
        <tbody id="tbl-queries"><tr><td colspan="5" style="text-align:center;padding:20px"><span class="sk" style="width:60%;height:12px">&nbsp;</span></td></tr></tbody>
      </table>
    </div>
  </div>
  <div class="panel">
    <div class="panel-head" style="margin-bottom:10px"><h2>Top páginas</h2></div>
    <div class="table-wrap">
      <table class="perf-tbl" id="tbl-pages-table">
        <thead id="thead-pages"><tr>
          <th data-field="page"        data-tbl="pages">Página <i class="sort-icon">⇅</i></th>
          <th data-field="clicks"      data-tbl="pages" style="text-align:right">Cliques <i class="sort-icon">⇅</i></th>
          <th data-field="impressions" data-tbl="pages" style="text-align:right">Impressões <i class="sort-icon">⇅</i></th>
          <th data-field="ctr"         data-tbl="pages" style="text-align:right">CTR <i class="sort-icon">⇅</i></th>
          <th data-field="position"    data-tbl="pages" style="text-align:right">Posição <i class="sort-icon">⇅</i></th>
        </tr></thead>
        <tbody id="tbl-pages"><tr><td colspan="5" style="text-align:center;padding:20px"><span class="sk" style="width:60%;height:12px">&nbsp;</span></td></tr></tbody>
      </table>
    </div>
  </div>
</div>

<div class="panel">
  <div class="panel-head">
    <h2>Últimas auditorias</h2>
    <a href="/reports" class="btn btn-ghost btn-sm">Ver relatórios →</a>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>Data</th><th>Tipo</th><th>Run ID</th><th>Resumo</th></tr></thead>
      <tbody>{run_rows or no_runs}</tbody>
    </table>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
(function() {{
  let _chart = null;
  let _activeDays = 28;
  let _data = {{ queries: [], pages: [] }};
  let _sort = {{
    queries: {{ col: 'clicks', dir: -1 }},
    pages:   {{ col: 'clicks', dir: -1 }},
  }};

  const _rowFn = {{
    queries: r => `<tr>
      <td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${{r.query}}</td>
      <td style="text-align:right;font-variant-numeric:tabular-nums">${{r.clicks.toLocaleString('pt-BR')}}</td>
      <td style="text-align:right;font-variant-numeric:tabular-nums">${{r.impressions.toLocaleString('pt-BR')}}</td>
      <td style="text-align:right">${{r.ctr.toFixed(1)}}%</td>
      <td style="text-align:right">${{r.position.toFixed(1)}}</td>
    </tr>`,
    pages: r => `<tr>
      <td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px">${{r.page}}</td>
      <td style="text-align:right;font-variant-numeric:tabular-nums">${{r.clicks.toLocaleString('pt-BR')}}</td>
      <td style="text-align:right;font-variant-numeric:tabular-nums">${{r.impressions.toLocaleString('pt-BR')}}</td>
      <td style="text-align:right">${{r.ctr.toFixed(1)}}%</td>
      <td style="text-align:right">${{r.position.toFixed(1)}}</td>
    </tr>`,
  }};

  function renderTable(tbl) {{
    const rows = _data[tbl];
    const s = _sort[tbl];
    const sorted = [...rows].sort((a, b) => {{
      const av = a[s.col], bv = b[s.col];
      if (typeof av === 'string') return s.dir * av.localeCompare(bv, 'pt-BR');
      return s.dir * (av - bv);
    }});
    const tbody = document.getElementById('tbl-' + tbl);
    tbody.innerHTML = sorted.length
      ? sorted.map(_rowFn[tbl]).join('')
      : '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:16px">Sem dados</td></tr>';
    // update header indicators
    document.querySelectorAll('[data-tbl="'+tbl+'"]').forEach(th => {{
      th.classList.remove('sort-asc', 'sort-desc');
      const icon = th.querySelector('.sort-icon');
      if (th.dataset.field === s.col) {{
        th.classList.add(s.dir === 1 ? 'sort-asc' : 'sort-desc');
        if (icon) icon.textContent = s.dir === 1 ? '↑' : '↓';
      }} else {{
        if (icon) icon.textContent = '⇅';
      }}
    }});
  }}

  document.querySelectorAll('.perf-tbl th[data-field]').forEach(th => {{
    th.addEventListener('click', () => {{
      const tbl   = th.dataset.tbl;
      const field = th.dataset.field;
      const s = _sort[tbl];
      if (s.col === field) {{ s.dir *= -1; }}
      else {{ s.col = field; s.dir = field === 'query' || field === 'page' ? 1 : -1; }}
      renderTable(tbl);
    }});
  }});

  function fmtN(n) {{
    if (n >= 1e6) return (n/1e6).toFixed(1)+' M';
    if (n >= 1e3) return (n/1e3).toFixed(1)+' mil';
    return n.toLocaleString('pt-BR');
  }}

  function fmtDate(d) {{
    const p = d.split('-'); return p[2]+'/'+p[1];
  }}

  function renderDelta(delta, invert) {{
    if (delta === null || delta === undefined) return '';
    const good = invert ? delta < 0 : delta > 0;
    const cls  = Math.abs(delta) < 0.1 ? 'delta-flat' : (good ? 'delta-up' : 'delta-down');
    const arr  = delta > 0 ? '▲' : (delta < 0 ? '▼' : '—');
    return `<span class="kpi-delta ${{cls}}">${{arr}} ${{Math.abs(delta).toFixed(1)}}%</span>`;
  }}

  function setKPIs(kpis) {{
    const cfg = [
      ['clicks',      v => fmtN(v),          false],
      ['impressions', v => fmtN(v),          false],
      ['ctr',         v => v.toFixed(2)+'%', false],
      ['position',    v => v.toFixed(1),     true ],
    ];
    cfg.forEach(([k, fmt, inv]) => {{
      const info = kpis[k] || {{}};
      document.getElementById('kv-'+k).textContent = fmt(info.value || 0);
      document.getElementById('kd-'+k).innerHTML   = renderDelta(info.delta, inv);
      const prevFmt = k==='ctr'
        ? (info.prev||0).toFixed(2)+'%'
        : k==='position' ? (info.prev||0).toFixed(1) : fmtN(info.prev||0);
      document.getElementById('kp-'+k).textContent = info.prev != null ? 'Anterior: '+prevFmt : '';
    }});
  }}

  function buildChart(series) {{
    const labels = series.map(r => fmtDate(r.date));
    const clicks = series.map(r => r.clicks);
    const impr   = series.map(r => r.impressions);
    if (_chart) {{ _chart.destroy(); _chart = null; }}
    const ctx = document.getElementById('perf-chart').getContext('2d');
    _chart = new Chart(ctx, {{
      type: 'line',
      data: {{
        labels,
        datasets: [
          {{
            label: 'Cliques', data: clicks, yAxisID: 'yL',
            borderColor: '#4285F4', backgroundColor: 'rgba(66,133,244,0.08)',
            fill: true, tension: 0.35, pointRadius: 2, pointHoverRadius: 5, borderWidth: 2,
          }},
          {{
            label: 'Impressões', data: impr, yAxisID: 'yR',
            borderColor: '#7C3AED', backgroundColor: 'rgba(124,58,237,0.06)',
            fill: true, tension: 0.35, pointRadius: 2, pointHoverRadius: 5, borderWidth: 2,
          }},
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        interaction: {{ mode: 'index', intersect: false }},
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            callbacks: {{ label: ctx => ctx.dataset.label+': '+ctx.parsed.y.toLocaleString('pt-BR') }}
          }}
        }},
        scales: {{
          x: {{
            grid: {{ color: 'rgba(0,0,0,0.04)' }},
            ticks: {{ font: {{ size: 11 }}, maxTicksLimit: 14, maxRotation: 0 }},
          }},
          yL: {{
            type: 'linear', position: 'left',
            grid: {{ color: 'rgba(0,0,0,0.04)' }},
            ticks: {{ font: {{ size: 11 }}, color: '#4285F4', callback: v => fmtN(v) }},
          }},
          yR: {{
            type: 'linear', position: 'right',
            grid: {{ drawOnChartArea: false }},
            ticks: {{ font: {{ size: 11 }}, color: '#7C3AED', callback: v => fmtN(v) }},
          }},
        }}
      }}
    }});
  }}

  function setLoading(on) {{
    const btn   = document.getElementById('dash-refresh');
    const pills = document.querySelectorAll('.period-pill');
    btn.classList.toggle('spinning', on);
    btn.disabled = on;
    pills.forEach(b => {{ b.disabled = on; }});
    if (on) {{
      document.getElementById('gemini-dash-text').innerHTML =
        '<span class="sk" style="width:100%;height:13px;display:block;margin-bottom:7px">&nbsp;</span>' +
        '<span class="sk" style="width:72%;height:13px;display:block">&nbsp;</span>';
    }}
  }}

  function renderAI(raw) {{
    const el = document.getElementById('gemini-dash-text');
    if (!raw) {{ el.textContent = 'Análise não disponível.'; return; }}
    const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    const fmt = s => esc(s).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
    const lines = raw.split(/\\r?\\n/);
    let html = '', inList = false;
    for (let raw_line of lines) {{
      const line = raw_line.trim();
      if (!line) {{
        if (inList) {{ html += '</ul>'; inList = false; }}
        continue;
      }}
      if (/^\*\*[^*]+\*\*\s*:?\s*$/.test(line) || /^#+\s/.test(line)) {{
        if (inList) {{ html += '</ul>'; inList = false; }}
        const text = line.replace(/^\*\*([^*]+)\*\*\s*:?\s*$/, '$1').replace(/^#+\s+/, '');
        html += '<div style="font-weight:700;color:var(--ink);font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-top:14px;margin-bottom:5px;padding-bottom:4px;border-bottom:1px solid var(--line)">' + esc(text) + '</div>';
      }} else if (/^[\*\-]\s+/.test(line)) {{
        if (!inList) {{ html += '<ul>'; inList = true; }}
        html += '<li>' + fmt(line.replace(/^[\*\-]\s+/, '')) + '</li>';
      }} else {{
        if (inList) {{ html += '</ul>'; inList = false; }}
        html += '<p>' + fmt(line) + '</p>';
      }}
    }}
    if (inList) html += '</ul>';
    el.innerHTML = html;
  }}

  function skAI() {{
    document.getElementById('gemini-dash-text').innerHTML =
      '<span class="sk" style="width:100%;height:13px;display:block;margin-bottom:7px">&nbsp;</span>' +
      '<span class="sk" style="width:88%;height:13px;display:block;margin-bottom:7px">&nbsp;</span>' +
      '<span class="sk" style="width:72%;height:13px;display:block">&nbsp;</span>';
  }}

  async function loadAI(days, force) {{
    skAI();
    try {{
      const r = await fetch('/dashboard/ai?period='+days+(force?'&force=1':''));
      const d = await r.json();
      renderAI(d.ai_summary || (d.ai_error ? '⚠ '+d.ai_error : ''));
    }} catch(e) {{
      renderAI('⚠ Erro Gemini: '+e.message);
    }}
  }}

  function showError(msg) {{
    ['clicks','impressions','ctr','position'].forEach(k => {{
      document.getElementById('kv-'+k).textContent = '—';
      document.getElementById('kd-'+k).innerHTML   = '';
      document.getElementById('kp-'+k).textContent = '';
    }});
    document.getElementById('chart-period').textContent = 'Sem dados';
    if (_chart) {{ _chart.destroy(); _chart = null; }}
    ['tbl-queries','tbl-pages'].forEach(id => {{
      document.getElementById(id).innerHTML =
        '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:16px">—</td></tr>';
    }});
    document.getElementById('gemini-dash-text').textContent = '⚠ ' + msg;
  }}

  async function load(days, force) {{
    _activeDays = days;
    document.querySelectorAll('.period-pill').forEach(b =>
      b.classList.toggle('active', +b.dataset.days === days));
    setLoading(true);
    skAI();
    let gscOk = false;
    try {{
      const resp = await fetch('/dashboard/data?period='+days+(force?'&force=1':''));
      const data = await resp.json();
      if (data.error) {{
        showError('GSC indisponível: ' + data.error);
        return;
      }}
      setKPIs(data.kpis);
      buildChart(data.time_series || []);
      document.getElementById('chart-period').textContent = data.period || '';
      _data.queries = data.top_queries || [];
      _data.pages   = data.top_pages   || [];
      renderTable('queries');
      renderTable('pages');
      gscOk = true;
    }} catch(e) {{
      showError('Erro ao carregar dados: ' + e.message);
    }} finally {{
      setLoading(false);
    }}
    // Gemini runs independently — only if GSC data loaded
    if (gscOk) loadAI(days, force);
  }}

  document.querySelectorAll('.period-pill').forEach(b =>
    b.addEventListener('click', () => load(+b.dataset.days)));
  document.getElementById('dash-refresh').addEventListener('click',
    () => load(_activeDays, true));

  load(28);
}})();
</script>"""
    return page_shell("Dashboard", body)




@app.route("/kanban")
def kanban():
    try:
        rows = (
            get_supabase().table("recommendations")
            .select("id, priority, source, action, target, reason, owner, status, created_at")
            .order("priority", desc=True)
            .limit(300)
            .execute().data
        )
    except Exception as exc:
        return error_page(str(exc)), 503

    by_status = {status: [] for status, _, _ in KANBAN_COLUMNS}
    for row in rows:
        key = normalize_recommendation_status(row.get("status"))
        by_status.setdefault(key, []).append(row)

    lanes = ""
    for status, label, icon in KANBAN_COLUMNS:
        cards_html = ""
        for row in by_status.get(status, []):
            title = row.get("action") or row.get("source") or "Tarefa SEO"
            try:
                pv = float(row.get("priority") or 0)
            except Exception:
                pv = 0
            p_cls = "tag-priority-high" if pv >= 15 else "tag-priority-mid" if pv >= 8 else ""
            cards_html += f"""
<article class="task-card" draggable="true" data-id="{esc(row.get('id', ''))}">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:6px">
    <div class="task-title" style="flex:1">{esc(title)}</div>
    <button class="delete-btn" draggable="false" title="Excluir tarefa"
      onmousedown="event.stopPropagation()"
      onclick="event.stopPropagation();deleteCard(this, '{esc(row.get('id',''))}')">&#10005;</button>
  </div>
  <div class="task-meta">
    <span class="tag {p_cls}">P{esc(row.get('priority', ''))}</span>
    <span class="tag">{esc(row.get('source', ''))}</span>
    <span class="tag">{esc(row.get('owner') or 'SEO')}</span>
  </div>
  <div class="task-target">{esc(str(row.get('target', ''))[:100])}</div>
  <div class="task-reason">{esc(str(row.get('reason', ''))[:160])}</div>
</article>"""
        count = len(by_status.get(status, []))
        empty = f'<div class="empty-lane"><div class="empty-icon">○</div>Sem tarefas aqui</div>' if not count else ""
        lanes += f"""
<section class="lane" data-status="{esc(status)}">
  <div class="lane-head">
    <h3>{esc(icon)} {esc(label)}</h3>
    <span class="lane-count" id="count-{esc(status)}">{count}</span>
  </div>
  {cards_html}{empty}
</section>"""

    body = f"""
<div class="section-head">
  <div>
    <h1>Kanban SEO</h1>
    <p class="muted" style="margin-top:4px">Arraste as tarefas entre colunas — salvo automaticamente.</p>
  </div>
  <a href="/tools" class="btn btn-primary">+ Gerar tarefas</a>
</div>
<div class="kanban">{lanes}</div>
<script>
  let dragged = null;
  let dragOriginLane = null;

  document.querySelectorAll('.task-card').forEach(card => {{
    card.addEventListener('dragstart', e => {{
      dragged = card;
      dragOriginLane = card.closest('.lane');
      card.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', card.dataset.id);
    }});
    card.addEventListener('dragend', () => card.classList.remove('dragging'));
  }});

  function refreshCount(lane) {{
    const count = lane.querySelectorAll('.task-card').length;
    const badge = document.getElementById('count-' + lane.dataset.status);
    if (badge) badge.textContent = count;
    const empty = lane.querySelector('.empty-lane');
    if (count > 0 && empty) empty.remove();
    if (count === 0 && !lane.querySelector('.empty-lane')) {{
      lane.insertAdjacentHTML('beforeend',
        '<div class="empty-lane"><div class="empty-icon">○</div>Sem tarefas aqui</div>');
    }}
  }}

  document.querySelectorAll('.lane').forEach(lane => {{
    lane.addEventListener('dragover', e => {{ e.preventDefault(); lane.classList.add('drag-over'); }});
    lane.addEventListener('dragleave', () => lane.classList.remove('drag-over'));
    lane.addEventListener('drop', async e => {{
      e.preventDefault();
      lane.classList.remove('drag-over');
      if (!dragged || lane === dragged.closest('.lane')) return;

      const id     = dragged.dataset.id;
      const status = lane.dataset.status;

      // Optimistic update
      lane.insertBefore(dragged, lane.querySelector('.empty-lane') || null);
      refreshCount(lane);
      if (dragOriginLane) refreshCount(dragOriginLane);

      try {{
        const res  = await fetch('/kanban/update/' + id, {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{status}})
        }});
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || 'Erro ao salvar');
        showToast('Tarefa movida', 'ok');
      }} catch (err) {{
        // Rollback on failure
        if (dragOriginLane) {{
          dragOriginLane.appendChild(dragged);
          refreshCount(lane);
          refreshCount(dragOriginLane);
        }}
        showToast('Falha ao salvar — revertido', 'err');
      }}
    }});
  }});
</script>""" + """
<script>
function deleteCard(btn, id) {
  const card = btn.closest('.task-card');
  btn.style.display = 'none';
  const confirmEl = document.createElement('div');
  confirmEl.style.cssText = 'display:flex;gap:4px;align-items:center;font-size:11px;white-space:nowrap;margin-top:2px';
  const noBtn = document.createElement('button');
  noBtn.textContent = 'Não';
  noBtn.style.cssText = 'padding:1px 6px;border:1px solid #d1d5db;border-radius:4px;background:#fff;cursor:pointer;font-size:11px';
  noBtn.onmousedown = (e) => e.stopPropagation();
  noBtn.onclick = (e) => { e.stopPropagation(); confirmEl.remove(); btn.style.display = ''; };

  const yesBtn = document.createElement('button');
  yesBtn.textContent = 'Sim';
  yesBtn.style.cssText = 'padding:1px 6px;border:none;border-radius:4px;background:#dc2626;color:#fff;cursor:pointer;font-size:11px';
  yesBtn.onmousedown = (e) => e.stopPropagation();
  yesBtn.onclick = async (e) => {
    e.stopPropagation();
    card.style.opacity = '0.3';
    try {
      const res  = await fetch('/kanban/delete/' + id, { method: 'POST' });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'erro');
      const lane = card.closest('.lane');
      card.remove();
      const cnt  = lane.querySelectorAll('.task-card').length;
      const badge = document.getElementById('count-' + lane.dataset.status);
      if (badge) badge.textContent = cnt;
      if (cnt === 0 && !lane.querySelector('.empty-lane'))
        lane.insertAdjacentHTML('beforeend','<div class="empty-lane"><div class="empty-icon">○</div>Sem tarefas aqui</div>');
      if (typeof showToast === 'function') showToast('Tarefa excluída', 'ok');
    } catch (err) {
      card.style.opacity = '1';
      confirmEl.remove();
      btn.style.display = '';
      if (typeof showToast === 'function') showToast('Falha ao excluir', 'err');
    }
  };

  const label = document.createElement('span');
  label.textContent = 'Excluir?';
  label.style.color = '#6b7280';
  confirmEl.append(label, noBtn, yesBtn);
  btn.parentNode.insertBefore(confirmEl, btn.nextSibling);
}
</script>"""
    return page_shell("Kanban SEO", body)


@app.post("/kanban/update/<rec_id>")
def update_kanban_card(rec_id):
    data   = request.get_json(silent=True) or {}
    status = normalize_recommendation_status(data.get("status"))
    try:
        get_supabase().table("recommendations").update({"status": status}).eq("id", rec_id).execute()
        return jsonify({"ok": True, "status": status})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503


@app.post("/kanban/delete/<rec_id>")
def delete_kanban_card(rec_id):
    try:
        get_supabase().table("recommendations").delete().eq("id", rec_id).execute()
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503




@app.route("/blog-ideas")
def blog_ideas():
    try:
        sb     = get_supabase()
        page   = max(1, int(request.args.get("page", 1) or 1))
        offset = (page - 1) * PAGE_SIZE

        res  = (
            sb.table("content_changes")
            .select("id, status, url, provider, meta_title, meta_description, raw, created_at", count="exact")
            .ilike("provider", "query_suggester%")
            .order("created_at", desc=True)
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        rows  = res.data
        total = res.count or 0
    except Exception as exc:
        return error_page(str(exc)), 503

    STATUS_MAP = {"pending": ("badge-medium", "pendente"), "approved": ("badge-low", "aprovado"), "published": ("badge-info", "publicado")}

    cards_html = ""
    for row in rows:
        raw    = row.get("raw") or {}
        intent = raw.get("search_intent") or raw.get("intent", "")
        angle  = raw.get("angle") or raw.get("content_type", "")
        score  = raw.get("opportunity_score", "—")
        impr   = raw.get("impressions", 0)
        status = row.get("status", "pending")
        queries_list = raw.get("queries", [])[:5]
        sections_list = raw.get("sections", [])[:3]

        badge_cls, badge_label = STATUS_MAP.get(status, ("badge-gray", status))
        row_id    = str(row.get("id") or "")
        row_title = esc(row.get("meta_title", "") or "")
        meta_desc = esc(row.get("meta_description", "") or "")
        btn_label = "Ver conteúdo" if status == "approved" else "Gerar conteúdo"
        btn_icon  = "📄" if status == "approved" else "✨"

        query_tags = "".join(
            f'<span style="background:#f1f5f9;color:#475569;font-size:11px;padding:2px 8px;border-radius:10px;white-space:nowrap">{esc(q)}</span>'
            for q in queries_list
        )
        section_tags = "".join(
            f'<span style="background:#f0fdf4;color:#166534;font-size:11px;padding:2px 8px;border-radius:10px">{esc(s)}</span>'
            for s in sections_list
        )
        impr_fmt = f"{int(impr):,}".replace(",", ".") if impr else "—"

        cards_html += f"""
<div style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:20px 24px;margin-bottom:12px;display:flex;gap:20px;align-items:flex-start">
  <div style="flex:1;min-width:0">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap">
      <span class="badge {badge_cls}">{badge_label}</span>
      <span style="font-size:12px;color:var(--muted)">Score <strong>{esc(str(score))}</strong></span>
      <span style="font-size:12px;color:var(--muted)">{impr_fmt} impressões</span>
      {f'<span style="font-size:11px;background:#f3f4f6;color:#6b7280;padding:2px 8px;border-radius:8px">{esc(intent)}</span>' if intent else ''}
      {f'<span style="font-size:11px;background:#eff6ff;color:#1d4ed8;padding:2px 8px;border-radius:8px">{esc(angle)}</span>' if angle else ''}
    </div>
    <div style="font-size:16px;font-weight:700;color:#111827;margin-bottom:4px;line-height:1.4">{row_title}</div>
    {f'<div style="font-size:13px;color:#6b7280;margin-bottom:10px;line-height:1.5">{meta_desc}</div>' if meta_desc else ''}
    {f'<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px">{query_tags}</div>' if query_tags else ''}
    {f'<div style="display:flex;flex-wrap:wrap;gap:4px">{section_tags}</div>' if section_tags else ''}
  </div>
  <div style="flex-shrink:0;padding-top:4px">
    <button class="btn btn-sm btn-primary" onclick="openGenerate('{row_id}','{row_title}')">{btn_icon} {btn_label}</button>
  </div>
</div>"""

    modal = """
<div id="gen-modal" style="display:none;position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,.55);overflow-y:auto">
  <div style="background:#fff;max-width:900px;margin:32px auto;border-radius:12px;overflow:hidden;box-shadow:0 8px 40px rgba(0,0,0,.25)">
    <div style="display:flex;align-items:center;justify-content:space-between;padding:18px 24px;border-bottom:1px solid #e5e7eb">
      <div>
        <strong id="gen-title" style="font-size:16px"></strong>
        <span id="gen-provider-label" style="margin-left:10px;font-size:12px;color:#6b7280">Post de blog gerado por IA</span>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn btn-sm" onclick="copyHtml()" id="copy-btn" style="display:none">Copiar HTML</button>
        <button class="btn btn-sm" onclick="closeGenerate()">Fechar</button>
      </div>
    </div>
    <div id="gen-loading" style="padding:48px;text-align:center;color:#6b7280">
      <div style="font-size:32px;margin-bottom:16px">✨</div>
      <p id="gen-loading-msg" style="font-size:15px;margin-bottom:6px">Gerando conteúdo com Gemini...</p>
      <p id="gen-loading-sub" style="font-size:12px;color:#9ca3af">Isso pode levar 15–30 segundos</p>
    </div>
    <div id="gen-error" style="display:none;padding:32px 24px;color:#b91c1c;background:#fef2f2;margin:16px;border-radius:8px"></div>
    <div id="gen-tabs" style="display:none">
      <div style="padding:0 24px;border-bottom:1px solid #e5e7eb;display:flex;gap:0">
        <button onclick="showTab('preview')" id="tab-preview" class="gen-tab gen-tab-active">Pré-visualização</button>
        <button onclick="showTab('html')" id="tab-html" class="gen-tab">HTML</button>
      </div>
      <div id="tab-preview-content" style="padding:24px;max-height:65vh;overflow-y:auto">
        <div id="gen-preview" style="font-family:Georgia,serif;line-height:1.7;color:#1f2937;max-width:720px;margin:0 auto"></div>
      </div>
      <div id="tab-html-content" style="display:none;padding:24px">
        <textarea id="gen-html" readonly style="width:100%;height:55vh;font-family:monospace;font-size:12px;border:1px solid #e5e7eb;border-radius:6px;padding:12px;resize:vertical"></textarea>
      </div>
    </div>
  </div>
</div>
<style>
.gen-tab{padding:10px 18px;border:none;background:none;cursor:pointer;font-size:13px;font-weight:500;color:#6b7280;border-bottom:2px solid transparent}
.gen-tab-active{color:var(--brand);border-bottom-color:var(--brand)}
#gen-preview h1{font-size:26px;font-weight:700;line-height:1.3;margin:0 0 12px}
#gen-preview h2{font-size:20px;font-weight:700;margin:32px 0 10px;color:#111827;border-bottom:2px solid #f3f4f6;padding-bottom:6px}
#gen-preview h3{font-size:17px;font-weight:600;margin:22px 0 8px;color:#374151}
#gen-preview p{margin:0 0 14px;line-height:1.75}
#gen-preview ul,#gen-preview ol{margin:0 0 14px;padding-left:22px}
#gen-preview li{margin-bottom:7px;line-height:1.65}
#gen-preview table{width:100%;border-collapse:collapse;margin:20px 0;font-size:14px}
#gen-preview th{background:#f9fafb;font-weight:600;padding:10px 12px;text-align:left;border:1px solid #e5e7eb}
#gen-preview td{padding:9px 12px;border:1px solid #e5e7eb}
#gen-preview tr:nth-child(even) td{background:#fafafa}
#gen-preview .lead{font-size:17px;color:#374151;border-left:4px solid var(--brand);padding-left:16px;margin-bottom:24px;line-height:1.8}
#gen-preview .meta{font-size:12px;color:#9ca3af;margin-bottom:20px}
#gen-preview .toc{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px 20px;margin:20px 0}
#gen-preview .toc h2,.gen-preview .toc h3{margin-top:0;font-size:15px}
#gen-preview .toc ol,.gen-preview .toc ul{margin-bottom:0}
#gen-preview .toc a{color:var(--brand);text-decoration:none;font-size:14px}
#gen-preview .tip-box{background:#f0fdf4;border:1px solid #86efac;border-radius:8px;padding:16px 20px;margin:24px 0}
#gen-preview .tip-box h3{color:#166534;margin-top:0}
#gen-preview .highlight-box{background:#fffbeb;border-left:4px solid #f59e0b;padding:14px 18px;margin:20px 0;border-radius:0 6px 6px 0}
#gen-preview .product-recommendation{background:#faf5ff;border:1px solid #d8b4fe;border-radius:8px;padding:16px 20px;margin:24px 0}
#gen-preview .product-recommendation h3{color:#6b21a8;margin-top:0}
#gen-preview .product-recommendation a{color:#7c3aed;font-weight:600}
#gen-preview .faq{margin-top:32px}
#gen-preview .faq details{border-bottom:1px solid #e5e7eb;padding:12px 0}
#gen-preview .faq summary{font-weight:600;cursor:pointer;color:#1f2937;font-size:15px}
#gen-preview .faq details p{margin:10px 0 4px;color:#4b5563}
#gen-preview figure.image-suggestion{background:#f0f9ff;border:1px dashed #7dd3fc;border-radius:8px;margin:24px 0;overflow:hidden}
#gen-preview figure.image-suggestion img{display:block;width:100%;height:220px;object-fit:cover;background:#dbeafe url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='48' height='48' viewBox='0 0 24 24' fill='none' stroke='%2393c5fd' stroke-width='1.5'%3E%3Crect x='3' y='3' width='18' height='18' rx='2'/%3E%3Ccircle cx='8.5' cy='8.5' r='1.5'/%3E%3Cpath d='M21 15l-5-5L5 21'/%3E%3C/svg%3E") center/48px no-repeat}
#gen-preview figure.image-suggestion figcaption{padding:10px 14px;font-size:12px;color:#0369a1;font-style:italic;border-top:1px dashed #bae6fd}
#gen-preview .cta-final{background:var(--brand);color:#fff;border-radius:10px;padding:24px;margin:32px 0;text-align:center}
#gen-preview .cta-final a{color:#fff;font-weight:700;text-decoration:underline}
#gen-preview .satellite-content{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px 20px;margin:24px 0}
#gen-preview .satellite-content h3{margin-top:0;font-size:15px;color:#374151}
</style>
<script>
var _genHtml = '';
var _genCountdown = null;

function _setLoadingMsg(msg, sub) {
  document.getElementById('gen-loading-msg').textContent = msg;
  document.getElementById('gen-loading-sub').textContent = sub || '';
}

function _countdown(secs, id, title) {
  var remaining = secs;
  _setLoadingMsg('Quota atingida — aguardando...', 'Tentando novamente em ' + remaining + 's');
  _genCountdown = setInterval(function() {
    remaining--;
    if (remaining <= 0) {
      clearInterval(_genCountdown);
      _genCountdown = null;
      _setLoadingMsg('Gerando conteúdo com Gemini...', 'Segunda tentativa...');
      _doFetch(id, title, false);
    } else {
      document.getElementById('gen-loading-sub').textContent = 'Tentando novamente em ' + remaining + 's';
    }
  }, 1000);
}

function _doFetch(id, title, allowRetry) {
  fetch('/blog-ideas/' + id + '/generate', {method:'POST'})
    .then(function(r){ return r.json(); })
    .then(function(d) {
      if (d.rate_limited && allowRetry) {
        _countdown(90, id, title);
        return;
      }
      document.getElementById('gen-loading').style.display = 'none';
      if (d.rate_limited) {
        var el = document.getElementById('gen-error');
        el.textContent = 'Quota Gemini esgotada mesmo após aguardar. Espere alguns minutos e tente novamente.';
        el.style.display = 'block';
        return;
      }
      if (d.error) {
        var el = document.getElementById('gen-error');
        el.textContent = d.error;
        el.style.display = 'block';
        return;
      }
      if (!d.html) {
        var el = document.getElementById('gen-error');
        el.textContent = 'Gemini respondeu mas o conteúdo veio vazio. Tente novamente.';
        el.style.display = 'block';
        return;
      }
      _genHtml = d.html;
      if (d.provider) document.getElementById('gen-provider-label').textContent = 'Gerado por ' + d.provider;
      document.getElementById('gen-preview').innerHTML = _genHtml;
      document.getElementById('gen-html').value = _genHtml;
      document.getElementById('gen-tabs').style.display = 'block';
      document.getElementById('copy-btn').style.display = 'inline-block';
      showTab('preview');
    })
    .catch(function(e) {
      document.getElementById('gen-loading').style.display = 'none';
      var el = document.getElementById('gen-error');
      el.textContent = 'Erro de rede: ' + e.message;
      el.style.display = 'block';
    });
}

function openGenerate(id, title) {
  if (_genCountdown) { clearInterval(_genCountdown); _genCountdown = null; }
  document.getElementById('gen-title').textContent = title;
  document.getElementById('gen-loading').style.display = 'block';
  document.getElementById('gen-error').style.display = 'none';
  document.getElementById('gen-tabs').style.display = 'none';
  document.getElementById('copy-btn').style.display = 'none';
  document.getElementById('gen-modal').style.display = 'block';
  document.body.style.overflow = 'hidden';
  _setLoadingMsg('Gerando conteúdo com Gemini...', 'Isso pode levar 15–30 segundos');
  _doFetch(id, title, true);
}
function closeGenerate() {
  if (_genCountdown) { clearInterval(_genCountdown); _genCountdown = null; }
  document.getElementById('gen-modal').style.display = 'none';
  document.body.style.overflow = '';
  location.reload();
}
function showTab(tab) {
  document.getElementById('tab-preview-content').style.display = tab === 'preview' ? 'block' : 'none';
  document.getElementById('tab-html-content').style.display = tab === 'html' ? 'block' : 'none';
  document.getElementById('tab-preview').className = 'gen-tab' + (tab === 'preview' ? ' gen-tab-active' : '');
  document.getElementById('tab-html').className = 'gen-tab' + (tab === 'html' ? ' gen-tab-active' : '');
}
function copyHtml() {
  navigator.clipboard.writeText(_genHtml).then(function() {
    var btn = document.getElementById('copy-btn');
    var orig = btn.textContent;
    btn.textContent = 'Copiado!';
    setTimeout(function(){ btn.textContent = orig; }, 2000);
  });
}
</script>"""

    empty = '<div style="text-align:center;color:var(--muted);padding:48px 0">Nenhuma ideia salva. Use <a href="/tools?module=blog-ideas">Ferramentas → Ideias de Blog</a> para gerar.</div>'
    body = f"""
{modal}
<div class="section-head">
  <h1>Ideias de blog</h1>
  <a href="/tools?module=blog-ideas" class="btn btn-primary">+ Gerar ideias</a>
</div>
<p class="muted" style="margin-bottom:20px">Geradas a partir das queries do GSC com Gemini. Clique em <strong>Gerar conteúdo</strong> para criar o post completo.</p>
{cards_html or empty}
{_pagination_html(page, total, PAGE_SIZE, "/blog-ideas?")}"""
    return page_shell("Ideias de Blog", body)


@app.route("/blog-ideas/<idea_id>/generate", methods=["POST"])
def blog_idea_generate(idea_id):
    try:
        sb  = get_supabase()
        res = sb.table("content_changes").select("*").eq("id", idea_id).single().execute()
        row = res.data
    except Exception as exc:
        return jsonify({"html": None, "error": f"Ideia não encontrada: {exc}"}), 404

    raw  = row.get("raw") or {}
    idea = {**raw, "meta_title": row.get("meta_title", ""), "meta_description": row.get("meta_description", "")}

    from modules import blog_content
    result = blog_content.generate(idea)

    if not result.get("error"):
        try:
            sb.table("content_changes").update({"status": "approved"}).eq("id", idea_id).execute()
        except Exception:
            pass

    return jsonify(result)


TOOL_GROUPS = [
    {
        "label": "Performance & Rastreamento",
        "color": "#2563eb",
        "tools": [
            {
                "key": "gsc-api",
                "name": "Tendências GSC (API ao vivo)",
                "desc": "Compara semana atual vs anterior via API do Google Search Console. Detecta quedas de impressões e cliques por página.",
                "icon": "📉",
                "tags": ["urls", "comparison"],
                "badge": "API",
            },
            {
                "key": "keyword-tracker",
                "name": "Rastreamento de Posições",
                "desc": "Monitora a posição média das principais keywords por marca. Alerta quando uma página sai do top 10.",
                "icon": "📍",
                "tags": ["urls"],
                "badge": "API",
            },
            {
                "key": "cannibalization",
                "name": "Canibalização de Keywords",
                "desc": "Detecta quando duas ou mais páginas competem pela mesma keyword no Google. Ex: /lacoste e /tenis-lacoste ambas aparecem para 'tênis lacoste'.",
                "icon": "⚔️",
                "tags": [],
                "badge": "API",
            },
        ],
    },
    {
        "label": "Auditoria Técnica",
        "color": "#7c3aed",
        "tools": [
            {
                "key": "onpage",
                "name": "Auditoria On-Page",
                "desc": "Verifica title, H1, meta description, imagens e canônicas das páginas prioritárias.",
                "icon": "🔍",
                "tags": ["urls"],
            },
            {
                "key": "broken-links",
                "name": "Links Quebrados",
                "desc": "Rastreia o site e encontra links 404, cadeias de redirect e páginas órfãs sem links internos.",
                "icon": "🔗",
                "tags": ["urls", "max_pages"],
            },
            {
                "key": "sitemap",
                "name": "Sitemap & Robots.txt",
                "desc": "Verifica se páginas prioritárias estão no sitemap.xml e não estão bloqueadas no robots.txt.",
                "icon": "🗺️",
                "tags": [],
            },
            {
                "key": "indexability",
                "name": "Indexabilidade",
                "desc": "Detecta páginas com noindex acidental, canonicals erradas ou redirects que impedem indexação.",
                "icon": "🔎",
                "tags": ["urls"],
            },
            {
                "key": "duplicates",
                "name": "Conteúdo Duplicado",
                "desc": "Encontra titles e descriptions idênticos ou muito semelhantes entre páginas, que confundem a escolha de canônica pelo Google.",
                "icon": "📄",
                "tags": ["urls"],
            },
            {
                "key": "schema-check",
                "name": "Schema Markup",
                "desc": "Verifica se as páginas têm structured data correto (Product, BreadcrumbList, Organization). Ausência de schema = sem rich snippets.",
                "icon": "🏷️",
                "tags": ["urls"],
            },
        ],
    },
    {
        "label": "Inteligência IA",
        "color": "#8f1d2c",
        "tools": [
            {
                "key": "ai-insights",
                "name": "AI Insights — Gemini",
                "desc": "Busca dados ao vivo do GSC API e envia para o Gemini gerar resumo executivo, alertas críticos, saúde das marcas, tarefas ICE e rewrites de snippet. Gemini é usado automaticamente.",
                "icon": "✨",
                "tags": [],
                "badge": "Gemini",
            },
            {
                "key": "blog-ideas",
                "name": "Ideias de Blog",
                "desc": "Busca queries ao vivo do GSC API e usa Gemini para sugerir artigos com alto potencial de tráfego.",
                "icon": "✍️",
                "tags": ["top"],
                "badge": "Gemini",
            },
        ],
    },
    {
        "label": "Monitoramento",
        "color": "#16a34a",
        "tools": [
            {
                "key": "monitor",
                "name": "Monitor de Páginas",
                "desc": "Verifica disponibilidade e status HTTP das páginas prioritárias. Detecta quedas e redirecionamentos inesperados.",
                "icon": "🟢",
                "tags": ["urls", "max_pages"],
            },
        ],
    },
    {
        "label": "Sistema",
        "color": "#64748b",
        "tools": [
            {
                "key": "doctor",
                "name": "Diagnóstico do Ambiente",
                "desc": "Verifica chaves de API, conexão com Supabase, pasta GSC e dependências instaladas.",
                "icon": "🩺",
                "tags": [],
            },
        ],
    },
]


@app.route("/tools", methods=["GET", "POST"])
def tools():
    job_id = request.args.get("job_id", "")
    active_module = request.args.get("module", "") or request.form.get("module", "")
    job    = _get_job(job_id) if job_id else None
    values = {
        "module":         active_module or "gsc",
        "gsc":            request.form.get("gsc", "./gsc_exports"),
        "urls":           request.form.get("urls", ""),
        "top":            request.form.get("top", "10"),
        "max_pages":      request.form.get("max_pages", "200"),
        "provider":       request.form.get("provider", "auto"),
        "comparison":     request.form.get("comparison", "week"),
        "ai":             request.form.get("ai", "on"),
        "changes_log":    request.form.get("changes_log", os.environ.get("SEO_CHANGELOG_CSV", "")),
    }

    if request.method == "POST":
        try:
            job_id = start_tool_job(request.form)
            return redirect(url_for("tools", job_id=job_id, module=values["module"]))
        except Exception as exc:
            job = {"status": "failed", "error": str(exc), "stdout": "", "stderr": ""}

    # Build tool cards HTML
    tool_cards_html = ""
    for group in TOOL_GROUPS:
        cards = ""
        for t in group["tools"]:
            is_active = values["module"] == t["key"]
            badge = f'<span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px;background:var(--brand-mid);color:var(--brand-dark);margin-left:6px">{esc(t["badge"])}</span>' if t.get("badge") else ""
            active_cls = " tool-card-active" if is_active else ""
            cards += f"""
<div class="tool-card{active_cls}" data-module="{esc(t['key'])}" data-tags="{esc(','.join(t['tags']))}"
     onclick="selectTool('{esc(t['key'])}',{list(t['tags'])!r})">
  <div class="tool-card-icon">{esc(t['icon'])}</div>
  <div class="tool-card-body">
    <div class="tool-card-name">{esc(t['name'])}{badge}</div>
    <div class="tool-card-desc">{esc(t['desc'])}</div>
  </div>
</div>"""
        tool_cards_html += f"""
<div style="margin-bottom:20px">
  <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:{esc(group['color'])};margin-bottom:8px">{esc(group['label'])}</div>
  {cards}
</div>"""

    provider_options = "".join(
        f'<option value="{esc(k)}" {"selected" if values["provider"] == k else ""}>{esc(k)}</option>'
        for k in AI_PROVIDERS
    )

    output  = format_job_output(job) if job else "Selecione uma ferramenta e clique em Executar.\nA saída aparece aqui em tempo real."
    banner  = job_banner_html(job)
    poll_js = ""
    if job_id:
        poll_js = f"""<script>
    (function poll() {{
      fetch('/tools/status/{esc(job_id)}')
        .then(r => r.json())
        .then(data => {{
          document.getElementById('tool-output').textContent = data.output;
          const b = document.getElementById('job-banner');
          b.className = 'job-banner job-' + data.status;
          if (data.status === 'running') {{
            b.innerHTML = '<span><span class="spinner"></span> <strong>Executando…</strong></span><span>Saída sendo atualizada.</span>';
            setTimeout(poll, 1500);
          }} else if (data.status === 'completed') {{
            b.innerHTML = '<span><strong>✓ Concluído</strong></span><span>Ferramenta terminou com sucesso.</span>';
          }} else {{
            b.innerHTML = '<span><strong>✗ Falhou</strong></span><span>Veja a saída para detalhes.</span>';
          }}
        }});
    }})();
  </script>"""

    body = f"""
<div class="section-head" style="margin-bottom:20px">
  <div>
    <h1>Ferramentas SEO</h1>
    <p class="muted" style="margin-top:4px">Execute módulos de auditoria sem abrir o terminal.</p>
  </div>
</div>
<div style="display:grid;grid-template-columns:320px 1fr 1fr;gap:20px;align-items:start">

  <!-- Coluna 1: seleção de ferramenta -->
  <div class="panel" style="padding:18px 16px;max-height:82vh;overflow-y:auto">
    <div style="font-size:12px;font-weight:600;color:var(--muted);margin-bottom:14px;text-transform:uppercase;letter-spacing:.5px">Selecione a ferramenta</div>
    {tool_cards_html}
  </div>

  <!-- Coluna 2: configuração -->
  <form class="panel" method="post" id="tool-form"
        onsubmit="document.getElementById('run-btn').disabled=true;
                  document.getElementById('run-btn').textContent='Executando…';
                  document.getElementById('tool-output').textContent='Iniciando…';">
    <input type="hidden" name="module" id="module-input" value="{esc(values['module'])}">
    <div id="selected-tool-header" style="margin-bottom:18px">
      <div style="font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">Ferramenta selecionada</div>
      <div id="selected-tool-name" style="font-size:16px;font-weight:700;color:var(--ink)">—</div>
    </div>

    <div class="field is-hidden" data-field="gsc">
      <label>Pasta com CSVs do GSC</label>
      <input name="gsc" value="{esc(values['gsc'])}">
    </div>
    <div class="field is-hidden" data-field="urls">
      <label>URLs de foco <span class="muted">(opcional)</span></label>
      <input name="urls" value="{esc(values['urls'])}" placeholder="/lacoste /tenis-lacoste">
    </div>
    <div class="field is-hidden" data-field="comparison">
      <label>Período de comparação</label>
      <select name="comparison">
        <option value="week"  {"selected" if values['comparison'] == 'week'  else ""}>Semana anterior (últimos 7 dias vs 7 anteriores)</option>
        <option value="month" {"selected" if values['comparison'] == 'month' else ""}>Mês anterior (28 dias vs 28 dias anteriores)</option>
        <option value="year"  {"selected" if values['comparison'] == 'year'  else ""}>Ano anterior — YoY (últimos 28 dias vs mesmo período há 1 ano)</option>
      </select>
    </div>
    <div class="field is-hidden" data-field="top">
      <label>Número de oportunidades</label>
      <input name="top" type="number" min="1" max="100" value="{esc(values['top'])}">
    </div>
    <div class="field is-hidden" data-field="max_pages">
      <label>Máx. páginas a monitorar</label>
      <input name="max_pages" type="number" min="1" max="2000" value="{esc(values['max_pages'])}">
    </div>
    <div class="field is-hidden" data-field="changes_log">
      <label>Arquivo CSV de mudanças</label>
      <input name="changes_log" value="{esc(values['changes_log'])}" placeholder="caminho/para/controle.csv">
    </div>
    <div class="field is-hidden" data-field="provider">
      <label>Provedor de IA</label>
      <select name="provider">{provider_options}</select>
    </div>
    <div class="field is-hidden" data-field="ai">
      <label class="check-label">
        <input type="checkbox" name="ai" {"checked" if values['ai'] else ""}> Usar IA para enriquecer resultados
      </label>
    </div>
    <button id="run-btn" class="btn btn-primary is-hidden" type="submit" style="width:100%;justify-content:center;padding:11px;margin-top:16px;font-size:14px">
      ▶ Executar
    </button>
  </form>

  <!-- Coluna 3: saída -->
  <section class="panel">
    <div style="font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">Saída</div>
    {banner}
    <pre id="tool-output" class="output" style="min-height:300px">{esc(output)}</pre>
  </section>
</div>

<style>
  .tool-card {{
    display:flex; gap:12px; align-items:flex-start;
    padding:10px 12px; border-radius:var(--radius-sm);
    border:1px solid transparent; cursor:pointer;
    transition:background .15s, border-color .15s;
    margin-bottom:6px;
  }}
  .tool-card:hover {{ background:var(--line-light); border-color:var(--line); }}
  .tool-card-active {{ background:var(--brand-light); border-color:var(--brand-mid) !important; }}
  .tool-card-icon {{ font-size:20px; flex-shrink:0; margin-top:1px; }}
  .tool-card-name {{ font-size:13px; font-weight:700; color:var(--ink); margin-bottom:2px; }}
  .tool-card-desc {{ font-size:11px; color:var(--muted); line-height:1.45; }}
  .check-label {{ display:flex; align-items:center; gap:8px; font-size:13px; cursor:pointer; }}
</style>

<script>
  const TOOL_META = {{}};
  {''.join(f'TOOL_META["{t["key"]}"] = {{name: "{t["name"]}", tags: {t["tags"]!r}}};' for g in TOOL_GROUPS for t in g["tools"])}

  function selectTool(key, tags) {{
    // Update hidden input & header
    document.getElementById('module-input').value = key;
    const meta = TOOL_META[key] || {{}};
    document.getElementById('selected-tool-name').textContent = meta.name || key;

    // Highlight active card
    document.querySelectorAll('.tool-card').forEach(el => {{
      el.classList.toggle('tool-card-active', el.dataset.module === key);
    }});

    // Show/hide fields
    const show = new Set(tags || meta.tags || []);
    ['gsc','urls','comparison','top','max_pages','changes_log','provider','ai'].forEach(f => {{
      document.querySelectorAll('[data-field="' + f + '"]').forEach(el => {{
        el.classList.toggle('is-hidden', !show.has(f));
      }});
    }});

    // Show run button once a tool is selected
    document.getElementById('run-btn').classList.remove('is-hidden');
  }}

  // Init with current selection
  const initModule = document.getElementById('module-input').value;
  if (initModule && TOOL_META[initModule]) {{
    selectTool(initModule, TOOL_META[initModule].tags);
  }}
</script>
{poll_js}"""
    return page_shell("Ferramentas SEO", body)


@app.route("/tools/status/<job_id>")
def tool_status(job_id):
    job = _get_job(job_id)
    if not job:
        return jsonify({"status": "missing", "output": "Job não encontrado."}), 404
    return jsonify({"status": job.get("status", "running"), "output": format_job_output(job)})




@app.route("/url")
def url_detail():
    try:
        target  = request.args.get("target", "")
        sb      = get_supabase()
        issues  = sb.table("issues").select("severity, source, issue_type, title, status").ilike("target", f"%{target}%").limit(50).execute().data
        recs    = sb.table("recommendations").select("priority, source, action, reason, status").ilike("target", f"%{target}%").limit(50).execute().data
    except Exception as exc:
        return error_page(str(exc)), 503

    def sev_badge(s):
        cls = {"high": "badge-high", "medium": "badge-medium", "low": "badge-low"}.get(s, "badge-gray")
        return f'<span class="badge {cls}">{esc(s)}</span>'

    issue_rows = "".join(
        f"<tr><td>{sev_badge(i.get('severity', ''))}</td>"
        f"<td><span class='badge badge-brand'>{esc(i.get('source', ''))}</span></td>"
        f"<td>{esc(i.get('title', ''))}</td>"
        f"<td><span class='badge badge-gray'>{esc(i.get('status', ''))}</span></td></tr>"
        for i in issues
    )
    rec_rows = "".join(
        f"<tr><td><span class='badge badge-gray'>{esc(r.get('priority', ''))}</span></td>"
        f"<td><span class='badge badge-brand'>{esc(r.get('source', ''))}</span></td>"
        f"<td style='font-weight:600'>{esc(r.get('action', ''))}</td>"
        f"<td><span class='badge badge-gray'>{esc(r.get('status', ''))}</span></td></tr>"
        for r in recs
    )

    body = f"""
<div class="section-head">
  <h1>Detalhe da URL</h1>
  <a href="javascript:history.back()" class="btn btn-ghost">← Voltar</a>
</div>
<div class="panel" style="margin-bottom:20px">
  <p style="font-size:13px;color:var(--muted);margin-bottom:4px">Alvo</p>
  <code style="font-size:13px">{esc(target)}</code>
</div>

<h2 style="margin-bottom:10px">Issues ({len(issues)})</h2>
<div class="table-wrap" style="margin-bottom:20px">
  <table>
    <thead><tr><th>Severidade</th><th>Fonte</th><th>Issue</th><th>Status</th></tr></thead>
    <tbody>{issue_rows or '<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:20px">Sem issues.</td></tr>'}</tbody>
  </table>
</div>

<h2 style="margin-bottom:10px">Backlog ({len(recs)})</h2>
<div class="table-wrap">
  <table>
    <thead><tr><th>Prior.</th><th>Fonte</th><th>Ação</th><th>Status</th></tr></thead>
    <tbody>{rec_rows or '<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:20px">Sem ações.</td></tr>'}</tbody>
  </table>
</div>"""
    return page_shell("Detalhe URL", body)


@app.route("/reports")
def reports_list():
    import json as _json
    from pathlib import Path
    from config import REPORTS_FOLDER

    folder = Path(REPORTS_FOLDER)
    files  = sorted(folder.glob("report_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)

    # Module key → friendly label
    _MOD_LABELS = {
        "gsc_api": "Tendências GSC", "gsc": "GSC CSV", "onpage": "On-Page",
        "backlog": "Backlog", "broken_links": "Links Quebrados",
        "sitemap": "Sitemap", "indexability": "Indexabilidade",
        "duplicates": "Duplicados", "keyword_tracker": "Posições",
        "schema_check": "Schema", "cannibalization": "Canibalização",
        "ai_insights": "AI Insights", "ai_analysis": "Análise IA",
        "blog_ideas": "Blog Ideas", "monitor": "Monitor",
    }

    rows_html = ""
    for f in files:
        try:
            d = _json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            d = {}

        ts      = esc(d.get("_generated_at", f.stem.replace("report_", "").replace("_", " ", 1)))
        label   = esc(d.get("_label", "") or "completo")
        size_kb = round(f.stat().st_size / 1024, 1)

        # Detect which modules have data
        mods = [_MOD_LABELS[k] for k in _MOD_LABELS if d.get(k)]
        mods_html = " ".join(f'<span class="tag">{m}</span>' for m in mods) if mods else '<span style="color:var(--muted);font-size:12px">—</span>'

        fname = esc(f.name)
        rows_html += f"""
<tr>
  <td style="white-space:nowrap;font-size:13px">{ts}</td>
  <td style="font-size:13px">{label}</td>
  <td style="line-height:1.8">{mods_html}</td>
  <td style="text-align:right;color:var(--muted);font-size:12px">{size_kb} KB</td>
  <td style="text-align:center">
    <a href="/report?file={fname}" style="font-size:13px;font-weight:600;color:var(--primary);text-decoration:none">Ver</a>
  </td>
</tr>"""

    if not rows_html:
        rows_html = '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:32px">Nenhum relatório arquivado ainda. Rode uma auditoria em <a href="/tools">Ferramentas</a>.</td></tr>'

    body = f"""
<div class="section-head" style="margin-bottom:20px">
  <h1>Relatórios</h1>
  <a href="/report" style="font-size:13px;color:var(--primary);text-decoration:none;font-weight:600">&#8594; Ver último relatório</a>
</div>
<div class="panel">
  <div class="table-wrap">
    <table>
      <thead><tr><th>Data/hora</th><th>Escopo</th><th>Módulos</th><th style="text-align:right">Tamanho</th><th></th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
</div>"""
    return page_shell("Relatórios", body)


@app.route("/report")
def report_view():
    import json as _json
    from pathlib import Path
    from config import REPORTS_FOLDER

    # Support ?file=report_YYYY-MM-DD_HH-MM.json to view a specific snapshot
    requested_file = request.args.get("file", "").strip()
    folder = Path(REPORTS_FOLDER)

    if requested_file:
        # Security: only allow filenames, no path traversal
        safe_name = Path(requested_file).name
        if not safe_name.startswith("report_") or not safe_name.endswith(".json"):
            return error_page("Arquivo inválido."), 400
        report_file = folder / safe_name
    else:
        report_file = folder / "latest_report.json"

    if not report_file.exists():
        body = """
<div class="section-head"><h1>Relatório</h1></div>
<div class="no-insights">
  <h3>Nenhum relatório gerado ainda</h3>
  <p>Rode uma auditoria em <a href="/tools">Ferramentas</a> para gerar o primeiro relatório.</p>
</div>"""
        return page_shell("Relatório", body)

    try:
        data = _json.loads(report_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return error_page(str(exc)), 503

    generated_at = esc(data.get("_generated_at", ""))
    label        = esc(data.get("_label", "") or "completo")

    def _section(title, icon, content):
        if not content:
            return ""
        return f"""
<div class="panel" style="margin-bottom:20px">
  <div class="panel-head"><h2 class="panel-title">{icon} {title}</h2></div>
  {content}
</div>"""

    # ── GSC ────────────────────────────────────────────────────────────────────
    gsc = data.get("gsc") or {}
    gsc_html = ""
    if gsc:
        bm = gsc.get("benchmarks", {})
        ctr_avg = bm.get("avg_ctr", 0)
        pos_avg = round(bm.get("avg_position", 0), 1)
        gsc_html += f'<div style="display:flex;gap:24px;margin-bottom:16px"><span><strong>CTR médio:</strong> {ctr_avg:.2%}</span><span><strong>Posição média:</strong> {pos_avg}</span></div>'
        qws = gsc.get("quick_wins", [])[:15]
        if qws:
            rows = "".join(
                f"<tr><td>{esc(r.get('query',''))}</td>"
                f"<td>{round(float(r.get('position',0)),1)}</td>"
                f"<td>{int(r.get('impressions',0)):,}</td>"
                f"<td>{float(r.get('ctr',0)):.2%}</td></tr>"
                for r in qws
            )
            gsc_html += f'<p style="font-weight:600;margin-bottom:8px">Quick Wins ({len(qws)})</p><div class="table-wrap"><table><thead><tr><th>Query</th><th>Posição</th><th>Impressões</th><th>CTR</th></tr></thead><tbody>{rows}</tbody></table></div>'
        ctrs = gsc.get("low_ctr_pages", [])[:10]
        if ctrs:
            ctr_rows = []
            for r in ctrs:
                r_page = str(r.get("page") or "")
                r_impr = int(r.get("impressions") or 0)
                r_ctr  = float(r.get("ctr") or 0)
                r_pot  = int(r.get("potential_clicks") or 0)
                ctr_rows.append(
                    f"<tr><td><a href='/url?target={quote(r_page)}' style='font-size:12px'>{esc(r_page[-60:])}</a></td>"
                    f"<td>{r_impr:,}</td><td>{r_ctr:.2%}</td><td>+{r_pot:,}</td></tr>"
                )
            rows = "".join(ctr_rows)
            gsc_html += f'<p style="font-weight:600;margin:14px 0 8px">Páginas com CTR Baixo ({len(ctrs)})</p><div class="table-wrap"><table><thead><tr><th>Página</th><th>Impressões</th><th>CTR</th><th>Potencial</th></tr></thead><tbody>{rows}</tbody></table></div>'

    # ── On-page ────────────────────────────────────────────────────────────────
    onpage = data.get("onpage") or []
    onpage_html = ""
    if onpage:
        grade_map = {"A": "badge-ok", "B": "badge-info", "C": "badge-medium", "D": "badge-high", "F": "badge-high"}
        op_rows = []
        for p in onpage:
            p_url   = str(p.get("url") or "")
            p_grade = str(p.get("grade") or "")
            p_gcls  = grade_map.get(p_grade, "badge-gray")
            p_iss   = esc("; ".join((p.get("issues") or [])[:2]))
            p_warn  = esc("; ".join((p.get("warnings") or [])[:2]))
            op_rows.append(
                f"<tr><td><a href='/url?target={quote(p_url)}' style='font-size:12px'>{esc(p_url[-55:])}</a></td>"
                f"<td><span class='badge {p_gcls}'>{esc(p_grade)}</span></td>"
                f"<td>{p_iss}</td><td>{p_warn}</td></tr>"
            )
        rows = "".join(op_rows)
        onpage_html = f'<div class="table-wrap"><table><thead><tr><th>URL</th><th>Grade</th><th>Issues</th><th>Avisos</th></tr></thead><tbody>{rows}</tbody></table></div>'

    # ── Backlog top 15 ─────────────────────────────────────────────────────────
    backlog = data.get("backlog") or []
    backlog_html = ""
    if backlog:
        def pri_cls(p):
            try:
                v = float(p or 0)
                return "badge-high" if v >= 15 else ("badge-medium" if v >= 8 else "badge-gray")
            except Exception:
                return "badge-gray"
        bl_rows = []
        for i in backlog[:15]:
            i_pri    = str(i.get("priority") or "")
            i_src    = str(i.get("source") or "")
            i_action = str(i.get("action") or "")
            i_target = str(i.get("target") or "")[-60:]
            bl_rows.append(
                f"<tr><td><span class='badge {pri_cls(i_pri)}'>{esc(i_pri)}</span></td>"
                f"<td><span class='badge badge-brand'>{esc(i_src)}</span></td>"
                f"<td style='font-weight:600'>{esc(i_action)}</td>"
                f"<td style='font-size:12px;color:var(--muted)'>{esc(i_target)}</td></tr>"
            )
        rows = "".join(bl_rows)
        backlog_html = f'<div class="table-wrap"><table><thead><tr><th>Prior.</th><th>Fonte</th><th>Ação</th><th>Alvo</th></tr></thead><tbody>{rows}</tbody></table></div>'

    # ── GSC API drops + AI analysis ────────────────────────────────────────────
    gsc_api = data.get("gsc_api") or {}
    gsc_api_html = ""
    chart_js_html = ""
    if gsc_api and not gsc_api.get("error"):
        drops          = gsc_api.get("drops") or []
        brand_sum      = gsc_api.get("brand_summary") or {}
        ai_anal        = gsc_api.get("ai_analysis") or {}
        tag_sug        = gsc_api.get("tag_suggestions") or []
        page_cont      = gsc_api.get("page_content") or []
        period_cur     = esc(gsc_api.get("period_current", ""))
        period_prev    = esc(gsc_api.get("period_previous", ""))
        comp_label     = esc(gsc_api.get("comparison_label", "Semana anterior"))
        total_cur      = int(gsc_api.get("total_pages_cur") or 0)
        n_crit         = sum(1 for d in drops if d.get("severity") == "critical")
        n_warn         = len(drops) - n_crit

        # Stats bar
        gsc_api_html += f"""
<div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:20px;font-size:13px;padding:12px 16px;background:var(--surface);border-radius:8px;border:1px solid var(--border)">
  <span><strong>Comparação:</strong> {comp_label}</span>
  <span><strong>Período atual:</strong> {period_cur}</span>
  <span><strong>vs:</strong> {period_prev}</span>
  <span><strong>Páginas monitoradas:</strong> {total_cur:,}</span>
  <span style="color:#dc2626;font-weight:700">&#8595; {n_crit} quedas críticas</span>
  <span style="color:#d97706;font-weight:600">&#9651; {n_warn} avisos</span>
</div>"""

        # AI insights block
        if ai_anal and ai_anal.get("_ai_ok"):
            resumo   = esc(ai_anal.get("resumo_executivo") or "")
            padroes  = ai_anal.get("padroes") or []
            acoes    = ai_anal.get("acoes_prioritarias") or []
            urgentes = ai_anal.get("paginas_criticas") or []
            em_risco = ai_anal.get("marcas_em_risco") or []

            ai_parts = f'<div class="summary-box" style="margin-bottom:20px"><h3 style="margin-bottom:8px">Análise Gemini</h3><p style="margin:0">{resumo}</p></div>' if resumo else ""

            if em_risco:
                risk_cards = ""
                for r in em_risco:
                    m_name  = esc(str(r.get("marca") or "").replace("_", " ").title())
                    m_tier  = esc(str(r.get("tier") or ""))
                    m_res   = esc(str(r.get("resumo") or ""))
                    m_pri   = str(r.get("prioridade") or "media").lower()
                    t_cls   = "badge-high" if m_tier == "top" else "badge-medium"
                    p_cls   = "badge-high" if m_pri == "alta" else "badge-medium"
                    risk_cards += (
                        f"<div style='background:var(--surface);border:1px solid var(--border);border-radius:8px;"
                        f"padding:12px 16px;min-width:220px;flex:1'>"
                        f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px'>"
                        f"<strong>{m_name}</strong>"
                        f"<span class='badge {t_cls}' style='font-size:10px'>{m_tier}</span>"
                        f"<span class='badge {p_cls}' style='font-size:10px'>{m_pri}</span>"
                        f"</div><div style='font-size:12px;color:var(--ink-mid)'>{m_res}</div></div>"
                    )
                ai_parts += f'<h4 style="margin:0 0 10px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em">Marcas em Risco</h4><div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:20px">{risk_cards}</div>'

            if padroes:
                imp_map   = {"alto": "badge-high", "medio": "badge-medium", "baixo": "badge-gray"}
                def _p_row(p):
                    imp_val = str(p.get("impacto") or "")
                    imp_cls = imp_map.get(imp_val, "badge-gray")
                    return (
                        f"<tr><td style='font-weight:600'>{esc(p.get('nome',''))}</td>"
                        f"<td>{esc(p.get('descricao',''))}</td>"
                        f"<td>{esc(', '.join(p.get('marcas_afetadas') or []))}</td>"
                        f"<td><span class='badge {imp_cls}'>{esc(imp_val)}</span></td></tr>"
                    )
                p_rows = "".join(_p_row(p) for p in padroes)
                ai_parts += f'<h4 style="margin:0 0 8px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em">Padrões Identificados</h4><div class="table-wrap" style="margin-bottom:20px"><table><thead><tr><th>Padrão</th><th>Descrição</th><th>Marcas</th><th>Impacto</th></tr></thead><tbody>{p_rows}</tbody></table></div>'

            if acoes:
                a_rows = "".join(
                    f"<tr><td style='font-weight:700;color:#dc2626;text-align:center'>{esc(str(a.get('prioridade','')))}</td>"
                    f"<td style='font-weight:600'>{esc(a.get('acao',''))}</td>"
                    f"<td style='font-size:11px'>{esc(', '.join(a.get('paginas') or []))}</td>"
                    f"<td style='font-size:12px;color:var(--ink-mid)'>{esc(a.get('justificativa',''))}</td></tr>"
                    for a in acoes
                )
                ai_parts += f'<h4 style="margin:0 0 8px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em">Ações Prioritárias</h4><div class="table-wrap" style="margin-bottom:20px"><table><thead><tr><th>#</th><th>Ação</th><th>Páginas</th><th>Justificativa</th></tr></thead><tbody>{a_rows}</tbody></table></div>'

            gsc_api_html += f'<div style="margin-bottom:24px">{ai_parts}</div>'

        # Brand summary mini-cards
        if brand_sum:
            cards = ""
            for brand, info in list(brand_sum.items())[:8]:
                b_label  = brand.replace("_", " ").title()
                b_tier   = str(info.get("tier") or "")
                b_crit   = int(info.get("n_critical") or 0)
                b_warn   = int(info.get("n_warning") or 0)
                b_ctr    = int(info.get("n_ctr_issue") or 0)
                b_lost   = int(info.get("impressions_lost") or 0)
                b_gained = int(info.get("impressions_gained") or 0)
                b_net    = int(info.get("net_change") or 0)
                t_cls    = "badge-high" if b_tier == "top" else ("badge-medium" if b_tier == "good" else "badge-gray")
                # Border color: green if net positive, red if net negative, amber if zero
                border_col = "#16a34a" if b_net > 0 else ("#dc2626" if b_net < 0 else "#d97706")
                net_col    = "#16a34a" if b_net > 0 else "#dc2626"
                net_sign   = "+" if b_net >= 0 else ""
                ctr_badge  = f"<span style='color:#6b7280;margin-left:6px'>{b_ctr} ctr</span>" if b_ctr else ""
                cards += (
                    f"<div style='background:var(--surface);border:1px solid var(--border);border-left:3px solid {border_col};"
                    f"border-radius:8px;padding:10px 14px;min-width:180px;flex:1'>"
                    f"<div style='display:flex;align-items:center;gap:6px;margin-bottom:4px'>"
                    f"<span style='font-weight:600;font-size:13px'>{esc(b_label)}</span>"
                    f"<span class='badge {t_cls}' style='font-size:10px'>{esc(b_tier)}</span></div>"
                    f"<div style='font-size:12px'>"
                    f"<span style='color:#dc2626;margin-right:8px'>&#8595; {b_crit} críticas</span>"
                    f"<span style='color:#d97706'>{b_warn} avisos</span>{ctr_badge}</div>"
                    f"<div style='font-size:11px;margin-top:4px'>"
                    f"<span style='color:#dc2626'>&#8595; {b_lost:,} perdidas</span>"
                    f"<span style='color:#16a34a;margin-left:8px'>&#8593; {b_gained:,} ganhas</span></div>"
                    f"<div style='font-size:12px;font-weight:600;color:{net_col};margin-top:2px'>Neta: {net_sign}{b_net:,}</div>"
                    f"</div>"
                )
            gsc_api_html += f'<h4 style="margin:0 0 10px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em">Variação por Marca</h4><div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:24px">{cards}</div>'

        # ── Charts (Chart.js) ──────────────────────────────────────────────────
        import json as _json
        if drops and brand_sum:
            b_labels = [b.replace("_", " ").title() for b in list(brand_sum.keys())[:8]]
            b_net    = [int(v.get("net_change") or 0) for v in list(brand_sum.values())[:8]]
            b_colors = ["#16a34a" if n >= 0 else "#dc2626" for n in b_net]

            top_drops = [d for d in drops if d.get("severity") in ("critical", "warning")][:12]
            _s_base   = get_site_url()
            d_labels  = [str(d.get("page") or "").replace(_s_base, "")[-45:] for d in top_drops]
            d_vals    = [round((d.get("impressions_delta") or 0) * 100, 1) for d in top_drops]
            d_colors  = ["#dc2626" if d.get("severity") == "critical" else "#d97706" for d in top_drops]

            n_crit_chart = sum(1 for d in drops if d.get("severity") == "critical")
            n_warn_chart = sum(1 for d in drops if d.get("severity") == "warning")
            n_ctr_chart  = sum(1 for d in drops if d.get("severity") == "ctr_issue")

            b_labels_j = _json.dumps(b_labels)
            b_net_j    = _json.dumps(b_net)
            b_colors_j = _json.dumps(b_colors)
            d_labels_j = _json.dumps(d_labels)
            d_vals_j   = _json.dumps(d_vals)
            d_colors_j = _json.dumps(d_colors)

            chart_js_html = f"""
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<div style="display:grid;grid-template-columns:1fr 1fr 220px;gap:20px;margin-bottom:28px">
  <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px">
    <div style="font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:12px">Variação Neta de Impressões por Marca</div>
    <canvas id="chartBrand" height="180"></canvas>
  </div>
  <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px">
    <div style="font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:12px">Top Quedas de Impressões (%)</div>
    <canvas id="chartDrops" height="180"></canvas>
  </div>
  <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;display:flex;flex-direction:column;align-items:center;justify-content:center">
    <div style="font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:12px">Distribuição</div>
    <canvas id="chartDist" width="140" height="140"></canvas>
    <div style="font-size:11px;margin-top:10px;text-align:center">
      <span style="color:#dc2626;font-weight:600">{n_crit_chart} críticas</span> &nbsp;
      <span style="color:#d97706">{n_warn_chart} avisos</span>
      {'&nbsp; <span style="color:#6b7280">' + str(n_ctr_chart) + ' ctr</span>' if n_ctr_chart else ''}
    </div>
  </div>
</div>
<script>
(function(){{
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  const gridColor = isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)';
  const textColor = isDark ? '#9ca3af' : '#6b7280';
  Chart.defaults.color = textColor;

  new Chart(document.getElementById('chartBrand'), {{
    type: 'bar',
    data: {{
      labels: {b_labels_j},
      datasets: [{{ label: 'Variação neta', data: {b_net_j},
        backgroundColor: {b_colors_j}, borderRadius: 4 }}]
    }},
    options: {{ indexAxis: 'y', plugins: {{ legend: {{ display: false }} }},
      scales: {{ x: {{ grid: {{ color: gridColor }} }}, y: {{ grid: {{ display: false }} }} }} }}
  }});

  new Chart(document.getElementById('chartDrops'), {{
    type: 'bar',
    data: {{
      labels: {d_labels_j},
      datasets: [{{ label: 'Queda (%)', data: {d_vals_j},
        backgroundColor: {d_colors_j}, borderRadius: 4 }}]
    }},
    options: {{ indexAxis: 'y', plugins: {{ legend: {{ display: false }} }},
      scales: {{ x: {{ grid: {{ color: gridColor }} }}, y: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 10 }} }} }} }} }}
  }});

  const distData = [{n_crit_chart}, {n_warn_chart}{', ' + str(n_ctr_chart) if n_ctr_chart else ''}];
  const distLabels = ['Críticas', 'Avisos'{", 'CTR'" if n_ctr_chart else ''}];
  const distColors = ['#dc2626', '#d97706'{", '#6b7280'" if n_ctr_chart else ''}];
  new Chart(document.getElementById('chartDist'), {{
    type: 'doughnut',
    data: {{
      labels: distLabels,
      datasets: [{{ data: distData,
        backgroundColor: distColors, borderWidth: 0 }}]
    }},
    options: {{ cutout: '65%', plugins: {{ legend: {{ display: false }} }} }}
  }});
}})();
</script>"""

            gsc_api_html += chart_js_html

        # ── Tag suggestions (before / after) ──────────────────────────────────
        if tag_sug:
            # Build lookup from page_content by path
            content_by_path = {}
            _site_base = get_site_url()
            for pc in page_cont:
                path = pc.get("url", "").replace(_site_base, "")
                content_by_path[path] = pc

            sug_cards = ""
            for s in tag_sug:
                s_page  = str(s.get("page") or "")
                pc      = content_by_path.get(s_page) or {}
                brand   = str(pc.get("brand") or "").replace("_", " ").title()
                tier    = str(pc.get("tier") or "")
                impr_d  = pc.get("impressions_delta")
                sev     = str(pc.get("severity") or "")
                t_cls   = "badge-high" if tier == "top" else ("badge-medium" if tier == "good" else "badge-gray")
                sev_cls = "badge-high" if sev == "critical" else ("badge-medium" if sev == "warning" else "badge-gray")
                impr_str = f"{impr_d:+.0%}" if impr_d is not None else ""
                issue   = esc(str(s.get("main_issue") or ""))
                pri     = str(s.get("priority") or "media")
                pri_cls = "badge-high" if pri == "alta" else "badge-medium"
                full_url = get_site_url() + s_page

                is_blocked = pc.get("blocked", False)
                blocked_note = ""
                if is_blocked:
                    blocked_note = '<div style="font-size:11px;color:#6b7280;margin-bottom:8px;padding:5px 8px;background:rgba(0,0,0,.04);border-radius:4px">&#128274; Página bloqueada pelo WAF — tags atuais não verificadas. Sugestões geradas com base na URL/marca.</div>'

                def _diff_row(label, current, suggested):
                    raw_cur = str(current or "")
                    if not raw_cur or "bloqueado" in raw_cur.lower():
                        cur_html = "<span style='color:var(--muted);font-style:italic'>não verificado</span>"
                    else:
                        cur_html = esc(raw_cur)
                    sug_v    = esc(str(suggested or "—"))
                    sug_style = "color:#16a34a;font-weight:600" if suggested else "color:var(--muted)"
                    return (
                        f"<tr><td style='font-size:11px;color:var(--muted);white-space:nowrap;padding-right:12px'>{label}</td>"
                        f"<td style='font-size:12px'>{cur_html}</td>"
                        f"<td style='font-size:12px;{sug_style}'>{sug_v}</td></tr>"
                    )

                diff_rows = (
                    _diff_row("Title", pc.get("title"), s.get("suggested_title"))
                    + _diff_row("H1", pc.get("h1"), s.get("suggested_h1"))
                    + _diff_row("Description", pc.get("description"), s.get("suggested_description"))
                )

                sug_cards += f"""
<div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 20px;margin-bottom:14px">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap">
    <a href="{esc(full_url)}" target="_blank" style="font-size:13px;font-weight:600">{esc(s_page)}</a>
    {f'<span class="badge {t_cls}" style="font-size:10px">{esc(brand)} {esc(tier)}</span>' if brand else ''}
    <span class="badge {sev_cls}" style="font-size:10px">{esc(sev)} {impr_str}</span>
    <span class="badge {pri_cls}" style="font-size:10px">prioridade {esc(pri)}</span>
  </div>
  {blocked_note}
  {f'<div style="font-size:12px;color:#7c3aed;margin-bottom:10px;padding:6px 10px;background:rgba(124,58,237,.07);border-radius:6px">&#9888; {issue}</div>' if issue else ''}
  <table style="width:100%;border-collapse:collapse">
    <thead><tr>
      <th style="font-size:11px;color:var(--muted);text-align:left;padding-bottom:4px;width:90px">Tag</th>
      <th style="font-size:11px;color:var(--muted);text-align:left;padding-bottom:4px">Atual</th>
      <th style="font-size:11px;color:#16a34a;text-align:left;padding-bottom:4px">&#10003; Sugerido</th>
    </tr></thead>
    <tbody>{diff_rows}</tbody>
  </table>
</div>"""

            h4_style = "margin:0 0 12px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em"
            gsc_api_html += f'<h4 style="{h4_style}">Sugestões de Tags com IA — Antes & Depois</h4>{sug_cards}'

        # ── Drops table ────────────────────────────────────────────────────────
        if drops:
            drop_rows = []
            for d in drops[:40]:
                page      = str(d.get("page") or "").replace(get_site_url(), "")
                full_page = str(d.get("page") or "")
                brand     = str(d.get("brand") or "").replace("_", " ").title()
                tier      = str(d.get("tier") or "")
                sev       = str(d.get("severity") or "warning")
                sev_cls   = "badge-high" if sev == "critical" else ("badge-medium" if sev == "warning" else "badge-gray")
                sev_label = "Crítica" if sev == "critical" else ("Aviso" if sev == "warning" else "CTR")
                t_cls     = "badge-high" if tier == "top" else ("badge-medium" if tier == "good" else "")
                impr      = int(d.get("impressions") or 0)
                impr_d    = d.get("impressions_delta")
                click_d   = d.get("clicks_delta")
                pos       = d.get("position")
                impr_str  = f"{impr_d:+.0%}" if impr_d is not None else "-"
                click_str = f"{click_d:+.0%}" if click_d is not None else "-"
                pos_str   = str(round(pos, 1)) if pos is not None else "-"
                impr_col  = "color:#dc2626;font-weight:600" if (impr_d or 0) <= -0.25 else "color:#d97706;font-weight:600"
                brand_cell = f"<span class='badge {t_cls}' style='font-size:10px'>{esc(brand)}</span>" if brand else "<span style='color:var(--muted);font-size:11px'>—</span>"
                drop_rows.append(
                    f"<tr>"
                    f"<td><a href='{esc(full_page)}' target='_blank' style='font-size:11px;word-break:break-all'>{esc(page)}</a></td>"
                    f"<td>{brand_cell}</td>"
                    f"<td><span class='badge {sev_cls}'>{sev_label}</span></td>"
                    f"<td style='text-align:right'>{impr:,}</td>"
                    f"<td style='text-align:right;{impr_col}'>{impr_str}</td>"
                    f"<td style='text-align:right'>{click_str}</td>"
                    f"<td style='text-align:right'>{pos_str}</td>"
                    f"</tr>"
                )
            rows_html = "".join(drop_rows)
            h4_style2 = "margin:24px 0 8px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em"
            gsc_api_html += f'<h4 style="{h4_style2}">Todas as Páginas com Queda (por impacto)</h4><div class="table-wrap"><table><thead><tr><th>Página</th><th>Marca</th><th>Severidade</th><th>Impressões</th><th>&#916; Impr.</th><th>&#916; Cliques</th><th>Posição</th></tr></thead><tbody>{rows_html}</tbody></table></div>'
        else:
            gsc_api_html += '<p style="color:var(--muted);font-size:13px">Nenhuma queda detectada no período.</p>'

    # ── Keyword Tracker ────────────────────────────────────────────────────────
    kt_data = data.get("keyword_tracker") or {}
    kt_html = ""
    if kt_data and not kt_data.get("error"):
        kt_results = kt_data.get("results") or []
        kt_html += f"""
<div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:20px;font-size:13px;padding:12px 16px;background:var(--surface);border-radius:8px;border:1px solid var(--border)">
  <span><strong>Páginas verificadas:</strong> {esc(str(kt_data.get('pages_checked', 0)))}</span>
  <span style="color:#dc2626;font-weight:700">&#8595; {esc(str(kt_data.get('total_dropped', 0)))} quedas de posição</span>
  <span style="color:#d97706;font-weight:600">&#8855; {esc(str(kt_data.get('total_off_page', 0)))} keywords fora da pág. 1</span>
  <span style="font-size:12px;color:var(--muted)">{esc(kt_data.get('period_current', ''))}</span>
</div>"""
        for page in kt_results:
            if not page.get("top_drops") and not page.get("off_page1"):
                continue
            kw_rows = "".join(
                f"<tr>"
                f"<td>{esc(k.get('query',''))}</td>"
                f"<td style='text-align:center'>{esc(str(k.get('position','')))}</td>"
                f"<td style='text-align:center;color:{'#dc2626' if (k.get('delta') or 0)>0 else '#16a34a'}'>"
                f"{'&#8595;' if (k.get('delta') or 0)>0 else '&#8593;'}{esc(str(abs(k.get('delta') or 0)))}</td>"
                f"<td style='text-align:center'>{esc(str(k.get('impressions',0)))}</td>"
                f"<td><span class='tag' style='background:{'#fef2f2;color:#dc2626' if k.get('status')=='off_page1' else '#fffbeb;color:#d97706'}'>"
                f"{esc(k.get('status',''))}</span></td>"
                f"</tr>"
                for k in (page.get("top_drops") or [])[:8]
            )
            if not kw_rows:
                continue
            brand_badge = f' <span class="tag">{esc(page.get("brand",""))}</span>' if page.get("brand") else ""
            kt_html += f"""
<div style="margin-bottom:16px">
  <h4 style="font-size:13px;font-weight:600;margin:0 0 8px">{esc(page.get('page',''))}{brand_badge}</h4>
  <div class="table-wrap"><table><thead><tr><th>Keyword</th><th>Posição</th><th>&#916;</th><th>Impressões</th><th>Status</th></tr></thead><tbody>{kw_rows}</tbody></table></div>
</div>"""

    # ── Schema Check ───────────────────────────────────────────────────────────
    sc_data = data.get("schema_check") or {}
    sc_html = ""
    if sc_data and not sc_data.get("error"):
        sc_pages = sc_data.get("pages") or []
        sc_html += f"""
<div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:20px;font-size:13px;padding:12px 16px;background:var(--surface);border-radius:8px;border:1px solid var(--border)">
  <span><strong>Páginas auditadas:</strong> {esc(str(sc_data.get('total', 0)))}</span>
  <span style="color:#dc2626;font-weight:700">&#9888; {esc(str(sc_data.get('pages_with_issues', 0)))} com problemas</span>
  <span style="color:#d97706">BreadcrumbList ausente: {esc(str(sc_data.get('missing_breadcrumb', 0)))}</span>
  <span style="color:#d97706">ItemList ausente: {esc(str(sc_data.get('missing_itemlist', 0)))}</span>
  <span><strong>Score médio:</strong> {esc(str(sc_data.get('avg_score', 0)))}/100</span>
</div>"""
        sc_rows = "".join(
            f"<tr>"
            f"<td style='font-size:12px'>{esc(p.get('path', p.get('url','')))}</td>"
            f"<td>{esc(', '.join(p.get('schemas', [])) or '—')}</td>"
            f"<td style='color:#dc2626;font-size:12px'>{esc('; '.join(p.get('issues', [])))}</td>"
            f"<td style='text-align:center'><strong style='color:{'#16a34a' if p.get('score',0)>=80 else '#dc2626'}'>{esc(str(p.get('score', 0)))}</strong></td>"
            f"</tr>"
            for p in sc_pages if p.get("issues") or p.get("missing")
        )
        if sc_rows:
            sc_html += f'<div class="table-wrap"><table><thead><tr><th>Página</th><th>Schemas presentes</th><th>Problemas</th><th>Score</th></tr></thead><tbody>{sc_rows}</tbody></table></div>'

    # ── Cannibalization ────────────────────────────────────────────────────────
    can_data = data.get("cannibalization") or {}
    can_html = ""
    if can_data and not can_data.get("error"):
        can_items = can_data.get("cannibalized") or []
        can_html += f"""
<div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:20px;font-size:13px;padding:12px 16px;background:var(--surface);border-radius:8px;border:1px solid var(--border)">
  <span><strong>Queries canibalizadas:</strong> {esc(str(can_data.get('total', 0)))}</span>
  <span style="color:#dc2626;font-weight:700">&#9888; {esc(str(can_data.get('high', 0)))} críticas</span>
  <span style="color:#d97706">{esc(str(can_data.get('medium', 0)))} médias</span>
  <span style="font-size:12px;color:var(--muted)">{esc(can_data.get('period', ''))}</span>
</div>"""
        sev_colors = {"high": "#fef2f2;color:#dc2626", "medium": "#fffbeb;color:#d97706", "low": "#f0fdf4;color:#16a34a"}
        can_rows = "".join(
            f"<tr>"
            f"<td style='font-weight:600;font-size:13px'>{esc(item.get('query',''))}</td>"
            f"<td style='font-size:12px'>{esc(item.get('dominant_page',''))}<br>"
            f"<span style='color:var(--muted)'>{esc(', '.join(item.get('competing_pages',[])[:3]))}</span></td>"
            f"<td style='text-align:center'>{esc(str(item.get('page_count',0)))}</td>"
            f"<td style='text-align:right'>{esc(str(item.get('total_impressions',0)))}</td>"
            f"<td style='text-align:center'>"
            f"<span class='tag' style='background:{sev_colors.get(item.get('severity','low'))}'>"
            f"{esc(item.get('severity',''))}</span></td>"
            f"</tr>"
            for item in can_items[:50]
        )
        if can_rows:
            can_html += f'<div class="table-wrap"><table><thead><tr><th>Query</th><th>Páginas competindo</th><th>Págs.</th><th>Impressões</th><th>Severidade</th></tr></thead><tbody>{can_rows}</tbody></table></div>'

    no_data_html = ""
    if not any([gsc_html, onpage_html, backlog_html, gsc_api_html, kt_html, sc_html, can_html]):
        no_data_html = """
<div class="no-insights">
  <h3>Relatório sem dados de auditoria</h3>
  <p>Este snapshot não contém dados de GSC, on-page ou backlog.<br>
     Rode <a href="/tools">uma auditoria completa</a> para ver resultados aqui.</p>
</div>"""

    file_label = esc(report_file.name) if requested_file else "Último relatório"
    back_link  = '<a href="/reports" style="font-size:13px;color:var(--primary);text-decoration:none">&#8592; Todos os relatórios</a>'

    body = f"""
<div class="section-head" style="margin-bottom:20px">
  <div style="display:flex;flex-direction:column;gap:4px">
    {back_link}
    <h1 style="margin:0">{file_label}</h1>
    <span style="font-size:12px;color:var(--muted)">{generated_at} &middot; escopo: {label}</span>
  </div>
</div>
{no_data_html}
{_section("Tendências GSC — Quedas & Análise IA", "📉", gsc_api_html)}
{_section("Rastreamento de Posições", "📍", kt_html)}
{_section("Canibalização de Keywords", "⚔️", can_html)}
{_section("Schema Markup", "🏷️", sc_html)}
{_section("Google Search Console", "📊", gsc_html)}
{_section("On-Page Audit", "🔍", onpage_html)}
{_section("Backlog Priorizado", "📋", backlog_html)}"""

    return page_shell("Relatório", body)


@app.route("/ai-insights")
def ai_insights():
    try:
        from modules import gemini_insights
        data = gemini_insights.load_latest()
    except Exception as exc:
        return error_page(str(exc)), 503

    if not data:
        body = """
<div class="section-head"><h1>AI Insights</h1></div>
<div class="no-insights">
  <h3>Nenhum insight gerado ainda</h3>
  <p>Use as <a href="/tools">Ferramentas</a> e selecione o módulo <strong>AI Insights (Gemini)</strong> para gerar insights.</p>
</div>"""
        return page_shell("AI Insights", body)

    generated_at = data.get("_ai_generated_at", "")[:16].replace("T", " ")
    provider     = data.get("_ai_provider", "gemini")

    # ── Executive summary ──────────────────────────────────────────────────────
    summary_html = ""
    if data.get("executive_summary"):
        summary_html = f"""
<div class="summary-box">
  <h3>Resumo Executivo</h3>
  <p>{esc(data['executive_summary'])}</p>
</div>"""

    # ── Critical alerts ────────────────────────────────────────────────────────
    alerts_html = ""
    alerts = data.get("critical_alerts") or []
    if alerts:
        items = ""
        for alert in alerts:
            urgency = str(alert.get("urgency") or "baixa").lower()
            items += f"""
<div class="alert-item urgency-{esc(urgency)}">
  <div class="alert-dot"></div>
  <div>
    <div class="alert-title">{esc(alert.get('title',''))}</div>
    <div class="alert-desc">{esc(alert.get('description',''))}</div>
  </div>
</div>"""
        alerts_html = f"""
<div class="panel" style="margin-bottom:24px">
  <div class="panel-head"><h2 class="panel-title">Alertas Críticos</h2></div>
  {items}
</div>"""

    # ── Brand health ───────────────────────────────────────────────────────────
    brand_html = ""
    brand_health = data.get("brand_health") or {}
    if brand_health:
        cards = ""
        for brand, info in brand_health.items():
            score     = int(info.get("score") or 0)
            tier_val  = str(info.get("tier") or "")
            tier_cls  = "badge-high" if tier_val == "top" else "badge-medium"
            main_iss  = esc(info.get("main_issue") or "")
            priority  = esc(info.get("priority_action") or "")
            brand_label = brand.replace("_", " ").title()
            cards += f"""
<div class="brand-score-item">
  <div class="brand-score-header">
    <span class="brand-score-name">{esc(brand_label)} <span class="badge {tier_cls}" style="font-size:10px;margin-left:4px">{esc(tier_val)}</span></span>
    <span class="brand-score-num">{score}</span>
  </div>
  <div class="brand-score-bar"><div class="brand-score-fill" style="width:{score}%"></div></div>
  <div class="brand-score-meta">{main_iss}</div>
  <div style="font-size:12px;color:var(--ink-mid);margin-top:4px">{priority}</div>
</div>"""
        brand_html = f"""
<div class="panel" style="margin-bottom:24px">
  <div class="panel-head"><h2 class="panel-title">Saúde das Marcas</h2></div>
  <div class="insights-grid" style="margin-bottom:0">{cards}</div>
</div>"""

    # ── AI tasks table ─────────────────────────────────────────────────────────
    tasks_html = ""
    tasks = data.get("tasks") or []
    if tasks:
        def ice_badge(val, thresholds):
            hi, mid = thresholds
            if val >= hi:   return "badge-ok"
            if val >= mid:  return "badge-medium"
            return "badge-high"

        task_rows = ""
        for t in tasks:
            impact     = int(t.get("impact")     or 0)
            confidence = int(t.get("confidence") or 0)
            effort     = int(t.get("effort")     or 1)
            priority   = round((impact * confidence / 100) / max(effort, 1), 1)
            tier_val   = str(t.get("brand_tier") or "")
            tier_cls   = "badge-high" if tier_val == "top" else ("badge-medium" if tier_val == "good" else "badge-info")
            owner      = str(t.get("owner") or "SEO")
            task_rows += (
                f"<tr>"
                f"<td><strong>{priority}</strong></td>"
                f"<td style='max-width:300px'><strong>{esc(t.get('action',''))}</strong>"
                f"<br><small style='color:var(--muted)'>{esc(str(t.get('target',''))[:80])}</small></td>"
                f"<td style='max-width:280px;font-size:12px;color:var(--ink-mid)'>{esc(str(t.get('reason',''))[:120])}</td>"
                f"<td><span class='badge {ice_badge(impact,[70,40])}'>{impact}</span></td>"
                f"<td><span class='badge badge-gray'>{confidence}</span></td>"
                f"<td><span class='badge badge-gray'>{effort}</span></td>"
                f"<td><span class='badge badge-brand'>{esc(owner)}</span></td>"
                f"<td><span class='badge {tier_cls}'>{esc(tier_val) or 'all'}</span></td>"
                f"</tr>"
            )
        tasks_html = f"""
<div class="panel" style="margin-bottom:24px">
  <div class="panel-head">
    <h2 class="panel-title">Tarefas Geradas pela IA ({len(tasks)})</h2>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr>
        <th>Prior.</th><th>Ação</th><th>Motivo</th>
        <th>Impact</th><th>Conf.</th><th>Effort</th><th>Owner</th><th>Tier</th>
      </tr></thead>
      <tbody>{task_rows}</tbody>
    </table>
  </div>
</div>"""

    # ── Snippet rewrites ───────────────────────────────────────────────────────
    snippets_html = ""
    snippets = data.get("snippet_rewrites") or []
    if snippets:
        cards = ""
        for s in snippets:
            cards += f"""
<div class="snippet-card">
  <div class="snippet-url">{esc(s.get('url',''))}</div>
  <div class="snippet-problem">Problema: {esc(s.get('current_problem',''))}</div>
  <div class="snippet-label">Title sugerido</div>
  <div class="snippet-value">{esc(s.get('suggested_title',''))}</div>
  <div class="snippet-label">Meta description sugerida</div>
  <div class="snippet-value">{esc(s.get('suggested_description',''))}</div>
</div>"""
        snippets_html = f"""
<div class="panel" style="margin-bottom:24px">
  <div class="panel-head"><h2 class="panel-title">Reescritas de Snippet ({len(snippets)})</h2></div>
  {cards}
</div>"""

    # ── Content gaps ───────────────────────────────────────────────────────────
    gaps_html = ""
    gaps = data.get("content_gaps") or []
    if gaps:
        items = ""
        for g in gaps:
            impr = int(g.get("impressions") or 0)
            items += f"""
<div class="gap-item">
  <span class="gap-type">{esc(g.get('type',''))}</span>
  <div class="gap-title">{esc(g.get('title',''))}</div>
  <div class="gap-query">Query-alvo: {esc(g.get('target_query',''))} &middot; {impr:,} impressões</div>
  <div class="gap-reason">{esc(g.get('rationale',''))}</div>
</div>"""
        gaps_html = f"""
<div class="panel" style="margin-bottom:24px">
  <div class="panel-head"><h2 class="panel-title">Lacunas de Conteúdo ({len(gaps)})</h2></div>
  {items}
</div>"""

    body = f"""
<div class="section-head">
  <h1>AI Insights <span class="ai-badge">&#10024; {esc(provider.upper())}</span></h1>
  <span style="font-size:12px;color:var(--muted)">Gerado em {esc(generated_at)}</span>
</div>
{summary_html}
{alerts_html}
{brand_html}
{tasks_html}
{snippets_html}
{gaps_html}"""

    return page_shell("AI Insights", body)


# ═══════════════════════════════════════════════════════════════════════════════
# Full Audit — Master SEO audit with real-time progress via SSE
# ═══════════════════════════════════════════════════════════════════════════════

_AUDIT_JOBS: dict = {}
_AUDIT_LOCK = threading.Lock()

try:
    from config import BASE_DIR as _AUDIT_BASE_DIR
    _LAST_AUDIT_FILE = _AUDIT_BASE_DIR / ".last_full_audit.json"
except Exception:
    import pathlib as _pl
    _LAST_AUDIT_FILE = _pl.Path(".last_full_audit.json")


def _save_last_audit(results: dict) -> None:
    try:
        import json as _jj
        _LAST_AUDIT_FILE.write_text(
            _jj.dumps(results, ensure_ascii=False, default=str), encoding="utf-8"
        )
    except Exception:
        pass


def _load_last_audit() -> dict | None:
    try:
        if _LAST_AUDIT_FILE.exists():
            import json as _jj
            return _jj.loads(_LAST_AUDIT_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None

_AUDIT_CSS = """<style>
/* ── Progress steps ── */
.step-row{display:flex;align-items:center;gap:12px;padding:12px 16px;background:var(--panel);border-radius:8px;border:1px solid var(--line);box-shadow:var(--shadow-sm)}
.step-icon{width:22px;height:22px;flex-shrink:0}
.step-icon.running{animation:spin .9s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.step-label{font-weight:600;font-size:13px;flex:1;color:var(--ink)}
.step-summary{font-size:12px;color:var(--muted);flex:2}
.step-badge{font-size:11px;padding:3px 10px;border-radius:99px;font-weight:700;flex-shrink:0}
.step-badge.ok{background:var(--ok-bg);color:var(--ok)}
.step-badge.error{background:var(--bad-bg);color:var(--bad)}
.step-badge.warn{background:var(--warn-bg);color:var(--warn)}
.step-badge.running{background:var(--info-bg);color:var(--info)}
/* ── Health ring ── */
.health-ring{width:130px;height:130px;position:relative;flex-shrink:0}
.health-ring svg{transform:rotate(-90deg);display:block}
.health-number{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:32px;font-weight:800;line-height:1.1;pointer-events:none}
.health-number .hlabel{font-size:11px;font-weight:500;color:var(--muted);margin-top:2px;text-transform:uppercase;letter-spacing:.05em}
/* ── Report tabs ── */
.rtab-bar{display:flex;gap:2px;border-bottom:2px solid var(--line);margin-bottom:24px;overflow-x:auto}
.rtab{padding:10px 18px;font-size:13px;font-weight:500;color:var(--muted);cursor:pointer;border-radius:6px 6px 0 0;border:none;background:none;white-space:nowrap;transition:color .15s,background .15s}
.rtab:hover{color:var(--ink);background:var(--canvas)}
.rtab.active{color:var(--brand);border-bottom:2px solid var(--brand);margin-bottom:-2px;background:var(--brand-light);font-weight:600}
.rtab-content{display:none}.rtab-content.active{display:block}
/* ── Metric cards ── */
.metric-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:14px;margin-bottom:24px}
.metric-card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:18px 16px;box-shadow:var(--shadow-sm);transition:box-shadow .15s}
.metric-card:hover{box-shadow:var(--shadow-md)}
.metric-card .val{font-size:30px;font-weight:800;line-height:1;margin-bottom:6px;color:var(--ink)}
.metric-card .lbl{font-size:12px;color:var(--muted);font-weight:500}
.metric-card.bad  .val{color:var(--bad)}
.metric-card.ok   .val{color:var(--ok)}
.metric-card.warn .val{color:var(--warn)}
.metric-card.info .val{color:var(--info)}
/* ── Issue severity ── */
.issue-sev-high{color:var(--bad);font-weight:700;font-size:11px;padding:2px 8px;background:var(--bad-bg);border-radius:99px}
.issue-sev-medium{color:var(--warn);font-weight:700;font-size:11px;padding:2px 8px;background:var(--warn-bg);border-radius:99px}
.issue-sev-low{color:var(--muted);font-weight:600;font-size:11px;padding:2px 8px;background:var(--line-light);border-radius:99px}
/* ── Section headers in tabs ── */
.tab-section-title{font-size:15px;font-weight:700;color:var(--ink);margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
</style>"""


def _audit_register(job_id: str) -> _queue_mod.Queue:
    q: _queue_mod.Queue = _queue_mod.Queue()
    with _AUDIT_LOCK:
        _AUDIT_JOBS[job_id] = {"q": q, "status": "running", "result": None}
        if len(_AUDIT_JOBS) > 20:
            oldest = next(iter(_AUDIT_JOBS))
            del _AUDIT_JOBS[oldest]
    return q


def _audit_get(job_id: str) -> dict | None:
    with _AUDIT_LOCK:
        return _AUDIT_JOBS.get(job_id)


def _health_score(results: dict) -> int:
    """
    Balanced score (5-100).
    Base 40 + positive GSC signals (up to +47) - technical/drop penalties (up to -43).
    A large active site with real issues should land 35-55; excellent = 80+.
    """
    score   = 40
    gsc     = results.get("gsc", {})
    gsc_api = results.get("gsc_api", {})

    # ── Positive: traffic scale ───────────────────────────────────────────────
    top_pages    = gsc.get("top_pages", [])
    total_clicks = sum(int(p.get("clicks", 0) or 0) for p in top_pages)
    if   total_clicks >= 15000: score += 20
    elif total_clicks >= 5000:  score += 12
    elif total_clicks >= 1000:  score += 6
    elif total_clicks >= 200:   score += 2

    # ── Positive: ranking position ────────────────────────────────────────────
    top_queries = gsc.get("top_queries", [])
    if top_queries:
        avg_pos = sum(float(q.get("position", 50) or 50) for q in top_queries) / len(top_queries)
        if   avg_pos <= 3:  score += 15
        elif avg_pos <= 7:  score += 10
        elif avg_pos <= 15: score += 5

    # ── Positive: CTR quality ─────────────────────────────────────────────────
    if top_pages:
        tot_imp  = sum(int(p.get("impressions", 0) or 0) for p in top_pages)
        tot_clk  = sum(int(p.get("clicks",      0) or 0) for p in top_pages)
        site_ctr = (tot_clk / tot_imp * 100) if tot_imp > 0 else 0
        if   site_ctr >= 3.0: score += 12
        elif site_ctr >= 1.5: score += 6
        elif site_ctr >= 0.5: score += 2

    # ── Negative: on-page health (grade-based, capped) ────────────────────────
    critical_pages = sum(1 for p in results.get("onpage", [])
                         if isinstance(p, dict) and p.get("grade") in ("D", "F"))
    warn_pages     = sum(1 for p in results.get("onpage", [])
                         if isinstance(p, dict) and p.get("grade") == "C")
    score -= min(critical_pages * 5 + warn_pages * 2, 18)

    # ── Negative: CTR opportunity loss ────────────────────────────────────────
    score -= min(len(gsc.get("low_ctr_pages",   [])) // 4, 8)

    # ── Negative: keyword cannibalization ─────────────────────────────────────
    score -= min(len(gsc.get("cannibalization", [])) // 5, 5)

    # ── Negative: traffic drops (only critical ones) ──────────────────────────
    drops      = gsc_api.get("drops", [])
    crit_drops = sum(1 for d in drops if d.get("severity") == "critical")
    score -= min(crit_drops // 3, 12)

    return max(5, min(100, score))


def _run_full_audit(job_id: str, q: _queue_mod.Queue) -> None:
    def emit(step, label, status, summary="", data=None):
        q.put({"step": step, "label": label, "status": status,
               "summary": summary, "data": data or {}})

    results: dict = {}

    # ── Step 1: GSC ──────────────────────────────────────────────────────────
    emit("gsc", "GSC — Queries & Páginas", "running", "Buscando dados ao vivo...")
    try:
        from run import run_gsc
        gsc_data = run_gsc()
        results["gsc"] = gsc_data
        emit("gsc", "GSC — Queries & Páginas", "ok",
             f"{len(gsc_data.get('top_queries',[]))} queries · "
             f"{len(gsc_data.get('top_pages',[]))} páginas · "
             f"{len(gsc_data.get('quick_wins',[]))} quick wins")
    except Exception as exc:
        emit("gsc", "GSC — Queries & Páginas", "error", str(exc)[:100])
        results["gsc"] = {}

    # ── Step 2: Drops (GSC API) ──────────────────────────────────────────────
    emit("drops", "Detecção de Quedas (API)", "running", "Comparando período atual vs anterior...")
    try:
        from modules import gsc_api as _gapi
        api_data = _gapi.run(results, use_ai=False)
        results["gsc_api"] = api_data
        drops = api_data.get("drops", [])
        crit  = sum(1 for d in drops if d.get("severity") == "critical")
        emit("drops", "Detecção de Quedas (API)", "ok",
             f"{len(drops)} quedas detectadas · {crit} críticas")
    except Exception as exc:
        emit("drops", "Detecção de Quedas (API)", "error", str(exc)[:100])
        results["gsc_api"] = {}

    # ── Step 3: On-page ──────────────────────────────────────────────────────
    emit("onpage", "Auditoria On-Page", "running", "Analisando páginas prioritárias...")
    try:
        from run import run_onpage
        from config import PRIORITY_PAGES
        pages = list(dict.fromkeys(PRIORITY_PAGES))[:25]
        onpage_data = run_onpage(pages)
        results["onpage"] = onpage_data
        highs = sum(1 for p in onpage_data if isinstance(p, dict) and p.get("grade", "A") in ("D", "F"))
        meds  = sum(len(p.get("issues", [])) + len(p.get("warnings", []))
                    for p in onpage_data if isinstance(p, dict) and p.get("grade") == "C")
        total_issues = sum(len(p.get("issues", [])) for p in onpage_data if isinstance(p, dict))
        emit("onpage", "Auditoria On-Page", "ok",
             f"{len(onpage_data)} páginas · {highs} críticos (D/F) · {total_issues} problemas")
    except Exception as exc:
        emit("onpage", "Auditoria On-Page", "error", str(exc)[:100])
        results["onpage"] = []

    # ── Step 4: Content Gap ──────────────────────────────────────────────────
    emit("content", "Lacunas de Conteúdo", "running", "Analisando oportunidades de conteúdo...")
    try:
        from run import run_content_gap
        cg_data = run_content_gap(results.get("gsc", {}))
        results["content_gap"] = cg_data
        gaps = len(cg_data.get("gaps", []))
        opps = len(results.get("gsc", {}).get("content_opps", []))
        emit("content", "Lacunas de Conteúdo", "ok",
             f"{gaps} clusters · {opps} oportunidades de conteúdo")
    except Exception as exc:
        emit("content", "Lacunas de Conteúdo", "error", str(exc)[:100])
        results["content_gap"] = {}

    # ── Step 5: Backlog ──────────────────────────────────────────────────────
    emit("backlog", "Backlog Priorizado", "running", "Calculando ICE score das tarefas...")
    try:
        from actions import backlog as _backlog
        bl_data = _backlog.run(results, limit=30)
        results["backlog"] = bl_data
        highs_bl = sum(1 for t in bl_data if float(t.get("priority", 0)) >= 30)
        emit("backlog", "Backlog Priorizado", "ok",
             f"{len(bl_data)} tarefas · {highs_bl} alta prioridade")
    except Exception as exc:
        emit("backlog", "Backlog Priorizado", "error", str(exc)[:100])
        results["backlog"] = []

    # ── Step 6: AI Analysis ──────────────────────────────────────────────────
    emit("ai", "Análise Estratégica IA", "running", "Gerando insights com IA (30-60s)...")
    try:
        from run import run_ai_analysis
        ai_data = run_ai_analysis(results)
        results["ai_analysis"] = ai_data
        ok = ai_data.get("_ai_enhanced", False)
        emit("ai", "Análise Estratégica IA", "ok" if ok else "warn",
             "Análise gerada" if ok else "IA indisponível — análise básica")
    except Exception as exc:
        emit("ai", "Análise Estratégica IA", "error", str(exc)[:100])
        results["ai_analysis"] = {}

    # ── Done ─────────────────────────────────────────────────────────────────
    import datetime as _dt_audit
    health = _health_score(results)
    results["_health"] = health
    results["_completed_at"] = _dt_audit.datetime.now().strftime("%d/%m/%Y %H:%M")
    _save_last_audit(results)
    q.put({"done": True, "health": health})
    with _AUDIT_LOCK:
        if job_id in _AUDIT_JOBS:
            _AUDIT_JOBS[job_id].update({"status": "done", "result": results})


@app.route("/full-audit")
def full_audit():
    if request.args.get("new") != "1" and _LAST_AUDIT_FILE.exists():
        return redirect("/full-audit/report/last")
    body = """
<div class="section-head">
  <h1>Auditoria Completa</h1>
  <button class="btn btn-primary" id="start-btn" onclick="startAudit()">Iniciar Auditoria</button>
</div>
<p class="muted" style="margin-bottom:24px">Análise completa: GSC, quedas de tráfego, on-page, lacunas de conteúdo, backlog priorizado e análise estratégica com IA.</p>

<!-- Progress panel -->
<div id="progress-panel" style="display:none">
  <div class="panel" style="margin-bottom:20px">
    <div class="panel-head">
      <h2 class="panel-title">Progresso</h2>
      <span id="prog-pct" style="font-size:13px;color:var(--muted)">0%</span>
    </div>
    <div style="background:var(--line);border-radius:99px;height:6px;margin:0 20px 20px">
      <div id="prog-bar" style="background:var(--brand);height:6px;border-radius:99px;width:0%;transition:width .4s"></div>
    </div>
    <div id="steps-list" style="padding:0 20px 20px;display:flex;flex-direction:column;gap:8px"></div>
  </div>
</div>

<!-- Results panel (populated after redirect) -->
<div id="result-panel" style="display:none"></div>

<script>
var _jobId = null;
var _stepOrder = ['gsc','drops','onpage','content','backlog','ai'];
var _stepDone  = 0;
var _stepData  = {};

function startAudit() {
  document.getElementById('start-btn').disabled = true;
  document.getElementById('start-btn').textContent = 'Rodando...';
  document.getElementById('progress-panel').style.display = 'block';
  document.getElementById('steps-list').innerHTML = '';

  fetch('/full-audit/start', {method:'POST'})
    .then(r => r.json())
    .then(d => {
      _jobId = d.job_id;
      var es = new EventSource('/full-audit/stream/' + _jobId);
      es.onmessage = function(e) {
        var ev = JSON.parse(e.data);
        if (ev.keepalive) return;
        if (ev.done) {
          es.close();
          document.getElementById('start-btn').textContent = 'Nova Auditoria';
          document.getElementById('start-btn').disabled = false;
          window.location.href = '/full-audit';
          return;
        }
        handleStep(ev);
      };
      es.onerror = function() {
        es.close();
        document.getElementById('start-btn').disabled = false;
        document.getElementById('start-btn').textContent = 'Tentar novamente';
      };
    });
}

function handleStep(ev) {
  var id = 'step-' + ev.step;
  var el = document.getElementById(id);
  if (!el) {
    el = document.createElement('div');
    el.id = id;
    el.className = 'step-row';
    document.getElementById('steps-list').appendChild(el);
  }
  var iconHtml = {
    running: '<svg class="step-icon running" viewBox="0 0 16 16" fill="none" stroke="#2563eb" stroke-width="2"><path d="M8 2a6 6 0 1 1-4.24 1.76"/></svg>',
    ok:      '<svg class="step-icon" viewBox="0 0 16 16" fill="none" stroke="#16a34a" stroke-width="2"><circle cx="8" cy="8" r="6"/><polyline points="5 8 7 10.5 11 6"/></svg>',
    error:   '<svg class="step-icon" viewBox="0 0 16 16" fill="none" stroke="#dc2626" stroke-width="2"><circle cx="8" cy="8" r="6"/><line x1="6" y1="6" x2="10" y2="10"/><line x1="10" y1="6" x2="6" y2="10"/></svg>',
    warn:    '<svg class="step-icon" viewBox="0 0 16 16" fill="none" stroke="#d97706" stroke-width="2"><path d="M8 2l6 12H2z"/><line x1="8" y1="7" x2="8" y2="10"/><circle cx="8" cy="12" r=".5" fill="#d97706"/></svg>',
  }[ev.status] || '';
  el.innerHTML = iconHtml +
    '<span class="step-label">' + ev.label + '</span>' +
    '<span class="step-summary">' + (ev.summary || '') + '</span>' +
    '<span class="step-badge ' + ev.status + '">' + ev.status + '</span>';
  if (ev.status === 'ok' || ev.status === 'error' || ev.status === 'warn') _stepDone++;
  var pct = Math.round(_stepDone / _stepOrder.length * 100);
  document.getElementById('prog-bar').style.width = pct + '%';
  document.getElementById('prog-pct').textContent = pct + '%';
}
</script>"""
    return page_shell("Auditoria Completa", _AUDIT_CSS + body)


@app.route("/full-audit/start", methods=["POST"])
def full_audit_start():
    job_id = uuid.uuid4().hex
    q = _audit_register(job_id)
    threading.Thread(target=_run_full_audit, args=(job_id, q), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/full-audit/stream/<job_id>")
def full_audit_stream(job_id):
    job = _audit_get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    q = job["q"]

    def generate():
        while True:
            try:
                event = q.get(timeout=55)
                yield f"data: {_json_mod.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("done"):
                    break
            except _queue_mod.Empty:
                yield 'data: {"keepalive":true}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/full-audit/report/<job_id>")
def full_audit_report(job_id):
    if job_id == "last":
        R = _load_last_audit()
        if not R:
            return redirect("/full-audit?new=1")
    else:
        job = _audit_get(job_id)
        if not job or job.get("status") != "done":
            return redirect("/full-audit")
        R = job["result"] or {}

    health       = R.get("_health", 0)
    completed_at = R.get("_completed_at", "")
    gsc          = R.get("gsc", {})
    gsc_api      = R.get("gsc_api", {})
    onpage       = R.get("onpage", [])
    cg           = R.get("content_gap", {})
    backlog      = R.get("backlog", [])
    ai           = R.get("ai_analysis", {})

    # ── Health colour ────────────────────────────────────────────────────────
    hc   = "#16a34a" if health >= 75 else "#d97706" if health >= 50 else "#dc2626"
    hbg  = "#dcfce7" if health >= 75 else "#fef3c7" if health >= 50 else "#fee2e2"
    circ = 2 * 3.14159 * 54
    dash = circ * health / 100
    hlabel = "Excelente" if health >= 75 else "Atenção" if health >= 50 else "Crítico"
    hdesc  = ("Site bem otimizado — mantenha o monitoramento"        if health >= 75
              else "Bom volume, mas há melhorias importantes a fazer" if health >= 50
              else "Problemas sérios detectados — ação urgente necessária")

    highs = sum(1 for p in onpage if isinstance(p, dict) and p.get("grade", "A") in ("D", "F"))
    meds  = sum(1 for p in onpage if isinstance(p, dict) and p.get("grade") == "C")
    total_issues = sum(len(p.get("issues", [])) for p in onpage if isinstance(p, dict))
    drops = gsc_api.get("drops", [])
    qw    = gsc.get("quick_wins", [])
    bm    = gsc.get("benchmarks", {})

    health_html = f"""
<div style="display:flex;align-items:center;gap:28px;margin-bottom:28px;background:var(--panel);
     border:1px solid var(--line);border-radius:var(--radius-lg);padding:28px 32px;box-shadow:var(--shadow-sm)">
  <div class="health-ring">
    <svg width="130" height="130" viewBox="0 0 130 130">
      <circle cx="65" cy="65" r="54" fill="none" stroke="var(--line)" stroke-width="11"/>
      <circle cx="65" cy="65" r="54" fill="none" stroke="{hc}" stroke-width="11"
              stroke-dasharray="{dash:.1f} {circ:.1f}" stroke-linecap="round"/>
    </svg>
    <div class="health-number" style="color:{hc}">{health}<span class="hlabel">/ 100</span></div>
  </div>
  <div style="flex:1">
    <div style="display:inline-block;padding:3px 12px;border-radius:99px;font-size:12px;font-weight:700;
         background:{hbg};color:{hc};margin-bottom:8px">{hlabel}</div>
    <div style="font-size:22px;font-weight:800;margin-bottom:6px;color:var(--ink)">Score de Saúde SEO</div>
    <div style="color:var(--muted);font-size:13px;margin-bottom:16px">{hdesc}</div>
    <div style="display:flex;gap:24px;flex-wrap:wrap">
      <div style="text-align:center">
        <div style="font-size:24px;font-weight:800;color:{'var(--bad)' if highs>5 else 'var(--warn)' if highs>0 else 'var(--ok)'}">{highs}</div>
        <div style="font-size:11px;color:var(--muted)">Críticos</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:24px;font-weight:800;color:var(--warn)">{meds}</div>
        <div style="font-size:11px;color:var(--muted)">Avisos</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:24px;font-weight:800;color:{'var(--bad)' if len(drops)>3 else 'var(--warn)' if drops else 'var(--ok)'}">{len(drops)}</div>
        <div style="font-size:11px;color:var(--muted)">Quedas</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:24px;font-weight:800;color:var(--ok)">{len(qw)}</div>
        <div style="font-size:11px;color:var(--muted)">Quick wins</div>
      </div>
    </div>
  </div>
</div>"""

    # ── Metric cards ─────────────────────────────────────────────────────────
    total_q   = gsc.get("total_queries", len(gsc.get("top_queries", [])))
    total_p   = gsc.get("total_pages",   len(gsc.get("top_pages",   [])))
    avg_pos   = bm.get("avg_position", 0)
    avg_ctr   = bm.get("avg_ctr", 0)
    crit_drops= sum(1 for d in drops if d.get("severity") == "critical")

    def mcard(val, lbl, cls="", icon=""):
        return (f'<div class="metric-card {cls}">'
                f'<div class="val">{val}</div>'
                f'<div class="lbl">{lbl}</div>'
                f'</div>')

    metrics_html = f"""<div class="metric-grid">
  {mcard(f"{total_q:,}", 'Queries monitoradas', 'info')}
  {mcard(f"{total_p:,}", 'Páginas indexadas')}
  {mcard(f"{avg_pos:.1f}", 'Posição média', 'ok' if avg_pos < 6 else 'warn' if avg_pos < 10 else 'bad')}
  {mcard(f"{avg_ctr:.2f}%", 'CTR médio', 'ok' if avg_ctr >= 1.2 else 'warn' if avg_ctr >= 0.8 else 'bad')}
  {mcard(len(drops), 'Quedas detectadas', 'bad' if crit_drops > 0 else 'warn' if drops else 'ok')}
  {mcard(len(qw), 'Quick wins', 'ok' if qw else 'warn')}
  {mcard(total_issues, 'Problemas on-page', 'bad' if highs > 5 else 'warn' if highs > 0 else 'ok')}
  {mcard(len(backlog), 'Tarefas no backlog')}
</div>"""

    # ── Tab: Issues ──────────────────────────────────────────────────────────
    import re as _re

    def _categorize_issue(text: str) -> str:
        """Normalize a variable issue/warning string to a canonical category name."""
        t = str(text)
        pairs = [
            (_re.compile(r"meta title (?:ausente|curto|longo|duplicado)", _re.I), "Meta title"),
            (_re.compile(r"meta description (?:ausente|curta|longa|duplicada)", _re.I), "Meta description"),
            (_re.compile(r"h1 ausente", _re.I), "H1 ausente"),
            (_re.compile(r"múltiplos h1", _re.I), "Múltiplos H1"),
            (_re.compile(r"nenhum h2", _re.I), "H2 ausente"),
            (_re.compile(r"imagens? sem alt", _re.I), "Imagens sem alt text"),
            (_re.compile(r"canonical", _re.I), "Tag canonical"),
            (_re.compile(r"schema|json-ld", _re.I), "Schema markup"),
            (_re.compile(r"meta keywords", _re.I), "Meta keywords"),
            (_re.compile(r"conteúdo escasso|palavras", _re.I), "Conteúdo escasso"),
            (_re.compile(r"html muito grande", _re.I), "HTML muito grande"),
            (_re.compile(r"redirect 302", _re.I), "Redirect 302 temporário"),
            (_re.compile(r"conteúdo misto|http.*https", _re.I), "Conteúdo misto HTTP/HTTPS"),
            (_re.compile(r"órfã|link.*interno.*recebido", _re.I), "Página órfã"),
            (_re.compile(r"inacessível", _re.I), "Página inacessível"),
        ]
        for pattern, label in pairs:
            if pattern.search(t):
                return label
        # Fallback: trim variable parts (numbers, URLs) from start
        clean = _re.sub(r"^\d+\s+", "", t)
        clean = _re.sub(r"https?://\S+", "", clean).strip()
        return clean[:80] if clean else t[:80]

    # Build category → {severity, pages: set, example}
    cat_map: dict = {}
    for page in onpage:
        if not isinstance(page, dict):
            continue
        url = page.get("url", page.get("path", ""))
        grade = page.get("grade", "?")
        for issue in page.get("issues", []):
            cat = _categorize_issue(str(issue))
            if cat not in cat_map:
                cat_map[cat] = {"severity": "issue", "pages": [], "grades": []}
            cat_map[cat]["pages"].append(url)
            cat_map[cat]["grades"].append(grade)
        for warn in page.get("warnings", []):
            cat = _categorize_issue(str(warn))
            if cat not in cat_map:
                cat_map[cat] = {"severity": "warning", "pages": [], "grades": []}
            # Only upgrade to issue if never set as issue
            cat_map[cat]["pages"].append(url)
            cat_map[cat]["grades"].append(grade)

    # Sort by count descending, issues before warnings
    sorted_cats = sorted(
        cat_map.items(),
        key=lambda kv: (0 if kv[1]["severity"] == "issue" else 1, -len(kv[1]["pages"]))
    )

    if not sorted_cats:
        issues_html = '<p style="text-align:center;color:var(--muted);padding:24px">Nenhum problema encontrado.</p>'
    else:
        cat_rows = ""
        for idx, (cat, info) in enumerate(sorted_cats):
            count = len(info["pages"])
            sev = info["severity"]
            badge_color = "var(--bad)" if sev == "issue" else "var(--warn)"
            badge_label = "Erro" if sev == "issue" else "Aviso"
            # deduplicate pages preserving order
            seen = set()
            unique_pages = []
            for p in info["pages"]:
                if p not in seen:
                    seen.add(p)
                    unique_pages.append(p)
            page_links = "".join(
                f'<div style="padding:2px 0;font-size:12px">'
                f'<a href="/url?target={quote(p)}" title="{esc(p)}" '
                f'style="color:var(--accent)">{esc(p.replace(get_site_url(),"") or "/")}</a>'
                f'</div>'
                for p in unique_pages[:50]
            )
            more = f'<div style="color:var(--muted);font-size:11px">...e mais {len(unique_pages)-50} páginas</div>' if len(unique_pages) > 50 else ""
            cat_rows += f"""
<tr class="issue-cat-row" onclick="toggleCatDetail('cat{idx}')" style="cursor:pointer">
  <td style="width:36px;text-align:center;color:var(--muted);font-size:13px">▶</td>
  <td style="font-weight:500">{esc(cat)}</td>
  <td style="width:90px"><span style="background:{badge_color};color:#fff;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">{badge_label}</span></td>
  <td style="width:80px;text-align:right;font-weight:700;color:{badge_color}">{len(unique_pages)} página{"s" if len(unique_pages)!=1 else ""}</td>
</tr>
<tr id="cat{idx}" style="display:none;background:var(--surface2,#f9f9f9)">
  <td></td>
  <td colspan="3" style="padding:8px 4px 12px">{page_links}{more}</td>
</tr>"""

        issues_html = f"""
<style>
.issue-cat-row:hover td {{ background: var(--hover, rgba(0,0,0,.04)); }}
.issue-cat-row.open td:first-child {{ color:var(--accent) }}
</style>
<table class="data-table">
  <thead><tr><th style="width:36px"></th><th>Categoria do problema</th><th>Tipo</th><th style="text-align:right">Páginas afetadas</th></tr></thead>
  <tbody>{cat_rows}</tbody>
</table>
<script>
function toggleCatDetail(id) {{
  var row = document.getElementById(id);
  var btn = row.previousElementSibling.querySelector('td:first-child');
  if (row.style.display === 'none') {{
    row.style.display = '';
    btn.textContent = '▼';
    row.previousElementSibling.classList.add('open');
  }} else {{
    row.style.display = 'none';
    btn.textContent = '▶';
    row.previousElementSibling.classList.remove('open');
  }}
}}
</script>"""

    # ── Tab: Keywords ─────────────────────────────────────────────────────────
    qw_rows = "".join(
        f'<tr><td>{esc(r.get("query",""))}</td>'
        f'<td>{round(float(r.get("position",0)),1)}</td>'
        f'<td>{r.get("impressions",0):,}</td>'
        f'<td style="color:var(--muted);font-size:12px">{esc(r.get("action",""))}</td></tr>'
        for r in qw[:25]
    )
    drop_rows = "".join(
        f'<tr><td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
        f'<a href="/url?target={quote(d.get("page",""))}">{esc(d.get("page","")[-50:])}</a></td>'
        f'<td><span class="badge {"badge-high" if d.get("severity")=="critical" else "badge-medium"}">'
        f'{esc(d.get("severity",""))}</span></td>'
        f'<td style="color:var(--bad)">{d.get("impressions_change_pct",0):+.0f}%</td>'
        f'<td style="color:var(--muted);font-size:12px">{esc(d.get("reason",""))}</td></tr>'
        for d in drops[:20]
    )
    kw_html = f"""
<h3 class="tab-section-title">Quick Wins — posições 6–12 com volume</h3>
<table class="data-table" style="margin-bottom:28px">
  <thead><tr><th>Query</th><th>Posição</th><th>Impressões</th><th>Ação</th></tr></thead>
  <tbody>{qw_rows or '<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:16px">Nenhum quick win encontrado.</td></tr>'}</tbody>
</table>
<h3 class="tab-section-title">Quedas de Tráfego Detectadas</h3>
<table class="data-table">
  <thead><tr><th>Página</th><th>Severidade</th><th>Variação</th><th>Motivo</th></tr></thead>
  <tbody>{drop_rows or '<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:16px">Nenhuma queda detectada.</td></tr>'}</tbody>
</table>"""

    # ── Tab: Conteúdo ─────────────────────────────────────────────────────────
    opps = gsc.get("content_opps", [])
    opp_rows = "".join(
        f'<tr><td>{esc(r.get("query",""))}</td>'
        f'<td>{r.get("impressions",0):,}</td>'
        f'<td>{round(float(r.get("position",0)),1)}</td>'
        f'<td style="color:var(--muted);font-size:12px">{esc(r.get("content_action",""))}</td></tr>'
        for r in opps[:30]
    )
    low_ctr = gsc.get("low_ctr_pages", [])
    lctr_rows = "".join(
        f'<tr><td style="max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
        f'<a href="/url?target={quote(r.get("page",""))}">{esc(r.get("page","")[-55:])}</a></td>'
        f'<td>{r.get("impressions",0):,}</td>'
        f'<td style="color:var(--bad)">{r.get("ctr",0):.2f}%</td>'
        f'<td style="color:var(--ok)">{r.get("expected_ctr",0):.2f}%</td>'
        f'<td style="color:var(--warn)">{r.get("potential_clicks",0):,}</td></tr>'
        for r in low_ctr[:20]
    )
    content_html = f"""
<h3 class="tab-section-title">Oportunidades de Conteúdo por Query</h3>
<table class="data-table" style="margin-bottom:28px">
  <thead><tr><th>Query</th><th>Impressões</th><th>Posição</th><th>Ação sugerida</th></tr></thead>
  <tbody>{opp_rows or '<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:16px">Sem dados.</td></tr>'}</tbody>
</table>
<h3 class="tab-section-title">Páginas com CTR Abaixo do Esperado</h3>
<table class="data-table">
  <thead><tr><th>Página</th><th>Impressões</th><th>CTR atual</th><th>CTR esperado</th><th>Cliques perdidos</th></tr></thead>
  <tbody>{lctr_rows or '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:16px">Sem dados.</td></tr>'}</tbody>
</table>"""

    # ── Tab: Backlog ──────────────────────────────────────────────────────────
    def _short_target(t):
        _b = get_site_url()
        return (t.replace(_b, "").replace(_b.replace("://www.", "://"), "") or t)[:80]

    bl_rows = "".join(
        f'<tr>'
        f'<td style="font-weight:700;color:var(--brand);white-space:nowrap">{round(float(t.get("priority",0)),1)}</td>'
        f'<td style="font-weight:500">{esc(t.get("action",""))}</td>'
        f'<td style="color:var(--ink-mid);font-size:12px;word-break:break-word;max-width:220px">{esc(_short_target(str(t.get("target",""))))}</td>'
        f'<td style="color:var(--muted);font-size:12px">{esc(str(t.get("reason",""))[:120])}</td></tr>'
        for t in backlog[:30]
    )
    backlog_html = f"""
<table class="data-table">
  <thead><tr><th style="width:55px">ICE</th><th>Ação</th><th style="width:220px">Alvo</th><th>Motivo</th></tr></thead>
  <tbody>{bl_rows or '<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:16px">Backlog vazio.</td></tr>'}</tbody>
</table>"""

    # ── Tab: IA ───────────────────────────────────────────────────────────────
    ai_summary      = esc(ai.get("summary", "Análise IA não disponível."))
    ai_risks        = ai.get("critical_risks", [])
    ai_qw           = ai.get("quick_wins", [])
    ai_content      = ai.get("content_opportunities", [])
    ai_tech         = ai.get("technical_recommendations", [])
    ai_actions      = ai.get("next_actions", [])
    ai_confidence   = ai.get("confidence_notes", [])
    ai_input        = ai.get("input_summary", {})
    ai_enhanced     = ai.get("_ai_enhanced", False)

    def _risk_cards(items):
        if not items:
            return '<p style="color:var(--muted);padding:4px 0">Nenhum risco crítico detectado.</p>'
        out = ""
        for r in items[:6]:
            out += (
                f'<div style="border-left:3px solid var(--bad);padding:10px 14px;'
                f'background:var(--bad-bg);border-radius:0 6px 6px 0;margin-bottom:10px">'
                f'<div style="font-weight:700;font-size:13px;color:var(--bad);margin-bottom:4px">{esc(str(r.get("title","")))}​</div>'
                f'<div style="font-size:12px;color:var(--ink-mid);margin-bottom:4px">{esc(str(r.get("why_it_matters","")))}​</div>'
                f'<div style="font-size:12px;color:var(--brand);font-weight:600">→ {esc(str(r.get("next_step","")))}​</div>'
                f'</div>'
            )
        return out

    def _win_cards(items):
        if not items:
            return '<p style="color:var(--muted);padding:4px 0">Nenhum quick win identificado.</p>'
        out = ""
        for r in items[:6]:
            out += (
                f'<div style="border-left:3px solid var(--ok);padding:10px 14px;'
                f'background:var(--ok-bg);border-radius:0 6px 6px 0;margin-bottom:10px">'
                f'<div style="font-weight:700;font-size:13px;color:var(--ok);margin-bottom:4px">{esc(str(r.get("title","")))}​</div>'
                f'<div style="font-size:12px;color:var(--muted);margin-bottom:2px">Alvo: {esc(str(r.get("target","")))}​</div>'
                f'<div style="font-size:12px;color:var(--ink-mid)">Impacto: {esc(str(r.get("expected_impact","")))}​</div>'
                f'</div>'
            )
        return out

    def _content_rows(items):
        if not items:
            return '<p style="color:var(--muted)">Nenhuma oportunidade de conteúdo identificada.</p>'
        out = ""
        for r in items[:8]:
            out += (
                f'<div style="padding:10px 0;border-bottom:1px solid var(--line)">'
                f'<div style="font-weight:600;font-size:13px;margin-bottom:3px">{esc(str(r.get("title","")))}​</div>'
                f'<div style="font-size:12px;color:var(--muted)">Query: <strong>{esc(str(r.get("query_or_cluster","")))}​</strong>'
                f' · Ângulo: {esc(str(r.get("angle","")))}​</div>'
                f'</div>'
            )
        return out

    def _tech_rows(items):
        if not items:
            return '<p style="color:var(--muted)">Nenhuma recomendação técnica adicional.</p>'
        out = ""
        for r in items[:8]:
            out += (
                f'<div style="padding:10px 0;border-bottom:1px solid var(--line)">'
                f'<div style="font-weight:600;font-size:13px;margin-bottom:3px">{esc(str(r.get("title","")))}​</div>'
                f'<div style="font-size:12px;color:var(--muted)">Alvo: {esc(str(r.get("target","")))}​</div>'
                f'<div style="font-size:12px;color:var(--brand);margin-top:2px">Fix: {esc(str(r.get("fix","")))}​</div>'
                f'</div>'
            )
        return out

    def _action_list(items):
        if not items:
            return '<li style="color:var(--muted)">Sem ações definidas.</li>'
        return "".join(
            f'<li style="padding:5px 0;border-bottom:1px solid var(--line-light)">'
            f'<span style="font-weight:600;color:var(--brand);margin-right:6px">{i+1}.</span>'
            f'{esc(str(a))}</li>'
            for i, a in enumerate(items[:10])
        )

    ai_badge = ('<span style="background:var(--ok-bg);color:var(--ok);font-size:11px;font-weight:700;'
                'padding:2px 10px;border-radius:99px;margin-left:10px">IA ativa</span>' if ai_enhanced else
                '<span style="background:var(--warn-bg);color:var(--warn);font-size:11px;font-weight:700;'
                'padding:2px 10px;border-radius:99px;margin-left:10px">IA básica</span>')

    ai_context = ""
    if ai_input:
        ai_context = (f'<div style="font-size:12px;color:var(--muted);margin-bottom:20px">'
                      f'Analisado: {ai_input.get("gsc_queries",0)} queries GSC · '
                      f'{ai_input.get("onpage_pages",0)} páginas · '
                      f'{ai_input.get("backlog_items",0)} tarefas · '
                      f'{ai_input.get("content_gaps",0)} lacunas de conteúdo</div>')

    ai_html = f"""
<div class="panel" style="margin-bottom:20px">
  <div class="panel-head">
    <h3 class="panel-title">Resumo Executivo {ai_badge}</h3>
  </div>
  <div style="padding:16px 20px">
    {ai_context}
    <p style="line-height:1.8;color:var(--ink-mid);font-size:14px">{ai_summary}</p>
  </div>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px">
  <div class="panel">
    <div class="panel-head"><h3 class="panel-title">Riscos Críticos</h3></div>
    <div style="padding:12px 16px 16px">{_risk_cards(ai_risks)}</div>
  </div>
  <div class="panel">
    <div class="panel-head"><h3 class="panel-title">Quick Wins IA</h3></div>
    <div style="padding:12px 16px 16px">{_win_cards(ai_qw)}</div>
  </div>
</div>
<div class="panel" style="margin-bottom:20px">
  <div class="panel-head"><h3 class="panel-title">Próximas Ações (ordenadas por prioridade)</h3></div>
  <ul style="padding:8px 20px 16px;list-style:none">{_action_list(ai_actions)}</ul>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
  <div class="panel">
    <div class="panel-head"><h3 class="panel-title">Oportunidades de Conteúdo</h3></div>
    <div style="padding:12px 20px 16px">{_content_rows(ai_content)}</div>
  </div>
  <div class="panel">
    <div class="panel-head"><h3 class="panel-title">Recomendações Técnicas</h3></div>
    <div style="padding:12px 20px 16px">{_tech_rows(ai_tech)}</div>
  </div>
</div>
{f'<p style="font-size:11px;color:var(--muted);margin-top:16px">Notas de confiança: {" · ".join(esc(str(n)) for n in ai_confidence)}</p>' if ai_confidence else ""}"""

    # ── Overview tab rows (pre-computed to avoid f-string backslash issues) ──
    _no_data = "<tr><td colspan='4' style='text-align:center;color:var(--muted);padding:16px'>Sem dados.</td></tr>"
    tq_rows = "".join(
        f'<tr><td>{esc(r.get("query",""))}</td>'
        f'<td>{r.get("impressions",0):,}</td>'
        f'<td>{round(float(r.get("position",0)),1)}</td>'
        f'<td>{r.get("ctr",0):.2f}%</td></tr>'
        for r in gsc.get("top_queries", [])[:10]
    ) or _no_data
    tp_rows = "".join(
        '<tr><td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
        f'<a href="/url?target={quote(r.get("page",""))}">{esc(r.get("page","")[-40:])}</a></td>'
        f'<td>{r.get("clicks",0):,}</td>'
        f'<td>{r.get("impressions",0):,}</td>'
        f'<td>{r.get("ctr",0):.2f}%</td></tr>'
        for r in gsc.get("top_pages", [])[:10]
    ) or _no_data

    # ── Assemble ──────────────────────────────────────────────────────────────
    body = f"""
{_AUDIT_CSS}
<div class="section-head">
  <div>
    <h1>Relatório de Auditoria</h1>
    {f'<span style="font-size:12px;color:var(--muted);margin-top:2px;display:block">Gerada em {esc(completed_at)}</span>' if completed_at else ''}
  </div>
  <a href="/full-audit?new=1" class="btn btn-primary">+ Nova Auditoria</a>
</div>
{health_html}
{metrics_html}
<div class="rtab-bar">
  <button class="rtab active" id="tab-btn-overview"  onclick="showTab('tab-overview')">Visão Geral</button>
  <button class="rtab"        id="tab-btn-issues"    onclick="showTab('tab-issues')">Problemas ({total_issues})</button>
  <button class="rtab"        id="tab-btn-keywords"  onclick="showTab('tab-keywords')">Keywords</button>
  <button class="rtab"        id="tab-btn-content"   onclick="showTab('tab-content')">Conteúdo</button>
  <button class="rtab"        id="tab-btn-backlog"   onclick="showTab('tab-backlog')">Backlog ({len(backlog)})</button>
  <button class="rtab"        id="tab-btn-ai"        onclick="showTab('tab-ai')">IA</button>
</div>
<div id="tab-overview" class="rtab-content active">
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
    <div class="panel">
      <div class="panel-head"><h3 class="panel-title">Top Queries GSC</h3></div>
      <table class="data-table">
        <thead><tr><th>Query</th><th>Impressões</th><th>Posição</th><th>CTR</th></tr></thead>
        <tbody>{tq_rows}</tbody>
      </table>
    </div>
    <div class="panel">
      <div class="panel-head"><h3 class="panel-title">Top Páginas GSC</h3></div>
      <table class="data-table">
        <thead><tr><th>Página</th><th>Cliques</th><th>Imp.</th><th>CTR</th></tr></thead>
        <tbody>{tp_rows}</tbody>
      </table>
    </div>
  </div>
</div>
<div id="tab-issues"   class="rtab-content">{issues_html}</div>
<div id="tab-keywords" class="rtab-content">{kw_html}</div>
<div id="tab-content"  class="rtab-content">{content_html}</div>
<div id="tab-backlog"  class="rtab-content">{backlog_html}</div>
<div id="tab-ai"       class="rtab-content">{ai_html}</div>
<script>
function showTab(id) {{
  document.querySelectorAll('.rtab-content').forEach(function(el) {{ el.classList.remove('active'); }});
  document.querySelectorAll('.rtab').forEach(function(el) {{ el.classList.remove('active'); }});
  document.getElementById(id).classList.add('active');
  document.getElementById('tab-btn-' + id.replace('tab-','')).classList.add('active');
}}

function makeSortable(tbl) {{
  var ths = tbl.querySelectorAll('thead th');
  var sortCol = -1, sortDir = 1;
  ths.forEach(function(th, i) {{
    if (th.textContent.trim() === '') return;
    th.style.cssText += ';cursor:pointer;user-select:none;white-space:nowrap';
    var ic = document.createElement('i');
    ic.style.cssText = 'display:inline-block;margin-left:4px;opacity:.35;font-style:normal;font-size:10px';
    ic.textContent = '⇅';
    th.appendChild(ic);
    th.addEventListener('click', function() {{
      if (sortCol === i) {{ sortDir *= -1; }} else {{ sortCol = i; sortDir = 1; }}
      var tbody = tbl.querySelector('tbody');
      var rows = Array.from(tbody.querySelectorAll('tr'));
      var parseVal = function(cell) {{
        var t = (cell.textContent || '').trim().replace(/,/g,'').replace('%','');
        var n = parseFloat(t);
        return isNaN(n) ? (cell.textContent || '').trim() : n;
      }};
      rows.sort(function(a, b) {{
        var ac = a.cells[i], bc = b.cells[i];
        if (!ac || !bc) return 0;
        var av = parseVal(ac), bv = parseVal(bc);
        if (typeof av === 'string') return sortDir * av.localeCompare(bv, 'pt-BR');
        return sortDir * (av - bv);
      }});
      rows.forEach(function(r) {{ tbody.appendChild(r); }});
      ths.forEach(function(h, j) {{
        var hic = h.querySelector('i');
        if (!hic) return;
        if (j === sortCol) {{
          hic.textContent = sortDir === 1 ? '↑' : '↓';
          hic.style.opacity = '1'; hic.style.color = 'var(--brand)';
        }} else {{
          hic.textContent = '⇅';
          hic.style.opacity = '.35'; hic.style.color = '';
        }}
      }});
    }});
  }});
}}

document.querySelectorAll('.data-table').forEach(makeSortable);
</script>"""
    return page_shell("Auditoria Completa — Relatório", body)


# ── Settings ─────────────────────────────────────────────────────────────────

@app.route("/settings", methods=["GET", "POST"])
def settings():
    from config import _load_site_config

    msgs: list[tuple[str, str]] = []  # (text, type)

    # ── Handle form POST ──────────────────────────────────────────────────────
    if request.method == "POST":
        action = request.form.get("action", "site")

        if action == "site":
            site_url     = request.form.get("site_url", "").strip().rstrip("/")
            gsc_property = request.form.get("gsc_property", "").strip()
            cred_file    = request.files.get("gsc_credentials")

            if site_url and not site_url.startswith("http"):
                site_url = "https://" + site_url
            if gsc_property and not gsc_property.endswith("/"):
                gsc_property += "/"
            if gsc_property in ("/", ""):
                gsc_property = None

            save_site_config(site_url=site_url or None, gsc_property=gsc_property)

            if cred_file and cred_file.filename:
                (BASE_DIR / "gsc_credentials.json").write_bytes(cred_file.read())
                # Remove stale token so user must re-auth via the Connect button
                tok = BASE_DIR / ".gsc_token.json"
                if tok.exists():
                    tok.unlink()
                msgs.append(("Credenciais GSC salvas. Clique em 'Conectar com Google' para autenticar.", "ok"))

            # Clear GSC/dashboard caches
            for pat in (".dashboard_cache_*.json", ".dashboard_ai_*.json"):
                for f in BASE_DIR.glob(pat):
                    try: f.unlink()
                    except Exception: pass

            msgs.append(("Configurações de site salvas.", "ok"))

        elif action == "apikeys":
            key_map = {
                "gemini_key":      "GEMINI_API_KEY",
                "openrouter_key":  "OPENROUTER_API_KEY",
                "groq_key":        "GROQ_API_KEY",
                "mistral_key":     "MISTRAL_API_KEY",
                "anthropic_key":   "ANTHROPIC_API_KEY",
            }
            saved = []
            for field, env_key in key_map.items():
                val = request.form.get(field, "").strip()
                if val:
                    _update_env_file(env_key, val)
                    saved.append(env_key.replace("_API_KEY", "").replace("_KEY", "").title())
            if saved:
                msgs.append((f"Chaves salvas: {', '.join(saved)}.", "ok"))
            else:
                msgs.append(("Nenhuma chave alterada (campos em branco mantêm o valor atual).", "info"))

    # ── Flash messages from OAuth redirect ───────────────────────────────────
    for kind in ("gsc_ok", "gsc_err"):
        val = session.pop(kind, None)
        if val:
            msgs.append((val, "ok" if kind == "gsc_ok" else "bad"))

    # ── Read current state ────────────────────────────────────────────────────
    current_url      = get_site_url()
    current_prop     = get_gsc_property()
    has_creds        = (BASE_DIR / "gsc_credentials.json").exists()
    has_token        = (BASE_DIR / ".gsc_token.json").exists()
    cfg              = _load_site_config()
    available_sites  = cfg.get("available_gsc_sites") or []
    gsc_account      = cfg.get("gsc_account_email", "")

    # Current API key statuses (masked)
    api_keys = {
        "GEMINI_API_KEY":     os.environ.get("GEMINI_API_KEY", ""),
        "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY", ""),
        "GROQ_API_KEY":       os.environ.get("GROQ_API_KEY", ""),
        "MISTRAL_API_KEY":    os.environ.get("MISTRAL_API_KEY", ""),
        "ANTHROPIC_API_KEY":  os.environ.get("ANTHROPIC_API_KEY", ""),
    }

    # ── Build HTML pieces ─────────────────────────────────────────────────────
    def _msg_html(items):
        out = ""
        for text, t in items:
            bg  = {"ok": "var(--ok-bg)", "bad": "var(--bad-bg)", "info": "var(--info-bg)"}.get(t, "var(--info-bg)")
            clr = {"ok": "var(--ok)",    "bad": "var(--bad)",    "info": "var(--info)"   }.get(t, "var(--info)")
            out += (f'<div style="background:{bg};border:1px solid {clr};border-radius:8px;'
                    f'padding:11px 15px;margin-bottom:12px;color:{clr};font-size:13px;font-weight:600">'
                    f'{esc(text)}</div>')
        return out

    # GSC connection panel
    if has_token:
        gsc_account_line = (
            f'<span style="color:var(--muted);font-size:12px">{esc(gsc_account)}</span>'
            if gsc_account else ""
        )
        # Property selector
        if available_sites:
            opts = "".join(
                f'<option value="{esc(s)}" {"selected" if s == current_prop else ""}>{esc(s)}</option>'
                for s in available_sites
            )
            prop_input = (
                f'<select name="gsc_property" style="width:100%;padding:9px 12px;border:1px solid var(--line);'
                f'border-radius:6px;font-size:14px;color:var(--ink);background:var(--panel)">'
                f'{opts}</select>'
            )
        else:
            prop_input = (
                f'<input name="gsc_property" type="text" value="{esc(current_prop)}" '
                f'placeholder="https://www.seusite.com.br/" '
                f'style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:6px;font-size:14px;color:var(--ink)"/>'
            )

        gsc_panel = f"""
<div style="background:var(--ok-bg);border:1px solid var(--ok);border-radius:8px;padding:12px 16px;
            display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
  <div>
    <span style="color:var(--ok);font-weight:700;font-size:13px">&#10003; Conectado ao Google Search Console</span>
    {gsc_account_line}
  </div>
  <a href="/settings/gsc/disconnect"
     onclick="return confirm('Desconectar conta Google?')"
     class="btn btn-sm btn-danger" style="text-decoration:none">Desconectar</a>
</div>
<div>
  <label style="display:block;font-size:12px;font-weight:700;color:var(--muted);margin-bottom:6px;
                text-transform:uppercase;letter-spacing:.04em">Propriedade GSC</label>
  {prop_input}
  <p style="font-size:11px;color:var(--muted);margin-top:5px">
    Selecione a propriedade que deseja monitorar.
    Para domínios use <code>sc-domain:seusite.com.br</code>.
  </p>
</div>"""
    elif has_creds:
        gsc_panel = f"""
<div style="background:var(--warn-bg);border:1px solid var(--warn);border-radius:8px;padding:12px 16px;
            display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
  <span style="color:var(--warn);font-weight:600;font-size:13px">&#9888; Credenciais carregadas — autenticação pendente</span>
  <a href="/settings/gsc/connect" class="btn btn-sm"
     style="background:#fff;border:1px solid #ddd;display:inline-flex;align-items:center;gap:7px;padding:6px 12px;
            border-radius:6px;font-size:13px;font-weight:600;color:#3c4043;text-decoration:none;white-space:nowrap">
    <svg width="16" height="16" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.5 0 6.4 1.2 8.7 3.2l6.5-6.5C35.1 2.6 29.9 0 24 0 14.6 0 6.6 5.5 2.8 13.4l7.6 5.9C12.3 13.2 17.7 9.5 24 9.5z"/><path fill="#4285F4" d="M46.7 24.5c0-1.5-.1-3-.4-4.5H24v8.5h12.8c-.6 3-2.3 5.5-4.8 7.2l7.4 5.7c4.3-4 6.3-9.9 6.3-16.9z"/><path fill="#FBBC05" d="M10.4 28.7A14.5 14.5 0 0 1 9.5 24c0-1.6.3-3.2.8-4.7L2.7 13.4A23.9 23.9 0 0 0 0 24c0 3.8.9 7.4 2.8 10.6l7.6-5.9z"/><path fill="#34A853" d="M24 48c6 0 11-2 14.7-5.3l-7.4-5.7c-2 1.4-4.6 2.2-7.3 2.2-6.3 0-11.7-3.7-13.6-9.2l-7.6 5.9C6.6 42.5 14.6 48 24 48z"/></svg>
    Conectar com Google
  </a>
</div>
<div style="background:var(--line-light);border:1px solid var(--line);border-radius:8px;
            padding:14px 16px;font-size:12px;color:var(--ink-mid);line-height:1.7">
  <strong>Próximo passo:</strong> Clique em "Conectar com Google" acima para autorizar acesso ao Search Console.<br>
  Você será redirecionado para login Google e voltará automaticamente.
</div>"""
    else:
        gsc_panel = f"""
<div style="background:var(--line-light);border:1px solid var(--line);border-radius:8px;
            padding:16px;margin-bottom:16px;font-size:12px;color:var(--ink-mid);line-height:1.7">
  <strong>Como conectar o Google Search Console:</strong><br>
  1. Acesse <a href="https://console.cloud.google.com/" target="_blank" style="color:var(--brand)">console.cloud.google.com</a>
     → crie um projeto<br>
  2. Ative a <strong>Google Search Console API</strong> no projeto<br>
  3. Credenciais → Criar credencial → <strong>OAuth 2.0 → Aplicativo para Desktop</strong><br>
  4. Baixe o arquivo JSON e faça upload abaixo<br>
  5. Clique em <strong>Conectar com Google</strong> para autorizar
</div>
<div>
  <label style="display:block;font-size:12px;font-weight:700;color:var(--muted);margin-bottom:6px;
                text-transform:uppercase;letter-spacing:.04em">Upload gsc_credentials.json</label>
  <input type="file" name="gsc_credentials" accept=".json"
         style="padding:6px 0;font-size:13px;color:var(--ink-mid)"/>
  <p style="font-size:11px;color:var(--muted);margin-top:4px">
    Arquivo OAuth2 baixado do Google Cloud Console.
  </p>
</div>"""

    # API keys rows
    key_defs = [
        ("GEMINI_API_KEY",     "gemini_key",     "Gemini",      "Google — recomendado para análise de IA (gratuito)"),
        ("OPENROUTER_API_KEY", "openrouter_key", "OpenRouter",  "Acesso a múltiplos modelos gratuitos com uma única chave"),
        ("GROQ_API_KEY",       "groq_key",       "Groq",        "Llama 3.3 — muito rápido, gratuito"),
        ("MISTRAL_API_KEY",    "mistral_key",     "Mistral",     "Mistral — opcional"),
        ("ANTHROPIC_API_KEY",  "anthropic_key",   "Anthropic",   "Claude — melhor qualidade, pago"),
    ]
    key_rows = ""
    for env_k, field_k, label, desc in key_defs:
        current_val = api_keys.get(env_k, "")
        masked      = _mask_key(current_val)
        status_dot  = (
            '<span style="width:7px;height:7px;border-radius:50%;background:var(--ok);display:inline-block;margin-right:5px"></span>'
            if current_val else
            '<span style="width:7px;height:7px;border-radius:50%;background:var(--line);display:inline-block;margin-right:5px"></span>'
        )
        key_rows += f"""
<div style="display:grid;grid-template-columns:160px 1fr;gap:12px;align-items:start;
            padding:12px 0;border-bottom:1px solid var(--line-light)">
  <div>
    <div style="font-size:13px;font-weight:700;color:var(--ink)">{status_dot}{esc(label)}</div>
    <div style="font-size:11px;color:var(--muted);margin-top:2px">{esc(desc)}</div>
    {f'<div style="font-size:10px;color:var(--muted);font-family:monospace;margin-top:3px">{esc(masked)}</div>' if masked else ''}
  </div>
  <input
    name="{field_k}"
    type="password"
    autocomplete="new-password"
    placeholder="{'Alterar chave...' if current_val else 'Colar nova chave...'}"
    style="padding:8px 12px;border:1px solid var(--line);border-radius:6px;font-size:13px;
           color:var(--ink);width:100%;background:var(--panel)"
  />
</div>"""

    body = f"""
<div class="section-head" style="margin-bottom:24px">
  <h1>Configurações</h1>
</div>
{_msg_html(msgs)}

<div style="max-width:700px;display:flex;flex-direction:column;gap:20px">

  <!-- ── Panel 1: Site + GSC ─────────────────────────────────────── -->
  <div class="panel">
    <h2 style="margin-bottom:18px">Site</h2>
    <form method="POST" enctype="multipart/form-data">
      <input type="hidden" name="action" value="site">
      <div style="display:flex;flex-direction:column;gap:14px;margin-bottom:22px">
        <div>
          <label style="display:block;font-size:12px;font-weight:700;color:var(--muted);
                         margin-bottom:6px;text-transform:uppercase;letter-spacing:.04em">URL do Site</label>
          <input name="site_url" type="url" value="{esc(current_url)}"
            placeholder="https://www.seusite.com.br"
            style="width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:6px;font-size:14px;color:var(--ink)"/>
          <p style="font-size:11px;color:var(--muted);margin-top:4px">URL raiz do site a auditar, sem barra no final.</p>
        </div>
      </div>

      <hr style="border:none;border-top:1px solid var(--line);margin:0 0 18px">
      <h2 style="margin-bottom:14px">Google Search Console</h2>
      {gsc_panel}

      <div style="margin-top:20px">
        <button type="submit" class="btn btn-primary">Salvar</button>
      </div>
    </form>
  </div>

  <!-- ── Panel 2: API Keys ───────────────────────────────────────── -->
  <div class="panel">
    <h2 style="margin-bottom:4px">Chaves de API — IA</h2>
    <p style="font-size:12px;color:var(--muted);margin-bottom:16px">
      Deixe o campo em branco para manter a chave atual. As chaves ficam salvas no arquivo <code>.env</code> e nunca aparecem completas na tela.
    </p>
    <form method="POST">
      <input type="hidden" name="action" value="apikeys">
      {key_rows}
      <div style="margin-top:18px">
        <button type="submit" class="btn btn-primary">Salvar Chaves</button>
      </div>
    </form>
  </div>

</div>
"""
    return page_shell("Configurações", body)


@app.route("/settings/gsc/connect")
def gsc_connect():
    """Start Google OAuth flow for GSC."""
    cred_file = BASE_DIR / "gsc_credentials.json"
    if not cred_file.exists():
        session["gsc_err"] = "Faça upload do gsc_credentials.json primeiro."
        return redirect(url_for("settings"))
    try:
        from google_auth_oauthlib.flow import Flow as _Flow
        _flow = _Flow.from_client_secrets_file(
            str(cred_file),
            scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
            redirect_uri=url_for("gsc_callback", _external=True),
        )
        auth_url, state = _flow.authorization_url(
            prompt="consent", access_type="offline", include_granted_scopes="true"
        )
        session["gsc_oauth_state"] = state
        # Persist the PKCE code verifier (generated by newer google-auth-oauthlib)
        # so the callback can complete token exchange after recreating the flow object
        session["gsc_code_verifier"] = getattr(_flow, "code_verifier", None)
        return redirect(auth_url)
    except Exception as exc:
        session["gsc_err"] = f"Erro ao iniciar autenticação: {exc}"
        return redirect(url_for("settings"))


@app.route("/settings/gsc/callback")
def gsc_callback():
    """Handle Google OAuth callback, save token, fetch available properties."""
    cred_file = BASE_DIR / "gsc_credentials.json"
    state     = session.pop("gsc_oauth_state", None)
    try:
        from google_auth_oauthlib.flow import Flow as _Flow
        _flow = _Flow.from_client_secrets_file(
            str(cred_file),
            scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
            state=state,
            redirect_uri=url_for("gsc_callback", _external=True),
        )
        # Restore PKCE code verifier that was generated in gsc_connect
        _cv = session.pop("gsc_code_verifier", None)
        if _cv:
            _flow.code_verifier = _cv
        _flow.fetch_token(authorization_response=request.url)
        creds = _flow.credentials
        (BASE_DIR / ".gsc_token.json").write_text(creds.to_json(), encoding="utf-8")

        # Fetch available GSC properties + account email
        import requests as _req
        _sess = _req.Session()
        _sess.trust_env = False
        _hdr  = {"Authorization": f"Bearer {creds.token}"}

        sites: list[str] = []
        try:
            r = _sess.get("https://www.googleapis.com/webmasters/v3/sites",
                          headers=_hdr, timeout=10)
            if r.ok:
                sites = [s.get("siteUrl", "") for s in r.json().get("siteEntry", []) if s.get("siteUrl")]
        except Exception:
            pass

        email = ""
        try:
            r2 = _sess.get("https://www.googleapis.com/oauth2/v2/userinfo",
                            headers=_hdr, timeout=8)
            if r2.ok:
                email = r2.json().get("email", "")
        except Exception:
            pass

        save_site_config(
            available_gsc_sites=sites if sites else None,
            gsc_account_email=email if email else None,
        )
        # Auto-select property if none configured yet
        from config import _load_site_config as _lsc
        if sites and not _lsc().get("gsc_property"):
            # Try to find a matching property for current site URL
            base = get_site_url().rstrip("/")
            match = next((s for s in sites if s.rstrip("/") == base), sites[0])
            save_site_config(gsc_property=match)

        who = f" como {email}" if email else ""
        session["gsc_ok"] = f"Conectado{who}. {len(sites)} propriedade(s) encontrada(s)."
    except Exception as exc:
        session["gsc_err"] = f"Falha na autenticação Google: {exc}"

    return redirect(url_for("settings"))


@app.route("/settings/gsc/disconnect")
def gsc_disconnect():
    """Delete GSC OAuth token and clear saved property list."""
    tok = BASE_DIR / ".gsc_token.json"
    if tok.exists():
        tok.unlink()
    save_site_config(available_gsc_sites=None, gsc_account_email=None)
    session["gsc_ok"] = "Conta Google desconectada."
    return redirect(url_for("settings"))


if __name__ == "__main__":
    app.run(debug=True)
