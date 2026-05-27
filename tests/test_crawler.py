from collectors.crawler import is_internal, normalize_url, should_skip


def test_normalize_url_removes_query_fragment_and_trailing_slash():
    url = normalize_url("/lacoste/?utm_source=x#products")
    assert url == "https://www.secretoutlet.com.br/lacoste"


def test_internal_and_skip_rules():
    assert is_internal("/lacoste")
    assert is_internal("https://www.secretoutlet.com.br/lacoste")
    assert not is_internal("https://example.com/lacoste")
    assert should_skip("https://www.secretoutlet.com.br/image.jpg")
    assert should_skip("mailto:contato@example.com")
