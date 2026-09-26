-- 5B.5: invoker transactions, content-bound retries, and complete flow groups.
alter table public.ingest_event add column payload jsonb not null;
alter table public.ingest_event add column audit_log_id bigint not null;
alter table public.ingest_event alter column traffic_id set not null;
alter table public.ingest_event alter column prediction_id set not null;
alter table public.system_log add unique(log_id,node_id,profile_id);
alter table public.ingest_event add foreign key(audit_log_id,node_id,owner_profile_id)
    references public.system_log(log_id,node_id,profile_id);
alter table public.ingest_event add unique(traffic_id);
alter table public.ingest_event add unique(prediction_id);
alter table public.ingest_event add unique(alert_id);
alter table public.ingest_event add unique(audit_log_id);
alter table public.ingest_event add constraint event_content_matches_hash check (
    content_sha256=encode(extensions.digest(jsonb_build_object(
        'node_id',node_id,'profile_id',owner_profile_id,'event',payload)::text,'sha256'),'hex')
);

create function private.normalize_flow(p_node uuid,p_profile bigint,e jsonb)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare
    traffic jsonb := e->'traffic';
    prediction jsonb := e->'prediction';
    alert jsonb := e->'alert';
    stamp timestamptz;
    legacy_stamp text;
    event_id uuid;
    capture bigint;
    model bigint;
    deployment bigint;
begin
    if e is null or jsonb_typeof(e)<>'object' or octet_length(e::text)>65536
       or e-array['event_uuid','event_time','capture_id','model_id','deployment_id',
                  'traffic','prediction','alert']<>'{}'::jsonb
       or jsonb_typeof(traffic) is distinct from 'object'
       or jsonb_typeof(prediction) is distinct from 'object'
       or traffic-array['source_ip','destination_ip','source_port','destination_port','protocol',
                         'packet_size','flags','dataset_source','feature_payload']<>'{}'::jsonb
       or prediction-array['predicted_label','confidence_score','latency_ms','model_name',
                            'input_payload']<>'{}'::jsonb then
        raise exception 'invalid flow' using errcode='PT400';
    end if;
    event_id := (e->>'event_uuid')::uuid;
    model := (e->>'model_id')::bigint;
    deployment := (e->>'deployment_id')::bigint;
    capture := (e->>'capture_id')::bigint;
    if event_id is null or model is null or model<=0 or deployment is null or deployment<=0
       or capture<=0 or coalesce(e->>'event_time','') !~
       '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$'
       or coalesce(prediction->>'predicted_label','') not in ('Normal','Attack') then
        raise exception 'invalid flow' using errcode='PT400';
    end if;
    stamp := (e->>'event_time')::timestamptz;
    legacy_stamp := to_char(stamp at time zone 'UTC','YYYY-MM-DD HH24:MI:SS');
    if (prediction->>'confidence_score')::double precision not between 0 and 100
       or (prediction->>'latency_ms')::double precision < 0
       or (prediction->>'latency_ms')::double precision >= 'Infinity'::double precision
       or (traffic->>'source_port')::int not between 0 and 65535
       or (traffic->>'destination_port')::int not between 0 and 65535
       or (traffic->>'packet_size')::bigint<0 then
        raise exception 'invalid flow values' using errcode='PT400';
    end if;
    if alert is not null and alert<>'null'::jsonb then
        if jsonb_typeof(alert)<>'object'
           or alert-array['severity_level','alert_status','description']<>'{}'::jsonb
           or length(coalesce(alert->>'description',''))>2000 then
            raise exception 'invalid alert' using errcode='PT400';
        end if;
        alert := jsonb_build_object('node_id',p_node,'owner_profile_id',p_profile,
            'severity_level',coalesce(alert->>'severity_level','High'),
            'alert_status',coalesce(alert->>'alert_status','Open'),
            'description',alert->>'description','detected_at',legacy_stamp);
    else alert := null;
    end if;
    return jsonb_build_object('event_uuid',event_id,'event_time',stamp,'capture_id',capture,
        'deployment_id',deployment,
        'traffic',jsonb_build_object('node_id',p_node,'owner_profile_id',p_profile,
            'capture_id',capture,'timestamp',legacy_stamp,'source_ip',traffic->>'source_ip',
            'destination_ip',traffic->>'destination_ip','source_port',(traffic->>'source_port')::int,
            'destination_port',(traffic->>'destination_port')::int,'protocol',traffic->>'protocol',
            'packet_size',(traffic->>'packet_size')::bigint,'flags',traffic->>'flags',
            'dataset_source',traffic->>'dataset_source','feature_payload',traffic->>'feature_payload'),
        'prediction',jsonb_build_object('node_id',p_node,'owner_profile_id',p_profile,
            'model_id',model,'deployment_id',deployment,'model_name',prediction->>'model_name',
            'predicted_label',prediction->>'predicted_label',
            'confidence_score',(prediction->>'confidence_score')::double precision,
            'latency_ms',(prediction->>'latency_ms')::double precision,
            'prediction_timestamp',legacy_stamp,'alert_created',case when alert is null then 0 else 1 end,
            'input_payload',prediction->>'input_payload'),'alert',alert);
