"""
phrase_clusters.py

Finds phrase "types" by clustering instead of hand rules: each phrase from a tile_set_explorer corpus
becomes a vector of how often each tile appears (plus which tiles differ between the two sides, which
is what makes an identity interesting), and k-means groups similar phrases. Prints, for each cluster,
its size, the tiles it leans on, and sample phrases.

Usage (from the repo root):
    python -m core.phrase_clusters corpus.json --k 12
    python -m core.phrase_clusters corpus.json --scan 6 18      # silhouette for each k
"""
import argparse
import json
import random
import sys
from collections import Counter

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

from core.game_config import CONFIG
from core.math_engine import MathEngine

_TILE_ORDER = None


def load(path):
    phrases = json.load(open(path))["phrases"]
    eng = MathEngine(CONFIG)
    tiles = sorted({t for p in phrases for t in p["tiles"]} | {"="})
    index = {t: i for i, t in enumerate(tiles)}
    n = len(tiles)
    X = np.zeros((len(phrases), 2 * n + 1))
    for row, p in enumerate(phrases):
        toks = [t for t in eng.tokenize(p["eq"]) if t != ")"]
        left, _, right = "|".join(toks).partition("|=|")
        lc, rc = Counter(left.split("|")), Counter(right.split("|"))
        for t in toks:
            if t in index:
                X[row, index[t]] += 1
        for t in set(lc) | set(rc):
            if t in index:
                X[row, n + index[t]] = abs(lc[t] - rc[t])  # tiles used differently on the two sides
        X[row, -1] = len(toks)
    X[:, :n] /= np.maximum(X[:, :n].sum(axis=1, keepdims=True), 1)       # tile mix, not phrase length
    X[:, n:2 * n] /= np.maximum(X[:, n:2 * n].sum(axis=1, keepdims=True), 1)
    X[:, -1] /= X[:, -1].max()
    return phrases, tiles, X


def describe(phrases, tiles, X, labels, k, samples=6, seed=0):
    n = len(tiles)
    mean_all = X[:, :n].mean(axis=0)
    rng = random.Random(seed)
    out = []
    for c in range(k):
        idx = np.where(labels == c)[0]
        mix = X[idx, :n].mean(axis=0)
        lift = (mix + 1e-9) / (mean_all + 1e-9)
        top = [t for _, t in sorted(((mix[i] * np.log(lift[i] + 1e-9), tiles[i]) for i in range(n)), reverse=True)[:5]]
        kinds = Counter(phrases[i]["type"] for i in idx).most_common(2)
        out.append({"cluster": c, "size": len(idx), "signature_tiles": top, "hand_types": kinds,
                    "samples": [phrases[i]["eq"] for i in rng.sample(list(idx), min(samples, len(idx)))]})
    return sorted(out, key=lambda d: -d["size"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--k", type=int, default=12)
    ap.add_argument("--scan", nargs=2, type=int, metavar=("LO", "HI"))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    phrases, tiles, X = load(args.corpus)
    if args.scan:
        for k in range(args.scan[0], args.scan[1] + 1):
            km = KMeans(k, n_init=5, random_state=args.seed).fit(X)
            s = silhouette_score(X, km.labels_, sample_size=4000, random_state=args.seed)
            print(f"k={k:2d} silhouette={s:.3f} inertia={km.inertia_:.0f}", flush=True)
        return 0
    km = KMeans(args.k, n_init=10, random_state=args.seed).fit(X)
    info = describe(phrases, tiles, X, km.labels_, args.k)
    for d in info:
        print(f"\ncluster {d['cluster']:2d}  size={d['size']:5d}  tiles={d['signature_tiles']}  hand-types={d['hand_types']}")
        for s in d["samples"]:
            print("      ", s)
    if args.out:
        json.dump(info, open(args.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
