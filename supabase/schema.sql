-- Pravrudhi — Supabase schema for the multi-user surface.
--
-- Scope (Amendment, docs/superpowers/specs/2026-09-05-pravrudhi-multitenant-design.md):
-- a workspace's ledger and everything under research/ stays on disk at
-- ${PRAVRUDHI_WORKSPACES}/<user_id>/<slug>/. Supabase holds ONLY identity,
-- preferences, notes, chat threads, tool grants and the user -> workspace
-- mapping. No ledger row is ever copied in here.
--
-- RLS pattern copied from /home/ss/projects/kundali/supabase/schema.sql:
-- a user_id column with `auth.uid() = user_id` policies; `is_admin(uid)` as
-- a security-definer function, to avoid the self-referential-RLS-recursion
-- problem a normal (RLS-checked) query against a roles table would hit;
-- admin RPCs because auth.users is not exposed over PostgREST.
--
-- Apply with the Supabase CLI (`supabase db push`) or the dashboard SQL
-- editor. See README.md for what each table is for and what is deliberately
-- not here.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- app_role / user_roles — this account's role. Every signup defaults to
-- 'user'; promotion to 'admin' happens only through admin_set_role_by_email
-- below, never by a direct insert of a role someone chose for themselves.
-- ---------------------------------------------------------------------------
do $$ begin
  create type public.app_role as enum ('user', 'admin');
exception
  when duplicate_object then null;
end $$;

