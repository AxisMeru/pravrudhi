-- #1038 PR-4: cold-start coordination rows (engine 0.6.31). ADDITIVE: three new tables and ten functions in the existing `tenancy`
-- schema; no existing object is read, altered or dropped. Apply as a reviewed migration (never from a console); the matching
-- down-migration is supabase/wake_migration_v1_down.sql and drops ONLY these objects.
--
-- What is stored: coordination only. A judge-wake request (label first|second, kind wake|extend, an opaque lease reference the
-- waker returns, timestamps, the waker's answer), a replay nonce, and a per-user wake counter (user id, start times of the last
-- 24 h, cooldown). NEVER facts, results, paper text, contract ids or any screening input or output. Retention: requests 900 s,
-- nonces 180 s, counters 24 h after last activity; every function ignores an expired row and every write purges (bounded).
-- Access: RLS on, no policy, ALL privileges revoked from PUBLIC/anon/authenticated; the engine's narrow login role (tenancy_rpc)
-- may only EXECUTE the functions below (SECURITY DEFINER, fixed search_path). All times are the database clock, in epoch seconds.

begin;

create schema if not exists tenancy;

create table if not exists tenancy.wake_requests (
  nonce text primary key check (nonce ~ '^[0-9a-f]{32}$'),
  label text not null check (label in ('first', 'second')),
  kind text not null check (kind in ('wake', 'extend')),
  lease_ref text check (lease_ref is null or (char_length(lease_ref) between 1 and 200 and position('|' in lease_ref) = 0)),
  created_at double precision not null,
  expires_at double precision not null,
  answer text check (answer is null or answer in ('granted', 'already_awake', 'refused', 'failed')),
  answer_lease text check (answer_lease is null or (char_length(answer_lease) between 1 and 200 and position('|' in answer_lease) = 0)),
  answered_at double precision
);
create index if not exists wake_requests_label_idx on tenancy.wake_requests (label, kind, created_at desc);
create index if not exists wake_requests_expires_idx on tenancy.wake_requests (expires_at);

create table if not exists tenancy.wake_nonces (
  nonce text primary key check (nonce ~ '^[0-9a-f]{32}$'),
  expires_at double precision not null
);
create index if not exists wake_nonces_expires_idx on tenancy.wake_nonces (expires_at);

create table if not exists tenancy.wake_limits (
  user_id text primary key check (char_length(user_id) between 1 and 128),
  starts_json jsonb not null default '[]'::jsonb,
  active_since double precision,
  cool_until double precision not null default 0,
  expires_at double precision not null
);
create index if not exists wake_limits_expires_idx on tenancy.wake_limits (expires_at);

alter table tenancy.wake_requests enable row level security;
alter table tenancy.wake_nonces enable row level security;
alter table tenancy.wake_limits enable row level security;
revoke all on tenancy.wake_requests, tenancy.wake_nonces, tenancy.wake_limits from public;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on tenancy.wake_requests, tenancy.wake_nonces, tenancy.wake_limits from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on tenancy.wake_requests, tenancy.wake_nonces, tenancy.wake_limits from authenticated;
  end if;
end $$;

create or replace function tenancy._wake_now() returns double precision
language sql set search_path = pg_catalog as $$ select extract(epoch from clock_timestamp())::double precision $$;

-- Bounded purge of expired rows (called by every write; never by a read).
create or replace function tenancy._wake_purge() returns void
language plpgsql security definer set search_path = pg_catalog, tenancy as $$
declare t double precision := tenancy._wake_now();
begin
  delete from tenancy.wake_requests where nonce in (select nonce from tenancy.wake_requests where expires_at < t limit 200);
  delete from tenancy.wake_nonces where nonce in (select nonce from tenancy.wake_nonces where expires_at < t limit 200);
  delete from tenancy.wake_limits where user_id in (select user_id from tenancy.wake_limits where expires_at < t limit 200);
end $$;

