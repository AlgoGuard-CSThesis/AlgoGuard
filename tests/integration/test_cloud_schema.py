"""
tests/integration/test_cloud_schema.py

Stage 5B.1 evidence: the cloud schema, as applied to the local Supabase
stack, starts denied and stays structurally unable to link one node's
records to another's.

Requires:
    - Docker + `supabase start` already running
    - `supabase db reset` (applies supabase/migrations to the local stack)
    - `supabase status -o env > .env.supabase.local`
    - python -m pip install -r requirements-maintainer.txt

Run with:
    python -m pytest -m integration -v

Never point these tests at the cloud pilot project. Everything they write
happens inside a transaction that is rolled back, or is prefixed
`zz_migration_smoke_` and dropped.
"""

import uuid

import pytest
from stack_support import connect_local_database

psycopg2 = pytest.importorskip(
    "psycopg2",
    reason="integration lane needs: python -m pip install -r requirements-maintainer.txt",
)

pytestmark = pytest.mark.integration

# Tables that exist once the 5B.1 migrations are applied.
EXPECTED_TABLES = frozenset(
    {
        "profile",
        "user_role",
        "node",
        "node_membership",
        "training_run",
        "detection_model",
        "model_deployment",
        "capture_session",
        "network_traffic",
        "prediction",
        "alert",
        "report",
        "report_alert",
        "system_log",
        "ingest_event",
        "model_manifest",
        "deployment_activation_lock",
    }
)


@pytest.fixture()
def db(local_stack):
    connection = connect_local_database(psycopg2.connect, local_stack["db_url"])
    try:
        with connection.cursor() as cursor:
            cursor.execute("select to_regclass('public.profile') is not null")
            if not cursor.fetchone()[0]:
                pytest.skip(
                    "Cloud schema is not applied to the local stack. Run: supabase db reset"
                )
        yield connection
    finally:
        connection.rollback()
        connection.close()


def test_every_expected_table_exists(db):
    with db.cursor() as cursor:
        cursor.execute(
            "select relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind = 'r'"
        )
        present = {row[0] for row in cursor.fetchall()}
    assert EXPECTED_TABLES <= present, f"missing: {sorted(EXPECTED_TABLES - present)}"


def test_every_public_table_has_row_level_security(db):
    with db.cursor() as cursor:
        cursor.execute(
            "select relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity"
        )
        unprotected = [row[0] for row in cursor.fetchall()]
    assert unprotected == [], f"RLS is off on: {unprotected}"


def test_anon_holds_no_privileges_on_any_application_table(db):
    """A permanent invariant, not a stage-5B.1 one: an analyst is always
    an authenticated user, so `anon` never needs access to anything."""
    with db.cursor() as cursor:
        cursor.execute(
            "select table_name, privilege_type from information_schema.role_table_grants "
            "where table_schema = 'public' and grantee = 'anon'"
        )
        grants = cursor.fetchall()
    assert grants == [], f"anon can reach: {grants}"


def test_any_table_granted_to_authenticated_also_has_policies(db):
    """In 5B.1 both sides are empty. From 5B.4 the grants arrive with
    their policies — this test fails if one ever arrives without the
    other, which is the shape of an accidental data leak."""
    with db.cursor() as cursor:
        cursor.execute(
            """
            select distinct g.table_name
            from information_schema.role_table_grants g
            where g.table_schema = 'public'
              and g.grantee = 'authenticated'
              and exists (select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace
                          where n.nspname=g.table_schema and c.relname=g.table_name
                            and c.relkind='r')
              and not exists (
                  select 1 from pg_policies p
                  where p.schemaname = 'public' and p.tablename = g.table_name
              )
            """
        )
        granted_without_policies = [row[0] for row in cursor.fetchall()]
    assert granted_without_policies == [], (
        f"granted to authenticated with no RLS policy: {granted_without_policies}"
    )


def test_a_newly_created_table_starts_denied(db):
    """Proves the default privileges were stripped, not just the tables
    that existed when the baseline migration ran."""
    table = f"zz_migration_smoke_{uuid.uuid4().hex[:8]}"
    db.rollback()
    db.autocommit = True
    with db.cursor() as cursor:
        cursor.execute(f"create table public.{table} (id bigint)")
        try:
            cursor.execute(
                "select grantee, privilege_type from information_schema.role_table_grants "
                "where table_schema = 'public' and table_name = %s "
                "and grantee in ('anon', 'authenticated')",
                (table,),
            )
            grants = cursor.fetchall()
        finally:
            cursor.execute(f"drop table public.{table}")
    db.autocommit = False
    assert grants == [], f"a new table was born reachable: {grants}"


def test_a_new_function_starts_denied(db):
    with db.cursor() as cursor:
        cursor.execute(
            "create function public.zz_migration_smoke_acl() returns int language sql as 'select 1'"
        )
        for role in ("anon", "authenticated"):
            cursor.execute(
                "select has_function_privilege(%s, 'public.zz_migration_smoke_acl()', 'execute')",
                (role,),
            )
            assert cursor.fetchone()[0] is False


def test_exposed_views_preserve_invoker_permissions(db):
    with db.cursor() as cursor:
        cursor.execute(
            "select relname from pg_class c join pg_namespace n on n.oid=c.relnamespace "
            "where n.nspname='public' and c.relkind='v' "
            "and not coalesce(c.reloptions @> array['security_invoker=true'],false)"
        )
        assert cursor.fetchall() == []


