import sys
import os
# Add workspace root to python path to resolve absolute imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import logging
import json
import secrets
import threading
from datetime import datetime
from typing import List, Optional

from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from web.api.models import MoveModel, CreateGameModel, SwapModel
from web.api.serialize import game_to_model, tile_to_model, _tiles_from_model
from core.game_config import CONFIG
from core.game_entities import Tile
from core.session import GameSession, MODES, HUMAN
from web.api import persistence
from web.api.auth import AuthUser, optional_user
from web.api.store import get_store

load_dotenv()

# Ensure logs directory exists
os.makedirs("web/api/logs", exist_ok=True)
log_file = "web/api/logs/game_beta.log"

# Configure logging to file
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()]
)
logger = logging.getLogger("equadium_beta")

app = FastAPI()

# The frontend (e.g. on Vercel) calls this API from another origin.
# ALLOWED_ORIGINS is a comma-separated list, e.g. "https://equadium.vercel.app".
from web.api import rooms, me  # noqa: E402  (after app setup is fine; no circular import)
from fastapi.responses import JSONResponse  # noqa: E402


def _cors_origins(value: Optional[str]) -> List[str]:
    """Parses ALLOWED_ORIGINS. Browsers send origins with no trailing slash, so a pasted
    'https://site.vercel.app/' would never match; normalise it. Empty means any origin."""
    origins = [o.strip().rstrip("/") for o in (value or "").split(",") if o.strip()]
    return origins or ["*"]


@app.middleware("http")
async def turn_crashes_into_json_errors(request: Request, call_next):
    """Catch unexpected errors *inside* the CORS layer. An unhandled exception would otherwise
    produce a bare 500 with no CORS headers, which browsers report as a misleading 'blocked by
    CORS policy' and hides the real problem. (Added before CORSMiddleware so CORS wraps it.)"""
    try:
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(os.getenv("ALLOWED_ORIGINS")),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rooms.router)
app.include_router(me.router)

sessions = {}  # game_id -> GameSession (in-memory cache; signed-in games are also saved to the store)
_locks = {}    # game_id -> Lock, so two requests can't move the same game at once

# Middleware for structured request logging
@app.middleware("http")
async def log_requests(request: Request, call_next):
    if request.method == "GET" and request.url.path.startswith("/rooms"):
        return await call_next(request)   # clients poll these every second or two
    body = await request.body()
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "type": "REQUEST",
        "method": request.method,
        "url": str(request.url),
        "body": body.decode('utf-8')
    }
    logger.info(json.dumps(log_entry))
    response = await call_next(request)
    return response

# Helper to log game state
def log_game_state(game_id, game, action_type, extra=None):
    state = game_to_model(game)
    # Convert state dict values to dicts if they are Pydantic models
    def serialize_state(s):
        if isinstance(s, dict):
            return {k: serialize_state(v) for k, v in s.items()}
        elif hasattr(s, 'model_dump'): # For Pydantic v2
            return s.model_dump()
        elif hasattr(s, 'dict'): # For Pydantic v1
            return s.dict()
        elif isinstance(s, list):
            return [serialize_state(i) for i in s]
        return s
    
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "type": "GAME_STATE",
        "game_id": game_id,
        "action_type": action_type,
        "state": serialize_state(state),
        "extra": extra
    }
    logger.info(json.dumps(log_entry))

def _lock_for(game_id: str) -> threading.Lock:
    return _locks.setdefault(game_id, threading.Lock())


def _seat_users(session: GameSession) -> dict:
    owner = getattr(session, "owner_id", None)
    return {session.human_seats()[0]: owner} if owner and len(session.human_seats()) >= 1 else {}


