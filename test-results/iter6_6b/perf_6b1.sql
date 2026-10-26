\set ON_ERROR_STOP on
\pset pager off
begin;
-- Fixture identities, nodes and a model (all rolled back at the end).
insert into auth.users(id) values ('00000000-0000-4000-8000-00000000a001'), ('00000000-0000-4000-8000-00000000a002');
insert into public.profile(auth_user_id, username, is_active) values
  ('00000000-0000-4000-8000-00000000a001', 'perf_admin_6b1', true),
  ('00000000-0000-4000-8000-00000000a002', 'perf_analyst_6b1', true);
select profile_id as admin_id from public.profile where username = 'perf_admin_6b1' \gset
select profile_id as analyst_id from public.profile where username = 'perf_analyst_6b1' \gset
insert into public.user_role(profile_id, role) values (:admin_id, 'administrator'), (:analyst_id, 'analyst');
insert into public.node(node_id, display_name, status, approved_at, approved_by) values
  ('00000000-0000-4000-8000-0000000000a1', 'perf A', 'approved', now(), :admin_id),
  ('00000000-0000-4000-8000-0000000000b1', 'perf B', 'approved', now(), :admin_id),
  ('00000000-0000-4000-8000-0000000000c1', 'perf C', 'approved', now(), :admin_id);
insert into public.node_membership(node_id, profile_id, status, decided_at, decided_by) values
  ('00000000-0000-4000-8000-0000000000a1', :admin_id, 'approved', now(), :admin_id),
  ('00000000-0000-4000-8000-0000000000a1', :analyst_id, 'approved', now(), :admin_id);
insert into public.training_run(filename, upload_timestamp) values ('perf.csv', '2026-10-01 00:00:00');
select max(run_id) as run_id from public.training_run \gset
insert into public.detection_model(run_id, model_name) values (:run_id, 'Stacking Ensemble');
select max(model_id) as model_id from public.detection_model \gset

-- 130,000 alerts with their traffic and predictions over 30 days; A 60%, B 30%, C 10%.
create temp table perf_rows as
select i,
       case when i % 10 < 6 then '00000000-0000-4000-8000-0000000000a1'::uuid
            when i % 10 < 9 then '00000000-0000-4000-8000-0000000000b1'::uuid
            else '00000000-0000-4000-8000-0000000000c1'::uuid end as node_id,
       now() - (i * interval '20 seconds') as at
from generate_series(1, 130000) i;
insert into public.network_traffic(node_id, owner_profile_id, "timestamp", source_ip, recorded_at)
select node_id, :admin_id, to_char(at at time zone 'UTC', 'YYYY-MM-DD HH24:MI:SS'), '10.0.0.' || (i % 250), at
from perf_rows order by i;
insert into public.prediction(node_id, owner_profile_id, traffic_id, model_id, predicted_label, confidence_score, prediction_timestamp, created_at)
select t.node_id, :admin_id, t.traffic_id, :model_id, 'Attack', 97.0, t."timestamp", t.recorded_at
from public.network_traffic t where t.owner_profile_id = :admin_id;
insert into public.alert(node_id, owner_profile_id, prediction_id, severity_level, alert_status, detected_at, description, created_at)
select p.node_id, :admin_id, p.prediction_id, 'High', case when p.prediction_id % 3 = 0 then 'Open' else 'Resolved' end,
       p.prediction_timestamp, 'perf', p.created_at
from public.prediction p where p.owner_profile_id = :admin_id;
insert into public.system_log(node_id, profile_id, module, action, status, message, "timestamp", created_at)
select node_id, :admin_id, 'Live Monitor', 'flow_accepted', case when i % 7 = 0 then 'Failed' else 'Success' end,
       'perf log ' || i, to_char(at at time zone 'UTC', 'YYYY-MM-DD HH24:MI:SS'), at
from perf_rows;
insert into public.report(node_id, owner_profile_id, report_type, generated_at, created_at)
select node_id, :admin_id, 'Operational evidence', to_char(at at time zone 'UTC', 'YYYY-MM-DD HH24:MI:SS'), at
from perf_rows where i % 65 = 0;
analyze public.network_traffic, public.prediction, public.alert, public.system_log, public.report;
select (select count(*) from public.alert) as alerts, (select count(*) from public.system_log) as logs,
       (select count(*) from public.report) as reports;

\echo === as administrator
set local role authenticated;
select set_config('request.jwt.claims', '{"sub": "00000000-0000-4000-8000-00000000a001", "role": "authenticated"}', true) \g /dev/null
\timing on
\echo Q1 alerts, this computer, first page
select count(*) from (select * from public.alert_detail where node_id = '00000000-0000-4000-8000-0000000000a1' order by alert_id desc limit 51) q;
\echo Q2 alerts, all computers, first page
select count(*) from (select * from public.alert_detail order by alert_id desc limit 51) q;
\echo Q3 alerts, this computer, Open, last 7 days
select count(*) from (select * from public.alert_detail where node_id = '00000000-0000-4000-8000-0000000000a1' and alert_status = 'Open' and detected_at >= to_char(now() at time zone 'UTC' - interval '7 days', 'YYYY-MM-DD HH24:MI:SS') order by alert_id desc limit 51) q;
\echo Q4 alerts, all computers, page 200
select count(*) from (select * from public.alert_detail order by alert_id desc limit 51 offset 10000) q;
\echo Q5 logs, this computer, first page
select count(*) from (select * from public.system_log where node_id = '00000000-0000-4000-8000-0000000000a1' order by log_id desc limit 26) q;
\echo Q6 logs, all computers, Failed, date range
select count(*) from (select * from public.system_log where status = 'Failed' and "timestamp" >= to_char(now() at time zone 'UTC' - interval '3 days', 'YYYY-MM-DD HH24:MI:SS') order by log_id desc limit 26) q;
\echo Q7 reports, all computers
select count(*) from (select * from public.report order by report_id desc limit 51) q;
\timing off
reset role;

\echo === as analyst (member of perf A only)
set local role authenticated;
select set_config('request.jwt.claims', '{"sub": "00000000-0000-4000-8000-00000000a002", "role": "authenticated"}', true) \g /dev/null
\timing on
\echo Q8 alerts, all visible (only perf A), first page
select count(*) from (select * from public.alert_detail order by alert_id desc limit 51) q;
\echo Q9 alerts asking for perf B by hand
select count(*) from (select * from public.alert_detail where node_id = '00000000-0000-4000-8000-0000000000b1' order by alert_id desc limit 51) q;
\echo Q10 logs, all visible, first page
select count(*) from (select * from public.system_log order by log_id desc limit 26) q;
\timing off
reset role;
rollback;