create table if not exists public.user_roles (
  user_id    uuid primary key references auth.users (id) on delete cascade,
  role       public.app_role not null default 'user',
  -- `on delete set null`, not cascade/restrict: deleting the admin who
  -- granted a role must not block deleting them, nor delete the grantee's
  -- row.
  added_by   uuid references auth.users (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.user_roles enable row level security;

-- security-definer helper avoids the self-referential-RLS-recursion problem
-- that would occur if the "is caller an admin" check were itself a normal
-- (RLS-checked) query against user_roles.
create or replace function public.is_admin(uid uuid)
returns boolean
language sql
security definer
set search_path = public
stable
as $$
  select exists (
    select 1 from public.user_roles where user_id = uid and role = 'admin'
  );
$$;

drop policy if exists "user_roles owner select" on public.user_roles;
create policy "user_roles owner select"
  on public.user_roles for select
  using (auth.uid() = user_id);

drop policy if exists "user_roles admin all" on public.user_roles;
create policy "user_roles admin all"
  on public.user_roles for all
  using (public.is_admin(auth.uid()))
  with check (public.is_admin(auth.uid()));

-- New signups default to 'user' automatically.
create or replace function public.handle_new_user_role()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.user_roles (user_id, role) values (new.id, 'user')
  on conflict (user_id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created_role on auth.users;
create trigger on_auth_user_created_role
  after insert on auth.users
  for each row execute function public.handle_new_user_role();

-- ---------------------------------------------------------------------------
-- profiles — identity. One row per auth.users row, kept in sync by trigger
-- rather than assigned by the client, so a user cannot claim someone else's
-- auth id or forge their own signup email.
-- ---------------------------------------------------------------------------
create table if not exists public.profiles (
  id           uuid primary key references auth.users (id) on delete cascade,
  email        text,
  display_name text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

alter table public.profiles enable row level security;

drop policy if exists "profiles owner select" on public.profiles;
create policy "profiles owner select"
  on public.profiles for select
  using (auth.uid() = id);

drop policy if exists "profiles owner update" on public.profiles;
create policy "profiles owner update"
  on public.profiles for update
  using (auth.uid() = id)
  with check (auth.uid() = id);

create or replace function public.handle_new_user_profile()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email) values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created_profile on auth.users;
create trigger on_auth_user_created_profile
  after insert on auth.users
  for each row execute function public.handle_new_user_profile();

-- ---------------------------------------------------------------------------
-- workspaces — the user -> workspace-slug mapping (application/workspaces.py).
-- The slug check mirrors ID_RE in application/objectives.py exactly: lower-
-- case letters, digits and hyphens, 2-63 characters, so a row this table
-- accepts is a slug workspace_dir() also accepts. The ledger and every other
-- artifact of the workspace itself stay on disk; this row only records that
-- the mapping exists.
-- ---------------------------------------------------------------------------
create table if not exists public.workspaces (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users (id) on delete cascade,
  slug       text not null check (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  created_at timestamptz not null default now(),
  unique (user_id, slug)
);

create index if not exists workspaces_user_id_idx on public.workspaces (user_id);

alter table public.workspaces enable row level security;

drop policy if exists "workspaces owner all" on public.workspaces;
create policy "workspaces owner all"
  on public.workspaces for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- preferences — an append-only log of Preference (application/memory.py):
-- key, value, source, set_at. The latest set_at per key wins on read, the
-- same rule the local JSONL store's `preferences()` applies, so the history
-- of a changed mind stays on record instead of being overwritten.
-- ---------------------------------------------------------------------------
create table if not exists public.preferences (
  id      uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  key     text not null,
  value   jsonb not null,
  source  text not null,
  set_at  timestamptz not null default now()
);

create index if not exists preferences_user_key_idx
  on public.preferences (user_id, key, set_at desc);

alter table public.preferences enable row level security;

drop policy if exists "preferences owner all" on public.preferences;
create policy "preferences owner all"
  on public.preferences for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- memory_notes — durable facts a user asked to remember; the MemoryNote
-- shape from application/memory.py (text, source, created), scoped to the
-- workspace the note was recorded in. `remember()`'s refusal of bare numeric
-- claims about ledger results is enforced in application code, not here:
-- this table has no view onto any ledger to check a claim against.
-- ---------------------------------------------------------------------------
create table if not exists public.memory_notes (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users (id) on delete cascade,
  workspace_slug text not null check (workspace_slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  text           text not null,
  source         text not null,
  created_at     timestamptz not null default now()
);

create index if not exists memory_notes_user_workspace_idx
  on public.memory_notes (user_id, workspace_slug, created_at desc);

alter table public.memory_notes enable row level security;

drop policy if exists "memory_notes owner all" on public.memory_notes;
create policy "memory_notes owner all"
  on public.memory_notes for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- chat_threads / chat_turns — the conversation, not a ledger fact (the
-- ChatThread/ChatTurn shapes from application/memory.py). chat_turns is a
-- child of chat_threads, so its policy joins back to the thread's owner the
-- same way kundali's life_events policy joins back to birth_profiles.
-- ---------------------------------------------------------------------------
create table if not exists public.chat_threads (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users (id) on delete cascade,
  workspace_slug text check (workspace_slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create index if not exists chat_threads_user_id_idx on public.chat_threads (user_id);

alter table public.chat_threads enable row level security;

drop policy if exists "chat_threads owner all" on public.chat_threads;
create policy "chat_threads owner all"
  on public.chat_threads for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create table if not exists public.chat_turns (
  id         uuid primary key default gen_random_uuid(),
  thread_id  uuid not null references public.chat_threads (id) on delete cascade,
  role       text not null check (role in ('user', 'assistant')),
  content    text not null,
  created_at timestamptz not null default now()
);

create index if not exists chat_turns_thread_created_idx
  on public.chat_turns (thread_id, created_at);

alter table public.chat_turns enable row level security;

drop policy if exists "chat_turns owner all" on public.chat_turns;
create policy "chat_turns owner all"
  on public.chat_turns for all
  using (
    exists (
      select 1 from public.chat_threads t
      where t.id = chat_turns.thread_id
        and t.user_id = auth.uid()
    )
  )
  with check (
    exists (
      select 1 from public.chat_threads t
      where t.id = chat_turns.thread_id
        and t.user_id = auth.uid()
    )
  );

-- ---------------------------------------------------------------------------
-- tool_grants — which tools a user's account may call. An owner can see
-- their own grants but not create one for themselves; only an admin can
-- grant or revoke, so this is the tool_grants counterpart to user_roles'
-- owner-select-plus-admin-all split above.
-- ---------------------------------------------------------------------------
create table if not exists public.tool_grants (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users (id) on delete cascade,
  tool_id    text not null,
  granted_at timestamptz not null default now(),
  unique (user_id, tool_id)
);

create index if not exists tool_grants_user_id_idx on public.tool_grants (user_id);

alter table public.tool_grants enable row level security;

drop policy if exists "tool_grants owner select" on public.tool_grants;
create policy "tool_grants owner select"
  on public.tool_grants for select
  using (auth.uid() = user_id);

drop policy if exists "tool_grants admin all" on public.tool_grants;
create policy "tool_grants admin all"
  on public.tool_grants for all
  using (public.is_admin(auth.uid()))
  with check (public.is_admin(auth.uid()));

-- ---------------------------------------------------------------------------
-- admin_set_role_by_email — `auth.users` isn't exposed over PostgREST, so
-- "look up a user by email and set their role" for the admin UI goes through
-- this security-definer function instead.
--
-- Bootstrap: when this is called with no JWT context at all (the dashboard
-- SQL editor, not a client request through PostgREST), `auth.uid()` is null
-- and the admin check is skipped — that is what lets the very first admin be
-- promoted before any admin row exists. Any call carrying a real user's JWT
-- (`auth.uid()` not null) must already belong to an admin.
-- ---------------------------------------------------------------------------
create or replace function public.admin_set_role_by_email(target_email text, new_role public.app_role)
returns void
language plpgsql
security definer
set search_path = public, auth
as $$
declare
  target_id uuid;
begin
  if auth.uid() is not null and not public.is_admin(auth.uid()) then
    raise exception 'not authorized';
  end if;
  select id into target_id from auth.users where email = target_email;
  if target_id is null then
    raise exception 'no user found with that email';
  end if;
  insert into public.user_roles (user_id, role, added_by)
  values (target_id, new_role, auth.uid())
  on conflict (user_id) do update
    set role = excluded.role, added_by = excluded.added_by, updated_at = now();
end;
$$;

grant execute on function public.admin_set_role_by_email(text, public.app_role) to authenticated;

-- ---------------------------------------------------------------------------
-- chat_turns.meta — a turn's citations, refusals and tool calls (the honesty
-- pass's receipt from application/chat.py::converse), empty for a user's
-- turn. Added after chat_turns already shipped, so it is additive: a row
-- written before this column existed reads back as '{}'::jsonb, not null.
-- ---------------------------------------------------------------------------
alter table public.chat_turns add column if not exists meta jsonb not null default '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- memory_notes.revised_at — when a note was last edited, null until it is.
-- A note the user can write but never correct goes stale and stays stale, so
-- the store gained an in-place revision (application/memory.py::revise) after
-- the table shipped. Additive: a row written before this column existed reads
-- back as null, which is exactly "never edited".
-- ---------------------------------------------------------------------------
alter table public.memory_notes add column if not exists revised_at timestamptz;
