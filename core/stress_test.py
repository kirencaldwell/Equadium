"""
stress_test.py

Plays many agent-vs-agent games through GameSession (the same code path the
web API uses) and checks engine invariants as it goes.

Usage (from the repo root):
    python -m core.stress_test --games 20 --seed 0
    python -m core.stress_test --games 100 --workers 4 --playbook-length 3 --json out.json

Exit code is non-zero if any game crashed or broke an invariant, so this can
be dropped into CI.

Invariants checked
  per turn : tiles are conserved (bag + racks + board never gain/lose tiles),
             scores never decrease, turn order alternates, no rack overflow
  per game : terminates within the turn limit, every equation on the final
             board is valid, no exceptions
Also reported (not failures): agent moves the engine rejected, per-turn time.
"""

import argparse
import json
import os
import random
import statistics
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from typing import List, Optional

from core.game_config import CONFIG
from core.session import GameSession

_worker_playbook = None
_worker_agent_types = ["search", "search"]


def _init_worker(playbook_length: int, agent_types):
    """Per-process setup. The (slow) playbook is only built if a playbook agent is requested."""
    global _worker_playbook, _worker_agent_types
    _worker_agent_types = agent_types
    if "playbook" in agent_types:
        from core.math_playbook import MathPlaybook
        _worker_playbook = MathPlaybook(CONFIG, max_length=playbook_length)


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------
def _expected_symbol_counts(config: dict) -> Counter:
    counts = Counter({sym: d["count"] for sym, d in config["tiles"].items()})
    counts["x"] += 1  # the seed tile is created outside the bag
    counts["="] = config["equals_tile"]["count"]
    return counts


def _all_symbol_counts(game) -> Counter:
    counts = Counter()
    for t in game.tile_bag:
        counts[t.symbol] += 1
    for t in game.equals_bag:
        counts[t.symbol] += 1
    for p in game.players:
        for t in p.rack:
            counts[t.symbol] += 1
    for row in game.board.grid:
        for t in row:
            if t is not None:
                counts[t.symbol] += 1
    return counts


def check_turn_invariants(session, expected: Counter, prev_scores: List[int]) -> List[str]:
    game = session.game
    problems = []
    if _all_symbol_counts(game) != expected:
        diff = _all_symbol_counts(game) - expected, expected - _all_symbol_counts(game)
        problems.append(f"tile conservation broken (extra={dict(diff[0])}, missing={dict(diff[1])})")
    for p, prev in zip(game.players, prev_scores):
        if p.score < prev:
            problems.append(f"{p.name}'s score decreased {prev} -> {p.score}")
    max_rack = game.config["max_rack_size"]
    for p in game.players:
        normal = sum(1 for t in p.rack if t.symbol != "=")
        if normal > max_rack:
            problems.append(f"{p.name} holds {normal} tiles, max is {max_rack}")
    return problems


def check_final_board(game) -> List[str]:
    """Every horizontal/vertical run of 2+ tiles on the board must be a valid equation."""
    problems = []
    board = game.board
    for r in range(board.height):
        for c in range(board.width):
            if board.grid[r][c] is None:
                continue
            for dr, dc in ((0, 1), (1, 0)):
                # only start at the beginning of a run
                pr, pc = r - dr, c - dc
                if 0 <= pr < board.height and 0 <= pc < board.width and board.grid[pr][pc] is not None:
                    continue
                run, tiles = board._get_contiguous_string(r, c, dr, dc)
                if len(tiles) > 1:
                    ok, msg = game.math.validate_equation(run)
                    if not ok:
                        problems.append(f"invalid equation on final board at ({r},{c}) "
                                        f"{'H' if dc else 'V'}: '{run}' ({msg})")
    return problems


