# Supabase Setup, Multiplayer, and Auth Plan

This plan details the Supabase database schema design and authentication setup to support:
1. Google OAuth user authentication.
2. User profiles.
3. Multiplayer matchmaking / game states.
4. Persistent playbooks.

---

## User Review Required

### 1. Database Schema
We need to create three tables in Supabase for authentication, user profiles, and multiplayer matchmaking/game state. 

Please run the following SQL schema in the **Supabase SQL Editor**:

```sql
-- Enable UUID extension
create extension if not exists "uuid-ossp";

-- 1. Profiles Table (Linked to Supabase Auth)
create table public.profiles (
  id uuid references auth.users on delete cascade primary key,
  email text unique not null,
  display_name text,
  avatar_url text,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Automatically create a profile when a user signs up via Google OAuth
create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.profiles (id, email, display_name, avatar_url)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'name', 'Player'),
    new.raw_user_meta_data->>'avatar_url'
  );
  return new;
end;
$$ language plpgsql security definer;

create or replace trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- 2. Games Table
create table public.games (
  id uuid default uuid_generate_v4() primary key,
  board jsonb not null, -- Stores board dimensions and current grid structure
  status text not null default 'pending', -- 'pending', 'active', 'finished'
  current_turn_player_id uuid, -- References profiles(id)
  equals_bag jsonb not null, -- Current letters/symbols left in equals bag
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- 3. Game Players Junction Table (For Multiplayer)
create table public.game_players (
  game_id uuid references public.games(id) on delete cascade,
  player_id uuid references public.profiles(id) on delete cascade,
  rack jsonb not null default '[]'::jsonb, -- Player's current private tile rack
  score integer not null default 0,
  turn_order integer not null,
  primary key (game_id, player_id)
);

-- 4. Playbook Cache (From previous task)
create table public.playbook_cache (
  id text primary key,
  metadata jsonb not null,
  catalog jsonb not null,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);
```

---

### 2. Google OAuth Configuration in Supabase
To enable Google login:
1. Go to your **Google Cloud Console** -> **APIs & Services** -> **Credentials**.
2. Create an **OAuth 2.0 Client ID** (Web application).
3. In your **Supabase Dashboard**, go to **Authentication** -> **Providers** -> **Google**.
4. Enable the Google provider, then copy the **Redirect URL** from Supabase.
5. In Google Cloud Console, add that URL to **Authorized redirect URIs**.
6. Copy the Google Client ID and Client Secret into the Supabase Google provider settings and save.

---

## Open Questions

> [!IMPORTANT]
> **Authentication Flow Choice**:
> * **Option A (Frontend Direct)**: The frontend logs in using the Supabase Javascript SDK (`supabase.auth.signInWithOAuth({ provider: 'google' })`). It passes the JWT token to our FastAPI backend (`Authorization: Bearer <JWT>`) for authenticated API requests (validating turns, submitting moves).
> * **Option B (Server Sessions)**: The backend manages authentication redirects and session cookies.
>
> *We strongly recommend **Option A** as it is standard, lightweight, and easy to deploy on Vercel.*

---

## Proposed Changes

Once the schema is established, we will implement the following changes:

### [Frontend Component]

#### [MODIFY] [package.json](file:///Users/kirencaldwell/Documents/Equadium/web/frontend/package.json)
* Add `@supabase/supabase-js` package.

#### [MODIFY] [index.html](file:///Users/kirencaldwell/Documents/Equadium/web/frontend/index.html)
* Add Auth UI (Login / Logout buttons, user profile display).
* Add Multiplayer matchmaking controls (List pending games, Create Match, Join Match).

#### [MODIFY] [main.ts](file:///Users/kirencaldwell/Documents/Equadium/web/frontend/src/main.ts)
* Initialize the Supabase Client.
* Implement login flow & session tracking.
* Update fetch requests to games to send the bearer token so the backend knows who is playing.
* Use Supabase Realtime subscriptions to automatically refresh the board state when the opponent makes a move.

### [Backend Component]

#### [MODIFY] [requirements.txt](file:///Users/kirencaldwell/Documents/Equadium/requirements.txt)
* Add `pyjwt` or `supabase-py` for JWT validation.

#### [MODIFY] [main.py](file:///Users/kirencaldwell/Documents/Equadium/web/api/main.py)
* Add JWT token validation middleware to check that requests come from valid authenticated users.
* Replace the local `games` dictionary with database queries to the `games` and `game_players` tables.
* Validate that players are only making moves on their active turns.

---

## Verification Plan

### Manual Verification
1. Open the UI, click "Sign In with Google", and verify session tokens.
2. Open two separate browser tabs/windows (signed in as different users).
3. Create a game, join it from the second user, and make turns.
4. Verify that the state syncs in real-time between players via Supabase Realtime.
