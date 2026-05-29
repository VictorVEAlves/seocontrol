-- Run this on a database that was created with the older global-sites schema.
-- It removes the global UNIQUE(base_url) rule and makes the same domain safe
-- for different users by scoping sites to owner_user_id.

alter table public.sites
  add column if not exists owner_user_id uuid references auth.users(id) on delete cascade;

-- Best-effort backfill: when an existing site is linked to exactly one user,
-- make that user the owner before adding the new unique constraint.
with single_owner as (
  select
    site_id,
    min(user_id::text)::uuid as user_id,
    count(distinct user_id) as owners
  from public.user_site_settings
  group by site_id
)
update public.sites s
set owner_user_id = so.user_id
from single_owner so
where s.id = so.site_id
  and so.owners = 1
  and s.owner_user_id is null;

alter table public.sites drop constraint if exists sites_base_url_key;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'sites_owner_user_base_url_key'
      and conrelid = 'public.sites'::regclass
  ) then
    alter table public.sites
      add constraint sites_owner_user_base_url_key unique (owner_user_id, base_url);
  end if;
end $$;

create index if not exists idx_sites_owner_user
  on public.sites(owner_user_id);

create index if not exists idx_sites_owner_base_url
  on public.sites(owner_user_id, base_url);

-- Harden direct API access. The Flask server must use SUPABASE_SERVICE_ROLE_KEY
-- for operational reads/writes; anon/authenticated clients should not read
-- server tables directly while RLS is disabled there.
revoke all on all tables in schema public from anon, authenticated;
grant select, insert, update, delete on all tables in schema public to service_role;
grant select, insert, update, delete on public.user_site_settings to authenticated;

revoke all on all sequences in schema public from anon, authenticated;
grant usage, select on all sequences in schema public to service_role;