exception when invalid_text_representation or numeric_value_out_of_range
    or datetime_field_overflow or invalid_datetime_format then
    raise exception 'invalid flow values' using errcode='PT400';
end
$$;
revoke all on function private.normalize_flow(uuid,bigint,jsonb) from public,anon;
grant execute on function private.normalize_flow(uuid,bigint,jsonb) to authenticated;

-- Even a direct table insert must match its payload, relationships and audit row.
create function private.validate_event_group() returns trigger
language plpgsql security invoker set search_path='' as $$
declare v jsonb;
begin
    v := private.normalize_flow(new.node_id,new.owner_profile_id,new.payload);
    if new.event_uuid<>(v->>'event_uuid')::uuid
       or new.event_time<>(v->>'event_time')::timestamptz
       or new.capture_id is distinct from (v->>'capture_id')::bigint
       or new.deployment_id is distinct from (v->>'deployment_id')::bigint
       or not exists(select 1 from public.network_traffic t where t.traffic_id=new.traffic_id
                     and to_jsonb(t) @> (v->'traffic'))
       or not exists(select 1 from public.prediction p where p.prediction_id=new.prediction_id
                     and p.traffic_id=new.traffic_id and to_jsonb(p) @> (v->'prediction'))
       or ((v->'alert'='null'::jsonb) <> (new.alert_id is null))
       or (new.alert_id is not null and not exists(select 1 from public.alert a
           where a.alert_id=new.alert_id and a.prediction_id=new.prediction_id
             and to_jsonb(a) @> (v->'alert')))
       or not exists(select 1 from public.system_log l where l.log_id=new.audit_log_id
           and l.node_id=new.node_id and l.profile_id=new.owner_profile_id
           and l.prediction_id=new.prediction_id and l.module='detection'
           and l.action='flow_accepted' and l.status='success') then
        raise exception 'invalid flow group' using errcode='PT400';
    end if;
    return new;
end
$$;
revoke all on function private.validate_event_group() from public,anon,authenticated;
create trigger complete_event_group before insert or update on public.ingest_event
    for each row execute function private.validate_event_group();

create function public.store_flow_batch(p_node_id uuid,p_profile_id bigint,p_events jsonb)
returns jsonb language plpgsql security invoker set search_path='' set lock_timeout='3s' as $$
declare
    e jsonb;
    v jsonb;
    event_id uuid;
    fingerprint text;
    previous public.ingest_event;
    t public.network_traffic;
    p public.prediction;
    a public.alert;
    traffic bigint;
    prediction bigint;
    alert bigint;
    audit bigint;
    answer jsonb := '[]';
