"""
Online play: private two-player rooms.

A player creates a room and shares its short code (or link). The second player
joins with the code. Each seat is guarded by a secret token that the client
sends in the `X-Player-Token` header; the server decides whose turn it is
from the token alone and never sends a player's rack to their opponent.

Rooms live in memory, so run a single server process (see README / deploy
notes) and expect games to be lost on restart.
"""
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from core.game_config import CONFIG
from core.session import GameSession
from web.api.models import MoveModel, SwapModel
from web.api import persistence, push
from web.api.auth import AuthUser, optional_user
from web.api.serialize import game_to_model, _tiles_from_model
from web.api.store import get_store

router = APIRouter(prefix="/rooms", tags=["online"])
logger = logging.getLogger("equadium_store")

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
    id: str = field(default_factory=lambda: secrets.token_urlsafe(9))
    tokens: Dict[str, str] = field(default_factory=dict)   # seat -> secret token
    labels: Dict[str, str] = field(default_factory=dict)   # seat -> display name
    seat_users: Dict[str, str] = field(default_factory=dict)   # seat -> signed-in user id
    version: int = 0
    last_active: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def full(self) -> bool:
        return len(self.tokens) == len(SEATS)

    def seat_for(self, token: Optional[str], user: Optional[AuthUser] = None) -> Optional[str]:
        """The seat held by this token, or by this signed-in user (so any device can resume)."""
        if token:
            for seat, tok in self.tokens.items():
                if secrets.compare_digest(tok, token):
                    return seat
        if user is not None:
            for seat, uid in self.seat_users.items():
                if uid == user.id:
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
    code = code.strip().upper()
    room = _rooms.get(code)
    if room is None:
        room = _load_room(code)
    if room is None:
        raise HTTPException(status_code=404, detail="No game with that code (it may have expired)")
    return room


def _load_room(code: str) -> Optional[Room]:
    """Rebuild a room from the store (after a restart, or when it aged out of memory)."""
    try:
        rec = get_store().get_game(code=code)
    except Exception:
        logger.exception("Could not load room %s", code)
        return None
    if not rec or rec.get("kind") != "room" or rec.get("status") == "abandoned":
        return None
    meta = rec["meta"]
    room = Room(code=code, id=rec["id"], session=GameSession.from_dict(rec["state"]),
                tokens=meta.get("tokens", {}), labels=meta.get("labels", {}),
                seat_users=meta.get("seat_users", {}), version=meta.get("version", 0))
    with _rooms_lock:
        return _rooms.setdefault(code, room)


def _save_room(room: Room) -> None:
    """Write-through save; when the game just ended this also records each player's result."""
    kwargs = dict(code=room.code, labels=room.labels,
                  meta={"tokens": room.tokens, "labels": room.labels, "version": room.version})
    if room.session.is_over:
        persistence.finalize_if_over(room.id, "room", room.session, room.seat_users, **kwargs)
    else:
        persistence.save_game(room.id, "room", room.session, room.seat_users,
                              status="active" if room.full else "waiting", **kwargs)


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


def _require_seat(room: Room, token: Optional[str], user: Optional[AuthUser] = None) -> str:
    seat = room.seat_for(token, user)
    if seat is None:
        raise HTTPException(status_code=403, detail="You are not a player in this game")
    return seat


def _require_started(room: Room) -> None:
    if not room.full:
        raise HTTPException(status_code=409, detail="Waiting for your opponent to join")


def _new_code() -> str:
    """A join code unused in memory *and* in the store (saved rooms outlive the process)."""
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if code in _rooms:
            continue
        try:
            if get_store().get_game(code=code) is None:
                return code
        except Exception:
            return code   # store down: accept the tiny collision risk rather than block play


