-- Check row counts for the SEO Control Center tables.
-- Useful before and after running clear_database.sql.

select 'sites' as table_name, count(*) as rows from public.sites
union all select 'urls', count(*) from public.urls
union all select 'crawl_runs', count(*) from public.crawl_runs
union all select 'page_snapshots', count(*) from public.page_snapshots
union all select 'gsc_queries', count(*) from public.gsc_queries
union all select 'gsc_pages', count(*) from public.gsc_pages
union all select 'issues', count(*) from public.issues
union all select 'recommendations', count(*) from public.recommendations
union all select 'content_changes', count(*) from public.content_changes
order by table_name;
