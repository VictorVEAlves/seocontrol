"""AI-assisted synthesis for SEO audit results."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from config import (BASE_DIR, get_business_context, get_site_name, get_site_url,
                    get_site_id, get_site_owner_user_id)
from modules.ai_layer import call_json

AI_ANALYSIS_FILE = BASE_DIR / "reports" / "ai_analysis_latest.json"


def _analysis_file() -> Path:
    user_id = get_site_owner_user_id()
    site_id = get_site_id()
    if user_id or site_id:
        raw_key = "|".join([user_id or "local", site_id or "", get_site_url() or "default"])
        key = hashlib.sha1(raw_key.encode("utf-8")).hexdigest()
        return BASE_DIR / ".runtime" / "ai_analysis" / f"ai_analysis_{key}.json"
    return AI_ANALYSIS_FILE

AI_ANALYSIS_SYSTEM = """Voce e um diretor de SEO tecnico e estrategico.

Sua tarefa e ler um resumo estruturado de auditoria SEO e devolver uma analise executiva acionavel.

Regras:
- Seja especifico e priorize por impacto de negocio, risco e velocidade.
- Nao invente metricas que nao foram fornecidas.
- Diferencie problema tecnico, oportunidade de conteudo e oportunidade comercial.
- Retorne APENAS JSON valido, sem markdown e sem texto fora do JSON.

Formato:
{
  "summary": "...",
  "critical_risks": [{"title": "...", "why_it_matters": "...", "next_step": "..."}],
  "quick_wins": [{"title": "...", "target": "...", "expected_impact": "..."}],
  "content_opportunities": [{"title": "...", "query_or_cluster": "...", "angle": "..."}],
  "technical_recommendations": [{"title": "...", "target": "...", "fix": "..."}],
  "next_actions": ["...", "..."],
  "confidence_notes": ["...", "..."]
}"""


def _take(rows: list | None, limit: int) -> list:
    return list(rows or [])[:limit]


def compact_results(results: dict[str, Any]) -> dict[str, Any]:
    """Keep only high-signal fields so the AI prompt stays small and stable."""
    gsc = results.get("gsc", {}) or {}
    compact = {
        "generated_at": datetime.now().isoformat(),
        "site": {
            "name": get_site_name(),
            "url": get_site_url(),
            "business_context": get_business_context(),
        },
        "scope": results.get("_urls", []),
        "gsc": {
            "top_queries": _take(gsc.get("top_queries"), 12),
            "page_ctr_opportunities": _take(gsc.get("low_ctr_pages"), 12),
            "query_content_opportunities": _take(gsc.get("content_opps"), 12),
            "position_opportunities": _take(gsc.get("pos_opps"), 12),
            "quick_wins": _take(gsc.get("quick_wins"), 12),
        },
        "onpage": [
            {
                "url": row.get("url"),
                "grade": row.get("grade"),
                "score": row.get("score"),
                "issues": _take(row.get("issues"), 6),
                "warnings": _take(row.get("warnings"), 6),
                "title": row.get("title"),
                "description": row.get("description"),
            }
            for row in _take(results.get("onpage"), 20)
        ],
        "backlog": _take(results.get("backlog"), 20),
        "content_gap": _take((results.get("content_gap") or {}).get("gaps"), 12),
        "indexability_issues": _take((results.get("indexability") or {}).get("issues"), 20),
        "snippet_issues": _take((results.get("snippets") or {}).get("issues"), 20),
        "product_issues": _take((results.get("products") or {}).get("issues"), 20),
        "link_suggestions": _take((results.get("link_suggestions") or {}).get("suggestions"), 20),
        "blog_ideas": _take(results.get("blog_ideas"), 12),
    }
    return compact


def save_analysis(analysis: dict[str, Any]) -> None:
    analysis_file = _analysis_file()
    analysis_file.parent.mkdir(parents=True, exist_ok=True)
    analysis_file.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run(
    results: dict[str, Any],
    provider: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    compact = compact_results(results)
    fallback = {
        "summary": "Analise por IA indisponivel. A auditoria base foi mantida.",
        "critical_risks": [],
        "quick_wins": [],
        "content_opportunities": [],
        "technical_recommendations": [],
        "next_actions": [],
        "confidence_notes": ["Configure uma chave de IA no .env para habilitar a sintese."],
    }
    analysis = call_json(
        prompt=json.dumps(compact, ensure_ascii=False, indent=2),
        system_prompt=AI_ANALYSIS_SYSTEM,
        provider=provider,
        api_key=api_key,
        fallback=fallback,
    )
    analysis["input_summary"] = {
        "gsc_queries": len(compact["gsc"]["top_queries"]),
        "onpage_pages": len(compact["onpage"]),
        "backlog_items": len(compact["backlog"]),
        "content_gaps": len(compact["content_gap"]),
    }
    save_analysis(analysis)
    return analysis
