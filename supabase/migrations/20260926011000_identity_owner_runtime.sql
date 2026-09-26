-- 5B.3 live correction: postgres cannot grant USAGE on platform-owned auth.
-- Read the identity installed by PostgREST after token verification instead.
create or replace function private.current_profile_id()
returns bigint language sql stable security definer set search_path = '' as $$
    select p.profile_id from public.profile p
    where p.auth_user_id =
        (nullif(current_setting('request.jwt.claims', true), '')::jsonb->>'sub')::uuid
      and p.is_active
$$;

create or replace function public.administration_command(p_actor uuid, p_request_id uuid,
                                               p_body jsonb, p_auth_user uuid default null)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    actor bigint;
    target bigint;
    request private.administration_request;
    answer jsonb;
    operation text := p_body->>'action';
    node_uuid uuid;
    desired text;
begin
    select p.profile_id into actor from public.profile p
        join public.user_role r on r.profile_id=p.profile_id and r.role='administrator'
        where p.auth_user_id=p_actor and p.is_active;
    if actor is null then raise exception 'access denied' using errcode='PT403'; end if;
    if p_request_id is null or p_body is null or jsonb_typeof(p_body)<>'object'
       or octet_length(p_body::text)>4096 or p_body ? 'password'
       or operation is null or operation not in
       ('create_account','membership','reassign','node_status','role','account_status') then
        raise exception 'invalid request' using errcode='PT400';
    end if;
    insert into private.administration_request(request_id,actor_profile_id,body)
        values(p_request_id,actor,p_body) on conflict do nothing;
    select * into request from private.administration_request
        where request_id=p_request_id for update;
    if request.actor_profile_id<>actor or request.body<>p_body then
        raise exception 'request conflict' using errcode='PT409';
    end if;
    if request.result is not null then return request.result; end if;

    if operation='create_account' then
        if coalesce(p_body->>'email','') !~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'
           or length(p_body->>'email')>254
           or length(coalesce(p_body->>'username','')) not between 1 and 80
           or coalesce(p_body->>'role','') not in ('analyst','administrator') then
            raise exception 'invalid account' using errcode='PT400';
        end if;
        -- Auth ownership markers are checked by the hosted Edge Function via
        -- the Auth admin API. This service-only RPC trusts that checked ID;
        -- the narrow SQL owner has no privileges on Supabase's auth schema.
        if p_auth_user is null then
            return jsonb_build_object('status','pending');
        end if;
        insert into public.profile(auth_user_id,username,email)
            values(p_auth_user,p_body->>'username',lower(p_body->>'email'))
            returning profile_id into target;
        insert into public.user_role(profile_id,role,granted_by)
            values(target,p_body->>'role',actor);
        answer := jsonb_build_object('status','completed','profile_id',target,
                                     'auth_user_id',p_auth_user);
    elsif operation in ('membership','reassign') then
        target := (p_body->>'profile_id')::bigint;
        node_uuid := (p_body->>'node_id')::uuid;
        desired := p_body->>'status';
        if target is null or node_uuid is null or desired is null
           or desired not in ('approved','rejected','revoked') then
            raise exception 'invalid membership' using errcode='PT400';
        end if;
        -- Serialize default-node changes for one profile.
        perform 1 from public.profile where profile_id=target and is_active for update;
        if not found then raise exception 'invalid target' using errcode='PT400'; end if;
        perform 1 from public.node where node_id=node_uuid for update;
        if not found then raise exception 'invalid target' using errcode='PT400'; end if;
        if operation='reassign' then
            if desired<>'approved' or (p_body->>'from_node_id')::uuid is null
               or (p_body->>'from_node_id')::uuid=node_uuid then
                raise exception 'invalid reassignment' using errcode='PT400';
            end if;
            update public.node_membership set status='revoked',is_default=false,
                decided_at=now(),decided_by=actor
                where profile_id=target and node_id=(p_body->>'from_node_id')::uuid;
            if not found then raise exception 'invalid target' using errcode='PT400'; end if;
        end if;
        if desired='approved' then
            update public.node set status='approved',approved_at=now(),approved_by=actor
                where node_id=node_uuid;
        end if;
        if desired='approved' and coalesce((p_body->>'is_default')::boolean,false) then
            update public.node_membership set is_default=false where profile_id=target;
        end if;
        insert into public.node_membership(node_id,profile_id,status,is_default,decided_at,decided_by)
            values(node_uuid,target,desired,
                desired='approved' and coalesce((p_body->>'is_default')::boolean,false),now(),actor)
            on conflict (node_id,profile_id) do update set status=excluded.status,
                is_default=excluded.is_default,decided_at=excluded.decided_at,decided_by=actor;
        answer := jsonb_build_object('status','completed','node_id',node_uuid,'profile_id',target);
    elsif operation='node_status' then
        node_uuid := (p_body->>'node_id')::uuid;
        desired := p_body->>'status';
        if desired is null or desired not in ('approved','rejected','revoked') then
            raise exception 'invalid node state' using errcode='PT400';
        end if;
        update public.node set status=desired,
            approved_at=case when desired='approved' then now() else approved_at end,
            approved_by=case when desired='approved' then actor else approved_by end
            where node_id=node_uuid;
        if not found then raise exception 'invalid target' using errcode='PT400'; end if;
        if desired<>'approved' then
            update public.node_membership set status=desired,is_default=false,
                decided_at=now(),decided_by=actor where node_id=node_uuid;
        end if;
        answer := jsonb_build_object('status','completed');
    else
        target := (p_body->>'profile_id')::bigint;
        if target is null or target=actor then
            raise exception 'self administration refused' using errcode='PT403';
        end if;
        perform 1 from public.profile where profile_id=target for update;
        if not found then raise exception 'invalid target' using errcode='PT400'; end if;
        if operation='role' then
            desired := p_body->>'role';
            if desired is null or desired not in ('analyst','administrator') then
                raise exception 'invalid role' using errcode='PT400';
            end if;
            delete from public.user_role where profile_id=target;
            insert into public.user_role(profile_id,role,granted_by) values(target,desired,actor);
        else
            if jsonb_typeof(p_body->'is_active') is distinct from 'boolean' then
                raise exception 'invalid account state' using errcode='PT400';
            end if;
            update public.profile set is_active=(p_body->>'is_active')::boolean
                where profile_id=target;
        end if;
        answer := jsonb_build_object('status','completed');
    end if;
    update private.administration_request set result=answer where request_id=p_request_id;
    return answer;
exception
    when unique_violation then raise exception 'request conflict' using errcode='PT409';
    when foreign_key_violation or check_violation or invalid_text_representation
        or not_null_violation or numeric_value_out_of_range then
        raise exception 'invalid request' using errcode='PT400';
end
$$;
