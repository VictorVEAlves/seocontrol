import json
import os
from datetime import datetime
from pathlib import Path

from config import get_site_name, get_site_url

# ── Helpers ───────────────────────────────────────────────────────────────────

def _badge(score):
    if score is None:
        return '<span class="badge badge-gray">—</span>'
    if score >= 85:
        return f'<span class="badge badge-green">{score}</span>'
    if score >= 60:
        return f'<span class="badge badge-yellow">{score}</span>'
    return f'<span class="badge badge-red">{score}</span>'


def _grade_badge(grade):
    colors = {"A": "badge-green", "B": "badge-yellow", "C": "badge-orange", "D": "badge-red", "F": "badge-red"}
    cls = colors.get(grade, "badge-gray")
    return f'<span class="badge {cls}">{grade}</span>'


def _status_badge(status):
    if status == 200:
        return f'<span class="badge badge-green">{status}</span>'
    if status in [301, 302]:
        return f'<span class="badge badge-yellow">{status}</span>'
    return f'<span class="badge badge-red">{status}</span>'


def _table(headers: list, rows: list, empty_msg: str = "Nenhum item encontrado") -> str:
    if not rows:
        return f'<p class="empty-msg">✓ {empty_msg}</p>'
    ths = "".join(f"<th>{h}</th>" for h in headers)
    trs = ""
    for row in rows:
        cells = "".join(f"<td>{c}</td>" for c in row)
        trs += f"<tr>{cells}</tr>"
    return f'<div class="table-wrap"><table><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table></div>'


def _section(title: str, icon: str, content: str, section_id: str = "") -> str:
    sid = section_id or title.lower().replace(" ", "-")
    return f"""
<section class="card" id="{sid}">
  <div class="card-header">
    <span class="card-icon">{icon}</span>
    <h2>{title}</h2>
  </div>
  <div class="card-body">
    {content}
  </div>
</section>"""


# ── Section builders ──────────────────────────────────────────────────────────

