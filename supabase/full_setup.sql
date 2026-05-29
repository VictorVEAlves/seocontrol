-- SEO Control - Supabase full setup
-- Use this on a new/empty Supabase project.
--
-- How to run:
-- 1. Open Supabase Dashboard -> SQL Editor.
-- 2. Paste this entire file.
-- 3. Run once.
--
-- Required server env after this:
-- SUPABASE_URL
-- SUPABASE_KEY
-- SUPABASE_SERVICE_ROLE_KEY
--
-- The app uses Supabase Auth for login/signup and the service role key on the
-- server for writes. Never expose SUPABASE_SERVICE_ROLE_KEY in the browser.

create extension if not exists pgcrypto;

-- -----------------------------------------------------------------------------
-- User-owned site/audit data
-- -----------------------------------------------------------------------------

create table if not exists public.sites (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid references auth.users(id) on delete cascade,
  name text not null,
  base_url text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_user_id, base_url)
);

create table if not exists public.urls (
  id uuid primary key default gen_random_uuid(),
  site_id uuid not null references public.sites(id) on delete cascade,
  url text not null,
  path text not null,
  url_type text not null default 'unknown',
  is_priority boolean not null default false,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  unique (site_id, url)
);

create table if not exists public.crawl_runs (
  id uuid primary key default gen_random_uuid(),
  site_id uuid not null references public.sites(id) on delete cascade,
  run_type text not null,
  scope jsonb not null default '[]'::jsonb,
  status text not null default 'completed',
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  summary jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.page_snapshots (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.crawl_runs(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  url_id uuid references public.urls(id) on delete set null,
  url text not null,
  final_url text,
  status_code integer,
  redirected boolean not null default false,
  title text,
  title_length integer,
  meta_description text,
  description_length integer,
  h1_count integer,
  h1_texts jsonb not null default '[]'::jsonb,
  h2_count integer,
  word_count integer,
  canonical text,
  schemas jsonb not null default '[]'::jsonb,
  images_total integer,
  images_no_alt integer,
  score integer,
  grade text,
  issues jsonb not null default '[]'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.gsc_queries (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.crawl_runs(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  query text not null,
  clicks integer not null default 0,
  impressions integer not null default 0,
  ctr numeric,
  position numeric,
  opportunity_type text not null default 'top_query',
  potential_clicks integer,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.gsc_pages (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.crawl_runs(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  url_id uuid references public.urls(id) on delete set null,
  page text not null,
  clicks integer not null default 0,
  impressions integer not null default 0,
  ctr numeric,
  position numeric,
  opportunity_type text not null default 'top_page',
  potential_clicks integer,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.issues (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.crawl_runs(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  url_id uuid references public.urls(id) on delete set null,
  source text not null,
  severity text not null default 'medium',
  issue_type text not null,
  title text not null,
  description text,
  target text,
  status text not null default 'open',
  evidence jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  resolved_at timestamptz
);

create table if not exists public.recommendations (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.crawl_runs(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  url_id uuid references public.urls(id) on delete set null,
  source text not null,
  action text not null,
  target text not null,
  reason text,
  impact numeric,
  confidence numeric,
  effort numeric,
  priority numeric,
  owner text,
  status text not null default 'open',
  evidence jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);

create table if not exists public.content_changes (
  id uuid primary key default gen_random_uuid(),
  site_id uuid not null references public.sites(id) on delete cascade,
  url_id uuid references public.urls(id) on delete set null,
  url text not null,
  provider text,
  status text not null default 'pending',
  meta_title text,
  meta_description text,
  meta_keywords text,
  h1 text,
  description_html text,
  raw jsonb not null default '{}'::jsonb,
  generated_at timestamptz,
  published_at timestamptz,
  created_at timestamptz not null default now()
);

-- -----------------------------------------------------------------------------
-- User/site settings
-- -----------------------------------------------------------------------------

create table if not exists public.user_site_settings (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  site_id uuid not null references public.sites(id) on delete cascade,
  site_url text not null,
  site_name text,
  settings jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, site_id)
);

-- -----------------------------------------------------------------------------
-- Indexes
-- -----------------------------------------------------------------------------

create index if not exists idx_sites_owner_user on public.sites(owner_user_id);
create index if not exists idx_sites_owner_base_url on public.sites(owner_user_id, base_url);
create index if not exists idx_urls_site_path on public.urls(site_id, path);
create index if not exists idx_crawl_runs_site_created on public.crawl_runs(site_id, created_at desc);
create index if not exists idx_page_snapshots_run on public.page_snapshots(run_id);
create index if not exists idx_page_snapshots_site_created on public.page_snapshots(site_id, created_at desc);
create index if not exists idx_gsc_queries_run on public.gsc_queries(run_id);
create index if not exists idx_gsc_queries_site_created on public.gsc_queries(site_id, created_at desc);
create index if not exists idx_gsc_pages_run on public.gsc_pages(run_id);
create index if not exists idx_gsc_pages_site_created on public.gsc_pages(site_id, created_at desc);
create index if not exists idx_issues_status_priority on public.issues(status, severity);
create index if not exists idx_issues_site_status on public.issues(site_id, status);
create index if not exists idx_recommendations_priority on public.recommendations(status, priority desc);
create index if not exists idx_recommendations_site_status on public.recommendations(site_id, status);
create index if not exists idx_content_changes_site_created on public.content_changes(site_id, created_at desc);
create index if not exists idx_user_site_settings_user_updated
  on public.user_site_settings(user_id, updated_at desc);
create index if not exists idx_user_site_settings_site
  on public.user_site_settings(site_id);

-- -----------------------------------------------------------------------------
-- updated_at helper
-- -----------------------------------------------------------------------------

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_sites_updated_at on public.sites;
create trigger trg_sites_updated_at
before update on public.sites
for each row execute function public.set_updated_at();

drop trigger if exists trg_user_site_settings_updated_at on public.user_site_settings;
create trigger trg_user_site_settings_updated_at
before update on public.user_site_settings
for each row execute function public.set_updated_at();

-- -----------------------------------------------------------------------------
-- RLS / permissions
-- -----------------------------------------------------------------------------
-- Current app architecture:
-- - Flask authenticates users with Supabase Auth.
-- - Flask server uses SUPABASE_SERVICE_ROLE_KEY for database writes/reads.
-- - Routes filter records by active site_id.
--
-- For that reason, operational tables keep RLS disabled for server-side ingestion.
-- user_site_settings has RLS enabled as a safety layer for any authenticated
-- client-side access.

alter table public.sites disable row level security;
alter table public.urls disable row level security;
alter table public.crawl_runs disable row level security;
alter table public.page_snapshots disable row level security;
alter table public.gsc_queries disable row level security;
alter table public.gsc_pages disable row level security;
alter table public.issues disable row level security;
alter table public.recommendations disable row level security;
alter table public.content_changes disable row level security;

alter table public.user_site_settings enable row level security;

drop policy if exists "Users can read their site settings" on public.user_site_settings;
create policy "Users can read their site settings"
  on public.user_site_settings for select
  using (auth.uid() = user_id);

drop policy if exists "Users can insert their site settings" on public.user_site_settings;
create policy "Users can insert their site settings"
  on public.user_site_settings for insert
  with check (auth.uid() = user_id);

drop policy if exists "Users can update their site settings" on public.user_site_settings;
create policy "Users can update their site settings"
  on public.user_site_settings for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "Users can delete their site settings" on public.user_site_settings;
create policy "Users can delete their site settings"
  on public.user_site_settings for delete
  using (auth.uid() = user_id);

grant usage on schema public to anon, authenticated, service_role;

-- Operational tables are server-only. The publishable/anon key must not be able
-- to read them directly while RLS is disabled.
revoke all on all tables in schema public from anon, authenticated;
grant select, insert, update, delete on all tables in schema public to service_role;
grant select, insert, update, delete on public.user_site_settings to authenticated;

revoke all on all sequences in schema public from anon, authenticated;
grant usage, select on all sequences in schema public to service_role;

-- Make future operational tables server-only by default.
alter default privileges in schema public
revoke select, insert, update, delete on tables from anon, authenticated;

alter default privileges in schema public
grant select, insert, update, delete on tables to service_role;

alter default privileges in schema public
revoke usage, select on sequences from anon, authenticated;

alter default privileges in schema public
grant usage, select on sequences to service_role;
