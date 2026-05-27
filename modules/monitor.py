from actions import backlog, persist
from run import (
    run_broken_links,
    run_content_gap,
    run_gsc,
    run_indexability,
    run_onpage,
    run_products,
    run_regression,
    run_sitemap,
    run_snippets,
)


def run(scope: list, gsc_folder: str = None, max_pages: int = 100) -> dict:
    results = {"_urls": scope}
    results["gsc"] = run_gsc(folder=gsc_folder, urls=scope)
    results["onpage"] = run_onpage(scope)
    broken = run_broken_links(urls=scope, max_pages=max_pages)
    results["broken_links"] = broken
    results["sitemap"] = run_sitemap(crawl_data=broken.get("crawl_data"), priority_pages=scope)
    results["indexability"] = run_indexability(scope)
    results["snippets"] = run_snippets(results["onpage"])
    results["content_gap"] = run_content_gap(results["gsc"])
    results["products"] = run_products(scope)
    results["backlog"] = backlog.run(results, limit=50)
    run_id = persist.save_audit_results(results, run_type="monitor", scope=scope)
    results["run_id"] = run_id
    results["regression"] = run_regression()
    return results
