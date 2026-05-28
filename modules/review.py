"""
Gera uma página HTML para revisão e aplicação manual das otimizações geradas.

Lê o pending_changes.json e exibe cada campo com botão de copiar,
organizado por página para facilitar a edição manual no CMS do cliente.
"""

from pathlib import Path
from datetime import datetime
import json

from config import BASE_DIR, get_site_name, get_site_url

PENDING_FILE  = BASE_DIR / "pending_changes.json"
BRIEFS_FILE   = BASE_DIR / "blog_briefs.json"
REPORTS_FOLDER = BASE_DIR / "reports"


def _char_badge(text: str, min_c: int, max_c: int) -> str:
    n = len(text)
    if n < min_c:
        color = "#e67e22"
        label = f"{n} chars — curto (mín {min_c})"
    elif n > max_c:
        color = "#e74c3c"
        label = f"{n} chars — longo (máx {max_c})"
    else:
        color = "#27ae60"
        label = f"{n} chars — ok"
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600">{label}</span>'


def _card(page: dict) -> str:
    url        = page.get("_url", "")
    provider   = page.get("_provider", "")
    gen_at     = page.get("_generated_at", "")[:16].replace("T", " ")
    status     = page.get("status", "pending")
    title      = page.get("meta_title", "")
    desc       = page.get("meta_description", "")
    h1         = page.get("h1", "")
    keywords   = page.get("meta_keywords", "")
    desc_html  = page.get("description_html", "")
    warnings   = page.get("_warnings", [])

    status_color = "#27ae60" if status == "published" else "#e67e22"
    status_label = "Publicado" if status == "published" else "Pendente"

    warn_html = ""
    if warnings:
        items = "".join(f"<li>{w}</li>" for w in warnings)
        warn_html = f'<ul style="color:#e67e22;margin:4px 0 12px 16px;font-size:13px">{items}</ul>'

    full_url = url if url.startswith("http") else get_site_url() + url

    return f"""
<div class="card" id="{url.strip('/').replace('/', '-')}">
  <div class="card-header">
    <div>
      <a href="{full_url}" target="_blank" class="page-url">{full_url}</a>
      <span style="margin-left:12px;background:{status_color};color:#fff;
                   padding:2px 8px;border-radius:4px;font-size:12px">{status_label}</span>
    </div>
    <div style="font-size:12px;color:#888">{provider} · {gen_at}</div>
  </div>
  {warn_html}

  <div class="field-group">
    <div class="field-label">Meta Title {_char_badge(title, 50, 60)}</div>
    <div class="field-box" onclick="copyThis(this)">{_esc(title)}</div>
  </div>

  <div class="field-group">
    <div class="field-label">Meta Description {_char_badge(desc, 145, 160)}</div>
    <div class="field-box" onclick="copyThis(this)">{_esc(desc)}</div>
  </div>

  <div class="field-group">
    <div class="field-label">H1 {_char_badge(h1, 40, 65)}</div>
    <div class="field-box" onclick="copyThis(this)">{_esc(h1)}</div>
  </div>

  <div class="field-group">
    <div class="field-label">Meta Keywords</div>
    <div class="field-box" onclick="copyThis(this)">{_esc(keywords)}</div>
  </div>

  <div class="field-group">
    <div class="field-label">Descrição HTML
      <small style="color:#888;font-weight:400">(cole no editor HTML do CMS)</small>
    </div>
    <div class="field-box code" onclick="copyThis(this)">{_esc(desc_html)}</div>
  </div>
</div>"""


