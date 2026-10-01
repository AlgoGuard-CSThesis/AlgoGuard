-- 5E: source-qualified historical identities, import receipts and write gates.
alter table public.profile add column legacy_source_id uuid;
alter table public.profile add column legacy_username text;
alter table public.profile drop constraint profile_legacy_admin_id_key;
alter table public.profile drop constraint profile_has_an_identity;
alter table public.profile add constraint profile_has_an_identity check (
    auth_user_id is not null or legacy_admin_id is not null or legacy_source_id is not null
);
create unique index profile_source_admin on public.profile(legacy_source_id,legacy_admin_id);
create unique index profile_unqualified_legacy_admin on public.profile(legacy_admin_id)
    where legacy_source_id is null;

alter table public.node add column migration_state text
    check (migration_state in ('importing','verified','live','frozen'));

create table private.legacy_source (
    source_id uuid primary key,
    node_id uuid not null unique references public.node(node_id),
    snapshot_sha256 text not null check (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    counts jsonb not null,
    imported_at timestamptz not null default now(),
    enabled_at timestamptz,
    state text not null check (state in ('verified','live','frozen'))
);
create table private.legacy_record (
    source_id uuid not null references private.legacy_source(source_id),
    source_table text not null,
    source_key text not null,
    target_table text not null,
    target_key jsonb not null,
    original_record jsonb not null,
    unresolved boolean not null default false,
    primary key(source_id,source_table,source_key)
);
alter table private.legacy_source enable row level security;
alter table private.legacy_record enable row level security;
revoke all on private.legacy_source,private.legacy_record from public,anon,authenticated,service_role;
grant select on private.legacy_source,private.legacy_record to authenticated;
create policy administrator_review on private.legacy_source for select to authenticated
    using(private.is_administrator());
create policy administrator_review on private.legacy_record for select to authenticated
    using(private.is_administrator());
create view public.legacy_import_review with(security_invoker=true) as
    select source_id,source_table,source_key,target_table,target_key,original_record,unresolved
    from private.legacy_record;
grant select on public.legacy_import_review to authenticated;

-- Preserve unattributed evidence, but never guess which analyst should receive it.
do $$
declare t text;
begin
    foreach t in array array['capture_session','network_traffic','prediction','alert',
                             'report','system_log'] loop
        execute format('alter table public.%I add column legacy_unresolved boolean '
                       'not null default false',t);
        execute format('create policy legacy_attribution_guard on public.%I as restrictive '
                       'for all to authenticated using '
                       '(not legacy_unresolved or private.is_administrator()) with check '
                       '(not legacy_unresolved or private.is_administrator())',t);
    end loop;
end
$$;

-- Existing installs retain their behavior (NULL); imported nodes need explicit enablement.
create or replace function private.can_write_node(wanted uuid) returns boolean
language sql stable security definer set search_path='' as $$
    select private.is_operator() and exists (
        select 1 from public.node_membership m join public.node n using(node_id)
        where m.profile_id=private.current_profile_id() and m.node_id=wanted
          and m.status='approved' and n.status='approved'
          and (n.migration_state is null or n.migration_state='live')
    )
$$;

-- Reads remain possible while writers are frozen. Do not inherit the write gate.
create function private.can_read_enrolled_node(wanted uuid) returns boolean
language sql stable security definer set search_path='' as $$
    select private.is_operator() and exists (
        select 1 from public.node_membership m join public.node n using(node_id)
        where m.profile_id=private.current_profile_id() and m.node_id=wanted
          and m.status='approved' and n.status='approved'
    )
$$;
grant create on schema private to algoguard_identity_owner;
alter function private.can_read_enrolled_node(uuid) owner to algoguard_identity_owner;
revoke create on schema private from algoguard_identity_owner;
revoke all on function private.can_read_enrolled_node(uuid) from public,anon,service_role;
grant execute on function private.can_read_enrolled_node(uuid) to authenticated;
create or replace function private.can_read_node(wanted uuid) returns boolean
language sql stable security invoker set search_path='' as $$
    select private.is_administrator() or private.can_read_enrolled_node(wanted)
$$;
