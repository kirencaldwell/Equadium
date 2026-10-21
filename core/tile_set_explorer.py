"""
tile_set_explorer.py

Asks "which tile TYPES make for good play?" without playing games. It samples a large pool of random
expressions from every tile type, finds the pairs that are equal (so E1 = E2 is a legal phrase),
checks each pair with the real rules engine, and sorts the phrases into types (see phrase_report.classify).
Phrases may be any length, since each side is grown by a grammar instead of brute force.

Every phrase records the tile types it needs, so any candidate tile set can then be scored by filtering
the same corpus: how many phrase types can it spell, how many distinct phrases per type, and how long
they are. Drop one tile type at a time to see what it is worth, or grow a set greedily.

Usage (from the repo root):
    python -m core.tile_set_explorer build --samples 200000 --out corpus.json
    python -m core.tile_set_explorer analyse corpus.json
"""
import argparse
import json
import random
import signal
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

import sympy as sp

from core.game_config import CONFIG
from core.math_engine import MathEngine
from core.phrase_report import TYPES, classify

ATOMS = ["x", "a", "b", "i", "1", "2", "3", "4", "1/2", "1/3", "1/6", "1/x", "(x+a)", "(x+b)",
         "x**2", "x**3", "x**4", "e^x", "sin(x)", "cos(x)", "ln(x)"]
OPENERS = ["d/dx(", "int(", "exp(", "("]
BRACKET_TILES = {"(", ")"}

_engine = None


class _Timeout(Exception):
    pass


def _limited(fn, seconds, *args):
    """Run fn(*args), giving up after `seconds` (SymPy can take very long on a nested integral)."""
    def handler(signum, frame):
        raise _Timeout()
    old = signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn(*args)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _eng():
    global _engine
    if _engine is None:
        _engine = MathEngine(CONFIG)
    return _engine


def sample_tokens(rng, atoms, max_len=8, depth=0):
    """A random run of tiles: terms joined by + or -, each a product of atoms and bracketed groups."""
    toks = []
    for t in range(rng.choice([1, 1, 2, 2, 3])):
        if t:
            toks.append(rng.choice("+-"))
        for _ in range(rng.choice([1, 1, 2, 2, 3])):
            if depth < 2 and rng.random() < 0.18:
                op = rng.choice(OPENERS)
                inner = sample_tokens(rng, atoms, max_len, depth + 1)
                toks += [op] + inner + [")"]
            else:
                toks.append(rng.choice(atoms))
            if len(toks) >= max_len:
                return toks
    return toks


_POINTS = [(sp.Rational(1, 3), sp.Rational(2, 5), sp.Rational(3, 7)),
           (sp.Rational(5, 7), sp.Rational(3, 2), sp.Rational(7, 5))]


def key_of(tokens):
    """Numeric fingerprint of what the tiles compute, so equal expressions land in one bucket."""
    eng = _eng()
    eng._consts, eng._collecting = [], False
    expr = eng._parse_token_list(tokens)
    for c in list(eng._consts) + [eng.k, sp.Symbol("C")]:
        expr = expr.subs(c, 0) if expr.has(c) else expr
    x, a, b = eng.x, sp.Symbol("a"), sp.Symbol("b")
    if expr.free_symbols - {x, a, b}:
        return None
    parts = []
    for px, pa, pb in _POINTS:
        v = complex(sp.N(expr.subs({x: px, a: pa, b: pb}), 15))
        if v != v or abs(v) == float("inf"):
            return None
        parts.append(f"{v.real:.6g},{v.imag:.6g}")
    return "|".join(parts)


def _sample_chunk(args):
    seed, n, atoms = args
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        toks = sample_tokens(rng, atoms)
        try:
            k = _limited(key_of, 2, toks)
        except Exception:
            continue
        if k is not None:
            out.append((k, toks))
    return out


def tileset(tokens):
    return sorted({t for t in tokens if t not in BRACKET_TILES or t == "("})


def _validate_chunk(pairs):
    eng = _eng()
    out = []
    for left, right in pairs:
        l, r = list(left), list(right)
        if ("int(" in l or "int(" in r) and "C" not in l + r:
            r = r + ["+", "C"]
        s = "".join(l) + "=" + "".join(r)
        try:
            ok, _ = _limited(eng.validate_equation, 3, s)
        except Exception:
            ok = False
        if ok:
            out.append({"eq": s, "type": classify(s), "tiles": sorted(set(tileset(l + ["="] + r)) - {"="}),
                        "len": len(eng.tokenize(s))})
    return out


