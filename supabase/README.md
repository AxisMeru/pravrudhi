# Pravrudhi Supabase schema

This schema backs Pravrudhi's hosted multi-user surface only. A local, single-user install
(`pravrudhi app` run on your own machine) never touches Supabase and never sees a login screen —
see `docs/superpowers/specs/2026-09-05-pravrudhi-multitenant-design.md`, Shape (a).

## Applying it

Either:

- **Supabase CLI**: `supabase link` to your project, then `supabase db push` (or paste the file
  into a new migration under `supabase/migrations/` first, if you want it tracked as one).
- **Dashboard SQL editor**: open the project's SQL editor, paste the full contents of
  `schema.sql`, and run it. Every statement is `create ... if not exists` / `create or replace`,
  so re-running the file after a partial failure is safe.

## What each table is for

- **`user_roles`** — this account's role (`user` or `admin`). Every signup defaults to `user` via
  a trigger on `auth.users`; only `admin_set_role_by_email` (below) can promote one to `admin`.
- **`profiles`** — identity: one row per `auth.users` row, created by trigger so a user can't
  forge someone else's id or email.
- **`workspaces`** — the mapping from a user to the workspace slugs they've created. The
  workspace's ledger and everything else under its `research/` directory stays on disk at
  `${PRAVRUDHI_WORKSPACES}/<user_id>/<slug>/`; this table only records that the slug exists for
  that user, matching the slug rule `application/objectives.py::ID_RE` already enforces locally.
- **`preferences`** — an append-only log of `Preference` (`application/memory.py`): a key/value a
  user set, with who set it and when. The latest `set_at` per key is what a reader uses; older
  rows are kept, not overwritten.
- **`memory_notes`** — durable facts a user asked to remember (`MemoryNote`), scoped to the
  workspace they were recorded in.
- **`chat_threads` / `chat_turns`** — conversation history (`ChatThread` / `ChatTurn`). Turns are
  a child of threads; a turn's owner is whoever owns its thread.
- **`tool_grants`** — which tools a user's account may call. An owner can see their own grants;
  only an admin can create or revoke one.

All tables have row level security enabled and owner-only policies (`auth.uid() = user_id`, or a
join to the owning row for a child table). `user_roles` and `tool_grants` additionally allow an
admin full access, via the security-definer `is_admin(uid)` — a normal RLS-checked query against
`user_roles` inside its own policy would recurse, which is why that check has to live outside RLS.

## What is deliberately NOT here

No ledger row, candidate, objective, observation, or anything else the kernel's hash chain
already covers. Per the Amendment in the multitenant design doc: a workspace's ledger lives only
on disk, one file per workspace, because a hash chain proves a sequence of events wasn't altered
but proves nothing about who honestly set a `user_id` column inside one. Supabase holds identity,
preferences, notes, chat threads, tool grants, and the user → workspace mapping — never a copy of
evidence. If Supabase and a workspace's ledger ever disagree about something the ledger covers,
the ledger wins; nothing in this schema is a second source of truth for it.

## Promoting the first admin

The schema ships with no admin, no hardcoded uuid, and no hardcoded email — every account starts
as `user`. After applying the schema, the operator promotes their own account by running, in the
dashboard SQL editor (not through a client library, so the call carries no JWT and the bootstrap
path in `admin_set_role_by_email` applies):

```sql
select public.admin_set_role_by_email('you@example.com', 'admin');
```

The target account must already exist (i.e. have signed up at least once) before this call, since
the function looks it up by email in `auth.users`. Once one admin exists, every subsequent call to
`admin_set_role_by_email` — including further promotions — must be made by an authenticated admin;
a non-admin caller gets `not authorized`.
