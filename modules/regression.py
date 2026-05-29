from modules.supabase_store import _client
from config import get_site_id


def run(limit: int = 2) -> dict:
    sb = _client()
    query = sb.table("crawl_runs").select("id, created_at, run_type, summary")
    site_id = get_site_id()
    if site_id:
        query = query.eq("site_id", site_id)
    runs = query.order("created_at", desc=True).limit(limit).execute().data
    if len(runs) < 2:
        return {"status": "insufficient_data", "runs": runs, "regressions": []}

    current, previous = runs[0], runs[1]
    regressions = []
    current_summary = current.get("summary") or {}
    previous_summary = previous.get("summary") or {}
    for key in sorted(set(current_summary) | set(previous_summary)):
        cur = current_summary.get(key, 0) or 0
        prev = previous_summary.get(key, 0) or 0
        if isinstance(cur, (int, float)) and isinstance(prev, (int, float)) and cur > prev:
            regressions.append({"metric": key, "previous": prev, "current": cur, "delta": cur - prev})
    return {"status": "ok", "current": current, "previous": previous, "regressions": regressions}
