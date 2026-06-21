import sys
import os
# Add workspace root to python path to resolve absolute imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import logging
import json
from datetime import datetime
from typing import List, Optional

import jwt  # PyJWT
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from web.api.models import PlayerModel, BoardModel, MoveModel, TileModel
from core.game_manager import EquadiumGame
from core.game_config import CONFIG
from core.ai_agent import AIAgent
from core.math_playbook import MathPlaybook
from core.game_entities import Move, Tile

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

games = {} # Simple in-memory storage
agents = {} # Store AI agents per game

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

def game_to_model(game):
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
        "current_player": game.players[game.current_turn_index].name,
        "equals_pile_count": len(game.equals_bag)
    }

@app.post("/games/create")
def create_game(current_user: Optional[str] = Depends(get_current_user)):
    game_id = str(len(games))

    # Initialize game with Human + AI
    all_players = ["Human", "AI_Opponent"]
    game = EquadiumGame(all_players, CONFIG, verbose=True)
    games[game_id] = game

    # Track which Supabase user owns the "Human" seat
    game._owner_user_id = current_user

    # Initialize AI Agent
    playbook = MathPlaybook(CONFIG, max_length=4)
    agents[game_id] = AIAgent("AI_Opponent", playbook, CONFIG, verbose=True)

    return {"game_id": game_id}


@app.post("/games/{game_id}/join")
def join_game(game_id: str, current_user: Optional[str] = Depends(get_current_user)):
    """Second player joins a pending multiplayer game."""
    if game_id not in games:
        raise HTTPException(status_code=404, detail="Game not found")
    game = games[game_id]
    owner = getattr(game, '_owner_user_id', None)
    if current_user and owner == current_user:
        raise HTTPException(status_code=400, detail="Cannot join your own game")
    # Assign joining user to the AI seat for future turn validation
    game._guest_user_id = current_user
    return {"game_id": game_id, "status": "joined"}

@app.get("/games/{game_id}")
def get_game(game_id: str):
    if game_id not in games:
        raise HTTPException(status_code=404, detail="Game not found")
    return game_to_model(games[game_id])

@app.post("/games/{game_id}/validate_move")
def validate_move(game_id: str, move: MoveModel):
    if game_id not in games:
        raise HTTPException(status_code=404, detail="Game not found")
    game = games[game_id]
    
    # 1. Convert MoveModel to Move object
    tiles_to_play = []
    for p in move.tiles_to_play:
        tile_data = p['tile']
        tile = Tile(symbol=tile_data['symbol'], points=tile_data['points'], expr_multiplier=tile_data['expr_multiplier'])
        tiles_to_play.append((p['r'], p['c'], tile))
        
    # 2. Simulate placement to validate
    game.board.place_tiles_temporarily(tiles_to_play)
    
    # Use direction from frontend
    direction = move.direction or "H"
    equations_data = game.board.get_all_new_equations(tiles_to_play, direction)
    
    # Math verification
    all_valid = True
    for eq_str, eq_tiles in equations_data:
        if not game.math.validate_equation(eq_str)[0]:
            all_valid = False
            break
            
    # 3. Rollback
    game.board.remove_tiles([(r, c) for r, c, _ in tiles_to_play])
    
    return {"valid": all_valid}


@app.post("/games/{game_id}/draw_equals")
def draw_equals(game_id: str, current_user: Optional[str] = Depends(get_current_user)):
    if game_id not in games:
        raise HTTPException(status_code=404, detail="Game not found")
    game = games[game_id]
    _assert_player_turn(game, current_user)
    current_player = game.players[game.current_turn_index]
    success = game.draw_equals_tile(current_player)
    return {"success": success}

from pydantic import BaseModel

class SwapModel(BaseModel):
    tile_indices: List[int]

@app.post("/games/{game_id}/swap")
def swap_tiles(game_id: str, swap_data: SwapModel, current_user: Optional[str] = Depends(get_current_user)):
    if game_id not in games:
        raise HTTPException(status_code=404, detail="Game not found")
    game = games[game_id]
    _assert_player_turn(game, current_user)
    human_player = next(p for p in game.players if p.name == "Human")

    # Map indices to actual tile objects in rack
    tiles_to_swap = [human_player.rack[i] for i in sorted(swap_data.tile_indices, reverse=True)]

    # Create a swap move
    swap_move = Move(n_tiles_to_swap=len(tiles_to_swap))

    # Execute swap
    success = game.execute_move(human_player, swap_move, tiles_to_swap=tiles_to_swap)

    # Trigger AI move if successful (solo games only)
    if success and not game.is_game_over and not _is_multiplayer(game):
        ai_player = next(p for p in game.players if p.name == "AI_Opponent")
        bot = agents[game_id]
        ai_move = bot.handle_turn(game)
        game.execute_move(ai_player, ai_move)

    return {"status": "success" if success else "failed"}

# ── Turn ownership helpers ──────────────────────────────────────────────────
def _is_multiplayer(game) -> bool:
    return getattr(game, '_guest_user_id', None) is not None


def _assert_player_turn(game, current_user: Optional[str]):
    """In a multiplayer game, ensure it's actually this user's turn."""
    if not _is_multiplayer(game) or current_user is None:
        return  # Solo game or unauthenticated – no restriction
    active_player_idx = game.current_turn_index
    owner = getattr(game, '_owner_user_id', None)
    guest = getattr(game, '_guest_user_id', None)
    # Player 0 == owner (Human seat), Player 1 == guest (previously AI seat)
    expected_user = owner if active_player_idx == 0 else guest
    if current_user != expected_user:
        raise HTTPException(status_code=403, detail="Not your turn")


@app.post("/games/{game_id}/play")
def play_move(game_id: str, move: MoveModel, current_user: Optional[str] = Depends(get_current_user)):
    if game_id not in games:
        raise HTTPException(status_code=404, detail="Game not found")
    game = games[game_id]
    _assert_player_turn(game, current_user)

    # 1. Convert MoveModel to Move object
    tiles_to_play = []
    for p in move.tiles_to_play:
        tile_data = p['tile']
        tile = Tile(symbol=tile_data['symbol'], points=tile_data['points'], expr_multiplier=tile_data['expr_multiplier'])
        tiles_to_play.append((p['r'], p['c'], tile))

    # Use direction from frontend
    direction = move.direction or "H"
    engine_move = Move(tiles_to_play=tiles_to_play, direction=direction)

    # 2. Determine active player
    active_player = game.players[game.current_turn_index]
    success = game.execute_move(active_player, engine_move)

    if success and not game.is_game_over and not _is_multiplayer(game):
        # 3. Trigger AI move in solo games
        ai_player = next(p for p in game.players if p.name == "AI_Opponent")
        bot = agents[game_id]
        ai_move = bot.handle_turn(game)
        game.execute_move(ai_player, ai_move)

    log_game_state(game_id, game, "MOVE_PLAYED", {"move": move.dict(), "success": success})

    return {"status": "success" if success else "failed"}

static_dir = "web/frontend/dist" if os.path.exists("web/frontend/dist") else "web/static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

