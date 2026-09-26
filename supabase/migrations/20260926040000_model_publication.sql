-- Historical manifests remain readable; only locked publications can activate.
alter table public.model_manifest add column dependency_lock text;
alter table public.model_manifest add column publication_id uuid unique;
alter table public.model_manifest add column source_sha256 text;
alter table public.model_manifest add constraint model_manifest_lock_digest
    check (dependency_lock is null or dependency_lock ~ '^[0-9a-f]{64}$');

create function private.preserve_manifest_identity()
returns trigger language plpgsql security invoker set search_path = '' as $$
begin
    if (to_jsonb(new) - array['status','activated_at','superseded_at','note'])
        is distinct from (to_jsonb(old) - array['status','activated_at','superseded_at','note']) then
        raise exception 'manifest identity is immutable' using errcode='23514';
    end if;
    return new;
end
$$;
revoke all on function private.preserve_manifest_identity() from public, anon, authenticated, service_role;
create trigger preserve_manifest_identity before update on public.model_manifest
    for each row execute function private.preserve_manifest_identity();

create function public.activate_model_publication(p_publication_id uuid)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
    target public.model_manifest;
    previous_id bigint;
    moment timestamptz := clock_timestamp();
begin
    perform 1 from public.deployment_activation_lock where lock_id=true for update;
    if not found then
        raise exception 'activation lock missing' using errcode='23514';
    end if;
    select * into target from public.model_manifest
        where publication_id=p_publication_id for update;
    if not found then
        raise exception 'publication missing' using errcode='23514';
    end if;
    -- A lost acknowledgement must not make an old, superseded release active again.
    if target.status in ('active','superseded') then
        return jsonb_build_object('manifest_id', target.manifest_id::text,
                                 'status', target.status, 'replayed', true);
    end if;
    if target.status <> 'published' or target.dependency_lock is null
       or target.source_sha256 is null then
        raise exception 'publication not verified' using errcode='23514';
    end if;
    select deployment_id into previous_id from public.model_deployment where is_active=1;
    update public.model_deployment set is_active=0 where is_active=1;
    update public.model_manifest set status='superseded', superseded_at=moment
        where status='active';
    update public.detection_model set is_deployed=0 where is_deployed=1;
    update public.model_deployment set is_active=1, replaced_deployment_id=previous_id,
        deployed_at=to_char(moment at time zone 'UTC', 'YYYY-MM-DD HH24:MI:SS')
        where deployment_id=target.deployment_id and model_id=target.model_id;
    if not found then
        raise exception 'deployment missing' using errcode='23514';
    end if;
    update public.model_manifest set status='active', activated_at=moment
        where manifest_id=target.manifest_id;
    update public.detection_model set is_deployed=1,
        deployed_at=to_char(moment at time zone 'UTC', 'YYYY-MM-DD HH24:MI:SS')
        where model_id=target.model_id;
    return jsonb_build_object('manifest_id', target.manifest_id::text,
                             'status', 'active', 'replayed', false);
end
$$;
revoke all on function public.activate_model_publication(uuid)
    from public, anon, authenticated, service_role;
grant execute on function public.activate_model_publication(uuid) to postgres;
