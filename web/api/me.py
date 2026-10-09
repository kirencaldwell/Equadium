"""The signed-in player's profile, saved games and stats."""
import logging
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends

from web.api import persistence
from web.api.auth import AuthUser, auth_enabled, require_user
from web.api.store import get_store

router = APIRouter(prefix="/me", tags=["account"])
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
    try:
        recs = get_store().list_user_games(user.id)
    except Exception:
        logger.exception("Could not list games for %s", user.id)
        return {"games": []}
    games = []
    for rec in recs:
        if _age_days(rec["updated_at"]) > STALE_AFTER_DAYS:
            continue
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
    try:
        rows = get_store().list_results(user.id)
    except Exception:
        logger.exception("Could not load stats for %s", user.id)
        rows = []
    return persistence.compute_stats(rows)