def _build_gsc_section(gsc: dict) -> str:
    if not gsc or "error" in gsc:
        return "<p>Dados GSC não disponíveis. Execute com --gsc &lt;pasta&gt;</p>"

    trend = gsc.get("trend", {})
    html  = ""

    # KPI row
    if trend:
        cl_trend = trend.get("clicks_trend", 0)
        im_trend = trend.get("impressions_trend", 0)
        cl_arrow = "↑" if cl_trend > 0 else "↓"
        im_arrow = "↑" if im_trend > 0 else "↓"
        cl_color = "green" if cl_trend > 0 else "red"
        im_color = "green" if im_trend > 0 else "red"

        html += f"""
<div class="kpi-row">
  <div class="kpi"><div class="kpi-val">{trend.get("total_clicks",0):,}</div><div class="kpi-label">Cliques (28d)</div>
    <div class="kpi-trend" style="color:{cl_color}">{cl_arrow} {abs(cl_trend)}% vs período anterior</div></div>
  <div class="kpi"><div class="kpi-val">{trend.get("total_impressions",0):,}</div><div class="kpi-label">Impressões (28d)</div>
    <div class="kpi-trend" style="color:{im_color}">{im_arrow} {abs(im_trend)}% vs período anterior</div></div>
  <div class="kpi"><div class="kpi-val">{trend.get("avg_ctr",0):.2f}%</div><div class="kpi-label">CTR médio</div></div>
  <div class="kpi"><div class="kpi-val">{trend.get("avg_position",0):.1f}</div><div class="kpi-label">Posição média</div></div>
  <div class="kpi"><div class="kpi-val">{trend.get("best_day_clicks",0)}</div>
    <div class="kpi-label">Melhor dia ({trend.get("best_day","—")})</div></div>
</div>
<div class="chart-wrap">
  <canvas id="chartClicks" height="80"></canvas>
</div>
<script>
(function(){{
  var ctx = document.getElementById("chartClicks").getContext("2d");
  new Chart(ctx, {{
    type: "bar",
    data: {{
      labels: {json.dumps(trend.get("daily_labels", []))},
      datasets: [
        {{ label: "Cliques", data: {json.dumps(trend.get("daily_clicks", []))},
           backgroundColor: "rgba(200,16,46,0.7)", yAxisID: "y" }},
        {{ label: "Impressões", data: {json.dumps(trend.get("daily_impressions", []))},
           type: "line", borderColor: "#666", borderWidth:1.5,
           pointRadius:2, fill:false, yAxisID: "y1" }}
      ]
    }},
    options: {{
      responsive:true, interaction:{{mode:"index",intersect:false}},
      scales: {{
        y:  {{ position:"left",  title:{{display:true,text:"Cliques"}} }},
        y1: {{ position:"right", title:{{display:true,text:"Impressões"}}, grid:{{drawOnChartArea:false}} }}
      }}
    }}
  }});
}})();
</script>
"""

    # CTR Opportunities
    ctr_rows = [
        [r.get("query","")[:60],
         f'{r.get("impressions",0):,}',
         f'{r.get("position",0):.1f}',
         r.get("query_type",""),
         r.get("content_action",""),
         f'<strong>+{r.get("potential_clicks",0)}</strong> cliques/mês']
        for r in gsc.get("content_opps", [])[:15]
    ]
    html += "<h3>Oportunidades de conteudo por query</h3>"
    html += '<p class="hint">Queries com boa posição mas CTR abaixo do benchmark — melhorar title/description resolve.</p>'
    html += _table(["Query","Impressões","CTR atual","Posição","CTR esperado","Ganho estimado"], ctr_rows,
                   "Nenhuma oportunidade de CTR significativa")

    # Quick wins
    qw_rows = [
        [r.get("query","")[:60],
         f'{r.get("impressions",0):,}',
         f'{r.get("position",0):.1f}',
         r.get("action","")]
        for r in gsc.get("quick_wins", [])[:15]
    ]
    html += "<h3>Quick Wins — Posições 7–12</h3>"
    html += '<p class="hint">Uma otimização on-page pode empurrar essas queries para o top 5.</p>'
    html += _table(["Query","Impressões","Posição","Ação sugerida"], qw_rows,
                   "Nenhum quick win encontrado")

    # Low CTR pages
    lcp_rows = [
        [f'<a href="{r.get("page","")}" target="_blank">{r.get("page","")[:60]}</a>',
         f'{r.get("impressions",0):,}',
         f'{r.get("ctr",0):.2f}%',
         f'{r.get("position",0):.1f}',
         f'<strong>+{r.get("potential_clicks",0)}</strong>']
        for r in gsc.get("low_ctr_pages", [])[:15]
    ]
    html += "<h3>Páginas com CTR Abaixo do Esperado</h3>"
    html += _table(["Página","Impressões","CTR atual","Posição","Potencial de cliques"], lcp_rows,
                   "Nenhuma página com CTR crítico")

    return html


def _build_onpage_section(onpage: list) -> str:
    if not onpage:
        return "<p>Auditoria on-page não executada.</p>"

    avg_score = sum(p.get("score", 0) for p in onpage) / len(onpage)
    critical  = [p for p in onpage if p.get("grade") in ("D", "F")]
    ok_count  = len([p for p in onpage if p.get("grade") == "A"])

    html = f"""
<div class="kpi-row">
  <div class="kpi"><div class="kpi-val">{len(onpage)}</div><div class="kpi-label">Páginas auditadas</div></div>
  <div class="kpi"><div class="kpi-val">{avg_score:.0f}/100</div><div class="kpi-label">Score médio</div></div>
  <div class="kpi" style="color:#C8102E"><div class="kpi-val">{len(critical)}</div><div class="kpi-label">Críticas (D/F)</div></div>
  <div class="kpi" style="color:#2e7d32"><div class="kpi-val">{ok_count}</div><div class="kpi-label">Saudáveis (A)</div></div>
</div>
"""

    rows = []
    for p in sorted(onpage, key=lambda x: x.get("score", 100)):
        url = p.get("url", "").replace(get_site_url(), "")
        issues_html = ""
        for iss in p.get("issues", []):
            issues_html += f'<span class="tag tag-red">{iss[:60]}</span> '
        for w in p.get("warnings", []):
            issues_html += f'<span class="tag tag-yellow">{w[:60]}</span> '
        rows.append([
            f'<a href="{p.get("url","")}" target="_blank">{url[:50]}</a>',
            _grade_badge(p.get("grade", "?")),
            str(p.get("title_length", 0)),
            str(p.get("description_length", 0)),
            str(p.get("h1_count", 0)),
            "✓" if p.get("has_faq_schema") else "✗",
            str(p.get("word_count", 0)),
            issues_html or '<span class="tag tag-green">OK</span>',
        ])

    html += _table(
        ["URL","Grade","Title","Desc","H1","Schema","Palavras","Issues/Warnings"],
        rows
    )
    return html


