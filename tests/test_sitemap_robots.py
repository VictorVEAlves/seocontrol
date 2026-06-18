from modules import sitemap_robots


def test_parse_sitemap_ignores_image_locs_and_external_cdn(monkeypatch):
    monkeypatch.setattr(sitemap_robots, "get_site_url", lambda: "https://example.com")

    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
            xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
      <url>
        <loc>https://example.com/categoria</loc>
        <image:image>
          <image:loc>https://cdn.dooca.store/946/products/0-1.jpg</image:loc>
        </image:image>
      </url>
      <url>
        <loc>https://cdn.dooca.store/946/products/0-2.jpg</loc>
      </url>
      <url>
        <loc>https://example.com/uploads/banner.jpg</loc>
      </url>
      <url>
        <loc>https://example.com/produto</loc>
      </url>
    </urlset>"""

    urls, indexes = sitemap_robots._parse_sitemap_xml(xml)

    assert indexes == []
    assert urls == [
        "https://example.com/categoria",
        "https://example.com/produto",
    ]


def test_parse_sitemap_index_keeps_child_sitemaps(monkeypatch):
    monkeypatch.setattr(sitemap_robots, "get_site_url", lambda: "https://example.com")

    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.com/sitemap-products.xml</loc></sitemap>
      <sitemap><loc>https://example.com/sitemap-categories.xml</loc></sitemap>
    </sitemapindex>"""

    urls, indexes = sitemap_robots._parse_sitemap_xml(xml)

    assert urls == []
    assert indexes == [
        "https://example.com/sitemap-products.xml",
        "https://example.com/sitemap-categories.xml",
    ]


def test_analyze_reuses_crawl_canonical_without_refetch(monkeypatch):
    monkeypatch.setattr(sitemap_robots, "get_site_url", lambda: "https://example.com")
    monkeypatch.setattr(
        sitemap_robots,
        "fetch_sitemap_urls",
        lambda: {
            "urls": ["https://example.com/a"],
            "robots": {"disallows": []},
            "sitemaps_checked": ["https://example.com/sitemap.xml"],
            "errors": [],
        },
    )

    def fail_get_page(url):
        raise AssertionError(f"unexpected refetch: {url}")

    monkeypatch.setattr(sitemap_robots, "get_page", fail_get_page)

    result = sitemap_robots.analyze(
        crawl_data={
            "pages": {
                "https://example.com/a": {
                    "final_url": "https://example.com/a",
                    "canonical": "https://example.com/b",
                }
            }
        },
        priority_pages=["/a"],
    )

    assert result["canonical_issues"] == [
        {"url": "/a", "canonical": "/b", "final_url": "/a"}
    ]
