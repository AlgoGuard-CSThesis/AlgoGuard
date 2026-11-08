-- 6B.3: audited retention with a dry-run preview, bounded batches and relationship-safe deletes.
--
-- Timestamp roles: server receipt times decide what is old (network_traffic.recorded_at,
-- prediction/alert/report/system_log.created_at, ingest_event.received_at,
-- capture_session.closed_at). Installation clocks (event_time, detected_at, "timestamp") are shown
-- to people but never decide retention, so a wrong clock can neither delete evidence early nor
-- keep it forever; implausible ones are reported for review. Initial targets: 30 days for
-- operational records, 90 days for reports. A kept report keeps every alert it cites with that
-- alert's whole flow. Active capture sessions, model manifests, deployments, training records and
-- model files are never touched. Only administrators preview or run a cleanup; each run is audited.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'algoguard_retention_owner') then
        create role algoguard_retention_owner nologin noinherit;
    end if;
end
$$;
grant algoguard_retention_owner to postgres;
grant usage on schema public, private to algoguard_retention_owner;
grant select, delete on public.capture_session, public.network_traffic, public.prediction,
    public.alert, public.report, public.report_alert, public.system_log, public.ingest_event
    to algoguard_retention_owner;
grant insert on public.system_log to algoguard_retention_owner;
grant usage on sequence public.system_log_log_id_seq to algoguard_retention_owner;
grant select on public.model_manifest, public.node_health to algoguard_retention_owner;
grant execute on function private.is_administrator(), private.current_profile_id()
    to algoguard_retention_owner;
create policy retention_maintenance on public.capture_session to algoguard_retention_owner
    using (true) with check (true);
create policy retention_maintenance on public.network_traffic to algoguard_retention_owner
    using (true) with check (true);
create policy retention_maintenance on public.prediction to algoguard_retention_owner
    using (true) with check (true);
create policy retention_maintenance on public.alert to algoguard_retention_owner
    using (true) with check (true);
create policy retention_maintenance on public.report to algoguard_retention_owner
    using (true) with check (true);
create policy retention_maintenance on public.report_alert to algoguard_retention_owner
    using (true) with check (true);
create policy retention_maintenance on public.system_log to algoguard_retention_owner
    using (true) with check (true);
create policy retention_maintenance on public.ingest_event to algoguard_retention_owner
    using (true) with check (true);
create policy retention_maintenance on public.model_manifest to algoguard_retention_owner
    using (true);
create policy retention_maintenance on public.node_health to algoguard_retention_owner
    using (true);

-- One row per applied batch; a repeated request returns its first result.
create table private.retention_run (
    request_id        uuid primary key,
    actor_profile_id  bigint not null references public.profile (profile_id),
    requested_at      timestamptz not null default now(),
    operational_days  integer not null,
    report_days       integer not null,
    batch_size        integer not null,
    result            jsonb not null
);
grant select, insert on private.retention_run to algoguard_retention_owner;

-- Flows (one per prediction) older than the operational cutoff and cited by no kept report.
create function private.retention_expired_predictions(p_operational timestamptz,
                                                      p_reports timestamptz)
returns setof bigint language sql stable security definer set search_path = '' as $$
    select p.prediction_id from public.prediction p
    where p.created_at < p_operational
      and not exists (
          select 1 from public.alert a
          join public.report_alert ra on ra.alert_id = a.alert_id
          join public.report r on r.report_id = ra.report_id
          where a.prediction_id = p.prediction_id and r.created_at >= p_reports)
$$;

-- Standalone logs older than the cutoff. A flow's own logs (its prediction's logs and the audit
-- row its event anchor refers to) go only together with the flow. Cleanup's own audit stays as
-- long as reports do.
create function private.retention_expired_logs(p_operational timestamptz, p_reports timestamptz)
returns setof bigint language sql stable security definer set search_path = '' as $$
    select l.log_id from public.system_log l
    where l.created_at < p_operational
      and l.prediction_id is null
      and not (l.module = 'Retention' and l.created_at >= p_reports)
      and not exists (select 1 from public.ingest_event e where e.audit_log_id = l.log_id)
$$;

create function private.retention_policy(p_operational_days integer, p_report_days integer)
returns timestamptz[] language plpgsql stable security definer set search_path = '' as $$
begin
    if not private.is_administrator() then
        raise exception 'access denied' using errcode = 'PT403';
    end if;
    if p_operational_days is null or p_operational_days not between 30 and 3650
       or p_report_days is null or p_report_days not between 90 and 3650 then
        raise exception 'invalid retention policy' using errcode = 'PT400';
    end if;
    return array[now() - make_interval(days => p_operational_days),
                 now() - make_interval(days => p_report_days)];
end
$$;

create function public.retention_preview(p_operational_days integer default 30,
                                         p_report_days integer default 90)
