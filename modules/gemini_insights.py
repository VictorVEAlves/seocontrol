"""
Gemini-powered SEO insights layer.

Pipeline:
  audit_results (dict)
      → compact_audit()       — extrai métricas-chave, descarta ruído
      → build_prompt()        — monta prompt estruturado em PT-BR
      → call_gemini()         — chama Gemini Flash via REST
      → parse_response()      — valida e normaliza o JSON
      → insights_to_tasks()   — converte para itens de backlog com ICE

Usage via run.py:
  python run.py --module ai-insights --ai --provider gemini
  python run.py --all --ai --provider gemini --save-db
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from config import BASE_DIR, get_brand_clusters, get_business_context, get_site_url

INSIGHTS_FILE = BASE_DIR / "reports" / "ai_insights_latest.json"

# ── Gemini call ───────────────────────────────────────────────────────────────

# Order: lite first (reliable free tier), then full flash, then 2.0 as fallback
_GEMINI_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _extract_text(response_json: dict) -> str:
    """Extract text from Gemini response, handling thinking-token responses (2.5+)."""
    candidates = response_json.get("candidates", [])
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    # Gemini 2.5 may return thought parts before the actual answer; skip them
    text_parts = [p.get("text", "") for p in parts if "text" in p and not p.get("thought")]
    return "".join(text_parts) or "".join(p.get("text", "") for p in parts)


def _call_gemini_raw(prompt: str, api_key: str) -> str:
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
        },
    }
    last_error = ""
    for model in _GEMINI_MODELS:
        url = f"{_GEMINI_BASE}/{model}:generateContent?key={api_key}"
        try:
            r = requests.post(url, json=body, timeout=90)
            if r.status_code == 429:
                time.sleep(5)
                continue
            if r.status_code == 503:
                time.sleep(3)
                continue
            r.raise_for_status()
            text = _extract_text(r.json())
            if text:
                return text
            last_error = f"Resposta vazia do modelo {model}"
        except Exception as exc:
            last_error = str(exc)
            continue
    raise RuntimeError(f"Gemini falhou em todos os modelos: {last_error}")


def _parse_json_safe(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?", "", raw).rstrip("`").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
    return {}


# ── Compact audit data ────────────────────────────────────────────────────────

def _fmt_page(p: dict) -> dict:
    return {
        "url": str(p.get("page") or p.get("url") or "").replace(get_site_url(), ""),
        "clicks": int(p.get("clicks") or 0),
        "impr": int(p.get("impressions") or 0),
        "ctr": f"{float(p.get('ctr') or 0):.2f}%",
        "pos": round(float(p.get("position") or 0), 1),
    }


def _fmt_query(q: dict) -> dict:
    return {
        "query": str(q.get("query") or ""),
        "clicks": int(q.get("clicks") or 0),
        "impr": int(q.get("impressions") or 0),
        "ctr": f"{float(q.get('ctr') or 0):.2f}%",
        "pos": round(float(q.get("position") or 0), 1),
    }


def compact_audit(results: dict) -> dict:
    gsc_api = results.get("gsc_api") or {}
    gsc_csv = results.get("gsc") or {}

    # ── Source: GSC API (preferred — live data from Google Search Console) ──────
    use_api = bool(gsc_api and not gsc_api.get("error"))

    # On-page issues (grade C, D, F) — from onpage module if available
    bad_onpage = [
        {
            "url": str(p.get("url") or "").replace(get_site_url(), ""),
            "grade": p.get("grade"),
            "issues": p.get("issues", [])[:4],
        }
        for p in (results.get("onpage") or [])
        if p.get("grade") in ("C", "D", "F")
    ][:20]

    existing_backlog = [
        {"action": i.get("action", ""), "target": str(i.get("target") or "")}
        for i in (results.get("backlog") or [])[:10]
    ]

    if use_api:
        drops        = gsc_api.get("drops") or []
        brand_sum    = gsc_api.get("brand_summary") or {}
        tag_sug      = gsc_api.get("tag_suggestions") or []
        page_cont    = gsc_api.get("page_content") or []
        prev_analysis = gsc_api.get("ai_analysis") or {}

        # Brand health scores derived from brand_summary
        brand_health: dict[str, dict] = {}
        for brand, info in brand_sum.items():
            n_crit  = int(info.get("n_critical") or 0)
            n_warn  = int(info.get("n_warning") or 0)
            net     = int(info.get("net_change") or 0)
            lost    = int(info.get("impressions_lost") or 0)
            gained  = int(info.get("impressions_gained") or 0)
            score   = max(0, min(100, 100 - n_crit * 20 - n_warn * 8 + (10 if net > 0 else 0)))
            brand_health[brand] = {
                "tier":               info.get("tier"),
                "score":              score,
                "n_critical":         n_crit,
                "n_warning":          n_warn,
                "impressions_lost":   lost,
                "impressions_gained": gained,
                "net_change":         net,
            }

        # Top drops (by _score, already sorted) for the prompt
        top_drops = [
            {
                "url":               d["page"].replace(get_site_url(), ""),
                "brand":             d.get("brand"),
                "tier":              d.get("tier"),
                "severity":          d.get("severity"),
                "impressions":       d.get("impressions"),
                "impressions_delta": f"{d['impressions_delta']:+.0%}" if d.get("impressions_delta") is not None else None,
                "clicks_delta":      f"{d['clicks_delta']:+.0%}" if d.get("clicks_delta") is not None else None,
                "position":          d.get("position"),
            }
            for d in drops[:20]
        ]

        # Current page metadata (title/H1/desc) for pages already audited
        page_tags = [
            {
                "url":         pc.get("url", "").replace(get_site_url(), ""),
                "title":       pc.get("title", ""),
                "h1":          pc.get("h1", ""),
                "description": (pc.get("description") or "")[:120],
                "severity":    pc.get("severity"),
            }
            for pc in page_cont[:15]
            if not pc.get("blocked")
        ]

        # Existing tag suggestions from gsc_api's own Gemini call — summarize them
        tag_summary = [
            {"url": s.get("page"), "issue": s.get("main_issue"), "priority": s.get("priority")}
            for s in tag_sug[:10]
        ]

        return {
            "site":              get_site_url(),
            "data_source":       "gsc_api",
            "brand_tiers":       {},
            "period_current":    gsc_api.get("period_current", ""),
            "period_previous":   gsc_api.get("period_previous", ""),
            "comparison":        gsc_api.get("comparison_label", ""),
            "total_pages":       gsc_api.get("total_pages_cur", 0),
            "brand_health":      brand_health,
            "top_drops":         top_drops,
            "page_tags":         page_tags,
            "tag_issues_summary": tag_summary,
            "previous_ai_patterns": prev_analysis.get("padroes", [])[:3],
            "onpage_issues":     bad_onpage,
            "existing_backlog":  existing_backlog,
        }

    # ── Fallback: CSV-based GSC data ─────────────────────────────────────────────
    top_pages = sorted(
        gsc_csv.get("top_pages", []),
        key=lambda x: int(x.get("impressions") or 0), reverse=True,
    )[:25]
    ctr_problems = sorted(
        [p for p in gsc_csv.get("low_ctr_pages", []) if int(p.get("impressions") or 0) > 10000],
        key=lambda x: int(x.get("potential_clicks") or 0), reverse=True,
    )[:15]
    top_queries  = sorted(
        gsc_csv.get("top_queries", []),
        key=lambda x: int(x.get("impressions") or 0), reverse=True,
    )[:20]
    quick_wins   = sorted(
        gsc_csv.get("quick_wins", []),
        key=lambda x: float(x.get("position") or 99),
    )[:10]
    brand_health_csv: dict[str, dict] = {}
    page_map = {str(p.get("page") or "").replace(get_site_url(), ""): p for p in top_pages}
    for brand, cluster in get_brand_clusters().items():
        pillar = cluster["pillar"]
        p = page_map.get(pillar)
        if p:
            brand_health_csv[brand] = {
                "tier":   cluster.get("tier", "good"),
                "pillar": pillar,
                "impr":   int(p.get("impressions") or 0),
                "clicks": int(p.get("clicks") or 0),
                "ctr":    f"{float(p.get('ctr') or 0):.2f}%",
                "pos":    round(float(p.get("position") or 0), 1),
            }
    benchmarks = gsc_csv.get("benchmarks", {})
    return {
        "site":        get_site_url(),
        "data_source": "gsc_csv",
        "brand_tiers": {},
        "benchmarks": {
            "avg_ctr":      f"{benchmarks.get('avg_ctr', 0):.2f}%",
            "avg_position": round(benchmarks.get("avg_position", 0), 1),
        },
        "top_pages_by_impressions": [_fmt_page(p) for p in top_pages],
        "ctr_opportunities":        [_fmt_page(p) for p in ctr_problems],
        "top_queries":              [_fmt_query(q) for q in top_queries],
        "quick_win_queries":        [_fmt_query(q) for q in quick_wins],
        "onpage_issues":            bad_onpage,
        "brand_health":             brand_health_csv,
        "existing_backlog":         existing_backlog,
    }


# ── Prompt builder ────────────────────────────────────────────────────────────

def _system_ctx() -> str:
    return f"""Você é um especialista sênior em SEO técnico e estratégico.

