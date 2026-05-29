-- Multi-user foundation for SEO Control.
-- Run this after supabase/schema.sql.

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

create index if not exists idx_user_site_settings_user_updated
  on public.user_site_settings(user_id, updated_at desc);

create index if not exists idx_user_site_settings_site
  on public.user_site_settings(site_id);

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

grant select, insert, update, delete on public.user_site_settings to authenticated;
