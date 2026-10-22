-- Down-migration of wake_migration_v1.sql: drops ONLY the cold-start coordination objects (three tables, twelve functions).
begin;
drop function if exists tenancy.wake_end(text, boolean, double precision, double precision);
drop function if exists tenancy.wake_active(text, double precision);
drop function if exists tenancy.wake_begin(text, double precision, double precision, double precision, double precision);
drop function if exists tenancy.wake_nonce(text, double precision);
drop function if exists tenancy.wake_lease(text);
drop function if exists tenancy.wake_state(text, double precision);
drop function if exists tenancy.wake_deliver(text, double precision);
drop function if exists tenancy.wake_open(double precision, double precision);
drop function if exists tenancy.wake_extend(text, text, text, double precision, double precision);
drop function if exists tenancy.wake_ensure(text, text, double precision, double precision, double precision);
drop function if exists tenancy._wake_purge();
drop function if exists tenancy._wake_now();
drop table if exists tenancy.wake_limits;
drop table if exists tenancy.wake_nonces;
drop table if exists tenancy.wake_requests;

-- restore the pre-v2 canary() body (the one live before 22 Oct, captured with pg_get_functiondef)
create or replace function tenancy.canary()
 returns jsonb
 language plpgsql
 security definer
 set search_path to 'pg_catalog', 'tenancy'
as $function$
declare tables_bad integer; fns_ok integer; fns_bad integer; schema_create boolean;
begin
  -- Any privilege on a tenancy table or sequence for the login role, the Supabase API roles or PUBLIC (an ACL entry for grantee 0).
  select count(*) into tables_bad from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'tenancy' and c.relkind in ('r', 'S', 'v', 'm', 'p')
     and (exists (select 1 from aclexplode(coalesce(c.relacl, acldefault((case when c.relkind = 'S' then 's' else 'r' end)::"char", c.relowner))) a
                  where a.grantee = 0 or a.grantee in (select oid from pg_roles where rolname in ('tenancy_rpc', 'anon', 'authenticated', 'service_role'))));
  -- EXECUTE on the tenancy functions: the login role has exactly the 16 granted ones; PUBLIC and the API roles have none.
  select count(*) into fns_ok from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'tenancy' and has_function_privilege('tenancy_rpc', p.oid, 'EXECUTE');
  select count(*) into fns_bad from pg_proc p join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'tenancy' and exists (select 1 from aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                                           where a.privilege_type = 'EXECUTE' and (a.grantee = 0 or a.grantee in (select oid from pg_roles where rolname in ('anon', 'authenticated', 'service_role'))));
  schema_create := has_schema_privilege('tenancy_rpc', 'tenancy', 'CREATE');
  return jsonb_build_object('ok', tables_bad = 0 and fns_ok = 16 and fns_bad = 0 and not schema_create, 'table_grants', tables_bad, 'functions_executable', fns_ok, 'functions_open_to_others', fns_bad,
    'orgs', (select count(*) from tenancy.org), 'keys', (select count(*) from tenancy.api_key));
end $function$;

commit;