def _brief_card(brief: dict) -> str:
    slug       = brief.get("url_slug", "")
    h1         = brief.get("h1", "")
    title      = brief.get("meta_title", "")
    desc       = brief.get("meta_description", "")
    intro      = brief.get("introduction", "")
    sections   = brief.get("sections", [])
    faq        = brief.get("faq", [])
    links      = brief.get("internal_links", [])
    cta        = brief.get("cta_section", "")
    words      = brief.get("estimated_words", 1200)
    gen_at     = brief.get("_generated_at", "")[:16].replace("T", " ")
    opp        = brief.get("_opportunity", {})

    sections_html = "".join(
        f"<li><strong>{s.get('h2','')}</strong> — {_esc(s.get('summary',''))}</li>"
        for s in sections
    )
    faq_html = "".join(
        f"<li><strong>Q:</strong> {_esc(f.get('question',''))}<br>"
        f"<span style='color:#555'>{_esc(f.get('answer',''))}</span></li>"
        for f in faq
    )
    links_html = "".join(
        f"<li><code>{l.get('url','')}</code> — <em>{l.get('anchor','')}</em> ({l.get('where','')})</li>"
        for l in links
    )

    return f"""
<div class="card brief-card">
  <div class="card-header">
    <div>
      <span class="page-url">/{slug}</span>
      <span style="margin-left:12px;background:#8e44ad;color:#fff;
                   padding:2px 8px;border-radius:4px;font-size:12px">Brief</span>
    </div>
    <div style="font-size:12px;color:#888">{gen_at} · ~{words} palavras</div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;
              font-size:12px;background:#f8f6ff;padding:10px;border-radius:6px">
    <div>Impressões: <strong>{opp.get('impressions',0):,}</strong></div>
    <div>Potencial/mês: <strong>~{opp.get('potential_clicks',0):,} cliques</strong></div>
    <div>Score: <strong>{opp.get('score',0)}</strong></div>
    <div>Queries: <strong>{len(opp.get('top_queries',[]))}</strong></div>
  </div>

  <div class="field-group">
    <div class="field-label">Meta Title {_char_badge(title, 50, 60)}</div>
    <div class="field-box" onclick="copyThis(this)">{_esc(title)}</div>
  </div>

  <div class="field-group">
    <div class="field-label">Meta Description {_char_badge(desc, 145, 160)}</div>
    <div class="field-box" onclick="copyThis(this)">{_esc(desc)}</div>
  </div>

  <div class="field-group">
    <div class="field-label">H1 {_char_badge(h1, 40, 65)}</div>
    <div class="field-box" onclick="copyThis(this)">{_esc(h1)}</div>
  </div>

  <div class="field-group">
    <div class="field-label">Introdução</div>
    <div class="field-box" onclick="copyThis(this)">{_esc(intro)}</div>
  </div>

  <div class="field-group">
    <div class="field-label">Seções (H2s)</div>
    <ul style="margin:4px 0 0 16px;line-height:1.8;font-size:14px">{sections_html}</ul>
  </div>

  {'<div class="field-group"><div class="field-label">FAQ</div><ul style="margin:4px 0 0 16px;line-height:2;font-size:14px">' + faq_html + '</ul></div>' if faq else ''}

  {'<div class="field-group"><div class="field-label">Links Internos Sugeridos</div><ul style="margin:4px 0 0 16px;line-height:1.8;font-size:13px">' + links_html + '</ul></div>' if links else ''}

  {'<div class="field-group"><div class="field-label">CTA Final</div><div class="field-box" onclick="copyThis(this)">' + _esc(cta) + '</div></div>' if cta else ''}
</div>"""


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