def _build_duplicate_section(dup: dict) -> str:
    if not dup:
        return "<p>Auditoria de duplicatas não executada.</p>"

    total = dup.get("total_issues", 0)
    html  = f'<div class="kpi-row"><div class="kpi"><div class="kpi-val">{total}</div><div class="kpi-label">Issues encontrados</div></div></div>'

    # Missing titles
    mt = dup.get("missing_titles", [])
    if mt:
        html += f'<h3>Meta Title Ausente ({len(mt)} páginas)</h3>'
        html += _table(["URL"], [[u] for u in mt], "Nenhum title ausente")

    # Missing descriptions
    md = dup.get("missing_descs", [])
    if md:
        html += f'<h3>Meta Description Ausente ({len(md)} páginas)</h3>'
        html += _table(["URL"], [[u] for u in md], "Nenhuma description ausente")

    # Duplicate titles
    dt = dup.get("duplicate_titles", [])
    if dt:
        html += f'<h3>Titles Duplicados ({len(dt)} pares)</h3>'
        html += _table(
            ["URL A","URL B","Title","Similaridade"],
            [[r["url_a"], r["url_b"], r.get("title_a","")[:60], r.get("similarity","")]
             for r in dt]
        )

    # Duplicate descriptions
    dd = dup.get("duplicate_descs", [])
    if dd:
        html += f'<h3>Descriptions Duplicadas ({len(dd)} pares)</h3>'
        html += _table(
            ["URL A","URL B","Description A","Similaridade"],
            [[r["url_a"], r["url_b"], r.get("desc_a","")[:80], r.get("similarity","")]
             for r in dd]
        )

    # Keyword issues
    ki = dup.get("keyword_issues", [])
    if ki:
        html += f'<h3>Meta Keywords Suspeitas ({len(ki)} páginas)</h3>'
        html += _table(
            ["URL","Keywords","Marca suspeita"],
            [[r["url"], r.get("keywords","")[:80], r.get("suspicious_brand","")] for r in ki]
        )

    return html


def _build_links_section(links: dict) -> str:
    if not links:
        return "<p>Análise de links não executada.</p>"

    html = f"""
<div class="kpi-row">
  <div class="kpi"><div class="kpi-val">{len(links.get("broken",[]))}</div><div class="kpi-label">Links quebrados</div></div>
  <div class="kpi"><div class="kpi-val">{len(links.get("redirect_chains",[]))}</div><div class="kpi-label">Cadeias de redirect</div></div>
  <div class="kpi"><div class="kpi-val">{len(links.get("orphans",[]))}</div><div class="kpi-label">Páginas órfãs</div></div>
  <div class="kpi"><div class="kpi-val">{len(links.get("nofollow_internal",[]))}</div><div class="kpi-label">Links nofollow internos</div></div>
</div>
"""

    broken = links.get("broken", [])
    html += f"<h3>Links Quebrados ({len(broken)})</h3>"
    html += _table(
        ["URL","Status","Encontrado em","Total de fontes"],
        [[r["url"], _status_badge(r["status"]),
          "<br>".join(r.get("linked_from", [])[:3]),
          str(r.get("total_sources", 0))]
         for r in broken],
        "Nenhum link quebrado encontrado"
    )

    chains = links.get("redirect_chains", [])
    html += f"<h3>Cadeias de Redirect ({len(chains)})</h3>"
    html += _table(
        ["Início","Intermediário","Destino final"],
        [[r["start"], r["intermediate"], r["final"]] for r in chains],
        "Nenhuma cadeia de redirect"
    )

    orphans = links.get("orphans", [])
    html += f"<h3>Páginas Prioritárias Órfãs ({len(orphans)})</h3>"
    html += '<p class="hint">Páginas com ≤ 2 links internos apontando. O Google raramente as rastreia bem.</p>'
    html += _table(
        ["URL","Links recebidos","De onde"],
        [[r["url"], str(r["incoming_count"]), ", ".join(r.get("sources",[])[:3])] for r in orphans],
        "Nenhuma página órfã entre as prioritárias"
    )

    return html


