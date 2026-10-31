"""
beta_playtest.py

Plays bot-vs-bot games on a registered config (core/configs.py) and reports how it plays,
next to the standard game, so a new idea can be judged before it goes to a beta room.

Usage (from the repo root):
    python -m core.beta_playtest --list
    python -m core.beta_playtest beta-small-rack --games 20 --workers 4
    python -m core.beta_playtest beta-small-rack --games 20 --no-compare --json out.json

Reported per config: invariant problems (a crash or broken rule is a bug in the config or the engine),
average winning/losing score, game length, how games end, how often a bot is forced to swap because it
had no legal play ("stuck" rate), and first-player advantage.
"""
import argparse
import json
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor

from core import stress_test
from core.configs import fingerprint, get_config, list_configs


def run(config_id: str, games: int, seed: int, workers: int, agents=("search", "search")) -> dict:
    seeds = [seed + i for i in range(games)]
    if workers > 1:
        with ProcessPoolExecutor(workers, initializer=stress_test._init_worker, initargs=(4, list(agents), config_id)) as pool:
            results = list(pool.map(stress_test.play_one_game, seeds))
    else:
        stress_test._init_worker(4, list(agents), config_id)
        results = [stress_test.play_one_game(s) for s in seeds]
    return report(config_id, results)


def report(config_id: str, results: list) -> dict:
    ok = [r for r in results if not r["crashed"] and "scores" in r]
    out = {"config": config_id, "fingerprint": fingerprint(get_config(config_id)), "games": len(results),
           "crashed": sum(r["crashed"] for r in results),
           "games_with_problems": sum(bool(r["problems"]) for r in results),
           "problems": [p for r in results for p in r["problems"]][:5]}
    if not ok:
        return out
    turns = [r["turns"] for r in ok]
    swaps = sum(r["actions"].get("swap", 0) for r in ok)
    out.update({
        "avg_winning_score": round(statistics.mean(max(r["scores"].values()) for r in ok), 1),
        "avg_losing_score": round(statistics.mean(min(r["scores"].values()) for r in ok), 1),
        "avg_turns": round(statistics.mean(turns), 1),
        "end_reasons": dict(stress_test.Counter(r["end_reason"] for r in ok)),
        "stuck_rate": round(swaps / max(1, sum(turns)), 3),
        "first_player_win_rate": round(sum(r["winners"] == [list(r["scores"])[0]] for r in ok) / len(ok), 2),
        "illegal_bot_moves": sum(r["illegal_agent_moves"] for r in ok),
    })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bot-vs-bot playtest of a game config")
    ap.add_argument("config", nargs="?", help="registered config id")
    ap.add_argument("--list", action="store_true", help="list registered configs")
    ap.add_argument("--games", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--no-compare", action="store_true", help="skip the standard-config baseline")
    ap.add_argument("--json", help="write the reports to this file")
    args = ap.parse_args(argv)
    if args.list or not args.config:
        for c in list_configs():
            print(f"{c['id']:22} {c['fingerprint']}  {c['name']}: {c['description']}")
        return 0
    get_config(args.config)   # fail fast on a bad id
    ids = [args.config] if args.no_compare or args.config == "standard" else ["standard", args.config]
    reports = [run(i, args.games, args.seed, args.workers) for i in ids]
    print(json.dumps(reports, indent=2))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(reports, f, indent=2)
    return 1 if any(r["games_with_problems"] for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
