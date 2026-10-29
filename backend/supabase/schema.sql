-- Verity: saved chats and each user's list of runs.
--
-- HOW TO USE: paste this whole file into the SQL Editor of the NEW Supabase project and press Run. It is safe to run
-- again: every statement is idempotent. Do not use the Supabase CLI and do not link a project.
--
-- Who reads and writes these tables: only Verity's server, with the project's secret key (service_role). The browser
-- never touches them: row level security is on with no policies, and anon and authenticated have no grants at all.
-- Every query the server makes names its owner, so a user only ever sees their own rows.
--
-- owner is "u:" + the Supabase user id for a signed-in user (or a session id for a judge access code).
-- Run files, events and spend ledgers do not live here: they stay in Verity's own SQLite database and disk.

create table if not exists public.threads (
  owner              text        not null,
  id                 uuid        not null,
  title              text,                              -- the first user message, cut to 60 characters; null until it exists
  facts              jsonb       not null default '{}'::jsonb,
  summary            text        not null default '',
  summarized_through integer     not null default 0,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  primary key (owner, id)
);

create table if not exists public.thread_turns (
  owner       text        not null,
  thread_id   uuid        not null,
  seq         integer     not null,                     -- a turn is two rows: the user's (odd seq) then Verity's (even seq)
  role        text        not null check (role in ('user', 'verity')),
  text        text        not null,
  tool_steps  jsonb,
  reply       jsonb,                                    -- {"check": ..., "reply": ...} as sent on the wire, 32 KB at most
  created_at  timestamptz not null default now(),
  primary key (owner, thread_id, seq),
  foreign key (owner, thread_id) references public.threads (owner, id) on update cascade on delete cascade
);

create table if not exists public.user_runs (
  owner       text        not null,
  run_id      text        not null,
  thread_id   uuid,
  title       text        not null,
  created_at  timestamptz not null default now(),
  primary key (owner, run_id)
);

create table if not exists public.user_credits (
  owner       text        not null primary key,
  email       text,
  role        text        not null default 'user',
  credits     integer     not null default 3,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index if not exists threads_owner_updated_idx on public.threads (owner, updated_at desc);
create index if not exists user_runs_owner_created_idx on public.user_runs (owner, created_at desc);

alter table public.threads      enable row level security;
alter table public.thread_turns enable row level security;
alter table public.user_runs    enable row level security;
alter table public.user_credits enable row level security;
-- No policies on purpose: with row level security on and no policy, anon and authenticated can read and write nothing.

revoke all on public.threads, public.thread_turns, public.user_runs, public.user_credits from anon, authenticated;
grant select, insert, update, delete on public.threads, public.thread_turns, public.user_runs, public.user_credits to service_role;