def build(samples, workers, seed, pairs_per_bucket, out_path):
    chunk = 500
    tasks = [(seed + i, chunk, ATOMS) for i in range(samples // chunk)]
    buckets = defaultdict(dict)
    with ProcessPoolExecutor(workers) as pool:
        for i, res in enumerate(pool.map(_sample_chunk, tasks)):
            for k, toks in res:
                buckets[k].setdefault("".join(toks), toks)
            if i % 20 == 0:
                print(f"sampled {(i + 1) * chunk}/{samples}, buckets={len(buckets)}", flush=True)
        rng = random.Random(seed)
        pairs = []
        for k, d in buckets.items():
            items = list(d.values())
            if len(items) < 2:
                continue
            cand = [(a, b) for a in range(len(items)) for b in range(a + 1, len(items))]
            rng.shuffle(cand)
            for a, b in cand[:pairs_per_bucket]:
                pairs.append((items[a], items[b]))
        print(f"{len(pairs)} candidate pairs from {sum(len(d) > 1 for d in buckets.values())} shared buckets", flush=True)
        step = 200
        chunks = [pairs[i:i + step] for i in range(0, len(pairs), step)]
        phrases = []
        for i, res in enumerate(pool.map(_validate_chunk, chunks)):
            phrases += res
            if i % 20 == 0:
                print(f"validated {min((i + 1) * step, len(pairs))}/{len(pairs)}, valid={len(phrases)}", flush=True)
    seen, uniq = set(), []
    for p in phrases:
        if p["eq"] not in seen:
            seen.add(p["eq"])
            uniq.append(p)
    json.dump({"samples": samples, "candidate_pairs": len(pairs), "phrases": uniq}, open(out_path, "w"))
    print(f"wrote {len(uniq)} distinct phrases to {out_path}")


def coverage(phrases, allowed):
    """Phrase types a tile set can spell: {type: distinct phrases}."""
    c = Counter()
    for p in phrases:
        if set(p["tiles"]) <= allowed:
            c[p["type"]] += 1
    return c


def analyse(path, min_per_type=20):
    data = json.load(open(path))
    phrases = data["phrases"]
    universe = set(ATOMS) | {"d/dx(", "int(", "exp(", "(", "+", "-", "C"}
    full = coverage(phrases, universe)
    print(f"{len(phrases)} phrases; phrases by type with every tile available:")
    for t in TYPES:
        print(f"  {t:20s} {full[t]}")
    lens = Counter(min(p["len"], 15) for p in phrases)
    print("phrase length (tiles):", dict(sorted(lens.items())))

    def score(allowed):
        c = coverage(phrases, allowed)
        return sum(1 for t in TYPES if c[t] >= min_per_type), sum(c.values())

    base_types, base_total = score(universe)
    print(f"\nall tiles: {base_types}/{len(TYPES)} types with >= {min_per_type} phrases, {base_total} phrases")
    print("\nWhat each tile is worth (remove it alone):")
    rows = []
    for t in sorted(universe):
        ty, tot = score(universe - {t})
        rows.append((base_total - tot, base_types - ty, t))
    for lost, lost_types, t in sorted(rows, reverse=True):
        print(f"  {t:8s} phrases lost={lost:6d} ({lost / base_total:5.1%})  types lost={lost_types}")

    print("\nGreedy build-up (most new phrase types and phrases per tile type):")
    chosen = {"+", "-", "(", "x"}
    while True:
        best = None
        for t in universe - chosen:
            ty, tot = score(chosen | {t})
            cand = (ty, tot, t)
            if best is None or cand > best:
                best = cand
        if best is None:
            break
        chosen.add(best[2])
        print(f"  + {best[2]:8s} -> {best[0]} types, {best[1]} phrases")
        if len(chosen) == len(universe):
            break


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--samples", type=int, default=100000)
    b.add_argument("--workers", type=int, default=4)
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--pairs-per-bucket", type=int, default=6)
    b.add_argument("--out", default="corpus.json")
    a = sub.add_parser("analyse")
    a.add_argument("corpus")
    a.add_argument("--min-per-type", type=int, default=20)
    args = ap.parse_args(argv)
    if args.cmd == "build":
        build(args.samples, args.workers, args.seed, args.pairs_per_bucket, args.out)
    else:
        analyse(args.corpus, args.min_per_type)
    return 0


if __name__ == "__main__":
    sys.exit(main())