begin
    if p_profile_id is distinct from private.current_profile_id()
       or not private.can_write_node(p_node_id) then
        raise exception 'access denied' using errcode='PT403';
    end if;
    if p_events is null or jsonb_typeof(p_events)<>'array'
       or jsonb_array_length(p_events) not between 1 and 50 then
        raise exception 'batch must contain 1 to 50 events' using errcode='PT400';
    end if;
    -- Acquire UUID locks in deterministic order to avoid deadlocks across batches.
    for event_id in select distinct (item->>'event_uuid')::uuid
        from jsonb_array_elements(p_events) item order by 1 loop
        if event_id is null then raise exception 'invalid event UUID' using errcode='PT400'; end if;
        perform pg_advisory_xact_lock(hashtextextended(event_id::text,13579));
    end loop;
    for e in select value from jsonb_array_elements(p_events) loop
        v := private.normalize_flow(p_node_id,p_profile_id,e);
        event_id := (v->>'event_uuid')::uuid;
        fingerprint := encode(extensions.digest(jsonb_build_object('node_id',p_node_id,
            'profile_id',p_profile_id,'event',e)::text,'sha256'),'hex');
        select * into previous from public.ingest_event where event_uuid=event_id;
        if found then
            if previous.content_sha256<>fingerprint or previous.payload<>e
               or previous.node_id<>p_node_id or previous.owner_profile_id<>p_profile_id then
                raise exception 'event conflict' using errcode='PT409';
            end if;
            traffic := previous.traffic_id;
            prediction := previous.prediction_id;
            alert := previous.alert_id;
            audit := previous.audit_log_id;
        else
            t := jsonb_populate_record(null::public.network_traffic,v->'traffic');
            insert into public.network_traffic(node_id,owner_profile_id,capture_id,"timestamp",
                source_ip,destination_ip,source_port,destination_port,protocol,packet_size,flags,
                dataset_source,feature_payload) values(t.node_id,t.owner_profile_id,t.capture_id,
                t."timestamp",t.source_ip,t.destination_ip,t.source_port,t.destination_port,
                t.protocol,t.packet_size,t.flags,t.dataset_source,t.feature_payload)
                returning traffic_id into traffic;
            p := jsonb_populate_record(null::public.prediction,v->'prediction');
            insert into public.prediction(node_id,owner_profile_id,traffic_id,model_id,deployment_id,
                model_name,predicted_label,confidence_score,prediction_timestamp,latency_ms,
                alert_created,input_payload) values(p.node_id,p.owner_profile_id,traffic,p.model_id,
                p.deployment_id,p.model_name,p.predicted_label,p.confidence_score,p.prediction_timestamp,
                p.latency_ms,p.alert_created,p.input_payload) returning prediction_id into prediction;
            alert := null;
            if v->'alert'<>'null'::jsonb then
                a := jsonb_populate_record(null::public.alert,v->'alert');
                insert into public.alert(node_id,owner_profile_id,prediction_id,severity_level,
                    alert_status,detected_at,description) values(a.node_id,a.owner_profile_id,
                    prediction,a.severity_level,a.alert_status,a.detected_at,a.description)
                    returning alert_id into alert;
            end if;
            insert into public.system_log(node_id,profile_id,module,action,status,prediction_id,
                "timestamp") values(p_node_id,p_profile_id,'detection','flow_accepted','success',
                prediction,t."timestamp") returning log_id into audit;
            insert into public.ingest_event(event_uuid,node_id,owner_profile_id,capture_id,
                deployment_id,event_time,content_sha256,traffic_id,prediction_id,alert_id,payload,
                audit_log_id) values(event_id,p_node_id,p_profile_id,t.capture_id,p.deployment_id,
                (v->>'event_time')::timestamptz,fingerprint,traffic,prediction,alert,e,audit);
        end if;
        answer := answer || jsonb_build_array(jsonb_build_object('event_uuid',event_id,
            'traffic_id',traffic::text,'prediction_id',prediction::text,'alert_id',alert::text,
            'audit_log_id',audit::text,'replayed',previous.event_uuid is not null,
            'persistence','committed'));
        previous := null;
    end loop;
    return answer;
exception
    when unique_violation then raise exception 'event conflict' using errcode='PT409';
    when foreign_key_violation or check_violation or not_null_violation
        or invalid_text_representation or numeric_value_out_of_range then
        raise exception 'invalid flow reference or value' using errcode='PT400';
end
$$;
revoke all on function public.store_flow_batch(uuid,bigint,jsonb) from public,anon;
grant execute on function public.store_flow_batch(uuid,bigint,jsonb) to authenticated;

create function public.detection_statistics(p_node_id uuid)
returns jsonb language sql stable security invoker set search_path='' as $$
    select jsonb_build_object('total_flows',count(*),'attack_count',count(*) filter(where predicted_label='Attack'),
        'normal_count',count(*) filter(where predicted_label='Normal'),'avg_latency_ms',avg(latency_ms),
        'avg_confidence',avg(confidence_score),'last_prediction_at',max(prediction_timestamp),
        'alerts_total',(select count(*) from public.alert where node_id=p_node_id),
        'alerts_open',(select count(*) from public.alert where node_id=p_node_id and alert_status='Open'))
    from public.prediction where node_id=p_node_id
$$;
create function public.traffic_source_counts(p_node_id uuid)
returns table(dataset_source text,flow_count bigint)
language sql stable security invoker set search_path='' as $$
    select dataset_source,count(*) from public.network_traffic where node_id=p_node_id
    group by dataset_source order by count(*) desc,dataset_source
$$;
revoke all on function public.detection_statistics(uuid),public.traffic_source_counts(uuid)
    from public,anon;
grant execute on function public.detection_statistics(uuid),public.traffic_source_counts(uuid)
    to authenticated;
