import app as dashboard


class _FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class _FakeContentQuery:
    def __init__(self, store, action="select", payload=None):
        self.store = store
        self.action = action
        self.payload = payload
        self.filters = {}

    def select(self, *args, **kwargs):
        self.action = "select"
        return self

    def update(self, payload):
        self.action = "update"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def ilike(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def range(self, *args, **kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        idea_id = self.filters.get("id")
        if self.action == "update":
            self.store["updates"].append({"filters": dict(self.filters), "payload": self.payload})
            row = self.store["rows"].setdefault(idea_id, {})
            row.update(self.payload)
            return _FakeResult([row])
        if idea_id is not None:
            row = self.store["rows"].get(idea_id)
            if row is None:
                raise RuntimeError("not found")
            return _FakeResult(row)
        return _FakeResult(list(self.store["rows"].values()), count=len(self.store["rows"]))


class _FakeSupabase:
    def __init__(self, rows):
        self.store = {"rows": rows, "updates": []}

    def table(self, name):
        assert name == "content_changes"
        return _FakeContentQuery(self.store)


def test_blog_content_view_reads_saved_html_without_ai(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    fake = _FakeSupabase({
        "idea-1": {
            "id": "idea-1",
            "description_html": "<article><h1>Salvo</h1></article>",
            "provider": "query_suggester",
            "raw": {"blog_content_provider": "gemini"},
        }
    })
    monkeypatch.setattr(dashboard, "get_supabase", lambda: fake)

    def _fail_generate(_idea):
        raise AssertionError("view route must not call AI generation")

    monkeypatch.setattr("modules.blog_content.generate", _fail_generate)

    response = dashboard.app.test_client().get("/blog-ideas/idea-1/content")

    assert response.status_code == 200
    assert response.get_json()["html"] == "<article><h1>Salvo</h1></article>"
    assert response.get_json()["provider"] == "gemini"
    assert fake.store["updates"] == []


def test_blog_content_generate_persists_html(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    fake = _FakeSupabase({
        "idea-2": {
            "id": "idea-2",
            "meta_title": "Pauta",
            "meta_description": "Descricao",
            "description_html": "",
            "raw": {"queries": ["camisa masculina"]},
        }
    })
    monkeypatch.setattr(dashboard, "get_supabase", lambda: fake)
    monkeypatch.setattr(
        "modules.blog_content.generate",
        lambda _idea: {
            "html": "<article><h1>Novo</h1></article>",
            "error": None,
            "rate_limited": False,
            "provider": "gemini",
        },
    )

    response = dashboard.app.test_client().post("/blog-ideas/idea-2/generate")

    assert response.status_code == 200
    assert response.get_json()["html"] == "<article><h1>Novo</h1></article>"
    update = fake.store["updates"][0]["payload"]
    assert update["status"] == "approved"
    assert update["description_html"] == "<article><h1>Novo</h1></article>"
    assert update["raw"]["blog_content_provider"] == "gemini"
    assert update["raw"]["blog_content_length"] == len("<article><h1>Novo</h1></article>")


def test_blog_ideas_page_opens_saved_content_when_html_exists(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    fake = _FakeSupabase({
        "idea-3": {
            "id": "idea-3",
            "status": "approved",
            "provider": "query_suggester",
            "meta_title": "Moda para o Inverno",
            "meta_description": "Conteudo salvo",
            "description_html": "<article><h1>Salvo</h1></article>",
            "raw": {"queries": ["moda inverno"], "sections": ["Introducao"]},
            "created_at": "2026-06-02T10:00:00+00:00",
        }
    })
    monkeypatch.setattr(dashboard, "get_supabase", lambda: fake)

    html = dashboard.app.test_client().get("/blog-ideas").get_data(as_text=True)

    assert "Ver conteúdo" in html
    assert "openBlogContent('idea-3','Moda para o Inverno',true)" in html
