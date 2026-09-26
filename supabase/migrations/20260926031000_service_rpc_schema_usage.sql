-- Hosted administration executes only its explicitly granted RPC. Do not rely
-- on platform bootstrap defaults for resolving that RPC after schema restore.
grant usage on schema public to service_role;
