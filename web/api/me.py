"""The signed-in player's profile, saved games and stats."""
import logging
import time
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, APIRouter, Depends

from web.api import persistence
from web.api.auth import AuthUser, auth_enabled, require_user
from web.api.store import get_store

router = APIRouter(prefix="/me", tags=["account"])


def _account_store():
    """The store for account data, or a 503 if it is only a stand-in: an empty answer would look like 'no games'."""
    store = get_store()
    if getattr(store, "degraded", False):
        raise HTTPException(status_code=503, detail="Your saved games are temporarily unavailable. Try again in a moment.")
    return store
logger = logging.getLogger("equadium_store")

STALE_AFTER_DAYS = 30   # abandoned games stop cluttering the Continue list


@router.get("")
def me(user: AuthUser = Depends(require_user)):
    store = get_store()
    try:
        store.upsert_profile(user.id, user.name, user.avatar_url)
    except Exception:
        logger.exception("Could not save profile for %s", user.id)
    return {"id": user.id, "email": user.email, "name": user.name, "avatar_url": user.avatar_url,
            "saving": getattr(store, "name", "none")}


def _age_days(iso: str) -> float:
    try:
        return (time.time() - datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) / 86400
    except Exception:
        return 0.0


@router.get("/games")
def my_games(user: AuthUser = Depends(require_user)):
    """Unfinished games to resume on any device. Room entries include the seat token, which is
    safe to hand back because the caller has proven (via their JWT) that they own that seat."""
    store = _account_store()
    try:
        recs = store.list_user_games(user.id)
    except Exception:
        # Say so instead of answering "no games": an empty 200 made the app wipe the player's list on a
        # transient storage hiccup (a cold start, a timeout), and the games only came back on a later fetch.
        logger.exception("Could not list games for %s", user.id)
        raise HTTPException(status_code=503, detail="Couldn't load your saved games right now")
    games = []
    for rec in recs:
        if _age_days(rec["updated_at"]) > STALE_AFTER_DAYS:
            continue
        if rec["kind"] == "solo" and rec["mode"] == "human_vs_human":
            continue          # old Pass & Play games: that mode no longer exists, so there is nothing to resume
        seat_users = rec["meta"].get("seat_users", {})
        seat = next((s for s, uid in seat_users.items() if uid == user.id), None)
        summary = rec.get("summary") or {}
        games.append({
            "kind": rec["kind"], "id": rec["id"], "code": rec.get("code"), "mode": rec["mode"],
            "status": rec["status"], "seat": seat,
            "token": rec["meta"].get("tokens", {}).get(seat) if rec["kind"] == "room" and seat else None,
            "current_player": summary.get("current_player"),
            "your_turn": summary.get("current_player") == seat,
            "players": summary.get("players", []),
            "turns_played": summary.get("turns_played", 0),
            "updated_at": rec["updated_at"],
        })
    return {"games": games}


@router.get("/stats")
def my_stats(user: AuthUser = Depends(require_user)):
    store = _account_store()
    try:
        rows = store.list_results(user.id)
    except Exception:
        # Say so rather than answering with zeros: an empty 200 made the app show "no stats" on a storage hiccup.
        logger.exception("Could not load stats for %s", user.id)
        raise HTTPException(status_code=503, detail="Couldn't load your stats right now. Try again in a moment.")
    return persistence.compute_stats(rows)
