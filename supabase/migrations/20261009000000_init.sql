-- Equadium: saved games, player stats and profiles.
-- Run in the Supabase SQL editor (or `supabase db push`).
--
-- Security model: the API talks to these tables with the *service role* key, which
-- bypasses row level security. RLS is enabled everywhere so that the public anon key
-- (which ships in the frontend) can read nothing except a user's own profile/results.
-- `games` deliberately has NO policies: rows contain secret room tokens.

create table if not exists public.profiles (
    user_id      uuid primary key references auth.users (id) on delete cascade,
    display_name text,
    avatar_url   text,
    created_at   timestamptz not null default now()
);

create table if not exists public.games (
    id          text primary key,
    kind        text not null check (kind in ('solo', 'room')),
    code        text unique,                      -- room join code (rooms only)
    mode        text not null,
    status      text not null check (status in ('waiting', 'active', 'finished', 'abandoned')),
    state       jsonb not null,                   -- full GameSession snapshot
    summary     jsonb not null default '{}'::jsonb,  -- small card for "continue" lists
    meta        jsonb not null default '{}'::jsonb,  -- room tokens / labels / version (server only)
    user_ids    uuid[] not null default '{}',     -- signed-in players in this game
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index if not exists games_user_ids_idx on public.games using gin (user_ids);
create index if not exists games_updated_at_idx on public.games (updated_at);

create table if not exists public.game_results (
    game_id     text not null,
    user_id     uuid not null references auth.users (id) on delete cascade,
    mode        text not null,
    opponent    text not null check (opponent in ('agent', 'human')),
    outcome     text not null check (outcome in ('win', 'loss', 'tie')),
    score       int not null,
    opp_score   int not null,
    plays       int not null default 0,
    swaps       int not null default 0,
    passes      int not null default 0,
    best_play   int not null default 0,
    turns       int not null default 0,
    finished_at timestamptz not null default now(),
    primary key (game_id, user_id)
);
create index if not exists game_results_user_idx on public.game_results (user_id, finished_at desc);

alter table public.profiles     enable row level security;
alter table public.games        enable row level security;
alter table public.game_results enable row level security;

drop policy if exists "read own profile" on public.profiles;
create policy "read own profile" on public.profiles for select using (auth.uid() = user_id);
drop policy if exists "read own results" on public.game_results;
create policy "read own results" on public.game_results for select using (auth.uid() = user_id);
