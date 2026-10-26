-- 6B.1: node-scoped read policies evaluated once per query instead of once per row.
-- The rule is unchanged from private.can_read_node(node_id): administrators read every node
-- (including rows without one); other operators read the approved nodes they hold an approved
-- membership of. Called per row, the old form took 26.7 s for page 200 of all alerts and 9.6 s
-- for an analyst asking for another node, at 130,000 alerts (docs/migration/06b-operations.md).
create function private.readable_node_ids() returns uuid[]
language sql stable security definer set search_path = '' as $$
    select coalesce(array_agg(m.node_id), '{}'::uuid[])
    from public.node_membership m join public.node n using (node_id)
    where private.is_operator() and m.profile_id = private.current_profile_id()
      and m.status = 'approved' and n.status = 'approved'
$$;
grant create on schema private to algoguard_identity_owner;
alter function private.readable_node_ids() owner to algoguard_identity_owner;
revoke create on schema private from algoguard_identity_owner;
revoke all on function private.readable_node_ids() from public, anon;
grant execute on function private.readable_node_ids() to authenticated;

-- "(select ...)" makes each an InitPlan: computed once for the statement, not per row.
alter policy node_read on public.alert using
    ((select private.is_administrator()) or node_id = any ((select private.readable_node_ids())::uuid[]));
alter policy node_read on public.capture_session using
    ((select private.is_administrator()) or node_id = any ((select private.readable_node_ids())::uuid[]));
alter policy node_read on public.ingest_event using
    ((select private.is_administrator()) or node_id = any ((select private.readable_node_ids())::uuid[]));
alter policy node_read on public.network_traffic using
    ((select private.is_administrator()) or node_id = any ((select private.readable_node_ids())::uuid[]));
alter policy node_read on public.node using
    ((select private.is_administrator()) or node_id = any ((select private.readable_node_ids())::uuid[]));
alter policy node_read on public.node_health using
    ((select private.is_administrator()) or node_id = any ((select private.readable_node_ids())::uuid[]));
alter policy node_read on public.prediction using
    ((select private.is_administrator()) or node_id = any ((select private.readable_node_ids())::uuid[]));
alter policy node_read on public.report using
    ((select private.is_administrator()) or node_id = any ((select private.readable_node_ids())::uuid[]));
alter policy log_read on public.system_log using
    ((select private.is_administrator()) or node_id = any ((select private.readable_node_ids())::uuid[]));