def test_definer_helpers_have_narrow_owner_and_pinned_path(db):
    with db.cursor() as cursor:
        cursor.execute(
            "select n.nspname,p.proname,p.proowner::regrole::text,p.proconfig "
            "from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
            "where n.nspname in ('public','private') and p.prosecdef"
        )
        functions = cursor.fetchall()
        assert functions
        for schema, name, owner, config in functions:
            assert owner == "algoguard_identity_owner", (schema, name, owner)
            assert 'search_path=""' in config or "search_path=" in config
        cursor.execute(
            "select rolcanlogin,rolsuper,rolbypassrls from pg_roles "
            "where rolname='algoguard_identity_owner'"
        )
        assert cursor.fetchone() == (False, False, False)


def test_service_can_resolve_only_explicitly_granted_administration_rpc(db):
    with db.cursor() as cursor:
        cursor.execute("select has_schema_privilege('service_role', 'public', 'usage')")
        assert cursor.fetchone()[0] is True
        cursor.execute(
            "select has_function_privilege('service_role', "
            "'public.administration_command(uuid,uuid,jsonb,uuid)', 'execute')"
        )
        assert cursor.fetchone()[0] is True
        cursor.execute(
            "select has_function_privilege('authenticated', "
            "'public.administration_command(uuid,uuid,jsonb,uuid)', 'execute')"
        )
        assert cursor.fetchone()[0] is False


def test_profile_stores_no_credentials(db):
    with db.cursor() as cursor:
        cursor.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'public' and table_name = 'profile'"
        )
        columns = {row[0] for row in cursor.fetchall()}
    assert "password_hash" not in columns
    assert not any("password" in column for column in columns)
    assert {"auth_user_id", "legacy_admin_id"} <= columns


def test_a_record_cannot_be_linked_across_nodes(db):
    """The structural half of node isolation: even with every privilege,
    a prediction cannot point at another node's traffic. Everything here
    is rolled back."""
    with db.cursor() as cursor:
        cursor.execute(
            "insert into public.profile (legacy_admin_id, username) "
            "values (900000001, %s) returning profile_id",
            (f"zz_migration_smoke_{uuid.uuid4().hex[:8]}",),
        )
        profile_id = cursor.fetchone()[0]

        node_a, node_b = uuid.uuid4(), uuid.uuid4()
        for node_id in (node_a, node_b):
            cursor.execute(
                "insert into public.node (node_id, display_name, status, approved_at, approved_by) "
                "values (%s, 'zz_migration_smoke node', 'approved', now(), %s)",
                (str(node_id), profile_id),
            )

        cursor.execute(
            "insert into public.network_traffic "
            '(node_id, owner_profile_id, "timestamp") '
            "values (%s, %s, '2026-01-01 00:00:00') returning traffic_id",
            (str(node_a), profile_id),
        )
        traffic_id = cursor.fetchone()[0]

        cursor.execute(
            "insert into public.training_run (filename, upload_timestamp) "
            "values ('zz_migration_smoke.csv', '2026-01-01 00:00:00') returning run_id"
        )
        run_id = cursor.fetchone()[0]
        cursor.execute(
            "insert into public.detection_model (run_id, model_name) "
            "values (%s, 'zz_migration_smoke') returning model_id",
            (run_id,),
        )
        model_id = cursor.fetchone()[0]

        cursor.execute("savepoint cross_node_attempt")
        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            cursor.execute(
                "insert into public.prediction "
                "(node_id, owner_profile_id, traffic_id, model_id, predicted_label, "
                " prediction_timestamp) "
                "values (%s, %s, %s, %s, 'Normal', '2026-01-01 00:00:01')",
                (str(node_b), profile_id, traffic_id, model_id),
            )
        cursor.execute("rollback to savepoint cross_node_attempt")

        # The same insert against its own node is accepted, so the test
        # above is about the node boundary and not about a broken insert.
        cursor.execute(
            "insert into public.prediction "
            "(node_id, owner_profile_id, traffic_id, model_id, predicted_label, "
            " prediction_timestamp) "
            "values (%s, %s, %s, %s, 'Normal', '2026-01-01 00:00:01')",
            (str(node_a), profile_id, traffic_id, model_id),
        )
    db.rollback()


def test_only_one_model_deployment_can_be_active(db):
    with db.cursor() as cursor:
        cursor.execute(
            "insert into public.training_run (filename, upload_timestamp) "
            "values ('zz_migration_smoke.csv', '2026-01-01 00:00:00') returning run_id"
        )
        run_id = cursor.fetchone()[0]
        cursor.execute(
            "insert into public.detection_model (run_id, model_name) "
            "values (%s, 'zz_migration_smoke') returning model_id",
            (run_id,),
        )
        model_id = cursor.fetchone()[0]

        insert = (
            "insert into public.model_deployment "
            "(model_id, run_id, artifact_path, deployed_at, is_active) "
            "values (%s, %s, %s, '2026-01-01 00:00:00', 1)"
        )
        cursor.execute(insert, (model_id, run_id, "zz_migration_smoke/a.joblib"))

        cursor.execute("savepoint second_active")
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cursor.execute(insert, (model_id, run_id, "zz_migration_smoke/b.joblib"))
        cursor.execute("rollback to savepoint second_active")
    db.rollback()


def test_data_api_refuses_an_anonymous_read(local_stack, local_http):
    """The same check from the outside: PostgREST with the publishable
    key must not return rows from any application table."""
    for table in ("profile", "node", "prediction", "alert", "system_log"):
        response = local_http.get(
            f"{local_stack['rest_url']}/{table}",
            params={"select": "*", "limit": "1"},
            headers={
                "apikey": local_stack["publishable_key"],
                "Authorization": f"Bearer {local_stack['publishable_key']}",
            },
        )
        assert response.status_code in (401, 403, 404), (
            f"anon read of {table} returned HTTP {response.status_code}"
        )
