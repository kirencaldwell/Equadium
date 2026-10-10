"""
Glue between live GameSessions and the Store: what gets saved, when results are
recorded, and how stats are aggregated. Persistence problems are logged and never
break a move: the in-memory game stays authoritative until the next save succeeds.
"""
import logging
from typing import Dict, List, Optional

from core.session import GameSession
from web.api.store import get_store, now_iso

logger = logging.getLogger("equadium_store")

# Only games where the signed-in player's result means something count toward stats:
# solo vs the computer, and online rooms.
def counts_for_stats(kind: str, mode: str) -> bool:
    return kind == "room" or (kind == "solo" and mode == "human_vs_agent")


def summary_for(session: GameSession, labels: Optional[Dict[str, str]] = None) -> dict:
    g = session.game
    return {
        "players": [{"name": p.name, "label": (labels or {}).get(p.name), "score": p.score} for p in g.players],
        "current_player": session.current_player.name,
        "turns_played": g.turns_played,
        "game_over": g.is_game_over,
    }


def save_game(game_id: str, kind: str, session: GameSession, seat_users: Dict[str, str], *,
              code: Optional[str] = None, status: Optional[str] = None,
              meta: Optional[dict] = None, labels: Optional[Dict[str, str]] = None) -> bool:
    """Write-through save. Returns False (after logging) if the store is unavailable."""
    store = get_store()
    if status is None:
        status = "finished" if session.is_over else "active"
    rec = {
        "id": game_id, "kind": kind, "code": code, "mode": session.mode, "status": status,
        "state": session.to_dict(), "summary": summary_for(session, labels),
        "meta": {**(meta or {}), "seat_users": seat_users},
        "user_ids": sorted(set(seat_users.values())),
    }
    try:
        store.save_game(rec)
        return True
    except Exception:
        logger.exception("Could not save game %s", game_id)
        return False


def result_rows(game_id: str, kind: str, session: GameSession, seat_users: Dict[str, str]) -> List[dict]:
    g = session.game
    winners = g.winners
    opponent = "agent" if any(s.kind == "agent" for s in session.seats.values()) else "human"
    rows = []
    for seat, user_id in seat_users.items():
        me = session.player(seat)
        theirs = [p for p in g.players if p.name != seat]
        mine = [r for r in session.history if r.player == seat and r.ok]
        plays = [r for r in mine if r.action == "play"]
        rows.append({
            "game_id": game_id, "user_id": user_id,
            "mode": "online" if kind == "room" else session.mode,
            "opponent": opponent,
            "outcome": "tie" if len(winners) > 1 and seat in winners else "win" if seat in winners else "loss",
            "score": me.score, "opp_score": max((p.score for p in theirs), default=0),
            "plays": len(plays),
            "swaps": sum(1 for r in mine if r.action == "swap"),
            "passes": sum(1 for r in mine if r.action == "pass"),
            "best_play": max((r.score_delta for r in plays), default=0),
            "turns": sum(1 for r in mine if r.action != "forfeit"),
            "forfeit": g.forfeited_by == seat,          # this player gave up (so the loss was by choice)
            "finished_at": now_iso(),
        })
    return rows


def finalize_if_over(game_id: str, kind: str, session: GameSession, seat_users: Dict[str, str], **save_kwargs) -> None:
    """When a game has just ended: record each player's result, then mark it finished.
    Safe to call repeatedly (results are upserted), so a failed attempt heals on the next one."""
    if not session.is_over or not seat_users:
        return
    store = get_store()
    try:
        if counts_for_stats(kind, session.mode):
            store.save_results(result_rows(game_id, kind, session, seat_users))
    except Exception:
        logger.exception("Could not record results for game %s", game_id)
        save_game(game_id, kind, session, seat_users, status="active", **save_kwargs)
        return
    save_game(game_id, kind, session, seat_users, status="finished", **save_kwargs)


def compute_stats(rows: List[dict]) -> dict:
    """Aggregate a player's result rows (newest first) into the numbers shown on the stats screen."""
    n = len(rows)
    wins = sum(r["outcome"] == "win" for r in rows)
    ties = sum(r["outcome"] == "tie" for r in rows)
    streak = 0
    for r in rows:
        if r["outcome"] != "win":
            break
        streak += 1

    def split(opp: str) -> dict:
        sub = [r for r in rows if r["opponent"] == opp]
        return {"games": len(sub), "wins": sum(r["outcome"] == "win" for r in sub)}

    return {
        "games": n, "wins": wins, "losses": n - wins - ties, "ties": ties,
        "win_rate": round(wins / n, 3) if n else 0.0,
        "avg_score": round(sum(r["score"] for r in rows) / n, 1) if n else 0.0,
        "best_score": max((r["score"] for r in rows), default=0),
        "best_play": max((r["best_play"] for r in rows), default=0),
        "total_plays": sum(r["plays"] for r in rows),
        "win_streak": streak,
        "forfeits": sum(bool(r.get("forfeit")) for r in rows),   # older rows have no such field
        "vs_computer": split("agent"), "vs_humans": split("human"),
        "recent": [{k: r[k] for k in ("game_id", "mode", "opponent", "outcome", "score", "opp_score", "finished_at")}
                   for r in rows[:10]],
    }
