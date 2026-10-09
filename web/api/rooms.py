"""
Online play: private two-player rooms.

A player creates a room and shares its short code (or link). The second player
joins with the code. Each seat is guarded by a secret token that the client
sends in the `X-Player-Token` header; the server decides whose turn it is
from the token alone and never sends a player's rack to their opponent.

Rooms live in memory, so run a single server process (see README / deploy
notes) and expect games to be lost on restart.
"""
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from core.game_config import CONFIG
from core.session import GameSession
from web.api.models import MoveModel, SwapModel
from web.api.serialize import game_to_model, _tiles_from_model

router = APIRouter(prefix="/rooms", tags=["online"])

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no 0/O/1/I/L lookalikes
CODE_LENGTH = 5
ROOM_TTL_SECONDS = 24 * 60 * 60
MAX_ROOMS = 2000
MAX_NAME = 16
SEATS = ("Player1", "Player2")


@dataclass
class Room:
    code: str
    session: GameSession
    tokens: Dict[str, str] = field(default_factory=dict)   # seat -> secret token
    labels: Dict[str, str] = field(default_factory=dict)   # seat -> display name
    version: int = 0
    last_active: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def full(self) -> bool:
        return len(self.tokens) == len(SEATS)

    def seat_for(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        for seat, tok in self.tokens.items():
            if secrets.compare_digest(tok, token):
                return seat
        return None

    def touch(self, bump: bool = True):
        self.last_active = time.time()
        if bump:
            self.version += 1


_rooms: Dict[str, Room] = {}
_rooms_lock = threading.Lock()


class NameModel(BaseModel):
    name: Optional[str] = None


def _clean_name(name: Optional[str], fallback: str) -> str:
    name = " ".join((name or "").split())[:MAX_NAME]
    return name or fallback


def _sweep() -> None:
    """Drop rooms nobody has touched for a day. Caller holds _rooms_lock."""
    cutoff = time.time() - ROOM_TTL_SECONDS
    for code in [c for c, r in _rooms.items() if r.last_active < cutoff]:
        del _rooms[code]


def _get_room(code: str) -> Room:
    room = _rooms.get(code.strip().upper())
    if room is None:
        raise HTTPException(status_code=404, detail="No game with that code (it may have expired)")
    return room


def _snapshot(room: Room, seat: Optional[str]) -> dict:
    """Game state as one particular player may see it: only their own rack."""
    state = game_to_model(room.session)
    players = []
    for p in state["players"]:
        d = p.model_dump()
        d["rack_count"] = len(d["rack"])
        if p.name != seat:
            d["rack"] = []
        players.append(d)
    state["players"] = players
    state["board"] = state["board"].model_dump()
    state["labels"] = dict(room.labels)
    state["joined"] = room.full
    return state


def _require_seat(room: Room, token: Optional[str]) -> str:
    seat = room.seat_for(token)
    if seat is None:
        raise HTTPException(status_code=403, detail="You are not a player in this game")
    return seat


def _require_started(room: Room) -> None:
    if not room.full:
        raise HTTPException(status_code=409, detail="Waiting for your opponent to join")


@router.post("")
def create_room(body: Optional[NameModel] = None):
    with _rooms_lock:
        _sweep()
        if len(_rooms) >= MAX_ROOMS:
            raise HTTPException(status_code=503, detail="The server is full right now, try again later")
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        while code in _rooms:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        room = Room(code=code, session=GameSession("human_vs_human", CONFIG))
        # Seat order decides who moves first, so give the host a random seat.
        seat = secrets.choice(SEATS)
        room.tokens[seat] = secrets.token_urlsafe(24)
        room.labels[seat] = _clean_name(body.name if body else None, "Player")
        room.touch()
        _rooms[code] = room
    return {"code": code, "seat": seat, "token": room.tokens[seat]}


@router.post("/{code}/join")
def join_room(code: str, body: Optional[NameModel] = None):
    room = _get_room(code)
    with room.lock:
        if room.full:
            raise HTTPException(status_code=409, detail="That game already has two players")
        seat = next(s for s in SEATS if s not in room.tokens)
        host = next(iter(room.labels.values()))
        name = _clean_name(body.name if body else None, "Guest")
        if name == host:
            name = f"{name} (2)"
        room.tokens[seat] = secrets.token_urlsafe(24)
        room.labels[seat] = name
        room.touch()
    return {"code": room.code, "seat": seat, "token": room.tokens[seat]}


@router.get("/{code}")
def get_room(code: str, since: Optional[int] = None, x_player_token: Optional[str] = Header(None)):
    """Current state for the caller. Pass `since=<version>` to long-poll cheaply:
    if nothing changed the reply is just {"changed": false}."""
    room = _get_room(code)
    seat = _require_seat(room, x_player_token)
    with room.lock:
        if since is not None and since == room.version:
            return {"changed": False, "version": room.version}
        return {"changed": True, "version": room.version, "seat": seat, "state": _snapshot(room, seat)}


def _act(room: Room, token: Optional[str], fn):
    seat = _require_seat(room, token)
    with room.lock:
        _require_started(room)
        try:
            record = fn(room.session, seat)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if record.ok:
            room.touch()
        return {
            "status": "success" if record.ok else "failed",
            "error": record.error,
            "score_delta": record.score_delta,
            "agent_moves": [],
            "game_over": room.session.is_over,
        }


@router.post("/{code}/validate_move")
def validate_move(code: str, move: MoveModel, x_player_token: Optional[str] = Header(None)):
    room = _get_room(code)
    _require_seat(room, x_player_token)
    with room.lock:
        tiles = _tiles_from_model(move)
        equations, error = room.session.game.evaluate_play(tiles, move.direction or "H")
        return {
            "valid": error is None,
            "reason": error,
            "equations": [eq for eq, _ in equations] if equations else [],
            "score": room.session.game.score_play(equations, tiles) if equations else 0,
        }


@router.post("/{code}/play")
def play(code: str, move: MoveModel, x_player_token: Optional[str] = Header(None)):
    room = _get_room(code)
    return _act(room, x_player_token,
                lambda s, seat: s.play(seat, _tiles_from_model(move), move.direction or "H"))


@router.post("/{code}/swap")
def swap(code: str, body: SwapModel, x_player_token: Optional[str] = Header(None)):
    room = _get_room(code)
    return _act(room, x_player_token, lambda s, seat: s.swap(seat, body.tile_indices))


@router.post("/{code}/pass")
def pass_turn(code: str, x_player_token: Optional[str] = Header(None)):
    room = _get_room(code)
    return _act(room, x_player_token, lambda s, seat: s.pass_turn(seat))
