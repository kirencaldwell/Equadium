"""
Summarises the anonymous per-game analytics records (collection "game_analytics") into score distributions.

    FIREBASE_PROJECT_ID=... FIREBASE_SERVICE_ACCOUNT='<service account json>' python -m web.api.analytics_report
    ... python -m web.api.analytics_report --csv turns.csv   # also one row per turn, for a spreadsheet

Run it on your own machine with the same two variables the API host uses (see README); it only reads.
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


def turn_rows(records):
    """One row per turn, for a spreadsheet: game, mode, end reason, turn number, seat kind, action, score, tiles."""
    for r in records:
        kinds = {p["seat"]: p["kind"] for p in r["players"]}
        for t in r["turn_log"]:
            yield [r["game_id"], r["mode"], r["end_reason"], t["n"], kinds.get(t["seat"]), t["action"], t["score"], " ".join(t["tiles"])]


def main(argv):
    import csv, json
    store = get_store()
    if not hasattr(store, "db") or getattr(store, "degraded", False):
        raise SystemExit("Firestore isn't configured: set FIREBASE_PROJECT_ID and FIREBASE_SERVICE_ACCOUNT.")
    records = [d.to_dict() for d in store.db.collection("game_analytics").stream()]
    print(json.dumps(report(records), indent=2))
    if "--csv" in argv:
        with open(argv[argv.index("--csv") + 1], "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["game_id", "mode", "end_reason", "turn", "seat_kind", "action", "score", "tiles"])
            w.writerows(turn_rows(records))


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
