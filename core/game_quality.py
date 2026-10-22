"""
game_quality.py

A scorecard for how well Equadium plays, measured from bot-vs-bot games. Each metric has a target band.

  1. Options per turn  : how many legal plays a turn offers (estimated: every candidate the search agent finds,
                         times the share of a sample of them the rules engine accepts), and how often a player
                         is stuck (no play, so a swap).
  2. Connectivity      : tiles of the board each play builds on, and how many equations one play forms.
  3. Reward for length : do long phrases pay? Correlation of phrase length with points, and the best long
                         option against the best short option on the same turn.
  4. Variety           : phrase types per game, phrases repeated across games, cosmetic plays.
  5. Game shape        : turns, winner score, margin, lead changes, how often the player behind at half-time wins.
  6. Skill signal      : a full-strength bot against a weaker one (--skill); it should win, but not every game.

Usage (from the repo root):
    python -m core.game_quality --games 200 --workers 4 --out quality.json
    python -m core.game_quality --games 100 --skill --out skill.json
"""
import argparse
import json
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

from core import phrase_report, session as session_mod, stress_test
from core.game_config import CONFIG
from core.game_entities import is_cosmetic_equation
from core.search_agent import SearchAgent

SAMPLE_VERIFY = 8      # candidates checked by the rules engine per turn to estimate how many are legal
LONG, SHORT = 9, 6     # phrase lengths (tiles, '=' included) counted as long / short

TARGETS = {
    "median_legal_options": ">= 20",
    "turns_with_under_3_options": "<= 10%",
    "stuck_rate": "<= 10%",
    "board_tiles_per_play": ">= 2",
    "multi_equation_plays": ">= 15%",
    "length_points_corr": ">= 0.5",
    "long_beats_short": ">= 70%",
    "types_per_game": ">= 9",
    "shared_with_other_games": "<= 20%",
    "cosmetic_share": "<= 5%",
    "winner_score": "350-450",
    "trailing_at_half_wins": "25-45%",
    "strong_bot_win_rate": "65-75%",
}


def _weak_agent(name, config):
    return SearchAgent(name, config, max_len=3, max_inner=1, max_verify=5, time_budget=1.5)


def _run(task):
    seed, skill = task
    turns, plays = [], []
    orig_init = session_mod.GameSession.__init__
    orig_make = session_mod.make_agent

    def make_agent(agent_type, name, config, playbook=None):
        return _weak_agent(name, config) if agent_type == "weak" else orig_make(agent_type, name, config, playbook)

    def patched(self, *a, **k):
        orig_init(self, *a, **k)
        game = self.game
        inner_score = game.score_play

        def score_play(equations_data, move_tiles):
            pts = inner_score(equations_data, move_tiles)
            placed = {id(t) for _, _, t in move_tiles}
            eqs = [e for e, _ in equations_data]
            plays.append({"player": game.players[game.current_turn_index].name, "points": pts, "eqs": eqs,
                          "n_eq": len(equations_data),
                          "board_tiles": len({id(t) for _, ts in equations_data for t in ts if id(t) not in placed}),
                          "length": max(len(ts) for _, ts in equations_data),
                          "cosmetic": all(is_cosmetic_equation([t.symbol for t in ts]) for _, ts in equations_data)})
            return pts
        game.score_play = score_play
        for seat in self.seats.values():
            agent = seat.agent
            if not isinstance(agent, SearchAgent):
                continue
            inner_turn = agent.handle_turn

            def handle_turn(g, agent=agent, inner_turn=inner_turn):
                move = inner_turn(g)
                turns.append(_measure_turn(g, agent, move))
                return move
            agent.handle_turn = handle_turn

    session_mod.GameSession.__init__ = patched
    session_mod.make_agent = make_agent
    stress_test._worker_agent_types = ["search", "weak"] if skill and seed % 2 == 0 else \
        ["weak", "search"] if skill else ["search", "search"]
    try:
        r = stress_test.play_one_game(seed)
    finally:
        session_mod.GameSession.__init__ = orig_init
        session_mod.make_agent = orig_make
    if r["crashed"] or "scores" not in r:
        return {"crashed": True, "problems": r["problems"][:1]}
    names = list(r["scores"])
    strong = None
    if skill:
        strong = names[0] if seed % 2 == 0 else names[1]
    return {"crashed": False, "turns": turns, "plays": plays, "scores": r["scores"], "winners": r["winners"],
            "names": names, "end": r["end_reason"], "n_turns": r["turns"], "actions": r["actions"],
            "action_seq": r["action_seq"], "strong": strong}