returns jsonb language plpgsql stable security definer set search_path = '' as $$
declare
    cutoffs timestamptz[] := private.retention_policy(p_operational_days, p_report_days);
    ops timestamptz := cutoffs[1];
    reps timestamptz := cutoffs[2];
begin
    return jsonb_build_object(
        'operational_cutoff', ops, 'report_cutoff', reps,
        'expiring', jsonb_build_object(
            'reports', (select count(*) from public.report where created_at < reps),
            'flows', (select count(*) from private.retention_expired_predictions(ops, reps)),
            'alerts', (select count(*) from public.alert where prediction_id in
                       (select private.retention_expired_predictions(ops, reps))),
            'logs', (select count(*) from private.retention_expired_logs(ops, reps)),
            'closed_sessions', (select count(*) from public.capture_session s
                where s.closed_at < ops and not exists (
                    select 1 from public.network_traffic t
                    where t.capture_id = s.capture_id and t.traffic_id not in (
                        select p.traffic_id from public.prediction p where p.prediction_id in
                            (select private.retention_expired_predictions(ops, reps)))))),
        'kept', jsonb_build_object(
            'alerts_held_by_reports', (select count(distinct a.alert_id) from public.alert a
                join public.prediction p on p.prediction_id = a.prediction_id
                join public.report_alert ra on ra.alert_id = a.alert_id
                join public.report r on r.report_id = ra.report_id
                where p.created_at < ops and r.created_at >= reps),
            'active_sessions', (select count(*) from public.capture_session where closed_at is null)),
        'suspect_client_times', coalesce((select jsonb_object_agg(node_id, n) from (
            select node_id, count(*) as n from public.ingest_event
            where event_time > received_at + interval '5 minutes'
               or event_time < received_at - interval '8 days'
            group by node_id) q), '{}'::jsonb),
        'capacity', jsonb_build_object(
            'database_bytes', pg_database_size(current_database()),
            'tables', (select jsonb_object_agg(c.relname, pg_total_relation_size(c.oid))
                from pg_class c join pg_namespace n on n.oid = c.relnamespace
                where n.nspname = 'public' and c.relname in ('capture_session', 'network_traffic',
                    'prediction', 'alert', 'report', 'report_alert', 'system_log',
                    'ingest_event')),
            'rows', jsonb_build_object(
                'network_traffic', (select count(*) from public.network_traffic),
                'prediction', (select count(*) from public.prediction),
                'alert', (select count(*) from public.alert),
                'report', (select count(*) from public.report),
                'system_log', (select count(*) from public.system_log),
                'ingest_event', (select count(*) from public.ingest_event)),
            'oldest', jsonb_build_object(
                'prediction', (select min(created_at) from public.prediction),
                'system_log', (select min(created_at) from public.system_log),
                'report', (select min(created_at) from public.report))),
        'models_kept', coalesce((select jsonb_object_agg(status, n) from (
            select status, count(*) as n from public.model_manifest group by status) m),
            '{}'::jsonb));
end
$$;

create function public.retention_apply(p_request_id uuid, p_batch_size integer default 500,
                                       p_operational_days integer default 30,
                                       p_report_days integer default 90)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    cutoffs timestamptz[] := private.retention_policy(p_operational_days, p_report_days);
    ops timestamptz := cutoffs[1];
    reps timestamptz := cutoffs[2];
    actor bigint := private.current_profile_id();
    previous private.retention_run;
    reports bigint[];
    flows bigint[];
    traffic bigint[];
    audits bigint[];
    counts jsonb;
    removed_links integer; removed_reports integer; removed_events integer;
    removed_logs integer; removed_alerts integer; removed_flows integer; removed_traffic integer;
    removed_other_logs integer; removed_sessions integer;
    answer jsonb;
