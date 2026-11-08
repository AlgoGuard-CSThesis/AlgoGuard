-- 6B.2: health reported by signed-in installations, read by administrators.
-- Last seen is the server's receipt time, never the installation's clock. A report needs a valid
-- user with an approved membership of an approved node; nothing here can start or stop a capture.
create table public.node_health (
    node_id          uuid primary key references public.node (node_id) on delete cascade,
    reported_by      bigint not null references public.profile (profile_id),
    received_at      timestamptz not null,
    app_version      text not null check (length(app_version) between 1 and 40),
    deployment_id    bigint check (deployment_id > 0),
    capture_status   text not null
                     check (capture_status in ('idle','starting','running','paused','stopping','error')),
    pending_events   integer not null check (pending_events >= 0),
    dropped_events   integer not null check (dropped_events >= 0),
    rejected_events  integer not null check (rejected_events >= 0)
);
comment on table public.node_health is
    'Latest health report per node. received_at is server time; stale receipt means stale status.';

alter table public.node_health enable row level security;
grant select on public.node_health to authenticated;
create policy node_read on public.node_health for select to authenticated
    using (private.can_read_node(node_id));
grant select, insert, update on public.node_health to algoguard_identity_owner;
create policy identity_maintenance on public.node_health to algoguard_identity_owner
    using (true) with check (true);

create function public.report_node_health(p_node_id uuid, p_health jsonb)
returns timestamptz language plpgsql security definer set search_path = '' as $$
declare
    actor bigint := private.current_profile_id();
    received timestamptz := now();
begin
    if actor is null or not private.can_write_node(p_node_id) then
        raise exception 'access denied' using errcode = 'PT403';
    end if;
    if p_health is null or jsonb_typeof(p_health) <> 'object'
       or p_health - array['app_version','deployment_id','capture_status','pending_events',
                           'dropped_events','rejected_events'] <> '{}'::jsonb then
        raise exception 'invalid health report' using errcode = 'PT400';
    end if;
    insert into public.node_health(node_id, reported_by, received_at, app_version, deployment_id,
                                   capture_status, pending_events, dropped_events, rejected_events)
    values (p_node_id, actor, received, p_health->>'app_version',
            (p_health->>'deployment_id')::bigint, p_health->>'capture_status',
            (p_health->>'pending_events')::integer, (p_health->>'dropped_events')::integer,
            (p_health->>'rejected_events')::integer)
    on conflict (node_id) do update set reported_by = excluded.reported_by,
        received_at = excluded.received_at, app_version = excluded.app_version,
        deployment_id = excluded.deployment_id, capture_status = excluded.capture_status,
        pending_events = excluded.pending_events, dropped_events = excluded.dropped_events,
        rejected_events = excluded.rejected_events;
    update public.node set last_seen_at = received where node_id = p_node_id;
    return received;
exception
    when invalid_text_representation or numeric_value_out_of_range or check_violation
        or not_null_violation then
        raise exception 'invalid health report' using errcode = 'PT400';
end
$$;
grant create on schema public to algoguard_identity_owner;
alter function public.report_node_health(uuid, jsonb) owner to algoguard_identity_owner;
revoke create on schema public from algoguard_identity_owner;
revoke all on function public.report_node_health(uuid, jsonb) from public, anon;
grant execute on function public.report_node_health(uuid, jsonb) to authenticated;
