"""
tune_tiles.py

Searches for tile *counts* (points stay as configured) that make bot-vs-bot
games hit a target winning score with almost no forced turns.

A "forced skip" is a turn where the search agent found no legal play and had
to swap tiles instead (the agent only swaps when it has nothing to play).

Usage (from the repo root):
    python -m core.tune_tiles --baseline
    python -m core.tune_tiles --iters 40 --games 20 --out tune_result.json
    python -m core.tune_tiles --validate tune_result.json --games 60 --seed 1000
"""
import argparse
import copy
import json
import random
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

from core import stress_test
from core.game_config import CONFIG

BASE_COUNTS = {s: d["count"] for s, d in CONFIG["tiles"].items()}

# Tiles whose count is held fixed. The equals pile is a separate free resource.
FIXED = set()

# Never let a tile type drop out of the game entirely, and cap runaway growth.
MIN_COUNT, MAX_COUNT = 1, 20


def _init(agent_types):
    stress_test._init_worker(4, agent_types)


def _tail_swaps(seq):
    """Swaps in the closing run of non-plays (the 'both players stuck' ending)."""
    n = 0
    for a in reversed(seq):
        if a == "play":
            break
        n += 1
    return n


def _run(task):
    counts, seed = task
    for sym, n in counts.items():
        CONFIG["tiles"][sym]["count"] = n
    r = stress_test.play_one_game(seed)
    if r["crashed"] or "scores" not in r:
        return {"crashed": True, "problems": r["problems"]}
    scores = list(r["scores"].values())
    return {
        "crashed": False,
        "problems": r["problems"],
        "winner": max(scores),
        "loser": min(scores),
        "turns": r["turns"],
        "swaps": r["actions"].get("swap", 0),
        "tail_swaps": _tail_swaps(r["action_seq"]),
        "plays": r["actions"].get("play", 0),
        "end": r["end_reason"],
        "illegal": r["illegal_agent_moves"],
        "seconds": r["seconds"],
    }


class Evaluator:
    def __init__(self, workers, agent_types=("search", "search")):
        self.pool = ProcessPoolExecutor(workers, initializer=_init, initargs=(list(agent_types),))

    def evaluate(self, counts, games, seed):
        results = list(self.pool.map(_run, [(counts, seed + i) for i in range(games)]))
        ok = [r for r in results if not r["crashed"]]
        if not ok:
            return {"crashed": len(results)}
        n = len(ok)
        return {
            "games": n,
            "crashed": len(results) - n,
            "problems": sum(bool(r["problems"]) for r in ok),
            "winner_score": statistics.mean(r["winner"] for r in ok),
            "winner_sd": statistics.pstdev([r["winner"] for r in ok]),
            "swaps_per_game": statistics.mean(r["swaps"] for r in ok),
            "mid_swaps": statistics.mean(r["swaps"] - r["tail_swaps"] for r in ok),
            "tail_swaps": statistics.mean(r["tail_swaps"] for r in ok),
            "plays_per_game": statistics.mean(r["plays"] for r in ok),
            "turns": statistics.mean(r["turns"] for r in ok),
            "swap_share": sum(r["swaps"] for r in ok) / max(1, sum(r["turns"] for r in ok)),
            "ends": dict(Counter(r["end"] for r in ok)),
            "illegal": sum(r["illegal"] for r in ok),
            "seconds": statistics.mean(r["seconds"] for r in ok),
        }


def loss(m, target, swap_weight):
    if "winner_score" not in m:
        return 1e9
    return abs(m["winner_score"] - target) / target + swap_weight * m["mid_swaps"] + 0.25 * swap_weight * m["tail_swaps"]


def fmt(m):
    if "winner_score" not in m:
        return "all games crashed"
    return (f"winner={m['winner_score']:.0f}±{m['winner_sd']:.0f} swaps mid={m['mid_swaps']:.1f} tail={m['tail_swaps']:.1f} "
            f"plays/game={m['plays_per_game']:.1f} turns={m['turns']:.0f} ends={m['ends']} "
            f"illegal={m['illegal']} {m['seconds']:.0f}s/game")


def propose(counts, rng):
    """Nudge a few tile counts up or down."""
    new = dict(counts)
    movable = [s for s in new if s not in FIXED]
    if rng.random() < 0.15:  # change the overall tile supply
        f = rng.choice([0.85, 0.9, 1.1, 1.15])
        return {s: (n if s in FIXED else max(MIN_COUNT, min(MAX_COUNT, round(n * f)))) for s, n in new.items()}
    for sym in rng.sample(movable, rng.randint(1, 4)):
        step = rng.choice([-3, -2, -1, 1, 2, 3])
        if rng.random() < 0.25:
            step = round(new[sym] * rng.choice([-0.5, 0.5, 1.0]))
        new[sym] = max(MIN_COUNT, min(MAX_COUNT, new[sym] + step))
    return new


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true", help="just evaluate the current config")
    ap.add_argument("--validate", help="re-evaluate the best counts stored in this result file")
    ap.add_argument("--iters", type=int, default=30)
    ap.add_argument("--games", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=9)
    ap.add_argument("--target", type=float, default=400)
    ap.add_argument("--swap-weight", type=float, default=0.12,
                    help="loss added per forced swap per game")
    ap.add_argument("--start", help="start from the best counts in this result file")
    ap.add_argument("--out", default="tune_result.json")
    args = ap.parse_args(argv)

    ev = Evaluator(args.workers)
    rng = random.Random(args.seed)

    if args.validate:
        counts = json.load(open(args.validate))["best_counts"]
        print(fmt(ev.evaluate(counts, args.games, args.seed)))
        return 0
    if args.baseline:
        print(fmt(ev.evaluate(BASE_COUNTS, args.games, args.seed)))
        return 0

    best_counts = json.load(open(args.start))["best_counts"] if args.start else dict(BASE_COUNTS)
    best = ev.evaluate(best_counts, args.games, args.seed)
    best_loss = loss(best, args.target, args.swap_weight)
    print(f"start  loss={best_loss:.3f}  {fmt(best)}", flush=True)
    history = [{"iter": 0, "loss": best_loss, "metrics": best, "counts": best_counts}]

    for i in range(1, args.iters + 1):
        cand = propose(best_counts, rng)
        # fresh seeds each round so we don't overfit one set of games
        seed = args.seed + 1000 * i
        m = ev.evaluate(cand, args.games, seed)
        l = loss(m, args.target, args.swap_weight)
        if l < best_loss:
            # re-score the incumbent on the same seeds so the comparison is fair
            inc = ev.evaluate(best_counts, args.games, seed)
            inc_loss = loss(inc, args.target, args.swap_weight)
            if l < inc_loss:
                diff = {k: (best_counts[k], v) for k, v in cand.items() if best_counts[k] != v}
                best_counts, best, best_loss = cand, m, l
                print(f"[{i}] ACCEPT loss={l:.3f} (incumbent {inc_loss:.3f}) {diff}\n      {fmt(m)}", flush=True)
                history.append({"iter": i, "loss": l, "metrics": m, "counts": cand})
                json.dump({"best_counts": best_counts, "best_metrics": best, "history": history},
                          open(args.out, "w"), indent=2)
                continue
        print(f"[{i}] reject  loss={l:.3f}  {fmt(m)}", flush=True)

    json.dump({"best_counts": best_counts, "best_metrics": best, "history": history},
              open(args.out, "w"), indent=2)
    print("\nBEST COUNTS:")
    print(json.dumps(best_counts, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
