from datetime import datetime, timedelta, timezone

import app as dashboard
from modules import supabase_store
import pytest


@pytest.fixture(autouse=True)
def _local_auth_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")


class _FakeQuery:
    def __init__(self, data):
        self.data = data

    def select(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def execute(self):
        return self


class _FakeSupabase:
    def __init__(self, data):
        self.data = data

    def table(self, name):
        assert name == "recommendations"
        return _FakeQuery(self.data)


def test_kanban_page_renders_reorderable_lanes(monkeypatch):
    monkeypatch.setattr(dashboard, "get_supabase", lambda: _FakeSupabase([
        {
            "id": "1",
            "priority": 10,
            "source": "gsc_api",
            "action": "Investigar queda",
            "target": "/categoria",
            "reason": "Queda recente.",
            "owner": "SEO",
            "status": "todo",
            "created_at": "2026-05-28T10:00:00+00:00",
            "completed_at": None,
            "site_id": "site-1",
            "evidence": {"_kanban": {"position": 1000}},
        }
    ]))

    with dashboard.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["active_site_id"] = "site-1"
        html = client.get("/kanban").get_data(as_text=True)

    assert "lane-cards" in html
    assert "Arraste entre colunas ou reordene" in html
    assert "Investigar queda" in html


def test_kanban_sort_uses_manual_position_before_priority():
    first = {
        "priority": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "evidence": {"_kanban": {"position": 1000}},
    }
    second = {
        "priority": 99,
        "created_at": "2026-01-01T00:00:00+00:00",
        "evidence": {"_kanban": {"position": 2000}},
    }
    unsorted = [second, first]

    assert sorted(unsorted, key=dashboard._kanban_sort_key) == [first, second]


def test_recommendation_signature_normalizes_url_and_case(monkeypatch):
    monkeypatch.setattr(supabase_store, "get_site_url", lambda: "https://example.com")

    one = supabase_store._recommendation_signature(
        "GSC_API", "Investigar queda", "https://example.com/categoria/"
    )
    two = supabase_store._recommendation_signature(
        "gsc_api", "  Investigar   queda  ", "/categoria"
    )

    assert one == two


def test_recent_done_recommendation_blocks_duplicate_but_old_done_does_not():
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)

    assert supabase_store._blocks_new_duplicate({"status": "todo"}, now=now)
    assert supabase_store._blocks_new_duplicate({
        "status": "done",
        "evidence": {"_kanban": {"moved_at": (now - timedelta(days=7)).isoformat()}},
    }, now=now)
    assert not supabase_store._blocks_new_duplicate({
        "status": "done",
        "evidence": {"_kanban": {"moved_at": (now - timedelta(days=45)).isoformat()}},
    }, now=now)


class _SiteIsolationQuery:
    def __init__(self, db, table, action=None, payload=None):
        self.db = db
        self.table = table
        self.action = action
        self.payload = payload
        self.filters = {}
        self.data = []

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

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        if self.table == "sites" and self.action == "select":
            if self.filters.get("id") == "legacy-site":
                self.data = [{"id": "legacy-site", "owner_user_id": None}]
            elif (
                self.filters.get("owner_user_id") == "user-1"
                and self.filters.get("base_url") == "https://example.com"
            ):
                self.data = [{"id": "owned-site"}]
        elif self.table == "sites" and self.action == "update":
            raise RuntimeError("duplicate key value violates unique constraint")
        elif self.table == "user_site_settings" and self.action == "update":
            self.db.repointed = dict(self.filters, **self.payload)
            self.data = [{"site_id": self.payload["site_id"]}]
        return self


class _SiteIsolationSupabase:
    def __init__(self):
        self.repointed = {}

    def table(self, name):
        return _SiteIsolationQuery(self, name)


def test_upsert_site_uses_owned_site_when_legacy_site_conflicts(monkeypatch):
    db = _SiteIsolationSupabase()
    monkeypatch.setattr(supabase_store, "get_site_id", lambda: "legacy-site")
    monkeypatch.setattr(supabase_store, "get_site_owner_user_id", lambda: "user-1")

    site_id = supabase_store._upsert_site(db, "Example", "https://example.com")

    assert site_id == "owned-site"
    assert db.repointed["user_id"] == "user-1"
    assert db.repointed["site_id"] == "owned-site"