def _measure_turn(game, agent, move):
    """How many plays did this turn offer, and what were the best long and short ones worth?"""
    cands = getattr(agent, "last_candidates", []) or []
    n = len(cands)
    out = {"player": game.players[game.current_turn_index].name, "candidates": n, "played": bool(move and move.is_play)}
    if not n:
        out.update(legal_ratio=0.0, est_legal=0, best_long=None, best_short=None)
        return out
    step = max(1, n // SAMPLE_VERIFY)
    sample = cands[::step][:SAMPLE_VERIFY]
    legal, best_long, best_short = 0, None, None
    player = game.players[game.current_turn_index]
    for where, cand in sample:
        mv = agent._to_move(game, player, where, cand)
        if mv is None:
            continue
        eqs, err = game.evaluate_play(mv.tiles_to_play, where[2])
        if err is not None:
            continue
        legal += 1
        pts = type(game).score_play(eqs, mv.tiles_to_play)
        length = len(cand.tokens)
        if length >= LONG:
            best_long = max(best_long or 0, pts)
        if length <= SHORT:
            best_short = max(best_short or 0, pts)
    ratio = legal / len(sample)
    out.update(legal_ratio=ratio, est_legal=n * ratio, best_long=best_long, best_short=best_short)
    return out


def _pearson(xs, ys):
    if len(xs) < 3:
        return 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx and sy else 0.0


def _shape(g):
    """Lead changes and whether the player behind at half-time came back."""
    names = g["names"]
    score = {n: 0 for n in names}
    leaders = []
    for p in g["plays"]:
        score[p["player"]] += p["points"]
        a, b = score[names[0]], score[names[1]]
        leaders.append(names[0] if a > b else names[1] if b > a else None)
    changes, last = 0, None
    for ld in leaders:
        if ld and last and ld != last:
            changes += 1
        last = ld or last
    half = leaders[len(leaders) // 2] if leaders else None
    winner = g["winners"][0] if len(g["winners"]) == 1 else None
    comeback = None if not half or not winner else (winner != half)
    return changes, comeback


def summarize(games):
    turns = [t for g in games for t in g["turns"]]
    plays = [p for g in games for p in g["plays"]]
    opts = sorted(t["est_legal"] for t in turns)
    paired = [t for t in turns if t["best_long"] is not None and t["best_short"] is not None]
    shapes = [_shape(g) for g in games]
    comebacks = [c for _, c in shapes if c is not None]
    winners = [max(g["scores"].values()) for g in games]
    margins = [max(g["scores"].values()) - min(g["scores"].values()) for g in games]
    variety = phrase_report.analyse([g["plays"] for g in games])
    m = {
        "games": len(games),
        # 1. options
        "median_legal_options": statistics.median(opts),
        "p10_legal_options": opts[len(opts) // 10],
        "turns_with_under_3_options": sum(o < 3 for o in opts) / len(opts),
        "stuck_rate": sum(not t["played"] for t in turns) / len(turns),
        "candidates_per_turn": statistics.mean(t["candidates"] for t in turns),
        "legal_share_of_candidates": statistics.mean(t["legal_ratio"] for t in turns if t["candidates"]),
        # 2. connectivity
        "board_tiles_per_play": statistics.mean(p["board_tiles"] for p in plays),
        "multi_equation_plays": sum(p["n_eq"] > 1 for p in plays) / len(plays),
        # 3. length
        "phrase_length": statistics.mean(p["length"] for p in plays),
        "length_points_corr": _pearson([p["length"] for p in plays], [p["points"] for p in plays]),
        "long_beats_short": (sum(t["best_long"] > t["best_short"] for t in paired) / len(paired)) if paired else None,
        "turns_with_long_and_short_option": len(paired) / len(turns),
        "points_by_length": {k: round(statistics.mean(p["points"] for p in plays if min(p["length"] // 3 * 3, 15) == k), 1)
                             for k in sorted({min(p["length"] // 3 * 3, 15) for p in plays})},
        # 4. variety
        "types_per_game": variety["types_per_game"],
        "shared_with_other_games": variety["shared_with_other_games"],
        "cosmetic_share": sum(p["cosmetic"] for p in plays) / len(plays),
        # 5. shape
        "turns": statistics.mean(g["n_turns"] for g in games),
        "winner_score": statistics.mean(winners),
        "winner_sd": statistics.pstdev(winners),
        "margin": statistics.mean(margins),
        "lead_changes": statistics.mean(c for c, _ in shapes),
        "trailing_at_half_wins": (sum(comebacks) / len(comebacks)) if comebacks else None,
        "ends": dict(Counter(g["end"] for g in games)),
    }
    strong = [g for g in games if g["strong"]]
    if strong:
        m["strong_bot_win_rate"] = sum(g["winners"] == [g["strong"]] for g in strong) / len(strong)
    return m


def show(m):
    def fmt(v):
        if isinstance(v, float):
            return f"{v:.1%}" if abs(v) <= 1 and not isinstance(v, bool) else f"{v:.1f}"
        return str(v)
    for k, v in m.items():
        target = TARGETS.get(k, "")
        print(f"  {k:32s} {fmt(v):>12s}   {('target ' + target) if target else ''}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=100)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--skill", action="store_true", help="full bot vs a weaker bot, alternating seats")
    ap.add_argument("--out", default="game_quality.json")
    args = ap.parse_args(argv)
    t0 = time.time()
    tasks = [(args.seed + i, args.skill) for i in range(args.games)]
    games, crashed = [], 0
    with ProcessPoolExecutor(args.workers, initializer=stress_test._init_worker, initargs=(4, ["search", "search"])) as pool:
        for i, g in enumerate(pool.map(_run, tasks)):
            if g["crashed"]:
                crashed += 1
            else:
                games.append(g)
            if (i + 1) % 10 == 0:
                print(f"{i + 1}/{args.games} games ({time.time() - t0:.0f}s)", flush=True)
    m = summarize(games)
    m["crashed"] = crashed
    show(m)
    json.dump({"metrics": m, "targets": TARGETS}, open(args.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
