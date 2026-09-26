-- Stage 5B.2 — protected role records and the helpers that read them.
--
-- The rule this migration exists to enforce: authorization reads the
-- database, never the token. A JWT is a snapshot of who someone was when
-- it was minted, and it stays valid until it expires — so if the token
-- decided permissions, demoting an Administrator would not take effect
-- for up to an hour. Token metadata may mirror a role for display
-- (plan, Part II); these functions are what policies and functions ask.
--
-- All three helpers live in `private`, which PostgREST does not serve, so
-- they are usable inside policies but not callable over the API.
-- SECURITY DEFINER because they read `public.user_role`, which RLS
-- protects: reading it through a policy that itself calls these helpers
-- would recurse. The owner (postgres) is not subject to that table's RLS,
-- which is what breaks the cycle.
--
-- `set search_path = ''` on every one of them, with every object written
-- out in full. A SECURITY DEFINER function without a pinned search_path
-- is the classic privilege-escalation hole: the caller controls which
-- schema `user_role` resolves to.

-- ---------------------------------------------------------------------
-- Who is calling
-- ---------------------------------------------------------------------
create or replace function private.current_profile_id()
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
    select p.profile_id
    from public.profile p
    where p.auth_user_id = (select auth.uid())
      and p.is_active
$$;

comment on function private.current_profile_id() is
    'Profile for the current access token, or NULL. Deactivated profiles resolve to NULL.';

-- ---------------------------------------------------------------------
-- What they are currently allowed to be
-- ---------------------------------------------------------------------
create or replace function private.has_role(wanted text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
        from public.user_role r
        where r.profile_id = (select private.current_profile_id())
          and r.role = wanted
    )
$$;

create or replace function private.is_administrator()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select private.has_role('administrator')
$$;

comment on function private.is_administrator() is
    'Reads the protected role record. A token minted before a demotion does not keep the privilege.';

-- ---------------------------------------------------------------------
-- Grants
-- ---------------------------------------------------------------------
-- USAGE lets a policy resolve these names; it grants no table access and
-- `private` is absent from the Data API's exposed schemas, so these are
-- not reachable as RPCs. EXECUTE is granted per function, never wholesale.
grant usage on schema private to authenticated;

revoke execute on all functions in schema private from public;
revoke execute on all functions in schema private from anon;

grant execute on function private.current_profile_id() to authenticated;
grant execute on function private.has_role(text) to authenticated;
grant execute on function private.is_administrator() to authenticated;

-- ---------------------------------------------------------------------
-- Structural guards: true even if a policy is wrong
-- ---------------------------------------------------------------------
-- Self-promotion is the first thing a hostile analyst tries. 5B.4's
-- policies keep them off this table entirely; this trigger means that
-- even a path that reaches the table cannot grant a role to its own
-- profile, and cannot grant one on behalf of somebody who is not
-- currently an Administrator.
--
-- granted_by IS NULL means trusted maintenance over a direct connection
-- (bootstrap_admin.py creating the first Administrator). The API roles
-- have no privileges on this table at all, so they cannot take that path.
create or replace function private.enforce_role_grant_authority()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if new.granted_by is null then
        return new;
    end if;

    if new.granted_by = new.profile_id then
        raise exception 'a profile cannot grant a role to itself'
            using errcode = '42501';
    end if;

    if not exists (
        select 1
        from public.user_role r
        where r.profile_id = new.granted_by
          and r.role = 'administrator'
    ) then
        raise exception 'only a current administrator can grant a role'
            using errcode = '42501';
    end if;

    return new;
end
$$;

create trigger user_role_grant_authority
    before insert or update on public.user_role
    for each row
    execute function private.enforce_role_grant_authority();

-- An account's cloud identity is set once. Re-pointing a profile at a
-- different auth.users row would silently transfer every record
-- attributed to that person, including their historical evidence.
create or replace function private.forbid_auth_identity_rebinding()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if old.auth_user_id is not null
       and new.auth_user_id is distinct from old.auth_user_id then
        raise exception 'profile.auth_user_id cannot be reassigned once set'
            using errcode = '42501';
    end if;
    return new;
end
$$;

create trigger profile_identity_is_permanent
    before update on public.profile
    for each row
    execute function private.forbid_auth_identity_rebinding();