def _get_session(game_id: str, user: Optional[AuthUser] = None) -> GameSession:
    """Finds a solo game (memory first, then the store) and checks the caller may use it.
    Games owned by a signed-in user are private to them; guest games are reachable by their
    (unguessable) id."""
    session = sessions.get(game_id)
    if session is None:
        try:
            rec = get_store().get_game(game_id=game_id)
        except Exception:
            logger.exception("Could not load game %s", game_id)
            rec = None
        if not rec or rec.get("kind") != "solo":
            raise HTTPException(status_code=404, detail="Game not found")
        session = GameSession.from_dict(rec["state"])
        owners = rec["user_ids"]
        session.owner_id = owners[0] if owners else None
        sessions[game_id] = session
        if session.is_over and rec["status"] != "finished":   # a finish that failed to save: retry
            persistence.finalize_if_over(game_id, "solo", session, _seat_users(session))
    owner = getattr(session, "owner_id", None)
    if owner is not None:
        if user is None:
            raise HTTPException(status_code=401, detail="Sign in to open this game")
        if user.id != owner:
            raise HTTPException(status_code=404, detail="Game not found")
    return session


def _save(game_id: str, session: GameSession) -> None:
    """Write-through save for signed-in players' games (guests' games stay in memory)."""
    users = _seat_users(session)
    if not users or session.mode == "agent_vs_agent":
        return
    if session.is_over:
        persistence.finalize_if_over(game_id, "solo", session, users)
    else:
        persistence.save_game(game_id, "solo", session, users)


def _acting_player(session, player: Optional[str]) -> str:
    """Which seat is acting. Defaults to the only human (vs agent) or whoever is to move."""
    if player:
        return player
    humans = session.human_seats()
    if len(humans) == 1:
        return humans[0]
    return session.current_player.name


def _run_action(session, fn, *args):
    """Runs a human action, then lets agent opponents respond."""
    try:
        record = fn(*args)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    agent_records = session.advance_agents() if record.ok else []
    return record, agent_records


def _result(session, record, agent_records):
    return {
        "status": "success" if record.ok else "failed",
        "error": record.error,
        "score_delta": record.score_delta,
        "agent_moves": [r.__dict__ for r in agent_records],
        "game_over": session.is_over,
    }


@app.get("/modes")
def list_modes():
    return {mode: [{"name": n, "kind": k} for n, k in seats] for mode, seats in MODES.items()}


@app.post("/games/create")
def create_game(body: Optional[CreateGameModel] = None, user: Optional[AuthUser] = Depends(optional_user)):
    mode = body.mode if body else "human_vs_agent"
    if mode not in MODES:
        raise HTTPException(status_code=400, detail=f"Unknown mode '{mode}'. Choose from: {', '.join(MODES)}")
    game_id = secrets.token_urlsafe(9)
    session = GameSession(mode, CONFIG)
    session.owner_id = user.id if user and mode != "agent_vs_agent" else None
    sessions[game_id] = session
    _save(game_id, session)
    return {"game_id": game_id, "mode": mode, "players": [p.name for p in session.game.players]}


@app.get("/games/{game_id}")
def get_game(game_id: str, user: Optional[AuthUser] = Depends(optional_user)):
    return game_to_model(_get_session(game_id, user))


@app.post("/games/{game_id}/validate_move")
def validate_move(game_id: str, move: MoveModel, user: Optional[AuthUser] = Depends(optional_user)):
    session = _get_session(game_id, user)
    tiles = _tiles_from_model(move)
    equations, error = session.game.evaluate_play(tiles, move.direction or "H")
    return {
        "valid": error is None,
        "reason": error,
        "equations": [eq for eq, _ in equations] if equations else [],
        "score": session.game.score_play(equations, tiles) if equations else 0,
    }


@app.post("/games/{game_id}/draw_equals")
def draw_equals(game_id: str, player: Optional[str] = None, user: Optional[AuthUser] = Depends(optional_user)):
    session = _get_session(game_id, user)
    with _lock_for(game_id):
        current_player = session.player(_acting_player(session, player))
        success = session.game.draw_equals_tile(current_player)
        _save(game_id, session)
    return {"success": success}


