-- 5B.4: explicit grants and current-record authorization on every public table.
create function private.is_operator() returns boolean
language sql stable security invoker set search_path='' as $$
    select private.has_role('analyst') or private.has_role('administrator')
$$;
create function private.can_write_node(wanted uuid) returns boolean
language sql stable security definer set search_path='' as $$
    select private.is_operator() and exists (
        select 1 from public.node_membership m join public.node n using(node_id)
        where m.profile_id=private.current_profile_id() and m.node_id=wanted
          and m.status='approved' and n.status='approved'
    )
$$;
grant create on schema private to algoguard_identity_owner;
alter function private.can_write_node(uuid) owner to algoguard_identity_owner;
revoke create on schema private from algoguard_identity_owner;
grant execute on function private.is_operator() to algoguard_identity_owner;
create function private.can_read_node(wanted uuid) returns boolean
language sql stable security invoker set search_path='' as $$
    select private.is_administrator() or private.can_write_node(wanted)
$$;
revoke all on function private.is_operator(),private.can_write_node(uuid),
    private.can_read_node(uuid) from public,anon;
grant execute on function private.is_operator(),private.can_write_node(uuid),
    private.can_read_node(uuid) to authenticated;

revoke all on all tables in schema public from anon,authenticated;
revoke all on all sequences in schema public from anon,authenticated;

grant select on public.profile,public.user_role,public.node,public.node_membership to authenticated;
create policy profile_read on public.profile for select to authenticated
    using (profile_id=private.current_profile_id() or private.is_administrator());
create policy role_read on public.user_role for select to authenticated
    using (profile_id=private.current_profile_id() or private.is_administrator());
create policy node_read on public.node for select to authenticated
    using (private.can_read_node(node_id));
create policy membership_read on public.node_membership for select to authenticated
    using (profile_id=private.current_profile_id() or private.is_administrator());

-- Rows are shared inside approved nodes; attribution cannot be forged.
do $$
declare t text;
begin
    foreach t in array array['capture_session','network_traffic','prediction','alert','report',
                             'ingest_event'] loop
        execute format('grant select,insert on public.%I to authenticated',t);
        execute format('create policy node_read on public.%I for select to authenticated '
                       'using (private.can_read_node(node_id))',t);
        execute format('create policy node_insert on public.%I for insert to authenticated '
                       'with check (private.can_write_node(node_id) '
                       'and owner_profile_id=private.current_profile_id())',t);
    end loop;
end
$$;
grant usage on sequence public.capture_session_capture_id_seq,public.network_traffic_traffic_id_seq,
    public.prediction_prediction_id_seq,public.alert_alert_id_seq,public.report_report_id_seq
    to authenticated;

-- Evidence, audit, and dedup anchors are append-only through the ordinary API.
-- Mutable operational fields use column grants, so identity/scope cannot move.
grant update (finished_at,packets_captured,packets_dropped,flows_emitted,status,closed_at)
    on public.capture_session to authenticated;
create policy session_update on public.capture_session for update to authenticated
    using (private.can_write_node(node_id) and owner_profile_id=private.current_profile_id())
    with check (private.can_write_node(node_id) and owner_profile_id=private.current_profile_id());
grant update (alert_status,description) on public.alert to authenticated;
create policy alert_update on public.alert for update to authenticated
    using (private.can_write_node(node_id)) with check (private.can_write_node(node_id));
grant delete on public.report to authenticated;
create policy report_delete on public.report for delete to authenticated
    using (private.can_write_node(node_id) and owner_profile_id=private.current_profile_id());

grant select,insert on public.system_log to authenticated;
grant usage on sequence public.system_log_log_id_seq to authenticated;
create policy log_read on public.system_log for select to authenticated
    using (private.can_read_node(node_id));
create policy log_insert on public.system_log for insert to authenticated
    with check (private.can_write_node(node_id) and profile_id=private.current_profile_id());

grant select,insert,delete on public.report_alert to authenticated;
create policy report_link_read on public.report_alert for select to authenticated using (
    exists(select 1 from public.report r where r.report_id=report_alert.report_id)
    and exists(select 1 from public.alert a where a.alert_id=report_alert.alert_id)
);
create policy report_link_insert on public.report_alert for insert to authenticated with check (
    private.can_write_node(node_id)
    and exists(select 1 from public.report r where r.report_id=report_alert.report_id
               and r.node_id=report_alert.node_id and r.owner_profile_id=private.current_profile_id())
    and exists(select 1 from public.alert a where a.alert_id=report_alert.alert_id
               and a.node_id=report_alert.node_id)
);
create policy report_link_delete on public.report_alert for delete to authenticated using (
    private.can_write_node(node_id)
    and exists(select 1 from public.report r where r.report_id=report_alert.report_id
               and r.owner_profile_id=private.current_profile_id())
);
-- Composite FKs alone do not validate NULL-node cross-node report links.
alter table public.report_alert add constraint report_link_report_exists
    foreign key(report_id) references public.report(report_id);
alter table public.report_alert add constraint report_link_alert_exists
    foreign key(alert_id) references public.alert(alert_id);
