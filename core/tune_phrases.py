"""
tune_phrases.py

Hill-climbs tile counts using bot-vs-bot games. The score rewards: winning scores near a target,
few forced swaps, many different phrase types, and few trivial phrases (x1 padding, number-only
equations). Points per tile stay as configured. Needs `core.phrase_report` metrics.

Usage (from the repo root):
    python -m core.tune_phrases --iters 25 --games 20 --out tune_phrases.json
"""
import argparse
import json
import math
import random
import sys
from concurrent.futures import ProcessPoolExecutor

from core import phrase_report as pr
from core.bag_report import BASE

MIN_COUNT, MAX_COUNT = 1, 20
FIXED = {"i"}  # the bots only just learned to play it; leave its count alone


def loss(m, target, bag_cap):
    if not m.get("games"):
        return 1e9
    bag = m["bag_total"]
    return (abs(m["winner"] - target) / target
            + 0.08 * m["swaps"]
            + 0.5 * m["times_one_numeric_share"]
            + 0.3 * (math.log2(len(pr.TYPES)) - m["type_entropy_bits"])
            + 0.01 * max(0, bag - bag_cap))


def fmt(m):
    return (f"bag={m['bag_total']} winner={m['winner']:.0f}±{m['winner_sd']:.0f} swaps={m['swaps']:.1f} "
            f"stalled={m['stalled']:.0%} types/game={m['types_per_game']:.1f} entropy={m['type_entropy_bits']:.2f}b "
            f"trivial={m['times_one_numeric_share']:.0%}")


def propose(counts, rng):
    new = dict(counts)
    movable = [s for s in new if s != "=" and s not in FIXED]
    for sym in rng.sample(movable, rng.randint(1, 4)):
        step = rng.choice([-2, -1, 1, 2])
        new[sym] = max(MIN_COUNT, min(MAX_COUNT, new[sym] + step))
    if rng.random() < 0.2:
        new["="] = max(8, new["="] + rng.choice([-2, 2]))
    return new


def score(pool, counts, games, seed):
    m = pr.evaluate(pool, counts, games, seed)
    m["bag_total"] = sum(v for k, v in counts.items() if k != "=")
    return m


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=25)
    ap.add_argument("--games", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--target", type=float, default=400)
    ap.add_argument("--bag-cap", type=int, default=180, help="tiles above this are penalised")
    ap.add_argument("--start", help="JSON file with {tile: count} to start from")
    ap.add_argument("--out", default="tune_phrases.json")
    args = ap.parse_args(argv)
    rng = random.Random(args.seed)
    best_counts = {**BASE, **json.load(open(args.start))} if args.start else dict(BASE)
    history = []
    with ProcessPoolExecutor(args.workers, initializer=pr._init, initargs=(["search", "search"],)) as pool:
        best = score(pool, best_counts, args.games, args.seed)
        best_loss = loss(best, args.target, args.bag_cap)
        print(f"start loss={best_loss:.3f} {fmt(best)}", flush=True)
        history.append({"iter": 0, "loss": best_loss, "metrics": best, "counts": best_counts})
        for i in range(1, args.iters + 1):
            cand = propose(best_counts, rng)
            seed = args.seed + 1000 * i  # fresh games each round so we don't overfit one set
            m = score(pool, cand, args.games, seed)
            l = loss(m, args.target, args.bag_cap)
            diff = {k: (best_counts[k], v) for k, v in cand.items() if best_counts[k] != v}
            if l < best_loss:
                inc = score(pool, best_counts, args.games, seed)
                inc_loss = loss(inc, args.target, args.bag_cap)
                if l < inc_loss:
                    best_counts, best, best_loss = cand, m, l
                    print(f"[{i}] ACCEPT loss={l:.3f} (incumbent {inc_loss:.3f}) {diff}\n      {fmt(m)}", flush=True)
                    history.append({"iter": i, "loss": l, "metrics": m, "counts": cand})
                    json.dump({"best_counts": best_counts, "best_metrics": best, "history": history},
                              open(args.out, "w"), indent=1)
                    continue
            print(f"[{i}] reject loss={l:.3f} {diff}  {fmt(m)}", flush=True)
    json.dump({"best_counts": best_counts, "best_metrics": best, "history": history}, open(args.out, "w"), indent=1)
    print("\nBEST COUNTS:\n" + json.dumps(best_counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