Site: {get_site_url() or "site configurado pelo cliente"}
Contexto do negócio: {get_business_context()}

Regras:
- Use apenas dados fornecidos na auditoria.
- Priorize páginas, marcas, entidades ou serviços configurados pelo cliente.
- Diferencie problema técnico, oportunidade de conteúdo e oportunidade comercial.
- CTR muito abaixo do benchmark indica possível problema de snippet, intenção ou desalinhamento de página."""


def build_prompt(compact: dict) -> str:
    data_source = compact.get("data_source", "gsc_csv")
    data_str    = json.dumps(compact, ensure_ascii=False, indent=2)

    api_note = (
        "Os dados vêm do Google Search Console API (ao vivo). "
        "'top_drops' são páginas que perderam impressões. "
        "'brand_health' mostra o saldo líquido (ganhos - perdas) por marca. "
        "'previous_ai_patterns' é a análise de padrões já feita na etapa anterior — use-a como contexto, não repita."
        if data_source == "gsc_api"
        else "Os dados vêm de exports CSV do GSC."
    )

    return f"""{_system_ctx()}

---

NOTA SOBRE OS DADOS: {api_note}

DADOS DA AUDITORIA SEO:
{data_str}

---

Analise os dados com profundidade e retorne APENAS um JSON válido com a estrutura abaixo.
Seja específico: use URLs reais, números reais, evite generalismos.
Priorize entidades/páginas configuradas como importantes quando existirem.
Se a fonte for gsc_api, use as quedas e variações netas — não invente dados que não estão nos dados.