alter table public.system_log add constraint log_prediction_exists
    foreign key(prediction_id) references public.prediction(prediction_id);

-- Stronger attribution relationships prevent attaching another user's flow
-- inside the same shared node, in addition to the existing node-pair FKs.
alter table public.capture_session add unique(capture_id,node_id,owner_profile_id);
alter table public.network_traffic add unique(traffic_id,node_id,owner_profile_id);
alter table public.prediction add unique(prediction_id,node_id,owner_profile_id);
alter table public.alert add unique(alert_id,node_id,owner_profile_id);
alter table public.network_traffic add foreign key(capture_id,node_id,owner_profile_id)
    references public.capture_session(capture_id,node_id,owner_profile_id);
alter table public.prediction add foreign key(traffic_id,node_id,owner_profile_id)
    references public.network_traffic(traffic_id,node_id,owner_profile_id);
alter table public.alert add foreign key(prediction_id,node_id,owner_profile_id)
    references public.prediction(prediction_id,node_id,owner_profile_id);
alter table public.ingest_event add foreign key(traffic_id,node_id,owner_profile_id)
    references public.network_traffic(traffic_id,node_id,owner_profile_id);
alter table public.ingest_event add foreign key(prediction_id,node_id,owner_profile_id)
    references public.prediction(prediction_id,node_id,owner_profile_id);
alter table public.ingest_event add foreign key(alert_id,node_id,owner_profile_id)
    references public.alert(alert_id,node_id,owner_profile_id);
alter table public.system_log add foreign key(prediction_id,node_id,profile_id)
    references public.prediction(prediction_id,node_id,owner_profile_id);
alter table public.detection_model add unique(model_id,run_id);
alter table public.model_deployment add unique(deployment_id,model_id);
alter table public.model_deployment add foreign key(model_id,run_id)
    references public.detection_model(model_id,run_id);
alter table public.prediction add foreign key(deployment_id,model_id)
    references public.model_deployment(deployment_id,model_id);
alter table public.model_manifest add foreign key(deployment_id,model_id)
    references public.model_deployment(deployment_id,model_id);

-- Do not disclose training-machine paths or error traces. Invoker views and
-- matching column grants provide useful metadata without a bypassing view owner.
grant select (run_id,created_by,filename,upload_timestamp,row_count,feature_count,normal_count,
    anomaly_count,target_column,class_distribution,validation_status,preprocessing_status,
    training_status,recommended_model_id,recommended_model_name,recommended_score,
    deployment_status,completed_at) on public.training_run to authenticated;
create policy training_read on public.training_run for select to authenticated
    using (private.is_operator());
create view public.training_summary with(security_invoker=true) as
    select run_id,created_by,filename,upload_timestamp,row_count,feature_count,normal_count,
    anomaly_count,target_column,class_distribution,validation_status,preprocessing_status,
    training_status,recommended_model_id,recommended_model_name,recommended_score,
    deployment_status,completed_at from public.training_run;
grant select (model_id,run_id,model_name,model_type,version,training_date,accuracy,precision_score,
    recall,f1_score,specificity,fpr,roc_auc,cpu_usage,ram_usage,training_time,model_size,
    evaluation_status,normalized_accuracy,normalized_precision,normalized_recall,normalized_f1,
    normalized_roc_auc,normalized_fpr,normalized_cpu,normalized_ram,normalized_model_size,
    overall_score,rank,is_recommended,is_deployed,deployed_at) on public.detection_model to authenticated;
create policy model_read on public.detection_model for select to authenticated
    using (private.is_operator());
create view public.model_summary with(security_invoker=true) as
    select model_id,run_id,model_name,model_type,version,training_date,accuracy,precision_score,
    recall,f1_score,specificity,fpr,roc_auc,cpu_usage,ram_usage,training_time,model_size,
    evaluation_status,normalized_accuracy,normalized_precision,normalized_recall,normalized_f1,
    normalized_roc_auc,normalized_fpr,normalized_cpu,normalized_ram,normalized_model_size,
    overall_score,rank,is_recommended,is_deployed,deployed_at from public.detection_model;
grant select (deployment_id,model_id,run_id,deployed_by,deployed_at,is_active,replaced_deployment_id)
    on public.model_deployment to authenticated;
create policy deployment_read on public.model_deployment for select to authenticated
    using (private.is_operator());
create view public.deployment_summary with(security_invoker=true) as
    select deployment_id,model_id,run_id,deployed_by,deployed_at,is_active,replaced_deployment_id
    from public.model_deployment;
grant select on public.training_summary,public.model_summary,public.deployment_summary
    to authenticated;
grant select on public.model_manifest to authenticated;
create policy manifest_read on public.model_manifest for select to authenticated
    using (private.is_operator() and status in ('published','active','superseded'));
-- deployment_activation_lock has RLS, zero API grants and zero API policies.

insert into storage.buckets(id,name,public) values('models','models',false)
    on conflict(id) do update set public=false;
create policy model_object_read on storage.objects for select to authenticated using (
    bucket_id='models' and exists(select 1 from public.model_manifest m
        where m.object_path=storage.objects.name and m.status in ('published','active','superseded'))
);
-- No ordinary Storage write policy; only trusted publication can change models.
