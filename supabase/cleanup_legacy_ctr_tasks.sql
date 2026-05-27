-- Remove old query-level CTR recommendations. The system now benchmarks CTR
-- only at page level; queries are used for content and intent opportunities.
--
-- Run manually in the Supabase SQL Editor after deploying the calibrated code,
-- then regenerate the backlog.

begin;

delete from public.recommendations
where status in ('open', 'todo', 'doing')
  and source = 'gsc'
  and action = 'Reescrever title e meta description para aumentar CTR';

commit;

-- Suggested regeneration:
-- python run.py --module backlog --gsc ./gsc_exports --save-db --no-report