-- ONE live wake request per judge: an unanswered request younger than p_req_ttl, or a granted one younger than p_budget, is reused.
create or replace function tenancy.wake_ensure(
  p_label text, p_nonce text, p_budget double precision, p_req_ttl double precision, p_row_ttl double precision
) returns jsonb language plpgsql security definer set search_path = pg_catalog, tenancy as $$
declare t double precision := tenancy._wake_now(); r tenancy.wake_requests%rowtype;
begin
  if p_label not in ('first', 'second') or p_nonce is null or p_nonce !~ '^[0-9a-f]{32}$'
     or p_budget is null or p_budget < 1 or p_budget > 3600 or p_req_ttl is null or p_req_ttl < 1 or p_req_ttl > 3600
     or p_row_ttl is null or p_row_ttl < 60 or p_row_ttl > 86400 then
    raise exception 'bad wake request' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtext('pravrudhi-wake-' || p_label));
  perform tenancy._wake_purge();
  select * into r from tenancy.wake_requests
    where label = p_label and kind = 'wake' and expires_at > t order by created_at desc limit 1;
  if found and t - r.created_at < p_budget
     and ((r.answer is null and t - r.created_at <= p_req_ttl) or r.answer in ('granted', 'already_awake')) then
    return jsonb_build_object('reused', true);
  end if;
  insert into tenancy.wake_requests (nonce, label, kind, created_at, expires_at) values (p_nonce, p_label, 'wake', t, t + p_row_ttl);
  return jsonb_build_object('reused', false);
end $$;

-- An extend request for a lease the waker granted. Rate-deduped: at most one extend row per judge per 30 s.
create or replace function tenancy.wake_extend(
  p_label text, p_nonce text, p_lease text, p_req_ttl double precision, p_row_ttl double precision
) returns jsonb language plpgsql security definer set search_path = pg_catalog, tenancy as $$
declare t double precision := tenancy._wake_now();
begin
  if p_label not in ('first', 'second') or p_nonce is null or p_nonce !~ '^[0-9a-f]{32}$'
     or p_lease is null or char_length(p_lease) not between 1 and 200 or position('|' in p_lease) > 0
     or p_req_ttl is null or p_req_ttl < 1 or p_req_ttl > 3600 or p_row_ttl is null or p_row_ttl < 60 or p_row_ttl > 86400 then
    raise exception 'bad extend request' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtext('pravrudhi-wake-' || p_label));
  perform tenancy._wake_purge();
  if exists (select 1 from tenancy.wake_requests where label = p_label and kind = 'extend' and expires_at > t and t - created_at < 30) then
    return jsonb_build_object('deduped', true);
  end if;
  insert into tenancy.wake_requests (nonce, label, kind, lease_ref, created_at, expires_at)
    values (p_nonce, p_label, 'extend', p_lease, t, t + p_row_ttl);
  return jsonb_build_object('deduped', false);
end $$;

-- What the box-side waker polls: unanswered requests younger than p_req_ttl, oldest first (a READ: no purge, no write).
create or replace function tenancy.wake_open(p_limit double precision, p_req_ttl double precision)
returns jsonb language plpgsql security definer set search_path = pg_catalog, tenancy as $$
declare t double precision := tenancy._wake_now(); out jsonb;
begin
  if p_limit is null or p_limit < 1 or p_limit > 50 or p_req_ttl is null or p_req_ttl < 1 or p_req_ttl > 3600 then
    raise exception 'bad wake list' using errcode = '22023';
  end if;
  select coalesce(jsonb_agg(jsonb_build_object('nonce', nonce, 'label', label, 'kind', kind, 'lease_ref', lease_ref) order by created_at), '[]'::jsonb)
    into out from (
      select nonce, label, kind, lease_ref, created_at from tenancy.wake_requests
      where answer is null and expires_at > t and t - created_at <= p_req_ttl order by created_at limit p_limit::int
    ) q;
  return out;
end $$;

-- The waker's answers: [{nonce, state, lease_ref}], at most 20. An unknown, expired, stale or already-answered request is ignored.
create or replace function tenancy.wake_deliver(p_results text, p_req_ttl double precision)
returns jsonb language plpgsql security definer set search_path = pg_catalog, tenancy as $$
declare t double precision := tenancy._wake_now(); items jsonb; it jsonb; n int := 0; c int; st text; ls text; nc text;
begin
  if p_req_ttl is null or p_req_ttl < 1 or p_req_ttl > 3600 or p_results is null or char_length(p_results) > 4096 then
    raise exception 'bad wake result' using errcode = '22023';
  end if;
  items := p_results::jsonb;
  if jsonb_typeof(items) <> 'array' then
    return jsonb_build_object('applied', 0);
  end if;
  for it in select value from jsonb_array_elements(items) limit 20 loop
    if jsonb_typeof(it) <> 'object' then continue; end if;
    nc := it ->> 'nonce'; st := it ->> 'state'; ls := it ->> 'lease_ref';
    if nc is null or nc !~ '^[0-9a-f]{32}$' or st is null or st not in ('granted', 'already_awake', 'refused', 'failed') then continue; end if;
    if ls is not null and (char_length(ls) not between 1 and 200 or position('|' in ls) > 0) then ls := null; end if;
    update tenancy.wake_requests set answer = st, answer_lease = ls, answered_at = t
      where nonce = nc and answer is null and expires_at > t and t - created_at <= p_req_ttl;
    get diagnostics c = row_count;
    n := n + c;
  end loop;
  return jsonb_build_object('applied', n);
