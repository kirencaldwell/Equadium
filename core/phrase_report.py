"""
phrase_report.py

Measures how varied the equations ("phrases") are across bot-vs-bot games: distinct phrases per
game, how often the same phrase shows up in different games, the most repeated phrases, and how
much of the scoring a handful of phrases account for. Used to tune tile counts for variety.

Usage (from the repo root):
    python -m core.phrase_report --games 40 --workers 4 --out phrases.json
    python -m core.phrase_report --candidates cands.json   # {"name": {"tile": count, ...}, ...}
"""
import argparse
import itertools
import json
import statistics
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

from core import stress_test
from core.bag_report import BASE, apply_counts
from core.game_entities import is_cosmetic_equation
from core.math_engine import MathEngine
from core.game_config import CONFIG
from core.session import GameSession


TYPES = ["integral", "derivative", "euler (i)", "log/exp", "trig", "binomial/parameter",
         "power rule", "rearrangement", "numeric", "times-one", "other"]


def classify(eq: str) -> str:
    """One primary type per phrase, first match wins (most 'interesting' structure first)."""
    if "int(" in eq:
        return "integral"
    if "d/dx(" in eq:
        return "derivative"
    if "i" in eq.replace("sin", "").replace("int", ""):
        return "euler (i)"
    if "ln(" in eq or "e^x" in eq or "exp(" in eq:
        return "log/exp"
    if "sin" in eq or "cos" in eq:
        # 1sin(x)=sin(x) style padding is "times-one", not a trig identity
        return "times-one" if _times_one(eq) else "trig"
    if _times_one(eq):
        return "times-one"
    if not any(ch in eq for ch in "xabk"):
        return "numeric"
    lhs, _, rhs = eq.partition("=")
    strip = lambda t: sorted(t.replace("(", "").replace(")", ""))
    if strip(lhs) == strip(rhs):
        return "rearrangement"
    if "**" in eq:
        return "power rule"
    if "(x+" in eq or "a" in eq or "b" in eq:
        return "binomial/parameter"
    return "other"


def _times_one(eq: str) -> bool:
    import re
    return bool(re.search(r"(?<![0-9/*^])1(?=[a-zA-Z(])", eq)) or bool(re.search(r"\)1(?![0-9/])", eq))


def _init(agent_types):
    stress_test._init_worker(4, agent_types)


def _run(task):
    counts, seed = task
    apply_counts(counts)
    plays = []
    holder = {}
    orig = GameSession.__init__

    def patched(self, *a, **k):
        orig(self, *a, **k)
        inner = self.game.score_play

        def wrapped(equations_data, move_tiles):
            pts = inner(equations_data, move_tiles)
            plays.append({"eqs": [e for e, _ in equations_data], "points": pts})
            return pts
        self.game.score_play = wrapped
    GameSession.__init__ = patched
    try:
        r = stress_test.play_one_game(seed)
    finally:
        GameSession.__init__ = orig
    if r["crashed"] or "scores" not in r:
        return {"crashed": True}
    sc = list(r["scores"].values())
    seq = r["action_seq"]
    tail = 0
    for a in reversed(seq):
        if a == "play":
            break
        tail += 1
    return {"crashed": False, "plays": plays, "turns": r["turns"], "winner": max(sc),
            "swaps": r["actions"].get("swap", 0), "tail": tail, "end": r["end_reason"]}


