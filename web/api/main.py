import sys
import os
# Add workspace root to python path to resolve absolute imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import logging
import json
from datetime import datetime
from typing import List, Optional

import jwt  # PyJWT
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from web.api.models import PlayerModel, BoardModel, MoveModel, TileModel, CreateGameModel, SwapModel
from core.game_config import CONFIG
from core.game_entities import Tile
from core.session import GameSession, MODES, HUMAN

load_dotenv()

# ── JWT validation ──────────────────────────────────────────────────────────
# Set SUPABASE_JWT_SECRET in your .env (Project Settings → API → JWT Secret)
SUPABASE_JWT_SECRET: Optional[str] = os.getenv("SUPABASE_JWT_SECRET")

_bearer_scheme = HTTPBearer(auto_error=False)

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme)
) -> Optional[str]:
    """Decode a Supabase JWT and return the user's UUID (sub claim).
    Returns None when no token is present (allows unauthenticated solo games).
    Raises 401 when a token is present but invalid.
    """
    if credentials is None:
        return None
    if not SUPABASE_JWT_SECRET:
        # JWT validation not configured – accept token as-is for local dev
        return None
    try:
        payload = jwt.decode(
            credentials.credentials,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
        return payload.get("sub")  # Supabase user UUID
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

sessions = {}  # game_id -> GameSession (simple in-memory storage)

# Middleware for structured request logging
@app.middleware("http")
async def log_requests(request: Request, call_next):
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

def tile_to_model(tile):
    if tile is None:
        return None
    return TileModel(
        symbol=tile.symbol,
        points=tile.points,
        expr_multiplier=tile.expr_multiplier
    )

def game_to_model(session):
    game = session.game
    return {
        "board": BoardModel(
            width=game.board.width,
            height=game.board.height,
            grid=[
                [tile_to_model(tile) for tile in row]
                for row in game.board.grid
            ]
        ),
        "players": [
            PlayerModel(
                name=p.name,
                score=p.score,
                rack=[tile_to_model(t) for t in p.rack],
                equals_available=p.equals_available
            )
            for p in game.players
        ],
        "current_player": session.current_player.name,
        "equals_pile_count": len(game.equals_bag),
        "mode": session.mode,
        "seats": {name: seat.kind for name, seat in session.seats.items()},
        "game_over": game.is_game_over,
        "end_reason": game.end_reason,
        "winners": game.winners if game.is_game_over else [],
        "turns_played": game.turns_played,
        "bag_count": len(game.tile_bag),
        "last_move": session.history[-1].__dict__ if session.history else None,
    }

def _get_session(game_id: str) -> GameSession:
    if game_id not in sessions:
        raise HTTPException(status_code=404, detail="Game not found")
    return sessions[game_id]

def _tiles_from_model(move: MoveModel):
    return [
        (p['r'], p['c'], Tile(symbol=p['tile']['symbol'], points=p['tile']['points'],
                              expr_multiplier=p['tile']['expr_multiplier']))
        for p in move.tiles_to_play
    ]

# ── Turn ownership helpers ──────────────────────────────────────────────────
def _is_multiplayer(session) -> bool:
    return getattr(session, '_guest_user_id', None) is not None


def _assert_player_turn(session, current_user: Optional[str]):
    """In an authenticated multiplayer game, ensure it's actually this user's turn."""
    if not _is_multiplayer(session) or current_user is None:
        return  # Solo game or unauthenticated – no restriction
    owner = getattr(session, '_owner_user_id', None)
    guest = getattr(session, '_guest_user_id', None)
    # Player 0 == owner, Player 1 == guest
    expected_user = owner if session.game.current_turn_index == 0 else guest
    if current_user != expected_user:
        raise HTTPException(status_code=403, detail="Not your turn")


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
def create_game(body: Optional[CreateGameModel] = None,
                current_user: Optional[str] = Depends(get_current_user)):
    mode = body.mode if body else "human_vs_agent"
    if mode not in MODES:
        raise HTTPException(status_code=400, detail=f"Unknown mode '{mode}'. Choose from: {', '.join(MODES)}")
    game_id = str(len(sessions))
    session = GameSession(mode, CONFIG, verbose=True)
    sessions[game_id] = session

    # Track which Supabase user owns the first seat
    session._owner_user_id = current_user
    return {"game_id": game_id, "mode": mode, "players": [p.name for p in session.game.players]}


@app.post("/games/{game_id}/join")
def join_game(game_id: str, current_user: Optional[str] = Depends(get_current_user)):
    """Second player joins a pending game, taking over the agent's seat (Human vs Agent -> Human vs Human)."""
    session = _get_session(game_id)
    owner = getattr(session, '_owner_user_id', None)
    if current_user and owner == current_user:
        raise HTTPException(status_code=400, detail="Cannot join your own game")
    agent_seats = [n for n, s in session.seats.items() if s.kind != HUMAN]
    if len(agent_seats) != 1 or _is_multiplayer(session):
        raise HTTPException(status_code=400, detail="Game has no open seat")
    session.make_seat_human(agent_seats[0], "Guest")
    session._guest_user_id = current_user
    return {"game_id": game_id, "status": "joined", "player": "Guest"}

@app.get("/games/{game_id}")
def get_game(game_id: str):
    return game_to_model(_get_session(game_id))

@app.post("/games/{game_id}/validate_move")
def validate_move(game_id: str, move: MoveModel):
    session = _get_session(game_id)
    tiles = _tiles_from_model(move)
    equations, error = session.game.evaluate_play(tiles, move.direction or "H")
    return {
        "valid": error is None,
        "reason": error,
        "equations": [eq for eq, _ in equations] if equations else [],
        "score": session.game.score_play(equations, tiles) if equations else 0,
    }


@app.post("/games/{game_id}/draw_equals")
def draw_equals(game_id: str, player: Optional[str] = None,
                current_user: Optional[str] = Depends(get_current_user)):
    session = _get_session(game_id)
    _assert_player_turn(session, current_user)
    current_player = session.player(_acting_player(session, player))
    success = session.game.draw_equals_tile(current_player)
    return {"success": success}


@app.post("/games/{game_id}/swap")
def swap_tiles(game_id: str, swap_data: SwapModel, player: Optional[str] = None,
               current_user: Optional[str] = Depends(get_current_user)):
    session = _get_session(game_id)
    _assert_player_turn(session, current_user)
    record, agent_records = _run_action(
        session, session.swap, _acting_player(session, player), swap_data.tile_indices)
    return _result(session, record, agent_records)


@app.post("/games/{game_id}/pass")
def pass_turn(game_id: str, player: Optional[str] = None,
              current_user: Optional[str] = Depends(get_current_user)):
    session = _get_session(game_id)
    _assert_player_turn(session, current_user)
    record, agent_records = _run_action(session, session.pass_turn, _acting_player(session, player))
    return _result(session, record, agent_records)


@app.post("/games/{game_id}/play")
def play_move(game_id: str, move: MoveModel, player: Optional[str] = None,
              current_user: Optional[str] = Depends(get_current_user)):
    session = _get_session(game_id)
    _assert_player_turn(session, current_user)
    record, agent_records = _run_action(
        session, session.play, _acting_player(session, player),
        _tiles_from_model(move), move.direction or "H")
    log_game_state(game_id, session, "MOVE_PLAYED", {"move": move.dict(), "success": record.ok})
    return _result(session, record, agent_records)


@app.post("/games/{game_id}/agent_step")
def agent_step(game_id: str):
    """Plays one turn for whichever agent is to move (watch an agent-vs-agent game unfold)."""
    session = _get_session(game_id)
    record = session.step_agent()
    if record is None:
        raise HTTPException(status_code=400, detail="It is not an agent's turn (or the game is over)")
    return {"move": record.__dict__, "game_over": session.is_over}


@app.post("/games/{game_id}/autoplay")
def autoplay(game_id: str):
    """Agent-vs-agent: plays the game to completion and returns the final results."""
    session = _get_session(game_id)
    try:
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
