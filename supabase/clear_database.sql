-- Clear all SEO Control Center data while keeping the schema.
-- Run this manually in the Supabase SQL Editor only when you want a clean database.
--
-- This deletes:
-- - sites
-- - urls
-- - crawl_runs
-- - page_snapshots
-- - gsc_queries
-- - gsc_pages
-- - issues
-- - recommendations
-- - content_changes
--
-- The table structure, indexes, grants and RLS settings are preserved.

begin;

truncate table
  public.content_changes,
  public.recommendations,
  public.issues,
  public.gsc_pages,
  public.gsc_queries,
  public.page_snapshots,
  public.crawl_runs,
  public.urls,
  public.sites
restart identity cascade;

commit;

-- Optional check after running:
-- select 'sites' as table_name, count(*) from public.sites
-- union all select 'urls', count(*) from public.urls
-- union all select 'crawl_runs', count(*) from public.crawl_runs
-- union all select 'page_snapshots', count(*) from public.page_snapshots
-- union all select 'gsc_queries', count(*) from public.gsc_queries
-- union all select 'gsc_pages', count(*) from public.gsc_pages
-- union all select 'issues', count(*) from public.issues
-- union all select 'recommendations', count(*) from public.recommendations
-- union all select 'content_changes', count(*) from public.content_changes;
