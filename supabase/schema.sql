-- SEO Audit foundation schema for Supabase.
-- Run this in Supabase SQL Editor before using `python run.py ... --save-db`.

create extension if not exists pgcrypto;

create table if not exists public.sites (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  base_url text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
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

create index if not exists idx_urls_site_path on public.urls(site_id, path);
create index if not exists idx_crawl_runs_site_created on public.crawl_runs(site_id, created_at desc);
create index if not exists idx_page_snapshots_run on public.page_snapshots(run_id);
create index if not exists idx_gsc_queries_run on public.gsc_queries(run_id);
create index if not exists idx_gsc_pages_run on public.gsc_pages(run_id);
create index if not exists idx_issues_status_priority on public.issues(status, severity);
create index if not exists idx_recommendations_priority on public.recommendations(status, priority desc);

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

-- RLS stays disabled for local server-side ingestion with the publishable key.
-- For production, prefer SUPABASE_SERVICE_ROLE_KEY on the server and add stricter RLS policies.
