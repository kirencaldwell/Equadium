# Equadium

A tile game where players build valid calculus/algebra equations on a crossword-style board.

## Game modes

All three run on the same engine (`core/session.py`), so what is stress-tested is what the web app serves.

| mode | seats | how |
|---|---|---|
| `human_vs_agent` (default) | you vs the search agent | `POST /games/create {"mode": "human_vs_agent"}`; the agent replies automatically after each of your moves |
| `human_vs_human` | two humans | `POST /games/create {"mode": "human_vs_human"}`; pass `?player=Player1` / `Player2` on `/play`, `/swap`, `/pass` (hot-seat), or join an open game via `/games/{id}/join` |
| `agent_vs_agent` | two agents | `POST /games/{id}/agent_step` (one turn) or `/autoplay` (whole game) |

Other endpoints: `GET /modes`, `GET /games/{id}`, `POST /games/{id}/validate_move` (returns a `reason` when illegal).
A rejected `play` returns `{"status": "failed", "error": "..."}` and does not consume the turn.

## Running the app

```
pip install -r requirements.txt
python -m uvicorn web.api.main:app --port 8000      # API
cd web/frontend && npm install && npm run dev        # UI at http://localhost:3000 (proxies the API)
```
For production, `npm run build` and the API serves `web/frontend/dist` itself.
Sign-in/online play is paused for now; the home screen offers solo, pass-and-play and bot-watching.

## Accounts, saved games and stats

Playing is open to guests. Signing in with Google adds:
- **Saved games**: solo games and online rooms are saved after every move and show up under *Your games*
  on any device you sign in on. Guests' solo games stay in memory only; guests' online rooms survive
  restarts but only on the device holding the seat token.
- **Stats**: every finished game against the computer or a friend (not Pass & Play, not bot-watching) is
  recorded: record, win rate, average and best score, best single play, streak, recent games.

### Setting up Supabase
1. Create a project at supabase.com.
2. **SQL editor**: run `supabase/migrations/20261009000000_init.sql`. It creates `games`, `game_results` and
   `profiles` with row level security on (the `games` table has no public policies; it holds room secrets).
3. **Google login**: Google Cloud Console -> create an OAuth client (type *Web application*) with the redirect URI
   `https://<project-ref>.supabase.co/auth/v1/callback`; then Supabase -> Authentication -> Providers -> Google:
   paste the client ID and secret.
4. **Redirect URLs**: Supabase -> Authentication -> URL Configuration: set *Site URL* to your deployed URL and add
   `http://localhost:3000` and the deployed URL under *Redirect URLs*.
5. **API env** (see `.env.example`): `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (secret, server only) and, if your project
   still signs tokens with HS256, `SUPABASE_JWT_SECRET`.
6. **Frontend env** (see `web/frontend/.env.example`): `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`.

The API checks every request's token (HS256 shared secret or the project's public JWKS, whichever the token uses),
so players can only open their own games. A storage outage never blocks a move: the game carries on in memory and
saves again on the next move.

## Deploying

- **Frontend (Vercel):** import the repo, set *Root Directory* to `web/frontend` (build `npm run build`, output `dist`),
  and add `VITE_API_URL` (your API's URL) plus the two `VITE_SUPABASE_*` variables. Vite bakes them in at build time,
  so redeploy after changing them.
- **API:** needs a long-running Python host (Render, Railway, Fly, ...), not Vercel serverless: games are cached in
  memory. Start command: `uvicorn web.api.main:app --host 0.0.0.0 --port $PORT`; install with
  `pip install -r requirements.txt`. Set `ALLOWED_ORIGINS` to your Vercel URL and the Supabase variables above.
  Run a single instance: the in-memory cache and per-game locks are per-process.

## Stress testing

```
python -m core.stress_test --games 50 --workers 4 --seed 0 --json out.json
python -m core.stress_test --games 10 --agents search playbook   # compare algorithms
```

Plays agent-vs-agent games and, after every turn, checks tile conservation, score monotonicity,
turn order and rack limits; at game end it re-validates every equation on the board.
Exits non-zero on any violation (CI friendly). Reports end reasons, play/swap counts,
first-player advantage, rejected agent moves and slowest turn.

## The agent

`core/search_agent.py` searches from the current rack each turn rather than looking things up in a
precomputed table (`core/math_playbook.py`, kept as a baseline: it grows as tiles^length and a length-4
table takes many minutes to build). It enumerates what the rack can spell, fingerprints each expression
numerically, and joins equal fingerprints (meet in the middle); SymPy only verifies the final candidates.

## Tests

```
pytest core web
```
