-- 5B.3: approved enrollment and recoverable hosted account administration.
-- This owner has only identity-table privileges, no login or BYPASSRLS.
do $$
begin
    if not exists (select 1 from pg_roles where rolname='algoguard_identity_owner') then
        create role algoguard_identity_owner nologin noinherit;
    end if;
end
$$;
grant algoguard_identity_owner to postgres;
grant usage on schema public, private, auth to algoguard_identity_owner;
grant create on schema public, private to algoguard_identity_owner;
grant select, insert, update, delete on public.profile, public.user_role,
    public.node, public.node_membership to algoguard_identity_owner;
grant usage on sequence public.profile_profile_id_seq,
    public.node_membership_membership_id_seq to algoguard_identity_owner;
grant select (id, email, raw_app_meta_data) on auth.users to algoguard_identity_owner;
grant execute on function auth.uid() to algoguard_identity_owner;

create policy identity_maintenance on public.profile to algoguard_identity_owner
    using (true) with check (true);
create policy identity_maintenance on public.user_role to algoguard_identity_owner
    using (true) with check (true);
create policy identity_maintenance on public.node to algoguard_identity_owner
    using (true) with check (true);
create policy identity_maintenance on public.node_membership to algoguard_identity_owner
    using (true) with check (true);

alter table public.node_membership drop constraint node_membership_status_check;
alter table public.node_membership add constraint node_membership_status_check
    check (status in ('pending', 'approved', 'rejected', 'revoked'));

create table private.administration_request (
    request_id uuid primary key,
    actor_profile_id bigint not null references public.profile(profile_id),
    body jsonb not null,
    result jsonb,
    created_at timestamptz not null default now()
);
alter table private.administration_request enable row level security;
revoke all on private.administration_request from public, anon, authenticated, service_role;
grant select, insert, update on private.administration_request to algoguard_identity_owner;
create policy identity_maintenance on private.administration_request to algoguard_identity_owner
    using (true) with check (true);

alter function private.current_profile_id() owner to algoguard_identity_owner;
alter function private.has_role(text) owner to algoguard_identity_owner;
alter function private.is_administrator() owner to algoguard_identity_owner;
alter function private.enforce_role_grant_authority() owner to algoguard_identity_owner;
alter function private.forbid_auth_identity_rebinding() owner to algoguard_identity_owner;
grant execute on function private.current_profile_id(), private.has_role(text),
    private.is_administrator() to algoguard_identity_owner;

create function public.request_enrollment(p_node_id uuid, p_display_name text,
                                          p_hostname_hint text default null)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
    actor bigint := private.current_profile_id();
    membership public.node_membership;
begin
    if actor is null then
        raise exception 'access denied' using errcode = 'PT403';
    end if;
    if p_node_id is null or p_display_name is null or length(trim(p_display_name)) not between 1 and 100
       or length(p_hostname_hint) > 255 then
        raise exception 'invalid enrollment' using errcode = 'PT400';
    end if;
    insert into public.node(node_id, display_name, hostname_hint)
    values (p_node_id, trim(p_display_name), p_hostname_hint) on conflict do nothing;
    insert into public.node_membership(node_id, profile_id)
    values (p_node_id, actor) on conflict (node_id, profile_id) do nothing;
    select * into membership from public.node_membership
        where node_id = p_node_id and profile_id = actor;
    -- Never return another person's node details or approval status.
    return jsonb_build_object('node_id', p_node_id, 'status', membership.status,
                             'is_default', membership.is_default);
end
$$;
alter function public.request_enrollment(uuid, text, text) owner to algoguard_identity_owner;
revoke all on function public.request_enrollment(uuid, text, text) from public, anon;
grant execute on function public.request_enrollment(uuid, text, text) to authenticated;

create function public.administration_authorized()
returns boolean language sql stable security invoker set search_path = '' as $$
    select private.is_administrator()
$$;
revoke all on function public.administration_authorized() from public, anon;
grant execute on function public.administration_authorized() to authenticated;

-- Only the hosted Edge Function's service credential may execute this RPC.
-- p_actor comes from Auth /user, never from a user-supplied body or JWT metadata.
-- Authorization is checked AGAIN for every call, including recovery/replays.
create function public.administration_command(p_actor uuid, p_request_id uuid,
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
    auth_candidate uuid;
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
        -- The server-owned marker proves that a partial Auth user belongs to
        -- this request. Never adopt an unrelated existing account by email.
        select u.id into auth_candidate from auth.users u
            where lower(u.email)=lower(p_body->>'email')
              and u.raw_app_meta_data->>'algoguard_request_id'=p_request_id::text;
        if p_auth_user is null then
            return jsonb_build_object('status','pending','auth_user_id',auth_candidate);
        end if;
        if auth_candidate is distinct from p_auth_user then
            raise exception 'account conflict' using errcode='PT409';
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
alter function public.administration_command(uuid,uuid,jsonb,uuid) owner to algoguard_identity_owner;
revoke all on function public.administration_command(uuid,uuid,jsonb,uuid)
    from public, anon, authenticated;
grant execute on function public.administration_command(uuid,uuid,jsonb,uuid) to service_role;
revoke create on schema public,private from algoguard_identity_owner;
