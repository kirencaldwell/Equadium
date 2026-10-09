"""
Precomputes the search agent's slow, deterministic SymPy lookups into core/search_cache.json:

  * which pairs of tiles the rules engine does not read as a plain product
  * the numeric value of d/dx and the integral for every anchor-tile combination the agent tries

Run it from the repo root with the same SymPy version as production (requirements.txt pins it):

    python -m core.precompute_search_cache            # about 2-6 minutes on 4 cores

Re-run it whenever tiles are added/removed (core/game_config.py) or core/math_engine.py changes.
The agent ignores a stale file (it checks the rules-engine hash and the SymPy version) and falls back to
computing lazily, which is correct but slow on small servers; a test reminds you to regenerate.
"""
import itertools
import json
import multiprocessing
import os
import sys
import time

from core.game_config import CONFIG
from core.math_engine import MathEngine
from core.search_agent import CACHE_FILE, _SEP, SearchAgent, cache_header, compute_calculus_vec, pair_is_bad

_engine = None


def _init():
    global _engine
    _engine = MathEngine(CONFIG)


def _do(task):
    kind, payload = task
    if kind == "calculus":
        ck, tokens = payload
        return task, compute_calculus_vec(_engine, ck, tokens)
    pk, a, b = payload
    return task, pair_is_bad(_engine, pk, a, b)


def build_tasks(value_tiles):
    tasks = [("pair", ("p", a, b)) for a, b in itertools.product(value_tiles, repeat=2)]
    tasks += [("pair", ("e<", s, None)) for s in value_tiles] + [("pair", ("e>", None, s)) for s in value_tiles]
    # exactly the anchor + companion shapes SearchAgent._anchor_sides tries
    for t in value_tiles:
        inners = [(t,)]
        for s in value_tiles:
            inners += [(t, "+", s), (s, "+", t), (t, s), (s, t)]
        for inner in inners:
            for ck in ("d/dx", "int"):
                tasks.append(("calculus", (ck, inner)))
    return tasks


def main() -> int:
    value_tiles = list(SearchAgent("precompute", CONFIG).value_tiles)
    tasks = build_tasks(value_tiles)
    print(f"{len(value_tiles)} value tiles -> {len(tasks)} lookups; header {cache_header()}", flush=True)
    pairs, calculus = {}, {}
    start = time.time()
    with multiprocessing.Pool(os.cpu_count() or 2, initializer=_init) as pool:
        for i, (task, value) in enumerate(pool.imap_unordered(_do, tasks, chunksize=8), 1):
            kind, payload = task
            if kind == "pair":
                pk, a, b = payload
                pairs[_SEP.join(x for x in (pk, a if pk != "e>" else None, b if pk != "e<" else None) if x is not None)] = value
            else:
                ck, tokens = payload
                calculus[_SEP.join((ck, *tokens))] = list(value) if value is not None else None
            if i % 250 == 0 or i == len(tasks):
                print(f"  {i}/{len(tasks)} ({time.time() - start:.0f}s)", flush=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"header": cache_header(), "pairs": pairs, "calculus": calculus}, f, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, CACHE_FILE)
    print(f"wrote {CACHE_FILE} ({os.path.getsize(CACHE_FILE) // 1024} KB, "
          f"{sum(v is not None for v in calculus.values())}/{len(calculus)} calculus values usable) in {time.time() - start:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
