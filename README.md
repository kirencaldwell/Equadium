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

It uses Firebase: **Authentication** for Google sign-in and **Firestore** for the data. Both are on the free
Spark plan (no credit card).

### Setting up Firebase
1. Create a project at console.firebase.google.com.
2. **Authentication** -> Get started -> Sign-in method -> **Google** -> Enable.
3. **Authentication** -> Settings -> **Authorized domains**: add your deployed domain (`localhost` is already there).
   Forgetting this is the most common cause of "this site isn't allowed to sign in".
4. **Firestore Database** -> Create database (production mode, pick a region), then paste `firestore.rules` into
   the Rules tab. The rules deny all direct access; only the API (service account) touches the data.
5. **Project settings** -> General -> Your apps -> add a **Web app** and copy its config into
   `web/frontend/.env` (`VITE_FIREBASE_*`, see `web/frontend/.env.example`).
6. **Project settings** -> Service accounts -> **Generate new private key**. Set the downloaded JSON, as one line,
   as `FIREBASE_SERVICE_ACCOUNT` on the API host, along with `FIREBASE_PROJECT_ID` (see `.env.example`).

The API verifies each request's ID token against Google's published signing certificates (no shared secret), so
players can only open their own games. A storage outage never blocks a move: the game carries on in memory and
saves again on the next move.

Without any of this the app still works as guest-only.

### Developing against the Firestore emulator
```
npx firebase-tools emulators:start --only firestore          # needs Java; listens on 127.0.0.1:8080
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 FIREBASE_PROJECT_ID=demo python -m uvicorn web.api.main:app --port 8000
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 pytest web/api/test_accounts.py   # also runs the store tests on real Firestore
```
Without `FIRESTORE_EMULATOR_HOST` the emulator tests are skipped and the store is tested against an in-process fake.

## Deploying

- **Frontend (Vercel):** import the repo, set *Root Directory* to `web/frontend` (build `npm run build`, output `dist`),
  and add `VITE_API_URL` (your API's URL) plus the four `VITE_FIREBASE_*` variables. Vite bakes them in at build
  time, so redeploy after changing them. Add the Vercel domain to Firebase's Authorized domains.
- **API:** needs a long-running Python host (Render, Railway, Fly, ...), not Vercel serverless: games are cached in
  memory. Start command: `uvicorn web.api.main:app --host 0.0.0.0 --port $PORT`; install with
  `pip install -r requirements.txt`. Set `ALLOWED_ORIGINS` to your Vercel URL plus `FIREBASE_PROJECT_ID` and
  `FIREBASE_SERVICE_ACCOUNT`. Run a single instance: the in-memory cache and per-game locks are per-process.

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
