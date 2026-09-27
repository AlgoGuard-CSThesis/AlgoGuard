alter table public.report add column report_uuid uuid unique;

create view public.alert_detail with(security_invoker=true) as
select a.*,p.predicted_label,p.confidence_score,p.model_name,t.source_ip,t.destination_ip
from public.alert a join public.prediction p on p.prediction_id=a.prediction_id
join public.network_traffic t on t.traffic_id=p.traffic_id;
grant select on public.alert_detail to authenticated;

create function public.create_operational_report(p_node_id uuid,p_profile_id bigint,
    p_request_id uuid,p_alert_ids bigint[])
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
    target public.report;
    selected bigint[];
    existing bigint[];
    inserted bigint;
begin
    if p_profile_id is distinct from private.current_profile_id()
       or not private.can_write_node(p_node_id) then
        raise exception 'access denied' using errcode='PT403';
    end if;
    if p_request_id is null or coalesce(array_length(p_alert_ids,1),0) not between 1 and 250 then
        raise exception 'select 1 to 250 alerts' using errcode='PT400';
    end if;
    select array_agg(distinct x order by x) into selected from unnest(p_alert_ids) x;
    perform pg_advisory_xact_lock(hashtextextended(p_request_id::text,35791));
    select * into target from public.report where report_uuid=p_request_id;
    if found then
        select array_agg(alert_id order by alert_id) into existing
            from public.report_alert where report_id=target.report_id;
        if target.node_id is distinct from p_node_id or target.owner_profile_id is distinct from p_profile_id
           or existing is distinct from selected then
            raise exception 'report identity conflict' using errcode='PT409';
        end if;
        return jsonb_build_object('report_id',target.report_id::text,'replayed',true);
    end if;
    insert into public.report(node_id,owner_profile_id,report_uuid,report_type,generated_at)
    values(p_node_id,p_profile_id,p_request_id,'Operational evidence',
        to_char(now() at time zone 'UTC','YYYY-MM-DD HH24:MI:SS')) returning * into target;
    insert into public.report_alert(report_id,alert_id,node_id)
    select target.report_id,alert_id,p_node_id from public.alert
        where alert_id=any(selected) and node_id=p_node_id;
    get diagnostics inserted = row_count;
    if inserted <> array_length(selected,1) then
        raise exception 'selected evidence unavailable' using errcode='PT400';
    end if;
    return jsonb_build_object('report_id',target.report_id::text,'replayed',false);
end
$$;
revoke all on function public.create_operational_report(uuid,bigint,uuid,bigint[])
    from public,anon,service_role;
grant execute on function public.create_operational_report(uuid,bigint,uuid,bigint[])
    to authenticated;