@app.post("/games/{game_id}/swap")
def swap_tiles(game_id: str, swap_data: SwapModel, player: Optional[str] = None,
               user: Optional[AuthUser] = Depends(optional_user)):
    session = _get_session(game_id, user)
    with _lock_for(game_id):
        record, agent_records = _run_action(
            session, session.swap, _acting_player(session, player), swap_data.tile_indices)
        if record.ok:
            _save(game_id, session)
    return _result(session, record, agent_records)


@app.post("/games/{game_id}/pass")
def pass_turn(game_id: str, player: Optional[str] = None, user: Optional[AuthUser] = Depends(optional_user)):
    session = _get_session(game_id, user)
    with _lock_for(game_id):
        record, agent_records = _run_action(session, session.pass_turn, _acting_player(session, player))
        if record.ok:
            _save(game_id, session)
    return _result(session, record, agent_records)


@app.post("/games/{game_id}/forfeit")
def forfeit_game(game_id: str, player: Optional[str] = None, user: Optional[AuthUser] = Depends(optional_user)):
    """Give up: a loss for the forfeiter and a win for the other player. Allowed on either side's turn."""
    session = _get_session(game_id, user)
    with _lock_for(game_id):
        record, agent_records = _run_action(session, session.forfeit, _acting_player(session, player))
        if record.ok:
            _save(game_id, session)
    return _result(session, record, agent_records)


@app.post("/games/{game_id}/play")
def play_move(game_id: str, move: MoveModel, player: Optional[str] = None,
              user: Optional[AuthUser] = Depends(optional_user)):
    session = _get_session(game_id, user)
    with _lock_for(game_id):
        record, agent_records = _run_action(
            session, session.play, _acting_player(session, player),
            _tiles_from_model(move), move.direction or "H")
        if record.ok:
            _save(game_id, session)
    log_game_state(game_id, session, "MOVE_PLAYED", {"move": move.dict(), "success": record.ok})
    return _result(session, record, agent_records)


@app.delete("/games/{game_id}")
def delete_game(game_id: str, user: Optional[AuthUser] = Depends(optional_user)):
    """Abandon a saved solo game (it disappears from the Continue list; no result is recorded)."""
    session = _get_session(game_id, user)
    if getattr(session, "owner_id", None) is None:
        raise HTTPException(status_code=400, detail="Only saved games can be deleted")
    persistence.save_game(game_id, "solo", session, _seat_users(session), status="abandoned")
    sessions.pop(game_id, None)
    return {"deleted": True}


@app.post("/games/{game_id}/agent_step")
def agent_step(game_id: str, user: Optional[AuthUser] = Depends(optional_user)):
    """Plays one turn for whichever agent is to move (watch an agent-vs-agent game unfold)."""
    session = _get_session(game_id, user)
    with _lock_for(game_id):
        record = session.step_agent()
    if record is None:
        raise HTTPException(status_code=400, detail="It is not an agent's turn (or the game is over)")
    return {"move": record.__dict__, "game_over": session.is_over}


@app.post("/games/{game_id}/autoplay")
def autoplay(game_id: str, user: Optional[AuthUser] = Depends(optional_user)):
    """Agent-vs-agent: plays the game to completion and returns the final results."""
    session = _get_session(game_id, user)
    try:
        with _lock_for(game_id):
            session.run_to_completion()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"results": session.game.get_final_results(), "end_reason": session.game.end_reason,
            "winners": session.game.winners}

# Production: serve the built frontend (cd web/frontend && npm run build).
# In development run `npm run dev` instead; Vite proxies API calls here.
_dist = "web/frontend/dist"
if os.path.exists(_dist):
    app.mount("/", StaticFiles(directory=_dist, html=True), name="static")
else:
    @app.get("/")
    def root():
        return {"message": "Equadium API is running. Build the frontend with `cd web/frontend && npm run build`, "
                           "or run `npm run dev` for development."}