def _build_clusters_section(clusters: dict) -> str:
    if not clusters:
        return "<p>Análise de clusters não executada.</p>"

    analysis  = clusters.get("cluster_analysis", [])
    top_linked = clusters.get("top_linked", [])
    orphans   = clusters.get("orphans", [])

    html = "<h3>Saúde dos Clusters de Marca</h3>"
    html += '<p class="hint">Score 100 = cluster com linkagem bidirecional perfeita (pillar ↔ subcategorias ↔ blog)</p>'

    rows = []
    for c in analysis:
        mfp = ", ".join(c.get("missing_from_pillar", [])[:3]) or "—"
        mtp = ", ".join(c.get("missing_to_pillar", [])[:3]) or "—"
        rows.append([
            f'<strong>{c["brand"]}</strong>',
            c["pillar"],
            _badge(c["health_score"]),
            str(c["pillar_incoming"]),
            str(c["cluster_size"]),
            f'<span class="tag tag-red">{mfp}</span>' if mfp != "—" else "✓",
            f'<span class="tag tag-yellow">{mtp}</span>' if mtp != "—" else "✓",
        ])
    html += _table(
        ["Marca","Pillar","Health","Links p/ pillar","Páginas","Pillar não linka para","Não linkam de volta"],
        rows, "Nenhum cluster configurado"
    )

    html += f"<h3>Páginas com Mais Links Internos</h3>"
    html += _table(
        ["URL","Links recebidos"],
        [[r["url"], str(r["incoming"])] for r in top_linked[:15]],
        "—"
    )

    return html


def _build_pagespeed_section(ps: list) -> str:
    if not ps:
        return "<p>Auditoria PageSpeed não executada.</p>"

    mobile_data  = [r for r in ps if r.get("strategy") == "mobile" and "error" not in r]
    desktop_data = [r for r in ps if r.get("strategy") == "desktop" and "error" not in r]

    def avg(data, key):
        vals = [r[key] for r in data if r.get(key) is not None]
        return f"{sum(vals)/len(vals):.0f}" if vals else "—"

    html = f"""
<div class="kpi-row">
  <div class="kpi"><div class="kpi-val">{avg(mobile_data,"performance_score")}</div><div class="kpi-label">Performance Mobile (média)</div></div>
  <div class="kpi"><div class="kpi-val">{avg(desktop_data,"performance_score")}</div><div class="kpi-label">Performance Desktop (média)</div></div>
  <div class="kpi"><div class="kpi-val">{avg(mobile_data,"seo_score")}</div><div class="kpi-label">SEO Score (média)</div></div>
</div>
"""

    rows = []
    for r in sorted(ps, key=lambda x: (x.get("performance_score") or 100)):
        if "error" in r:
            continue
        url = r["url"].replace(get_site_url(), "")
        issues = " ".join(f'<span class="tag tag-red">{i}</span>' for i in r.get("issues", []))
        rows.append([
            f'<a href="{r["url"]}" target="_blank">{url[:45]}</a>',
            r.get("strategy","—"),
            _badge(r.get("performance_score")),
            _badge(r.get("seo_score")),
            r.get("lcp","—"),
            r.get("cls","—"),
            r.get("tbt","—"),
            issues or "✓",
        ])

    html += _table(
        ["URL","Device","Perf","SEO","LCP","CLS","TBT","Issues"],
        rows, "Nenhum dado de PageSpeed"
    )
    return html


