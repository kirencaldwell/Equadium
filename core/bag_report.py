"""
bag_report.py

Plays bot-vs-bot games for several candidate tile bags and reports how each one behaves:
game length, forced swaps, which tiles are played rarely, which are left stranded in racks
or swapped away, and the spread of scores. Used to pick the bag in game_config.py.

Usage (from the repo root):
    python -m core.bag_report --games 20 --workers 4 --out bag_report.json
    python -m core.bag_report --candidates my_candidates.json   # {"name": {"tile": count, ...}, ...}
Candidates only need to list the tiles they change; "=" sets the equals-tile count.
"""
import argparse
import json
import statistics
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

from core import stress_test
from core.game_config import CONFIG
from core.session import GameSession

BASE = {s: d["count"] for s, d in CONFIG["tiles"].items()}
BASE["="] = CONFIG["equals_tile"]["count"]


def apply_counts(counts):
    for s, n in counts.items():
        if s == "=":
            CONFIG["equals_tile"]["count"] = n
        else:
            CONFIG["tiles"][s]["count"] = n


def _init(agent_types):
    stress_test._init_worker(4, agent_types)


def _run(task):
    counts, seed = task
    apply_counts(counts)
    swapped = Counter()
    orig = GameSession.__init__

    def patched(self, *a, **k):
        orig(self, *a, **k)
        inner = self.game.swap_tiles

        def wrapped(player, tiles, *aa, **kk):
            for t in tiles:
                swapped[t.symbol] += 1
            return inner(player, tiles, *aa, **kk)
        self.game.swap_tiles = wrapped
        stress_test_last["game"] = self.game

    stress_test_last = {}
    GameSession.__init__ = patched
    try:
        r = stress_test.play_one_game(seed)
    finally:
        GameSession.__init__ = orig
    if r["crashed"] or "scores" not in r:
        return {"crashed": True, "problems": r["problems"]}
    game = stress_test_last["game"]
    stranded = Counter(t.symbol for p in game.players for t in p.rack)
    on_board = Counter(t.symbol for row in game.board.grid for t in row if t is not None)
    sc = list(r["scores"].values())
    seq = r["action_seq"]
    tail = 0
    for a in reversed(seq):
        if a == "play":
            break
        tail += 1
    return {"crashed": False, "problems": r["problems"], "winner": max(sc), "loser": min(sc),
            "turns": r["turns"], "swaps": r["actions"].get("swap", 0), "tail": tail,
            "plays": r["actions"].get("play", 0), "end": r["end_reason"],
            "illegal": r["illegal_agent_moves"], "bag_left": len(game.tile_bag),
            "stranded": dict(stranded), "board": dict(on_board), "swapped": dict(swapped),
            "seconds": r["seconds"]}


def evaluate(pool, counts, games, seed):
    res = [r for r in pool.map(_run, [(counts, seed + i) for i in range(games)]) if not r["crashed"]]
    n = len(res)
    if not n:
        return {"games": 0}
    board, stranded, swapped = Counter(), Counter(), Counter()
    for r in res:
        board.update(r["board"]); stranded.update(r["stranded"]); swapped.update(r["swapped"])
    tiles = {s: c for s, c in counts.items() if s != "="}
    # per-tile "use rate": fraction of the copies in the bag that end up on the board
    use = {s: board[s] / (n * (c + (1 if s == "x" else 0))) for s, c in tiles.items()}
    total_tiles = sum(counts.values())
    return {
        "games": n, "bag_total": sum(tiles.values()), "total_with_equals": total_tiles,
        "problems": sum(bool(r["problems"]) for r in res), "illegal": sum(r["illegal"] for r in res),
        "winner": statistics.mean(r["winner"] for r in res),
        "winner_sd": statistics.pstdev([r["winner"] for r in res]),
        "gap": statistics.mean(r["winner"] - r["loser"] for r in res),
        "turns": statistics.mean(r["turns"] for r in res),
        "plays": statistics.mean(r["plays"] for r in res),
        "swaps": statistics.mean(r["swaps"] for r in res),
        "mid_swaps": statistics.mean(r["swaps"] - r["tail"] for r in res),
        "swap_share": sum(r["swaps"] for r in res) / max(1, sum(r["turns"] for r in res)),
        "bag_left": statistics.mean(r["bag_left"] for r in res),
        "stranded_per_game": sum(stranded.values()) / n,
        "ends": dict(Counter(r["end"] for r in res)),
        "use_rate": {s: round(v, 2) for s, v in use.items()},
        "use_spread": statistics.pstdev(use.values()),
        "swapped_per_game": {s: round(swapped[s] / n, 2) for s in tiles},
        "stranded_tiles": {s: round(stranded[s] / n, 2) for s in tiles},
        "seconds": statistics.mean(r["seconds"] for r in res),
    }


def show(name, m):
    if not m["games"]:
        print(f"{name}: all crashed"); return
    low = sorted(m["use_rate"].items(), key=lambda kv: kv[1])[:6]
    print(f"{name:14s} bag={m['bag_total']:3d} win={m['winner']:.0f}±{m['winner_sd']:.0f} gap={m['gap']:.0f} "
          f"turns={m['turns']:.0f} plays={m['plays']:.1f} swaps={m['swaps']:.1f} (mid {m['mid_swaps']:.1f}) "
          f"bagleft={m['bag_left']:.0f} stranded={m['stranded_per_game']:.1f} usespread={m['use_spread']:.2f} "
          f"ends={m['ends']} problems={m['problems']} illegal={m['illegal']}\n"
          f"{'':14s} least used: {low}", flush=True)


def scaled(base, scale, flatten=0.0, keep=()):
    """Scale every count and pull it toward the mean count by `flatten` (0..1)."""
    tiles = [s for s in base if s not in ("=",)]
    mean = statistics.mean(base[s] for s in tiles)
    out = dict(base)
    for s in tiles:
        if s in keep:
            continue
        out[s] = max(1, round(scale * (base[s] * (1 - flatten) + mean * flatten)))
    out["="] = max(8, round(scale * base["="]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--candidates", help="JSON file {name: {tile: count}}; default is a built-in grid")
    ap.add_argument("--out", default="bag_report.json")
    args = ap.parse_args(argv)
    if args.candidates:
        cands = {k: {**BASE, **v} for k, v in json.load(open(args.candidates)).items()}
    else:
        cands = {"current": dict(BASE)}
        for scale in (0.8, 0.65, 0.5):
            for flat in (0.0, 0.5):
                cands[f"s{scale}_f{flat}"] = scaled(BASE, scale, flat)
    out = {}
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(["search", "search"],)) as pool:
        for name, counts in cands.items():
            m = evaluate(pool, counts, args.games, args.seed)
            out[name] = {"counts": counts, "metrics": m}
            show(name, m)
            json.dump(out, open(args.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
