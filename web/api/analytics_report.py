"""
Summarises the anonymous per-game analytics records (collection "game_analytics") into score distributions.

    python -m web.api.analytics_report            # reads Firestore with the same env vars as the server
"""
import statistics
from collections import Counter

from web.api.store import get_store


def percentiles(xs, qs=(10, 25, 50, 75, 90, 99)):
    xs = sorted(xs)
    return {f"p{q}": xs[min(len(xs) - 1, int(len(xs) * q / 100))] for q in qs} if xs else {}


def report(records) -> dict:
    plays = [t["score"] for r in records for t in r["turn_log"] if t["action"] == "play"]
    finals = [p["score"] for r in records for p in r["players"]]
    return {
        "games": len(records),
        "turn_score": {"plays": len(plays), "mean": round(statistics.mean(plays), 1) if plays else 0, **percentiles(plays), "max": max(plays, default=0)},
        "game_score": {"mean": round(statistics.mean(finals), 1) if finals else 0, **percentiles(finals), "max": max(finals, default=0)},
        "turns_per_game": percentiles([r["turns"] for r in records]),
        "end_reasons": dict(Counter(r["end_reason"] for r in records)),
        "play_share_by_action": dict(Counter(t["action"] for r in records for t in r["turn_log"])),
    }


if __name__ == "__main__":
    import json
    store = get_store()
    docs = [d.to_dict() for d in store.db.collection("game_analytics").stream()] if hasattr(store, "db") else list(store._analytics.values())
    print(json.dumps(report(docs), indent=2))
