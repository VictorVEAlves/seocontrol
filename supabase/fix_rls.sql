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

grant usage on schema public to anon, authenticated;
grant select, insert, update, delete on all tables in schema public to anon, authenticated;
grant usage, select on all sequences in schema public to anon, authenticated;