# ---------------------------------------------------------------------------
# One game
# ---------------------------------------------------------------------------
def play_one_game(seed: int, playbook=None, agent_types=None) -> dict:
    agent_types = agent_types or _worker_agent_types
    playbook = playbook or _worker_playbook
    random.seed(seed)
    result = {"seed": seed, "crashed": False, "problems": []}
    start = time.time()
    try:
        session = GameSession("agent_vs_agent", CONFIG, playbook=playbook, agent_types=agent_types)
        game = session.game
        expected = _expected_symbol_counts(CONFIG)
        max_steps = CONFIG.get("max_turns", 75) * 2
        turn_times = []
        steps = 0
        last_mover: Optional[str] = None
        while not session.is_over and steps < max_steps:
            mover = session.current_player.name
            if mover == last_mover:
                result["problems"].append(f"turn order broken: {mover} moved twice in a row")
            prev_scores = [p.score for p in game.players]
            t0 = time.time()
            session.step_agent()
            turn_times.append(time.time() - t0)
            last_mover = mover
            result["problems"] += [f"turn {steps}: {p}" for p in check_turn_invariants(session, expected, prev_scores)]
            steps += 1
            if result["problems"]:
                break
        if not session.is_over and not result["problems"]:
            result["problems"].append("game did not terminate within the step budget")
        result["problems"] += check_final_board(game)

        actions = Counter(rec.action for rec in session.history)
        res = game.get_final_results()
        result.update({
            "end_reason": game.end_reason,
            "turns": game.turns_played,
            "scores": {p.name: p.score for p in game.players},
            "winners": game.winners,
            "actions": dict(actions),
            "illegal_agent_moves": session.illegal_agent_moves,
            "max_turn_seconds": max(turn_times) if turn_times else 0.0,
            "stats": res,
        })
    except Exception:
        result["crashed"] = True
        result["problems"].append(traceback.format_exc())
    result["seconds"] = time.time() - start
    return result


# ---------------------------------------------------------------------------
# Aggregate + report
# ---------------------------------------------------------------------------
def summarize(results: List[dict]) -> dict:
    ok = [r for r in results if not r["crashed"] and "scores" in r]
    summary = {
        "games": len(results),
        "crashed": sum(r["crashed"] for r in results),
        "games_with_problems": sum(bool(r["problems"]) for r in results),
        "illegal_agent_moves": sum(r.get("illegal_agent_moves", 0) for r in ok),
    }
    if ok:
        first, second = [], []
        wins = Counter()
        for r in ok:
            names = list(r["scores"])
            first.append(r["scores"][names[0]])
            second.append(r["scores"][names[1]])
            if len(r["winners"]) > 1:
                wins["tie"] += 1
            else:
                wins["first_player" if r["winners"][0] == names[0] else "second_player"] += 1
        summary.update({
            "end_reasons": dict(Counter(r["end_reason"] for r in ok)),
            "avg_turns": round(statistics.mean(r["turns"] for r in ok), 1),
            "avg_score_first_player": round(statistics.mean(first), 1),
            "avg_score_second_player": round(statistics.mean(second), 1),
            "outcomes": dict(wins),
            "actions": dict(sum((Counter(r["actions"]) for r in ok), Counter())),
            "avg_game_seconds": round(statistics.mean(r["seconds"] for r in ok), 2),
            "slowest_turn_seconds": round(max(r["max_turn_seconds"] for r in ok), 2),
        })
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Agent-vs-agent stress test")
    ap.add_argument("--games", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0, help="game i uses seed (seed + i)")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--playbook-length", type=int,
                    default=int(os.environ.get("EQUADIUM_PLAYBOOK_LENGTH", "4")))
    ap.add_argument("--agents", nargs=2, default=["search", "search"], metavar=("FIRST", "SECOND"),
                    help="algorithm for each seat: search | playbook (default: search search)")
    ap.add_argument("--json", help="write full per-game results to this file")
    args = ap.parse_args(argv)

    seeds = [args.seed + i for i in range(args.games)]
    t0 = time.time()
    if args.workers > 1:
        with ProcessPoolExecutor(args.workers, initializer=_init_worker,
                                 initargs=(args.playbook_length, args.agents)) as pool:
            results = []
            for r in pool.map(play_one_game, seeds):
                results.append(r)
                _progress(r, len(results), args.games)
    else:
        _init_worker(args.playbook_length, args.agents)
        results = []
        for seed in seeds:
            r = play_one_game(seed)
            results.append(r)
            _progress(r, len(results), args.games)

    summary = summarize(results)
    summary["wall_seconds"] = round(time.time() - t0, 1)
    print("\n=== STRESS TEST SUMMARY ===")
    print(json.dumps(summary, indent=2))
    bad = [r for r in results if r["problems"]]
    for r in bad:
        print(f"\n--- seed {r['seed']} FAILED ---")
        for p in r["problems"]:
            print(p)
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"summary": summary, "games": results}, f, indent=2, default=str)
    return 1 if bad else 0


def _progress(r: dict, done: int, total: int):
    status = "FAIL" if r["problems"] else "ok"
    scores = r.get("scores", {})
    print(f"[{done}/{total}] seed={r['seed']} {status} turns={r.get('turns')} "
          f"scores={scores} end={r.get('end_reason')} ({r['seconds']:.1f}s)", flush=True)


if __name__ == "__main__":
    sys.exit(main())