def generate(output_path: str = None) -> str:
    pending = []
    if PENDING_FILE.exists():
        try:
            pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    briefs = []
    if BRIEFS_FILE.exists():
        try:
            briefs = json.loads(BRIEFS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    if not pending and not briefs:
        print("  Nenhuma otimizacao pendente encontrada.")
        print("  Execute: python run.py --module generate --urls /categoria")
        return ""

    pending_count   = len([p for p in pending if p.get("status") == "pending"])
    published_count = len([p for p in pending if p.get("status") == "published"])

    page_cards  = "\n".join(_card(p) for p in pending)
    brief_cards = "\n".join(_brief_card(b) for b in briefs)

    briefs_section = ""
    if briefs:
        briefs_section = f"""
  <h2 style="margin:40px 0 16px;color:#8e44ad">
    Sugestões de Posts ({len(briefs)})
  </h2>
  {brief_cards}"""

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SEO Review — {get_site_name()}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #f0f2f5; color: #222; padding: 24px; }}
  .container {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .subtitle {{ color: #666; font-size: 14px; margin-bottom: 24px; }}
  .stats {{ display: flex; gap: 16px; margin-bottom: 28px; flex-wrap: wrap; }}
  .stat {{ background: #fff; border-radius: 10px; padding: 14px 20px;
           box-shadow: 0 1px 4px rgba(0,0,0,.08); min-width: 130px; }}
  .stat-n {{ font-size: 28px; font-weight: 700; }}
  .stat-l {{ font-size: 12px; color: #888; margin-top: 2px; }}
  .card {{ background: #fff; border-radius: 12px; padding: 20px 24px;
           margin-bottom: 20px; box-shadow: 0 1px 6px rgba(0,0,0,.09); }}
  .brief-card {{ border-left: 4px solid #8e44ad; }}
  .card-header {{ display: flex; justify-content: space-between; align-items: flex-start;
                  margin-bottom: 16px; flex-wrap: wrap; gap: 8px; }}
  .page-url {{ font-weight: 600; font-size: 15px; color: #1a73e8; text-decoration: none; }}
  .page-url:hover {{ text-decoration: underline; }}
  .field-group {{ margin-bottom: 14px; }}
  .field-label {{ font-size: 12px; font-weight: 600; text-transform: uppercase;
                  letter-spacing: .5px; color: #555; margin-bottom: 5px;
                  display: flex; align-items: center; gap: 8px; }}
  .field-box {{ background: #f7f8fa; border: 1px solid #e0e0e0; border-radius: 7px;
               padding: 10px 14px; font-size: 14px; line-height: 1.5; cursor: pointer;
               transition: background .15s; word-break: break-word; }}
  .field-box:hover {{ background: #eef3ff; border-color: #1a73e8; }}
  .field-box:active {{ background: #d2e3fc; }}
  .field-box.code {{ font-family: "Fira Mono", "Consolas", monospace; font-size: 12px;
                     white-space: pre-wrap; max-height: 200px; overflow-y: auto; }}
  .toast {{ position: fixed; bottom: 28px; right: 28px; background: #27ae60; color: #fff;
            padding: 10px 20px; border-radius: 8px; font-size: 14px; font-weight: 500;
            opacity: 0; pointer-events: none; transition: opacity .25s; z-index: 999; }}
  .toast.show {{ opacity: 1; }}
  h2 {{ font-size: 18px; }}
</style>
</head>
<body>
<div class="container">
  <h1>SEO Review — {get_site_name()}</h1>
  <p class="subtitle">Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')} · Clique em qualquer campo para copiar</p>

  <div class="stats">
    <div class="stat">
      <div class="stat-n" style="color:#e67e22">{pending_count}</div>
      <div class="stat-l">Pendentes</div>
    </div>
    <div class="stat">
      <div class="stat-n" style="color:#27ae60">{published_count}</div>
      <div class="stat-l">Publicados</div>
    </div>
    <div class="stat">
      <div class="stat-n" style="color:#8e44ad">{len(briefs)}</div>
      <div class="stat-l">Briefs de Blog</div>
    </div>
  </div>

  {'<h2 style="margin-bottom:16px">Otimizações de Páginas (' + str(len(pending)) + ')</h2>' if pending else ''}
  {page_cards}
  {briefs_section}
</div>

<div class="toast" id="toast">Copiado!</div>

<script>
function copyThis(el) {{
  const text = el.innerText;
  navigator.clipboard.writeText(text).then(() => {{
    const t = document.getElementById('toast');
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 1800);
  }}).catch(() => {{
    const r = document.createRange();
    r.selectNode(el);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(r);
    document.execCommand('copy');
    window.getSelection().removeAllRanges();
    const t = document.getElementById('toast');
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 1800);
  }});
}}
</script>
</body>
</html>"""

    if not output_path:
        REPORTS_FOLDER.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        output_path = str(REPORTS_FOLDER / f"review_{ts}.html")

    Path(output_path).write_text(html, encoding="utf-8")
    return output_path


def run():
    path = generate()
    if path:
        print(f"  Review gerado: {path}")
        import webbrowser, os
        webbrowser.open(f"file:///{os.path.abspath(path)}")