end $$;

-- The latest wake request of a judge: its answer (an unanswered one older than p_req_ttl reads as failed) and its age in seconds.
create or replace function tenancy.wake_state(p_label text, p_req_ttl double precision)
returns jsonb language plpgsql security definer set search_path = pg_catalog, tenancy as $$
declare t double precision := tenancy._wake_now(); r tenancy.wake_requests%rowtype;
begin
  if p_label not in ('first', 'second') or p_req_ttl is null or p_req_ttl < 1 or p_req_ttl > 3600 then
    raise exception 'bad wake state' using errcode = '22023';
  end if;
  select * into r from tenancy.wake_requests where label = p_label and kind = 'wake' and expires_at > t order by created_at desc limit 1;
  if not found then return jsonb_build_object('found', false); end if;
  return jsonb_build_object('found', true,
    'answer', case when r.answer is null and t - r.created_at > p_req_ttl then 'failed' else r.answer end,
    'age_s', t - r.created_at);
end $$;

-- The lease reference the waker last granted for a judge (what an extend carries), unexpired only.
create or replace function tenancy.wake_lease(p_label text)
returns jsonb language plpgsql security definer set search_path = pg_catalog, tenancy as $$
declare t double precision := tenancy._wake_now(); l text;
begin
  if p_label not in ('first', 'second') then raise exception 'bad wake lease' using errcode = '22023'; end if;
  select answer_lease into l from tenancy.wake_requests
    where label = p_label and kind = 'wake' and answer in ('granted', 'already_awake') and answer_lease is not null and expires_at > t
    order by answered_at desc limit 1;
  return jsonb_build_object('lease', l);
end $$;

-- Replay guard: true exactly once per nonce across every container (a unique-key insert), valid for p_ttl seconds.
create or replace function tenancy.wake_nonce(p_nonce text, p_ttl double precision)
returns jsonb language plpgsql security definer set search_path = pg_catalog, tenancy as $$
declare t double precision := tenancy._wake_now(); c int;
begin
  if p_nonce is null or p_nonce !~ '^[0-9a-f]{32}$' or p_ttl is null or p_ttl < 10 or p_ttl > 3600 then
    raise exception 'bad wake nonce' using errcode = '22023';
  end if;
  perform tenancy._wake_purge();
  delete from tenancy.wake_nonces where nonce = p_nonce and expires_at < t;
  insert into tenancy.wake_nonces (nonce, expires_at) values (p_nonce, t + p_ttl) on conflict (nonce) do nothing;
  get diagnostics c = row_count;
  return jsonb_build_object('first', c = 1);
end $$;