def analyse(games, extra=None):
    """games: list of per-game play lists. Phrase = one equation string formed by a play."""
    per_game = [Counter(e for p in g for e in p["eqs"]) for g in games]
    n = len(games)
    doc_freq = Counter()
    for c in per_game:
        doc_freq.update(set(c))
    all_counts = sum(per_game, Counter())
    total = sum(all_counts.values())
    pts = Counter()
    for g in games:
        for p in g:
            for e in p["eqs"]:
                pts[e] += p["points"] / max(1, len(p["eqs"]))
    total_pts = sum(pts.values()) or 1
    sets = [set(c) for c in per_game]
    jac = [len(a & b) / max(1, len(a | b)) for a, b in itertools.combinations(sets, 2)]
    # share of a game's phrases that also appeared in at least one other game
    shared = [statistics.mean(1.0 if doc_freq[e] > 1 else 0.0 for e in s) for s in sets if s]
    lens = [len(e) for e in all_counts.elements()]
    import math
    type_counts = [Counter(classify(e) for p in g for e in p["eqs"]) for g in games]
    all_types = sum(type_counts, Counter())
    tt = sum(all_types.values()) or 1
    entropy = -sum(c / tt * math.log(c / tt, 2) for c in all_types.values())
    eng = MathEngine(CONFIG)
    cosm = [is_cosmetic_equation(eng.tokenize(e)) for e in all_counts.elements()]
    out = {
        "cosmetic_share": sum(cosm) / max(1, len(cosm)),
        "types_overall": {t: round(all_types[t] / tt, 3) for t in TYPES},
        "types_per_game": statistics.mean(len(c) for c in type_counts),
        "type_entropy_bits": entropy,
        "times_one_numeric_share": (all_types["times-one"] + all_types["numeric"]) / tt,
        "games": n,
        "phrases_per_game": statistics.mean(sum(c.values()) for c in per_game),
        "distinct_per_game": statistics.mean(len(c) for c in per_game),
        "distinct_overall": len(all_counts),
        "distinct_ratio": len(all_counts) / max(1, total),
        "repeat_within_game": 1 - statistics.mean(len(c) / max(1, sum(c.values())) for c in per_game),
        "shared_with_other_games": statistics.mean(shared),
        "mean_pair_jaccard": statistics.mean(jac) if jac else 0.0,
        "games_with_top_phrase": max(doc_freq.values()) / n if doc_freq else 0,
        "top10_share_of_plays": sum(c for _, c in all_counts.most_common(10)) / max(1, total),
        "top10_share_of_points": sum(pts[e] for e, _ in all_counts.most_common(10)) / total_pts,
        "mean_phrase_len": statistics.mean(lens) if lens else 0,
        "top_phrases": [(e, c, doc_freq[e]) for e, c in all_counts.most_common(15)],
    }
    out.update(extra or {})
    return out


def show(name, m):
    print(f"{name:12s} games={m['games']} phrases/game={m['phrases_per_game']:.1f} distinct/game={m['distinct_per_game']:.1f} "
          f"distinct_overall={m['distinct_overall']} ({m['distinct_ratio']:.0%} of all plays) "
          f"shared_w_other_games={m['shared_with_other_games']:.0%} jaccard={m['mean_pair_jaccard']:.3f} "
          f"top10_plays={m['top10_share_of_plays']:.1%} top10_points={m['top10_share_of_points']:.1%} "
          f"len={m['mean_phrase_len']:.1f}\n      winner={m['winner']:.0f}±{m['winner_sd']:.0f} turns={m['turns']:.0f} "
          f"swaps={m['swaps']:.1f} (mid {m['mid_swaps']:.1f}) stalled={m['stalled']:.0%} types/game={m['types_per_game']:.1f} "
          f"entropy={m['type_entropy_bits']:.2f}b cosmetic={m['cosmetic_share']:.0%} trivial(times-one+numeric)={m['times_one_numeric_share']:.0%}\n      types={m['types_overall']}")
    for e, c, d in m["top_phrases"][:10]:
        print(f"      {c:3d}x in {d:2d} games  {e}")
    sys.stdout.flush()


def evaluate(pool, counts, games, seed):
    res = [r for r in pool.map(_run, [(counts, seed + i) for i in range(games)]) if not r["crashed"]]
    extra = {
        "winner": statistics.mean(r["winner"] for r in res),
        "winner_sd": statistics.pstdev([r["winner"] for r in res]),
        "turns": statistics.mean(r["turns"] for r in res),
        "swaps": statistics.mean(r["swaps"] for r in res),
        "mid_swaps": statistics.mean(r["swaps"] - r["tail"] for r in res),
        "stalled": sum(r["end"] == "stalled" for r in res) / len(res),
    }
    return analyse([r["plays"] for r in res], extra)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--candidates", help="JSON {name: {tile: count}}; default measures the current bag")
    ap.add_argument("--out", default="phrase_report.json")
    args = ap.parse_args(argv)
    cands = {"current": dict(BASE)}
    if args.candidates:
        cands = {k: {**BASE, **v} for k, v in json.load(open(args.candidates)).items()}
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
