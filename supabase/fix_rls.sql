-- Run this once in Supabase SQL Editor if saving fails with:
-- new row violates row-level security policy

alter table public.sites disable row level security;
alter table public.urls disable row level security;
alter table public.crawl_runs disable row level security;
alter table public.page_snapshots disable row level security;
alter table public.gsc_queries disable row level security;
alter table public.gsc_pages disable row level security;
alter table public.issues disable row level security;
alter table public.recommendations disable row level security;
alter table public.content_changes disable row level security;

grant usage on schema public to anon, authenticated, service_role;

-- Keep operational tables server-only. The Flask backend must use
-- SUPABASE_SERVICE_ROLE_KEY; do not grant anon/authenticated access while RLS is
-- disabled on these tables.
revoke all on all tables in schema public from anon, authenticated;
grant select, insert, update, delete on all tables in schema public to service_role;
grant select, insert, update, delete on public.user_site_settings to authenticated;

revoke all on all sequences in schema public from anon, authenticated;
grant usage, select on all sequences in schema public to service_role;
