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
