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
commit;
