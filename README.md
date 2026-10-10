# Equadium

A tile game where players build valid calculus/algebra equations on a crossword-style board.

## Game modes

The modes run on the same engine (`core/session.py`), so what is stress-tested is what the web app serves.

| mode | seats | how |
|---|---|---|
| `human_vs_agent` (default) | you vs the search agent | `POST /games/create {"mode": "human_vs_agent"}`; the agent replies automatically after each of your moves |
| `agent_vs_agent` | two agents | `POST /games/{id}/agent_step` (one turn) or `/autoplay` (whole game) |

Two-player games are played **online** (`POST /rooms`, see below). The two-humans-on-one-screen "Pass & Play" mode has been
removed: `POST /games/create {"mode": "human_vs_human"}` now returns 400, and old saved Pass & Play games no longer appear in
the Continue list. (The engine's `human_vs_human` mode still exists because online rooms use it.)

Other endpoints: `GET /modes`, `GET /games/{id}`, `POST /games/{id}/validate_move` (returns a `reason` when illegal).
A rejected `play` returns `{"status": "failed", "error": "..."}` and does not consume the turn.

## Running the app

```
pip install -r requirements.txt
python -m uvicorn web.api.main:app --port 8000      # API
cd web/frontend && npm install && npm run dev        # UI at http://localhost:3000 (proxies the API)
```
For production, `npm run build` and the API serves `web/frontend/dist` itself.
The home screen offers a game against the computer, watching the bots, and playing a friend online.

## Accounts, saved games and stats

Playing is open to guests. Signing in with Google adds:
- **Saved games**: solo games and online rooms are saved after every move and show up under *Your games*
  on any device you sign in on. Guests' solo games stay in memory only; guests' online rooms survive
  restarts but only on the device holding the seat token.
- **Stats**: every finished game against the computer or a friend (not bot-watching) is
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

## Turn notifications

Notifications are **on by default** for signed-in players: when the other player moves in an online game they get a push
notification ("Ann played for 12 points. Your turn!"). The first time someone starts, joins or plays in an online game on a
device, the browser's own permission prompt appears, and their answer there decides whether notifications arrive; a "no"
(or a dismissed prompt) is never nagged about again. Where the browser has already allowed notifications, a device is
registered silently on sign-in. The bell in the game bar (or "Notify me when it is my turn" in the account menu) is the
opt-out and opt-back-in. It uses standard Web Push, so there is no extra service to sign up for.

Server setup (once): run `python -m web.api.make_vapid_keys`, then set `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` and
`VAPID_SUBJECT` (a `mailto:` address) on the API server (Render) and redeploy. Without them the feature is off and the
bell is hidden. Subscriptions are stored in Firestore (`push_subscriptions`), so with the in-memory store they are lost on
restart.

If notifications don't arrive: turn them on, open the account menu and tap **Send me a test notification**: it goes through the
real server and says what happened (no device registered, the push service refused it, ...). The Render logs also say
`Push sent to ...`, `Push failed ...` or `Push skipped: VAPID keys are not set` for every move. Check that `VAPID_SUBJECT` is just
`mailto:you@yourdomain.com` (no trailing comment; stray text is stripped but double-check), that the public/private keys are a
matching pair, and that the player being notified is signed in (guests have no account to notify).

Notes: on iPhone/iPad notifications only work once the site is added to the Home Screen (the app has a manifest and icons for
that). Only the browsers' own push services are accepted as subscription endpoints. Tapping a notification opens that game.
`npm run e2e:push` checks the service worker and the signed-in UI, and `npm run e2e:push-default` (API started with any `VAPID_*` values) checks the default-on prompt rules; actual delivery to a phone has to be tried by hand.

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

## How a game ends

The game ends when the **last tile is drawn from the bag**: the player who drew it has had their turn, and the other player
gets one final "rebuttal" turn (a play, a swap or a pass), after which scores are final and the higher score wins (equal
scores tie). There is no bonus or penalty for tiles left in a rack. Two safety nets remain: three full rounds with nobody
playing ("stalled") and a 75-turn limit. A forfeit ends the game at once (see below). The app says "Last tile drawn · …
final turn" once the countdown starts. Games saved before this rule whose bag is already empty simply give each player one
more turn. `npm run e2e:endgame` checks the messages.

## Forfeiting

The flag in the game header lets a player give up (after a confirmation). It ends the game at once as a **loss for
the forfeiter and a win for the other player, whatever the score**, and it works on either side's turn. In an online
game the opponent's screen picks it up on its next poll and tells them they won. For signed-in players it is recorded
like any finished game (a loss, plus a `forfeits` count in `/me/stats`). API: `POST /games/{id}/forfeit?player=...` and `POST /rooms/{code}/forfeit`. Browser checks:
`npm run e2e:forfeit` (see `web/frontend/e2e/`).

## Keeping the agent fast

The agent's slowest work (SymPy integrals and a few parser quirks) is deterministic, so it is precomputed into
`core/search_cache.json` and loaded at startup. **Regenerate it whenever you add/remove tiles in
`core/game_config.py` or change `core/math_engine.py`:**

```
python -m core.precompute_search_cache      # ~15 s on 4 cores; use the SymPy version pinned in requirements.txt
```
A stale file is ignored (the agent still works, just slowly on small servers) and a test fails to remind you.
Each computer turn also has a soft time budget (default 6 s, `EQUADIUM_AGENT_TIME_BUDGET`): on a slow host the
agent stops deepening and skips uncached work, so it plays slightly weaker moves instead of making you wait.

## Look and feel

The theme is "Verdigris & Brass": ivory tiles on deep green felt, brass accents, ink-green chrome, with light and dark
modes (the felt board is green in both). Tiles are colour-coded by role: ivory (numbers, variables), parchment
(operators), patina (functions), brass (calculus, the high scorers) and carbon (the equals sign). Everything is in
`web/frontend/src/style.css`; the colours are CSS variables at the top of the file, so re-skinning is a matter of
changing those.

Typography is Cormorant Garamond (wordmark, scores), Jost (interface) and STIX Two Text (the math). The fonts are
bundled with the app via `@fontsource/*` packages, so there are no third-party font requests. The integral sign and
the interface icons are drawn as inline SVG (`src/tiles.ts`, `src/icons.ts`), because no bundled font carries a good
integral and emoji render differently on every device.

## Zooming the board

On touch screens, pinch the board to zoom (the browser's own page zoom is disabled on the board so the two don't fight); the
+/- buttons are hidden there and only shown for mouse users, who can also Ctrl/⌘ + scroll or pinch a trackpad. The target
button recentres. `npm run e2e:pinch` drives a real two-finger pinch.

## Rearranging the rack

Drag a rack tile onto another to move it there (mouse: native drag and drop; touch: press and drag, a short drag so taps still select).
Your arrangement is kept from turn to turn: tiles you still hold stay put and new ones are added at the end. Shuffle still
shuffles. `npm run e2e:rack` covers mouse, touch and the kept arrangement.

## Browser Back / Forward

Every screen has a URL (`#/online`, `#/stats`, `#/game/<id>`, `#/room/<CODE>`; the menu has none), so the browser's
Back/Forward buttons and a phone's back gesture move between screens instead of leaving the site. Back also closes an
open pop-up (help, swap, pass, account) first, and reloading restores the screen you were on. Leaving a game or the
online form *replaces* its history entry, so Back never lands on a finished game or a half-filled form. The logic is
in `web/frontend/src/nav.ts` and the navigation section of `src/main.ts`. To check it in a real browser:
`cd web/frontend && npm install --no-save playwright-core && npm run e2e:nav` (needs the app and API running; see the
header of `e2e/navigation.mjs`).

## Tests

```
pytest core web
```

## License

© 2026 Kiren Caldwell. All rights reserved (see `LICENSE`). The home screen shows the same notice.
