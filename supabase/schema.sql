create extension if not exists pgcrypto;

create table if not exists bot_sync_state (
  id text primary key,
  servers integer not null default 0,
  users integer not null default 0,
  commands integer not null default 0,
  uptime text not null default '0s',
  connected boolean not null default false,
  payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create table if not exists server_settings (
  id uuid primary key default gen_random_uuid(),
  server_id text not null,
  module text not null,
  enabled boolean not null default true,
  settings jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  unique(server_id,module)
);

create table if not exists blocked_words (
  id uuid primary key default gen_random_uuid(),
  server_id text not null,
  word text not null,
  severity text not null default 'delete',
  created_at timestamptz not null default now()
);

create table if not exists moderation_cases (
  id bigserial primary key,
  server_id text not null,
  action_type text not null,
  target_id text not null,
  target_tag text,
  moderator_id text,
  reason text,
  duration_seconds integer,
  created_at timestamptz not null default now()
);

create table if not exists audit_logs (
  id bigserial primary key,
  server_id text not null,
  event_type text not null,
  actor_id text,
  target_id text,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists warnings (
  id bigserial primary key,
  server_id text not null,
  user_id text not null,
  moderator_id text,
  reason text,
  points integer not null default 1,
  created_at timestamptz not null default now()
);

create table if not exists tickets (
  id uuid primary key default gen_random_uuid(),
  server_id text not null,
  channel_id text,
  user_id text,
  ticket_type text not null default 'support',
  status text not null default 'open',
  closed_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists custom_commands (
  id uuid primary key default gen_random_uuid(),
  server_id text not null,
  name text not null,
  response text not null,
  settings jsonb not null default '{}'::jsonb,
  enabled boolean not null default true,
  unique(server_id,name)
);

create table if not exists auto_responses (
  id uuid primary key default gen_random_uuid(),
  server_id text not null,
  trigger text not null,
  response text not null,
  settings jsonb not null default '{}'::jsonb,
  enabled boolean not null default true
);

create table if not exists trust_entries (
  id uuid primary key default gen_random_uuid(),
  server_id text not null,
  subject_id text not null,
  subject_type text not null default 'user',
  reason text,
  permissions jsonb not null default '{}'::jsonb,
  expires_at timestamptz
);

create index if not exists moderation_cases_server_created on moderation_cases(server_id,created_at desc);
create index if not exists audit_logs_server_created on audit_logs(server_id,created_at desc);
create index if not exists warnings_server_user on warnings(server_id,user_id,created_at desc);

insert into bot_sync_state(id) values('global') on conflict(id) do nothing;

-- The service role is used only server-side. Never expose SUPABASE_SERVICE_ROLE_KEY to the browser.