# ── Main report builder ───────────────────────────────────────────────────────

def generate(results: dict, output_path: str = None) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    gsc_html     = _build_gsc_section(results.get("gsc", {}))
    onpage_html  = _build_onpage_section(results.get("onpage", []))
    dup_html     = _build_duplicate_section(results.get("duplicates", {}))
    links_html   = _build_links_section(results.get("broken_links", {}))
    cluster_html = _build_clusters_section(results.get("internal_links", {}))
    ps_html      = _build_pagespeed_section(results.get("pagespeed", []))

    # Summary counters
    gsc_issues  = len(results.get("gsc", {}).get("low_ctr_pages", []))
    op_issues   = len([p for p in results.get("onpage", []) if p.get("grade") in ("D","F","C")])
    dup_issues  = results.get("duplicates", {}).get("total_issues", 0)
    br_issues   = len(results.get("broken_links", {}).get("broken", []))
    cls_issues  = len([c for c in results.get("internal_links", {}).get("cluster_analysis", []) if c.get("health_score", 100) < 80])

    d3_data_json = json.dumps(
        results.get("internal_links", {}).get("d3_data", {"nodes": [], "links": []}))

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SEO Audit — {get_site_name()} — {now}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Inter',sans-serif;background:#f0f2f5;color:#1a1a1a;font-size:14px}}
  a{{color:#C8102E;text-decoration:none}} a:hover{{text-decoration:underline}}

  /* Layout */
  .layout{{display:flex;min-height:100vh}}
  .sidebar{{width:240px;background:#1a1a1a;color:#fff;padding:24px 0;position:sticky;top:0;height:100vh;overflow-y:auto;flex-shrink:0}}
  .sidebar-logo{{padding:0 20px 24px;border-bottom:1px solid #333;margin-bottom:16px}}
  .sidebar-logo h1{{font-size:16px;font-weight:700;color:#fff}}
  .sidebar-logo p{{font-size:11px;color:#888;margin-top:4px}}
  .sidebar nav a{{display:flex;align-items:center;gap:10px;padding:10px 20px;color:#ccc;font-size:13px;transition:all .15s}}
  .sidebar nav a:hover,.sidebar nav a.active{{background:#C8102E;color:#fff;text-decoration:none}}
  .sidebar nav a .nav-badge{{margin-left:auto;background:#333;border-radius:10px;padding:1px 7px;font-size:11px}}
  .sidebar nav a:hover .nav-badge,.sidebar nav a.active .nav-badge{{background:rgba(255,255,255,.2)}}

  .main{{flex:1;padding:32px;overflow-x:hidden}}
  .page-header{{margin-bottom:28px}}
  .page-header h1{{font-size:22px;font-weight:700}}
  .page-header p{{color:#666;margin-top:4px}}

  /* Summary bar */
  .summary-bar{{display:flex;gap:16px;margin-bottom:28px;flex-wrap:wrap}}
  .summary-item{{background:#fff;border-radius:10px;padding:16px 20px;flex:1;min-width:140px;
    box-shadow:0 1px 4px rgba(0,0,0,.08);border-left:4px solid #ddd}}
  .summary-item.red{{border-left-color:#C8102E}}
  .summary-item.yellow{{border-left-color:#f59e0b}}
  .summary-item.green{{border-left-color:#2e7d32}}
  .summary-item .val{{font-size:28px;font-weight:700}}
  .summary-item .label{{font-size:12px;color:#666;margin-top:4px}}

  /* Cards */
  .card{{background:#fff;border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:24px;overflow:hidden}}
  .card-header{{display:flex;align-items:center;gap:12px;padding:20px 24px;border-bottom:1px solid #f0f0f0;background:#fafafa}}
  .card-header h2{{font-size:16px;font-weight:600}}
  .card-icon{{font-size:20px}}
  .card-body{{padding:24px}}

  /* KPI */
  .kpi-row{{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap}}
  .kpi{{background:#f7f7f7;border-radius:8px;padding:16px 20px;flex:1;min-width:120px;text-align:center}}
  .kpi-val{{font-size:24px;font-weight:700;color:#1a1a1a}}
  .kpi-label{{font-size:11px;color:#666;margin-top:4px;text-transform:uppercase;letter-spacing:.5px}}
  .kpi-trend{{font-size:12px;font-weight:600;margin-top:6px}}

  /* Tables */
  .table-wrap{{overflow-x:auto;border-radius:8px;border:1px solid #eee}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th{{background:#f7f7f7;padding:10px 14px;text-align:left;font-weight:600;color:#555;white-space:nowrap;border-bottom:2px solid #eee}}
  td{{padding:10px 14px;border-bottom:1px solid #f0f0f0;vertical-align:top;line-height:1.5}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:#fafafa}}
  h3{{font-size:14px;font-weight:600;margin:24px 0 8px;color:#1a1a1a}}
  h3:first-child{{margin-top:0}}

  /* Badges */
  .badge{{display:inline-block;padding:3px 9px;border-radius:20px;font-size:12px;font-weight:600}}
  .badge-green{{background:#e8f5e9;color:#2e7d32}}
  .badge-yellow{{background:#fffde7;color:#f57f17}}
  .badge-orange{{background:#fff3e0;color:#e65100}}
  .badge-red{{background:#fce4ec;color:#C8102E}}
  .badge-gray{{background:#f5f5f5;color:#666}}

  /* Tags */
  .tag{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;margin:2px}}
  .tag-red{{background:#fce4ec;color:#C8102E}}
  .tag-yellow{{background:#fffde7;color:#f57f17}}
  .tag-green{{background:#e8f5e9;color:#2e7d32}}

  /* Chart */
  .chart-wrap{{margin-bottom:28px;background:#fafafa;border-radius:8px;padding:16px}}

  /* Misc */
  .hint{{font-size:12px;color:#888;margin-bottom:12px;font-style:italic}}
  .empty-msg{{color:#2e7d32;font-weight:500;padding:12px;background:#e8f5e9;border-radius:6px}}

  /* D3 graph */
  #d3-graph{{width:100%;height:500px;background:#f7f7f7;border-radius:8px;border:1px solid #eee;overflow:hidden}}
  .node circle{{stroke:#fff;stroke-width:1.5px;cursor:pointer}}
  .node text{{font-size:10px;fill:#333;pointer-events:none}}
  .link{{stroke:#ddd;stroke-opacity:.6}}

  @media(max-width:900px){{
    .sidebar{{display:none}}
    .main{{padding:16px}}
    .kpi-row{{flex-direction:column}}
  }}
</style>
</head>
<body>
<div class="layout">

<!-- Sidebar -->
<aside class="sidebar">
  <div class="sidebar-logo">
    <h1>SEO Audit</h1>
    <p>{get_site_name()} · {now}</p>
  </div>
  <nav>
    <a href="#gsc">📊 GSC Analysis <span class="nav-badge">{gsc_issues}</span></a>
    <a href="#onpage">📄 On-page <span class="nav-badge">{op_issues}</span></a>
    <a href="#duplicates">🔁 Duplicatas <span class="nav-badge">{dup_issues}</span></a>
    <a href="#broken-links">🔗 Links Quebrados <span class="nav-badge">{br_issues}</span></a>
    <a href="#clusters">🗺 Clusters <span class="nav-badge">{cls_issues}</span></a>
    <a href="#pagespeed">⚡ PageSpeed</a>
  </nav>
</aside>

<!-- Main -->
<main class="main">
  <div class="page-header">
    <h1>Relatório SEO — {get_site_name()}</h1>
    <p>Gerado em {now}</p>
  </div>

  <div class="summary-bar">
    <div class="summary-item red">
      <div class="val">{br_issues}</div>
      <div class="label">Links Quebrados</div>
    </div>
    <div class="summary-item yellow">
      <div class="val">{op_issues}</div>
      <div class="label">Páginas com Issues On-page</div>
    </div>
    <div class="summary-item yellow">
      <div class="val">{dup_issues}</div>
      <div class="label">Duplicatas Encontradas</div>
    </div>
    <div class="summary-item yellow">
      <div class="val">{cls_issues}</div>
      <div class="label">Clusters com Saúde &lt; 80</div>
    </div>
    <div class="summary-item green">
      <div class="val">{gsc_issues}</div>
      <div class="label">Oportunidades GSC</div>
    </div>
  </div>

  {_section("Google Search Console", "📊", gsc_html, "gsc")}
  {_section("On-page Audit", "📄", onpage_html, "onpage")}
  {_section("Conteúdo Duplicado", "🔁", dup_html, "duplicates")}
  {_section("Links Quebrados & Estrutura", "🔗", links_html, "broken-links")}
  {_section("Clusters & Links Internos", "🗺", cluster_html + _d3_section(d3_data_json), "clusters")}
  {_section("PageSpeed / Core Web Vitals", "⚡", ps_html, "pagespeed")}

</main>
</div>

<script>
// Sidebar active link on scroll
const sections = document.querySelectorAll("section[id]");
const navLinks  = document.querySelectorAll(".sidebar nav a");
window.addEventListener("scroll", () => {{
  let current = "";
  sections.forEach(s => {{ if (window.scrollY >= s.offsetTop - 100) current = s.id; }});
  navLinks.forEach(a => {{
    a.classList.toggle("active", a.getAttribute("href") === "#" + current);
  }});
}});
</script>
</body>
</html>"""

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n✓ Relatório salvo em: {output_path}")

    return html


def _d3_section(d3_data_json: str) -> str:
    return f"""
<h3>Mapa de Links Internos</h3>
<p class="hint">Nós maiores = mais links internos recebidos. Vermelho = pillar, Azul = categoria, Verde = blog.</p>
<div id="d3-graph"></div>
<script>
(function(){{
  var data = {d3_data_json};
  if (!data.nodes || data.nodes.length === 0) {{
    document.getElementById("d3-graph").innerHTML = '<p style="padding:20px;color:#888">Execute o módulo de links internos para ver o grafo.</p>';
    return;
  }}
  var w = document.getElementById("d3-graph").clientWidth || 800;
  var h = 500;
  var color = {{"home":"#C8102E","pillar":"#C8102E","category":"#1565C0","blog":"#2e7d32","other":"#999","product":"#ccc"}};

  var svg = d3.select("#d3-graph").append("svg").attr("width",w).attr("height",h);
  var sim = d3.forceSimulation(data.nodes)
    .force("link", d3.forceLink(data.links).id(d=>d.id).distance(80))
    .force("charge", d3.forceManyBody().strength(-120))
    .force("center", d3.forceCenter(w/2, h/2))
    .force("collision", d3.forceCollide().radius(d=>d.size+4));

  var link = svg.append("g").selectAll("line").data(data.links).join("line")
    .attr("class","link").attr("stroke-width",1);

  var node = svg.append("g").selectAll("g").data(data.nodes).join("g").attr("class","node")
    .call(d3.drag()
      .on("start", (e,d)=>{{ if(!e.active) sim.alphaTarget(.3).restart(); d.fx=d.x; d.fy=d.y; }})
      .on("drag",  (e,d)=>{{ d.fx=e.x; d.fy=e.y; }})
      .on("end",   (e,d)=>{{ if(!e.active) sim.alphaTarget(0); d.fx=null; d.fy=null; }}));

  node.append("circle").attr("r",d=>d.size).attr("fill",d=>color[d.type]||"#999").attr("opacity",.85);
  node.append("title").text(d=>d.url);
  node.append("text").attr("dx",d=>d.size+3).attr("dy","0.35em").text(d=>d.label);

  sim.on("tick",()=>{{
    link.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y)
        .attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
    node.attr("transform",d=>`translate(${{d.x}},${{d.y}})`);
  }});
}})();
</script>
"""
