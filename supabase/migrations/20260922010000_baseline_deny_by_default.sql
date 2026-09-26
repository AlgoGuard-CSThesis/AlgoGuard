-- Stage 5B.1 — deny-by-default baseline.
--
-- Supabase ships a permissive `public` schema: `anon` and `authenticated`
-- get USAGE plus default privileges on every table created there. Every
-- later migration in this stage therefore lands in a schema that already
-- hands the API roles access, and "we'll lock it down afterwards" is how
-- a table ends up world-readable for the week nobody checked.
--
-- This migration runs first and removes those privileges, so 5B.4's
-- policies are the only thing that can ever grant access back.
--
-- Transaction control: never write BEGIN/COMMIT in a migration file. The
-- Supabase CLI and cloud_migrate.py both wrap one file in one transaction;
-- an inner COMMIT would break rollback-on-failure. For the same reason,
-- no CREATE INDEX CONCURRENTLY in migrations.

-- ---------------------------------------------------------------------
-- 1. A schema the Data API never exposes
-- ---------------------------------------------------------------------
-- `private` holds authorization helpers and migration bookkeeping. It is
-- absent from supabase/config.toml's `[api] schemas`, so PostgREST will
-- not serve it even if something in here is accidentally granted.
create schema if not exists private;

revoke all on schema private from public;
revoke all on schema private from anon, authenticated;

comment on schema private is
    'Not exposed through the Data API. Authorization helpers and migration bookkeeping only.';

-- ---------------------------------------------------------------------
-- 2. Strip existing privileges from the API roles
-- ---------------------------------------------------------------------
-- USAGE on the schema stays: 5B.4 grants specific privileges on specific
-- tables, and those grants are unreachable without it. USAGE alone exposes
-- nothing — a role still needs a table privilege AND an RLS policy.
revoke all on schema public from anon, authenticated;
grant usage on schema public to anon, authenticated;

revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke all on all functions in schema public from anon, authenticated;
revoke all on all routines in schema public from anon, authenticated;

-- PostgreSQL grants EXECUTE on new functions to PUBLIC unless told
-- otherwise, and PUBLIC includes anon. 5B.5's functions are the whole
-- write path, so this default is not one to leave in place.
revoke execute on all functions in schema public from public;
revoke create on schema public from public;

-- ---------------------------------------------------------------------
-- 3. Strip the *default* privileges, so new objects start denied too
-- ---------------------------------------------------------------------
-- All application migrations run as postgres. The managed supabase_admin
-- role belongs to the platform: postgres cannot alter its defaults. Do not
-- attempt to elevate privileges or silently ignore a failure for our creator.
alter default privileges for role postgres in schema public
    revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public
    revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public
    revoke execute on functions from anon, authenticated;
-- A per-schema REVOKE cannot subtract the global built-in PUBLIC EXECUTE
-- default. Remove it globally, including for helpers created in private.
alter default privileges for role postgres
    revoke execute on functions from public, anon, authenticated;