-- One wake at a time per user, a daily cap that counts failed wakes, a cooldown after a failed one. The user's row is locked
-- for the whole decision, so two containers cannot both start a wake. result: new | existing | cooldown | limit.
create or replace function tenancy.wake_begin(
  p_user text, p_per_day double precision, p_cooldown double precision, p_active_ttl double precision, p_row_ttl double precision
) returns jsonb language plpgsql security definer set search_path = pg_catalog, tenancy as $$
declare t double precision := tenancy._wake_now(); l tenancy.wake_limits%rowtype; starts jsonb; act double precision; cool double precision; res text; stamp double precision;
begin
  if p_user is null or char_length(p_user) not between 1 and 128 or p_per_day is null or p_per_day < 1 or p_per_day > 50
     or p_cooldown is null or p_cooldown < 0 or p_cooldown > 3600 or p_active_ttl is null or p_active_ttl < 30 or p_active_ttl > 3600
     or p_row_ttl is null or p_row_ttl < 3600 or p_row_ttl > 172800 then
    raise exception 'bad wake limit' using errcode = '22023';
  end if;
  perform tenancy._wake_purge();
  insert into tenancy.wake_limits (user_id, expires_at) values (p_user, t + p_row_ttl) on conflict (user_id) do nothing;
  select * into l from tenancy.wake_limits where user_id = p_user for update;
  select coalesce(jsonb_agg(v), '[]'::jsonb) into starts from jsonb_array_elements(l.starts_json) v where (v #>> '{}')::double precision > t - 86400;
  act := l.active_since; cool := l.cool_until;
  if act is not null and t - act > p_active_ttl then act := null; cool := t + p_cooldown; end if;  -- a dead wake is released as failed
  if act is not null then res := 'existing'; stamp := act;
  elsif cool > t then res := 'cooldown'; stamp := null;
  elsif jsonb_array_length(starts) >= p_per_day then res := 'limit'; stamp := null;
  else res := 'new'; stamp := t; act := t; starts := starts || to_jsonb(t);
  end if;
  update tenancy.wake_limits set starts_json = starts, active_since = act, cool_until = cool, expires_at = t + p_row_ttl where user_id = p_user;
  return jsonb_build_object('result', res, 'stamp', stamp);
end $$;

-- The stamp and age of the user's active wake, or null (none, or older than p_active_ttl). A read.
create or replace function tenancy.wake_active(p_user text, p_active_ttl double precision)
returns jsonb language plpgsql security definer set search_path = pg_catalog, tenancy as $$
declare t double precision := tenancy._wake_now(); act double precision;
begin
  if p_user is null or char_length(p_user) not between 1 and 128 or p_active_ttl is null or p_active_ttl < 30 or p_active_ttl > 3600 then
    raise exception 'bad wake active' using errcode = '22023';
  end if;
  select active_since into act from tenancy.wake_limits where user_id = p_user and expires_at > t;
  if act is not null and t - act > p_active_ttl then act := null; end if;
  return jsonb_build_object('stamp', act, 'age_s', case when act is null then null else t - act end);
end $$;

-- Release the user's wake. With a stamp, only if THAT wake is still the active one (a late end must not pop a newer wake).
create or replace function tenancy.wake_end(p_user text, p_ok boolean, p_stamp double precision, p_cooldown double precision)
returns jsonb language plpgsql security definer set search_path = pg_catalog, tenancy as $$
declare t double precision := tenancy._wake_now(); l tenancy.wake_limits%rowtype;
begin
  if p_user is null or char_length(p_user) not between 1 and 128 or p_ok is null or p_cooldown is null or p_cooldown < 0 or p_cooldown > 3600 then
    raise exception 'bad wake end' using errcode = '22023';
  end if;
  select * into l from tenancy.wake_limits where user_id = p_user for update;
  if not found then return jsonb_build_object('applied', false); end if;
  if p_stamp is not null and (l.active_since is null or abs(l.active_since - p_stamp) > 0.001) then
    return jsonb_build_object('applied', false);
  end if;
  update tenancy.wake_limits set active_since = null,
    cool_until = case when not p_ok and p_cooldown > 0 then t + p_cooldown else cool_until end where user_id = p_user;
  return jsonb_build_object('applied', true);
end $$;

-- The functions are the only door: nobody but the engine's login role may run them.
revoke all on function tenancy._wake_now() from public;
revoke all on function tenancy._wake_purge() from public;
revoke all on function tenancy.wake_ensure(text, text, double precision, double precision, double precision) from public;
revoke all on function tenancy.wake_extend(text, text, text, double precision, double precision) from public;
revoke all on function tenancy.wake_open(double precision, double precision) from public;
revoke all on function tenancy.wake_deliver(text, double precision) from public;
revoke all on function tenancy.wake_state(text, double precision) from public;
revoke all on function tenancy.wake_lease(text) from public;
revoke all on function tenancy.wake_nonce(text, double precision) from public;
revoke all on function tenancy.wake_begin(text, double precision, double precision, double precision, double precision) from public;
revoke all on function tenancy.wake_active(text, double precision) from public;
revoke all on function tenancy.wake_end(text, boolean, double precision, double precision) from public;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'tenancy_rpc') then
    grant usage on schema tenancy to tenancy_rpc;
    grant execute on function tenancy.wake_ensure(text, text, double precision, double precision, double precision) to tenancy_rpc;
    grant execute on function tenancy.wake_extend(text, text, text, double precision, double precision) to tenancy_rpc;
    grant execute on function tenancy.wake_open(double precision, double precision) to tenancy_rpc;
    grant execute on function tenancy.wake_deliver(text, double precision) to tenancy_rpc;
    grant execute on function tenancy.wake_state(text, double precision) to tenancy_rpc;
    grant execute on function tenancy.wake_lease(text) to tenancy_rpc;
    grant execute on function tenancy.wake_nonce(text, double precision) to tenancy_rpc;
    grant execute on function tenancy.wake_begin(text, double precision, double precision, double precision, double precision) to tenancy_rpc;
    grant execute on function tenancy.wake_active(text, double precision) to tenancy_rpc;
    grant execute on function tenancy.wake_end(text, boolean, double precision, double precision) to tenancy_rpc;
  end if;
end $$;

commit;
