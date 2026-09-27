alter table public.capture_session add column capture_uuid uuid unique;
alter table public.capture_session add column deployment_id bigint
    references public.model_deployment(deployment_id);
alter table public.capture_session add constraint capture_nonnegative_counts
    check (packets_captured>=0 and packets_dropped>=0 and flows_emitted>=0);

create function private.preserve_capture_lifecycle()
returns trigger language plpgsql security invoker set search_path = '' as $$
begin
    if old.closed_at is not null and (
        new.closed_at is null or new.status not in ('stopped','completed','error')
        or new.closed_at <> old.closed_at
    ) then
        raise exception 'closed capture cannot reopen' using errcode='23514';
    end if;
    if (new.node_id,new.owner_profile_id,new.capture_uuid,new.deployment_id)
       is distinct from (old.node_id,old.owner_profile_id,old.capture_uuid,old.deployment_id) then
        raise exception 'capture identity is immutable' using errcode='23514';
    end if;
    if new.packets_captured<old.packets_captured or new.packets_dropped<old.packets_dropped
       or new.flows_emitted<old.flows_emitted then
        raise exception 'capture counts cannot decrease' using errcode='23514';
    end if;
    return new;
end
$$;
revoke all on function private.preserve_capture_lifecycle() from public,anon,authenticated,service_role;
create trigger preserve_capture_lifecycle before update on public.capture_session
    for each row execute function private.preserve_capture_lifecycle();

create function public.open_cloud_capture(p_capture_uuid uuid, p_node_id uuid,
    p_profile_id bigint, p_deployment_id bigint, p_source text, p_filter text default '')
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
    result public.capture_session;
begin
    if p_profile_id is distinct from private.current_profile_id()
        or not private.can_write_node(p_node_id) then
        raise exception 'access denied' using errcode='PT403';
    end if;
    if p_source not in ('csv','pcap','live','manual') or p_capture_uuid is null
       or length(p_filter)>2000 then
        raise exception 'invalid capture' using errcode='PT400';
    end if;
    perform 1 from public.model_manifest where deployment_id=p_deployment_id and status='active';
    if not found then
        raise exception 'active model unavailable' using errcode='PT409';
    end if;
    insert into public.capture_session(capture_uuid,node_id,owner_profile_id,deployment_id,
        interface,bpf_filter,started_at)
    values(p_capture_uuid,p_node_id,p_profile_id,p_deployment_id,p_source,p_filter,
        to_char(now() at time zone 'UTC','YYYY-MM-DD HH24:MI:SS'))
    on conflict(capture_uuid) do nothing;
    select * into result from public.capture_session where capture_uuid=p_capture_uuid;
    if result.node_id is distinct from p_node_id or result.owner_profile_id is distinct from p_profile_id
       or result.deployment_id is distinct from p_deployment_id or result.interface is distinct from p_source
       or result.bpf_filter is distinct from p_filter then
        raise exception 'capture conflict' using errcode='PT409';
    end if;
    return jsonb_build_object('capture_id',result.capture_id::text,'status',result.status);
end
$$;
revoke all on function public.open_cloud_capture(uuid,uuid,bigint,bigint,text,text)
    from public,anon,service_role;
grant execute on function public.open_cloud_capture(uuid,uuid,bigint,bigint,text,text) to authenticated;

create function public.finalize_cloud_capture(p_capture_id bigint,p_values jsonb)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
    result public.capture_session;
    state text := p_values->>'status';
begin
    if state not in ('stopped','completed','error') or state is null then
        raise exception 'invalid terminal state' using errcode='PT400';
    end if;
    update public.capture_session set
        status=case when closed_at is null then state else status end,
        closed_at=coalesce(closed_at,now()),
        finished_at=coalesce(finished_at,to_char(now() at time zone 'UTC','YYYY-MM-DD HH24:MI:SS')),
        packets_captured=greatest(packets_captured,coalesce((p_values->>'packets_captured')::bigint,0)),
        packets_dropped=greatest(packets_dropped,coalesce((p_values->>'packets_dropped')::bigint,0)),
        flows_emitted=greatest(flows_emitted,coalesce((p_values->>'flows_emitted')::bigint,0))
    where capture_id=p_capture_id and owner_profile_id=private.current_profile_id()
    returning * into result;
    if not found then raise exception 'capture unavailable' using errcode='PT403'; end if;
    return jsonb_build_object('capture_id',result.capture_id::text,'status',result.status);
end
$$;
revoke all on function public.finalize_cloud_capture(bigint,jsonb) from public,anon,service_role;
grant execute on function public.finalize_cloud_capture(bigint,jsonb) to authenticated;

create function public.current_node_access(p_node_id uuid)
returns boolean language sql stable security invoker set search_path = '' as $$
    select private.can_write_node(p_node_id)
$$;
revoke all on function public.current_node_access(uuid) from public,anon,service_role;
grant execute on function public.current_node_access(uuid) to authenticated;