@router.post("")
def create_room(body: Optional[NameModel] = None, user: Optional[AuthUser] = Depends(optional_user)):
    with _rooms_lock:
        _sweep()
        if len(_rooms) >= MAX_ROOMS:
            raise HTTPException(status_code=503, detail="The server is full right now, try again later")
        code = _new_code()
        room = Room(code=code, session=GameSession("human_vs_human", CONFIG))
        # Seat order decides who moves first, so give the host a random seat.
        seat = secrets.choice(SEATS)
        room.tokens[seat] = secrets.token_urlsafe(24)
        room.labels[seat] = _clean_name((body.name if body else None) or (user.name if user else None), "Player")
        if user:
            room.seat_users[seat] = user.id
        room.touch()
        _rooms[code] = room
    _save_room(room)
    return {"code": code, "seat": seat, "token": room.tokens[seat]}


@router.post("/{code}/join")
def join_room(code: str, body: Optional[NameModel] = None, user: Optional[AuthUser] = Depends(optional_user)):
    room = _get_room(code)
    with room.lock:
        existing = room.seat_for(None, user)
        if existing:   # a signed-in player re-opening their own game on another device
            return {"code": room.code, "seat": existing, "token": room.tokens[existing]}
        if room.full:
            raise HTTPException(status_code=409, detail="That game already has two players")
        seat = next(s for s in SEATS if s not in room.tokens)
        host = next(iter(room.labels.values()))
        name = _clean_name((body.name if body else None) or (user.name if user else None), "Guest")
        if name == host:
            name = f"{name} (2)"
        room.tokens[seat] = secrets.token_urlsafe(24)
        room.labels[seat] = name
        if user:
            room.seat_users[seat] = user.id
        room.touch()
        _save_room(room)
    return {"code": room.code, "seat": seat, "token": room.tokens[seat]}


@router.get("/{code}")
def get_room(code: str, since: Optional[int] = None, x_player_token: Optional[str] = Header(None),
             user: Optional[AuthUser] = Depends(optional_user)):
    """Current state for the caller. Pass `since=<version>` to long-poll cheaply:
    if nothing changed the reply is just {"changed": false}."""
    room = _get_room(code)
    seat = _require_seat(room, x_player_token, user)
    with room.lock:
        if since is not None and since == room.version:
            return {"changed": False, "version": room.version}
        return {"changed": True, "version": room.version, "seat": seat, "state": _snapshot(room, seat)}


def _act(room: Room, token: Optional[str], user: Optional[AuthUser], fn):
    seat = _require_seat(room, token, user)
    with room.lock:
        _require_started(room)
        try:
            record = fn(room.session, seat)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if record.ok:
            room.touch()
            _save_room(room)
            push.notify_room_move(room, seat, record)   # tell the other player (fire and forget)
        return {
            "status": "success" if record.ok else "failed",
            "error": record.error,
            "score_delta": record.score_delta,
            "agent_moves": [],
            "game_over": room.session.is_over,
        }


@router.post("/{code}/validate_move")
def validate_move(code: str, move: MoveModel, x_player_token: Optional[str] = Header(None),
                  user: Optional[AuthUser] = Depends(optional_user)):
    room = _get_room(code)
    _require_seat(room, x_player_token, user)
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
def play(code: str, move: MoveModel, x_player_token: Optional[str] = Header(None),
         user: Optional[AuthUser] = Depends(optional_user)):
    room = _get_room(code)
    return _act(room, x_player_token, user,
                lambda s, seat: s.play(seat, _tiles_from_model(move), move.direction or "H"))


@router.post("/{code}/swap")
def swap(code: str, body: SwapModel, x_player_token: Optional[str] = Header(None),
         user: Optional[AuthUser] = Depends(optional_user)):
    room = _get_room(code)
    return _act(room, x_player_token, user, lambda s, seat: s.swap(seat, body.tile_indices))


@router.post("/{code}/pass")
def pass_turn(code: str, x_player_token: Optional[str] = Header(None),
              user: Optional[AuthUser] = Depends(optional_user)):
    room = _get_room(code)
    return _act(room, x_player_token, user, lambda s, seat: s.pass_turn(seat))


@router.post("/{code}/forfeit")
def forfeit(code: str, x_player_token: Optional[str] = Header(None),
            user: Optional[AuthUser] = Depends(optional_user)):
    """Give up: a loss for you and a win for your opponent. Unlike a move, this works on either side's turn."""
    room = _get_room(code)
    return _act(room, x_player_token, user, lambda s, seat: s.forfeit(seat))