{{
  "executive_summary": "Resumo executivo em 3-4 frases. Cite marcas específicas, volumes e o impacto neto mais relevante.",

  "critical_alerts": [
    {{
      "title": "Título curto do alerta",
      "description": "Descrição com dados específicos (impressões perdidas, delta %, posição)",
      "urgency": "alta|média|baixa",
      "brand_tier": "top|good|all"
    }}
  ],

  "brand_health": {{
    "nome_da_marca": {{
      "score": 0,
      "tier": "top|good",
      "main_issue": "Problema principal com dado numérico concreto",
      "priority_action": "Ação específica e mensurável"
    }}
  }},

  "tasks": [
    {{
      "action": "Descrição clara e acionável (verbo no infinitivo)",
      "target": "/url-ou-query",
      "reason": "Por que isso importa — cite números dos dados fornecidos",
      "impact": 1,
      "confidence": 1,
      "effort": 1,
      "owner": "SEO|Dev|Conteúdo",
      "source": "ai_insights",
      "brand_tier": "top|good|traffic"
    }}
  ],

  "content_gaps": [
    {{
      "type": "hub_page|product_guide|editorial|landing",
      "title": "Título sugerido para o conteúdo",
      "target_query": "query principal",
      "rationale": "Por que criar e qual resultado esperado (seja específico)"
    }}
  ],

  "snippet_rewrites": [
    {{
      "url": "/pagina",
      "current_problem": "O que está errado no snippet atual (use dados de page_tags se disponíveis)",
      "suggested_title": "Meta title sugerido (50-60 chars)",
      "suggested_description": "Meta description sugerida (145-160 chars) com CTA e diferencial de outlet"
    }}
  ]
}}

