from config import get_site_url


TITLE_MIN = 45
TITLE_MAX = 60
DESC_MIN = 140
DESC_MAX = 160


def _status(length: int, minimum: int, maximum: int) -> str:
    if length < minimum:
        return "short"
    if length > maximum:
        return "long"
    return "ok"


def analyze(onpage_results: list, pending_changes: list | None = None) -> dict:
    pending_by_url = {item.get("_url") or item.get("url"): item for item in pending_changes or []}
    rows = []
    issues = []
    base = get_site_url()
    for page in onpage_results or []:
        url = page.get("url", "")
        path = url.replace(base, "") or "/"
        pending = pending_by_url.get(path) or pending_by_url.get(url)
        title = page.get("title", "")
        desc = page.get("description", "")
        row = {
            "url": path,
            "published_title": title,
            "published_description": desc,
            "title_length": len(title),
            "description_length": len(desc),
            "title_status": _status(len(title), TITLE_MIN, TITLE_MAX),
            "description_status": _status(len(desc), DESC_MIN, DESC_MAX),
            "proposed_title": pending.get("meta_title") if pending else "",
            "proposed_description": pending.get("meta_description") if pending else "",
            "has_pending_change": bool(pending),
        }
        rows.append(row)
        if row["title_status"] != "ok" or row["description_status"] != "ok":
            issues.append(row)
    return {"total": len(rows), "issues": issues, "snippets": rows}


def run(onpage_results: list, pending_changes: list | None = None) -> dict:
    return analyze(onpage_results, pending_changes)
