-- Clippy Club: initial schema
-- Every table has RLS enabled with no public policies, so only the service role (pipeline) and the portal backend can read or write.

create extension if not exists pgcrypto;

-- Streamers and their clip permission
create table if not exists streamers (
  handle            text primary key,             -- Twitch login, lowercase
  display_name      text,
  permission        text not null default 'credit_only'
                    check (permission in ('allowlisted','credit_only','blocked')),
  permission_proof  text,                          -- where the written OK lives (no personal data)
  notes             text,
  updated_at        timestamptz not null default now()
);

-- Every candidate the scout looks at, from scouted to posted or rejected
create table if not exists clips (
  id                uuid primary key default gen_random_uuid(),
  source_url        text not null unique,
  source_platform   text not null default 'twitch',
  streamer          text references streamers(handle),
  source_created_at timestamptz,
  game              text not null default 'gta6',
  status            text not null default 'scouted'
                    check (status in ('scouted','rejected','cut','qa_failed','queued','approved','scheduled','posted','taken_down')),
  reject_reason     text,
  proof_frame_path  text,                          -- storage path of the saved GTA 6 proof frame
  send_test         text,                          -- who they'd send it to and what they'd type
  spec              jsonb,                         -- clip.py render spec
  render_path       text,                          -- storage path of the final mp4
  render_sha256     text,                          -- hash of the exact rendered file
  hook              text,
  caption           text,
  yt_title          text,
  yt_description    text,
  experiment_id     text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index if not exists clips_status_idx on clips(status);

-- An approval is valid only while the hashes still match the clip's current render and caption
create table if not exists approvals (
  id                uuid primary key default gen_random_uuid(),
  clip_id           uuid not null references clips(id) on delete cascade,
  render_sha256     text not null,
  caption_sha256    text not null,                 -- sha256 of caption + yt_title + yt_description
  approved_by       text not null,
  approved_at       timestamptz not null default now(),
  revoked_at        timestamptz
);
create index if not exists approvals_clip_idx on approvals(clip_id);

-- One row per clip per platform
create table if not exists posts (
  id                uuid primary key default gen_random_uuid(),
  clip_id           uuid not null references clips(id),
  platform          text not null check (platform in ('tiktok','youtube')),
  approval_id       uuid not null references approvals(id),
  blotato_post_id   text,
  scheduled_for     timestamptz,
  live_at           timestamptz,
  live_url          text,
  status            text not null default 'pending'
                    check (status in ('pending','scheduled','live','stuck','failed','deleted')),
  error             text,
  dry_run           boolean not null default true,
  created_at        timestamptz not null default now(),
  unique (clip_id, platform)                       -- ledger: a clip can never be posted twice per platform
);

-- Analytics reads over time
create table if not exists metrics (
  id                bigserial primary key,
  post_id           uuid not null references posts(id) on delete cascade,
  read_at           timestamptz not null default now(),
  plays             integer,
  likes             integer,
  shares            integer,
  comments          integer,
  saves             integer,
  followers_at_read integer
);
create index if not exists metrics_post_idx on metrics(post_id, read_at);

create table if not exists experiments (
  id                text primary key,              -- EXP-G# (own data) or EXP-R# (research)
  hypothesis        text not null,
  variable          text not null,
  metric            text not null,
  sample_size       integer,
  kill_condition    text not null,
  status            text not null default 'queued'
                    check (status in ('leading','promising','running','queued','early_miss','killed')),
  started_at        timestamptz,
  notes             text
);

create table if not exists takedowns (
  id                uuid primary key default gen_random_uuid(),
  streamer          text references streamers(handle),
  received_at       timestamptz not null default now(),
  request_source    text,
  clip_ids          uuid[],
  action            text,
  completed_at      timestamptz,
  notes             text
);

-- Platform pause switches, each reversible with a single update
create table if not exists pauses (
  platform          text primary key check (platform in ('tiktok','youtube','all')),
  paused_until      timestamptz,
  reason            text,
  updated_at        timestamptz not null default now()
);

alter table streamers   enable row level security;
alter table clips       enable row level security;
alter table approvals   enable row level security;
alter table posts       enable row level security;
alter table metrics     enable row level security;
alter table experiments enable row level security;
alter table takedowns   enable row level security;
alter table pauses      enable row level security;
