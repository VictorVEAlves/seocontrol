from modules.content_generator import list_pending
from modules.supabase_store import save_content_changes


def sync_pending_to_supabase() -> int:
    return save_content_changes(list_pending())


__all__ = ["sync_pending_to_supabase"]
