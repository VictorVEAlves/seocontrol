import importlib
import os

from dotenv import load_dotenv

load_dotenv()


REQUIRED_PACKAGES = ["requests", "bs4", "pandas", "supabase", "flask", "pytest"]
REQUIRED_ENV = ["SUPABASE_URL", "SUPABASE_KEY"]
EXPECTED_TABLES = [
    "sites",
    "urls",
    "crawl_runs",
    "page_snapshots",
    "gsc_queries",
    "gsc_pages",
    "issues",
    "recommendations",
    "content_changes",
]


def run(check_remote: bool = True) -> dict:
    packages = []
    for package in REQUIRED_PACKAGES:
        try:
            importlib.import_module(package)
            packages.append({"name": package, "ok": True, "error": ""})
        except Exception as exc:
            packages.append({"name": package, "ok": False, "error": str(exc)})

    env = [{"name": key, "ok": bool(os.environ.get(key))} for key in REQUIRED_ENV]
    tables = []
    if check_remote and all(item["ok"] for item in env):
        try:
            from modules.supabase_store import _client

            sb = _client()
            for table in EXPECTED_TABLES:
                try:
                    sb.table(table).select("id").limit(1).execute()
                    tables.append({"name": table, "ok": True, "error": ""})
                except Exception as exc:
                    tables.append({"name": table, "ok": False, "error": str(exc)})
        except Exception as exc:
            tables.append({"name": "supabase_connection", "ok": False, "error": str(exc)})

    ok = all(item["ok"] for item in packages) and all(item["ok"] for item in env) and all(item["ok"] for item in tables)
    return {"ok": ok, "packages": packages, "env": env, "tables": tables}


def print_result(result: dict) -> None:
    print("  Diagnostico do sistema")
    for group in ["packages", "env", "tables"]:
        if not result.get(group):
            continue
        print(f"   {group}:")
        for item in result[group]:
            symbol = "ok" if item["ok"] else "x"
            error = f" - {item.get('error')}" if item.get("error") else ""
            print(f"    {symbol} {item['name']}{error}")
    print(f"  Status geral: {'OK' if result.get('ok') else 'ATENCAO'}")