begin
    if p_request_id is null or p_batch_size is null or p_batch_size not between 1 and 5000 then
        raise exception 'invalid retention request' using errcode = 'PT400';
    end if;
    -- One cleanup at a time, so batches never interleave.
    perform pg_advisory_xact_lock(hashtextextended('algoguard-retention', 0));
    select * into previous from private.retention_run where request_id = p_request_id;
    if found then
        if previous.actor_profile_id <> actor or previous.operational_days <> p_operational_days
           or previous.report_days <> p_report_days or previous.batch_size <> p_batch_size then
            raise exception 'retention request conflict' using errcode = 'PT409';
        end if;
        return previous.result || jsonb_build_object('replayed', true);
    end if;

    -- 1. Reports past their period, links first.
    select coalesce(array_agg(report_id), '{}') into reports from (
        select report_id from public.report where created_at < reps
        order by report_id limit p_batch_size) q;
    delete from public.report_alert where report_id = any(reports);
    get diagnostics removed_links = row_count;
    delete from public.report where report_id = any(reports);
    get diagnostics removed_reports = row_count;

    -- 2. Whole flows no kept report cites and no remaining link holds: event anchor, logs,
    --    alert, prediction, then traffic.
    select coalesce(array_agg(prediction_id), '{}') into flows from (
        select x.prediction_id from private.retention_expired_predictions(ops, reps) x(prediction_id)
        where not exists (select 1 from public.alert a join public.report_alert ra
                          on ra.alert_id = a.alert_id where a.prediction_id = x.prediction_id)
        order by 1 limit p_batch_size) q;
    with gone as (delete from public.ingest_event where prediction_id = any(flows)
                  returning audit_log_id)
    select coalesce(array_agg(audit_log_id), '{}') into audits from gone;
    removed_events := cardinality(audits);
    delete from public.system_log where prediction_id = any(flows) or log_id = any(audits);
    get diagnostics removed_logs = row_count;
    delete from public.alert where prediction_id = any(flows);
    get diagnostics removed_alerts = row_count;
    with gone as (delete from public.prediction where prediction_id = any(flows)
                  returning traffic_id)
    select coalesce(array_agg(traffic_id), '{}') into traffic from gone;
    removed_flows := cardinality(traffic);
    delete from public.network_traffic t where t.traffic_id = any(traffic)
        and not exists (select 1 from public.prediction p where p.traffic_id = t.traffic_id)
        and not exists (select 1 from public.ingest_event e where e.traffic_id = t.traffic_id);
    get diagnostics removed_traffic = row_count;

    -- 3. Other old logs, then closed sessions nothing refers to any more.
    delete from public.system_log where log_id in (
        select l from private.retention_expired_logs(ops, reps) l order by 1 limit p_batch_size);
    get diagnostics removed_other_logs = row_count;
    delete from public.capture_session s where s.capture_id in (
        select c.capture_id from public.capture_session c
        where c.closed_at < ops
          and not exists (select 1 from public.network_traffic t where t.capture_id = c.capture_id)
          and not exists (select 1 from public.ingest_event e where e.capture_id = c.capture_id)
        order by c.capture_id limit p_batch_size);
    get diagnostics removed_sessions = row_count;

    counts := jsonb_build_object('reports', removed_reports, 'report_links', removed_links,
        'flows', removed_flows, 'alerts', removed_alerts, 'events', removed_events,
        'traffic', removed_traffic, 'logs', removed_logs + removed_other_logs,
        'closed_sessions', removed_sessions);
    answer := jsonb_build_object(
        'request_id', p_request_id, 'removed', counts,
        'operational_cutoff', ops, 'report_cutoff', reps,
        'more', exists (select 1 from public.report where created_at < reps)
             or exists (select 1 from private.retention_expired_predictions(ops, reps))
             or exists (select 1 from private.retention_expired_logs(ops, reps))
             or exists (select 1 from public.capture_session c where c.closed_at < ops
                 and not exists (select 1 from public.network_traffic t
                                 where t.capture_id = c.capture_id)));
    insert into public.system_log(node_id, profile_id, module, action, status, message, "timestamp")
    values (null, actor, 'Retention', 'retention_batch', 'Success',
            format('Removed %s flows (%s alerts), %s log entries, %s reports and %s closed '
                   'capture sessions; policy %s/%s days.', removed_flows, removed_alerts,
                   removed_logs + removed_other_logs, removed_reports, removed_sessions,
                   p_operational_days, p_report_days),
            to_char(now() at time zone 'UTC', 'YYYY-MM-DD HH24:MI:SS'));
    insert into private.retention_run(request_id, actor_profile_id, operational_days, report_days,
                                      batch_size, result)
    values (p_request_id, actor, p_operational_days, p_report_days, p_batch_size, answer);
    return answer;
end
$$;

grant create on schema public, private to algoguard_retention_owner;
alter function private.retention_expired_predictions(timestamptz, timestamptz)
    owner to algoguard_retention_owner;
alter function private.retention_expired_logs(timestamptz, timestamptz)
    owner to algoguard_retention_owner;
alter function private.retention_policy(integer, integer) owner to algoguard_retention_owner;
alter function public.retention_preview(integer, integer) owner to algoguard_retention_owner;
alter function public.retention_apply(uuid, integer, integer, integer)
    owner to algoguard_retention_owner;
revoke create on schema public, private from algoguard_retention_owner;
revoke all on function private.retention_expired_predictions(timestamptz, timestamptz),
    private.retention_expired_logs(timestamptz, timestamptz),
    private.retention_policy(integer, integer) from public, anon, authenticated;
revoke all on function public.retention_preview(integer, integer),
    public.retention_apply(uuid, integer, integer, integer) from public, anon;
grant execute on function public.retention_preview(integer, integer),
    public.retention_apply(uuid, integer, integer, integer) to authenticated;