REGRAS ICE para tasks:
- impact (1-100): >100k impressões = 70+, >250k = 85+, queda crítica = +15
- confidence (1-100): dados concretos disponíveis = 80+, inferência = 50
- effort (1-10): editar meta/título = 1-2, criar conteúdo = 3-4, mudança estrutural = 7-10
- Gere 8-15 tasks ordenadas por prioridade (impact × confidence/100 / effort)
- snippet_rewrites: mínimo 5, priorizando páginas com queda e tags disponíveis em page_tags
"""


# ── Main entry point ──────────────────────────────────────────────────────────

def run(
    results: dict,
    provider: str | None = None,
    api_key: str | None = None,
) -> dict:
    """Analyze audit results with Gemini and return structured insights + tasks."""
    from config import GEMINI_API_KEY, get_provider_api_key

    # Always use Gemini key — this module only works with the Gemini REST API.
    # Ignore any generic provider key that may have been resolved upstream.
    key = GEMINI_API_KEY or get_provider_api_key("gemini") or (api_key if provider == "gemini" else "")
    if not key:
        return {
            "_ai_enhanced": False,
            "_ai_error": "GEMINI_API_KEY não configurada no .env.",
            "executive_summary": "",
            "critical_alerts": [],
            "brand_health": {},
            "tasks": [],
            "content_gaps": [],
            "snippet_rewrites": [],
        }

    print("    Compactando dados da auditoria...")
    compact = compact_audit(results)

    print("    Chamando Gemini para análise estratégica...")
    try:
        raw = _call_gemini_raw(build_prompt(compact), key)
        data = _parse_json_safe(raw)
    except Exception as exc:
        return {
            "_ai_enhanced": False,
            "_ai_error": str(exc),
            "executive_summary": "",
            "critical_alerts": [],
            "brand_health": {},
            "tasks": [],
            "content_gaps": [],
            "snippet_rewrites": [],
        }

    if not isinstance(data, dict):
        return {
            "_ai_enhanced": False,
            "_ai_error": "Gemini retornou resposta não estruturada.",
            "executive_summary": "",
            "critical_alerts": [],
            "brand_health": {},
            "tasks": [],
            "content_gaps": [],
            "snippet_rewrites": [],
        }

    data["_ai_enhanced"] = True
    data["_ai_provider"] = "gemini"
    data["_ai_generated_at"] = datetime.now().isoformat()

    # Validate and clamp ICE scores
    for task in data.get("tasks", []):
        task["impact"]     = max(1, min(100, int(task.get("impact")     or 50)))
        task["confidence"] = max(1, min(100, int(task.get("confidence") or 70)))
        task["effort"]     = max(1, min(10,  int(task.get("effort")     or 3)))
        task["source"]     = "ai_insights"
        if "brand_tier" not in task:
            task["brand_tier"] = "all"

    # Persist to disk
    INSIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    INSIGHTS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"    ok análise salva em {INSIGHTS_FILE.name}")

    return data


# ── Convert insights to backlog items ─────────────────────────────────────────

def insights_to_backlog_items(insights: dict) -> list[dict]:
    """Convert AI-generated tasks into backlog items compatible with backlog.py format."""
    items = []
    for task in insights.get("tasks", []):
        impact     = max(1, min(100, int(task.get("impact")     or 50)))
        confidence = max(1, min(100, int(task.get("confidence") or 70)))
        effort     = max(1, min(10,  int(task.get("effort")     or 3)))
        priority   = round((impact * (confidence / 100)) / effort, 1)
        items.append({
            "source":     "ai_insights",
            "action":     task.get("action", ""),
            "target":     task.get("target", ""),
            "reason":     task.get("reason", ""),
            "impact":     impact,
            "confidence": confidence,
            "effort":     effort,
            "priority":   priority,
            "owner":      task.get("owner", "SEO"),
            "brand_tier": task.get("brand_tier", "all"),
            "evidence":   {"ai_generated": True, "source_module": "gemini_insights"},
        })
    return items


def load_latest() -> dict | None:
    """Load the most recent insights from disk."""
    if INSIGHTS_FILE.exists():
        try:
            return json.loads(INSIGHTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None
