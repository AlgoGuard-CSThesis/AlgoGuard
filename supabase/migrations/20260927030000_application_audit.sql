-- 5D.1: cloud System Logs parity without recursive or duplicated audit writes.

-- Filter choices through the caller's own RLS scope. Replaces the SQLite
-- get_log_filter_options() DISTINCT queries; hidden nodes contribute nothing
-- because the function runs as SECURITY INVOKER.
create function public.system_log_filter_options(p_node_id uuid)
returns jsonb language sql stable security invoker set search_path = '' as $$
    select jsonb_build_object(
        'modules', coalesce((select jsonb_agg(v order by v) from (
            select distinct l.module v from public.system_log l
            where l.node_id = p_node_id order by 1 limit 200) m), '[]'::jsonb),
        'statuses', coalesce((select jsonb_agg(v order by v) from (
            select distinct l.status v from public.system_log l
            where l.node_id = p_node_id order by 1 limit 200) s), '[]'::jsonb),
        'models', coalesce((select jsonb_agg(v order by v) from (
            select distinct l.model_name v from public.system_log l
            where l.node_id = p_node_id and l.model_name is not null
            order by 1 limit 200) d), '[]'::jsonb))
$$;
revoke all on function public.system_log_filter_options(uuid) from public, anon, service_role;
grant execute on function public.system_log_filter_options(uuid) to authenticated;

-- Application audit entries are queued locally and may be retried after a
-- lost acknowledgement. The client UUID makes the retry return the original
-- row instead of appending a duplicate; changed content under it conflicts.
alter table public.system_log add column event_uuid uuid unique;

create function public.append_system_log(p_node_id uuid, p_profile_id bigint,
    p_event_uuid uuid, p_values jsonb)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
    existing public.system_log;
    inserted bigint;
begin
    if p_profile_id is distinct from private.current_profile_id()
       or not private.can_write_node(p_node_id) then
        raise exception 'access denied' using errcode = 'PT403';
    end if;
    if p_event_uuid is null or p_values is null or jsonb_typeof(p_values) <> 'object'
       or p_values - array['module','action','status','message','model_name','timestamp']
          <> '{}'::jsonb
       or length(coalesce(p_values->>'module', '')) not between 1 and 80
       or length(coalesce(p_values->>'action', '')) not between 1 and 80
       or length(coalesce(p_values->>'status', '')) not between 1 and 40
       or length(coalesce(p_values->>'message', '')) > 2000
       or length(coalesce(p_values->>'model_name', '')) > 200
       or coalesce(p_values->>'timestamp', '') !~ '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$' then
        raise exception 'invalid log entry' using errcode = 'PT400';
    end if;
    perform pg_advisory_xact_lock(hashtextextended(p_event_uuid::text, 24680));
    select * into existing from public.system_log where event_uuid = p_event_uuid;
    if found then
        if (existing.node_id, existing.profile_id, existing.module, existing.action,
            existing.status, existing.message, existing.model_name, existing."timestamp")
           is distinct from (p_node_id, p_profile_id, p_values->>'module',
            p_values->>'action', p_values->>'status', p_values->>'message',
            p_values->>'model_name', p_values->>'timestamp') then
            raise exception 'log conflict' using errcode = 'PT409';
        end if;
        return jsonb_build_object('log_id', existing.log_id::text, 'replayed', true);
    end if;
    insert into public.system_log(node_id, profile_id, module, action, status, message,
        model_name, "timestamp", event_uuid)
    values (p_node_id, p_profile_id, p_values->>'module', p_values->>'action',
        p_values->>'status', p_values->>'message', p_values->>'model_name',
        p_values->>'timestamp', p_event_uuid)
    returning log_id into inserted;
    return jsonb_build_object('log_id', inserted::text, 'replayed', false);
exception
    -- A UUID already used on a node this caller cannot read is still a conflict,
    -- and says nothing about that hidden row.
    when unique_violation then raise exception 'log conflict' using errcode = 'PT409';
end
$$;
revoke all on function public.append_system_log(uuid, bigint, uuid, jsonb)
    from public, anon, service_role;
grant execute on function public.append_system_log(uuid, bigint, uuid, jsonb) to authenticated;
