"""
search_agent.py

An agent that finds plays by searching from the *current rack* each turn,
instead of consulting a precomputed table of every expression the tile set can
make (core/math_playbook.py). The table grows as (tile types)^(length); this
search grows with the rack (<= 15 tiles) and a depth knob.

How a turn works
  1. Pool: enumerate every grammar-valid expression the rack can spell (up to
     `max_len` tiles), fingerprint each by its numeric value at a few sample
     points, and index them by fingerprint. Cheap float math, no SymPy.
  2. For each tile on the board (an "anchor"), build one side of an equation
     around it (the anchor alone, anchor + a short rack expression,
     d/dx(anchor), int(anchor), ...) and look up the fingerprint of that side
     in the pool to find rack expressions equal to it (the other side).
     That lookup is the meet-in-the-middle step: no enumeration of pairs.
  3. Place each candidate on the board, estimate its score, and verify the
     best ones with the real engine (SymPy) until one passes. The engine stays
     the single source of truth for legality; the numeric fingerprints are
     only a prefilter, so a wrong fingerprint can cost a move but never allow
     an illegal one.
"""

import cmath
import hashlib
import itertools
import json
import logging
import math
import os
import random
import re
import time
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

import sympy as sp

from core.game_entities import Move, make_tile

logger = logging.getLogger("equadium.search_agent")

# Sample points (x, a, b, k) used to fingerprint expressions. Fixed so results
# are deterministic, and chosen to avoid 0/1 where identities degenerate.
SAMPLES = [(0.731, 1.427, -0.613, 0.913), (1.219, -0.842, 2.105, -1.337), (-0.457, 0.376, 1.671, 1.882)]
K = len(SAMPLES)
_ROUND = 7

# Tiles that can appear inside a plain (calculus-free) expression.
_FRACTION = re.compile(r"^(\d+)/(\d+)$")


def _tile_value(symbol: str) -> Optional[Tuple[float, ...]]:
    """Value of a single tile at each sample point, or None if it isn't a plain value tile."""
    out = []
    for x, a, b, k in SAMPLES:
        if symbol == "x":
            v = x
        elif symbol == "(x+a)":
            v = x + a
        elif symbol == "(x+b)":
            v = x + b
        elif symbol in ("x**2", "x**3", "x**4"):
            v = x ** int(symbol[-1])
        elif symbol in ("e^x", "exp(x)"):
            v = math.exp(x)
        elif symbol == "sin(x)":
            v = math.sin(x)
        elif symbol == "cos(x)":
            v = math.cos(x)
        elif symbol == "ln(x)":
            v = math.log(abs(x))  # real part of log(x); the engine is the final judge
        elif symbol == "1/x":
            v = 1 / x
        elif symbol == "k":
            v = k
        elif symbol == "a":
            v = a
        elif symbol == "b":
            v = b
        elif symbol.isdigit():
            v = float(symbol)
        elif _FRACTION.match(symbol):
            n, d = _FRACTION.match(symbol).groups()
            v = int(n) / int(d)
        else:
            return None
        out.append(v)
    return tuple(out)


def _key(vec) -> Optional[tuple]:
    if any(not math.isfinite(v) for v in vec):
        return None
    return tuple(round(v, _ROUND) for v in vec)


# d/dx and integral fingerprints depend only on the expression, so every agent
# (and every game in a stress run) shares one cache.
# SymPy no longer accepts modules="cmath", so give lambdify complex-valued functions by name.
_CMATH_NAMESPACE = [{"exp": cmath.exp, "log": cmath.log, "sin": cmath.sin, "cos": cmath.cos,
                     "sqrt": cmath.sqrt}, "math"]
_CALCULUS_CACHE: Dict[tuple, Optional[Tuple[float, ...]]] = {}
# Which tile pairs the engine does NOT read as a plain product ("2","3" -> 23). Also pure, so shared.
_PAIR_CACHE: Dict[tuple, bool] = {}

# Both caches are pure functions of the tile symbols and the math engine, but computing them with SymPy is
# slow (seconds per game on a fast core, minutes on a small server), so they are precomputed into this file
# by `python -m core.precompute_search_cache`. The file records the SymPy version and a hash of the rules
# engine it was built with and is ignored (falling back to computing lazily) if either has changed.
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "search_cache.json")
_SEP = "\x1f"


def _engine_hash() -> str:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "math_engine.py"), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def cache_header() -> dict:
    return {"schema": 1, "engine": _engine_hash(), "sympy": sp.__version__, "samples": [list(p) for p in SAMPLES]}


def load_cache_file(path: str = CACHE_FILE) -> bool:
    """Seeds the shared caches from the precomputed file. Returns False if it is missing or out of date."""
    try:
        with open(path) as f:
            data = json.load(f)
        if data.get("header") != cache_header():
            logger.warning("search_cache.json is out of date (rules engine, SymPy or samples changed): "
                           "ignoring it. Regenerate with `python -m core.precompute_search_cache`.")
            return False
        for key, bad in data["pairs"].items():
            kind, *toks = key.split(_SEP)
            _PAIR_CACHE[(kind, *toks)] = bad
        for key, vec in data["calculus"].items():
            kind, *toks = key.split(_SEP)
            _CALCULUS_CACHE[(kind, tuple(toks))] = tuple(vec) if vec is not None else None
        return True
    except FileNotFoundError:
        return False
    except Exception:
        logger.exception("Could not read %s; computing lazily instead", path)
        return False


def pair_is_bad(engine, kind: str, a: Optional[str], b: Optional[str] = None) -> bool:
    """
    kind "p": do tiles a then b fail to mean a*b?   kind "e<": a followed by exp(x)?   kind "e>": exp(x) followed by b?
    Cached process-wide; `engine` is only used on a miss.
    """
    key = (kind, a) if kind == "e<" else (kind, b) if kind == "e>" else (kind, a, b)
    if key in _PAIR_CACHE:
        return _PAIR_CACHE[key]
    try:
        if kind == "p":
            bad = sp.simplify(engine._parse_expression(a + b)
                              - engine._parse_expression(a) * engine._parse_expression(b)) != 0
        elif kind == "e<":
            bad = sp.simplify(engine._parse_expression(a + "exp(x)")
                              - engine._parse_expression(a) * engine._parse_expression("exp(x)")) != 0
        else:
            bad = sp.simplify(engine._parse_expression("exp(x)" + b)
                              - engine._parse_expression("exp(x)") * engine._parse_expression(b)) != 0
    except Exception:
        bad = True
    _PAIR_CACHE[key] = bad
    return bad


def compute_calculus_vec(engine, kind: str, inner_tokens: tuple) -> Optional[Tuple[float, ...]]:
    """Value (at the sample points) of d/dx or the integral of the expression the tokens spell, or None."""
    try:
        x, a, b, k = sp.symbols("x a b k")
        expr = engine._parse_expression("".join(inner_tokens))
        res = sp.diff(expr, x) if kind == "d/dx" else sp.integrate(expr, x)
        if not res.has(sp.Integral, sp.Piecewise, sp.zoo, sp.nan):
            f = sp.lambdify((x, a, b, k), res, _CMATH_NAMESPACE)
            vals = [complex(f(*pt)) for pt in SAMPLES]
            if all((abs(v.imag) < 1e-9 or res.has(sp.log)) and math.isfinite(v.real) for v in vals):
                return tuple(v.real for v in vals)
    except Exception:
        pass
    return None


load_cache_file()


class Candidate:
    __slots__ = ("tokens", "anchor_idx", "score")

    def __init__(self, tokens, anchor_idx, score):
        self.tokens = tokens
        self.anchor_idx = anchor_idx
        self.score = score


class SearchAgent:
    def __init__(self, name, config, max_len: int = 5, max_inner: int = 2,
                 max_verify: int = 30, node_budget: int = 150_000, time_budget: Optional[float] = None,
                 max_candidates: int = 4000, verbose: bool = False):
        """
        max_len    : longest rack expression (in tiles) the pool will contain
        max_inner  : longest expression combined with an anchor (anchor + A)
        max_verify : most candidates sent to the (slow) SymPy validator per turn
        node_budget: search-work cap per turn; the pool deepens only while it fits
        time_budget: soft wall-clock cap per turn in seconds (default 6, or EQUADIUM_AGENT_TIME_BUDGET). Once it
                     is spent the agent stops deepening, skips uncached SymPy work and stops verifying more
                     candidates: a slow host gets slightly weaker moves instead of long waits.
        max_candidates: stop collecting once this many plays are found (best are verified)
        """
        self.name = name
        self.config = config
        self.max_len = max_len
        self.max_inner = max_inner
        self.max_verify = max_verify
        self.node_budget = node_budget
        self.time_budget = time_budget if time_budget is not None else float(os.environ.get("EQUADIUM_AGENT_TIME_BUDGET", "6"))
        self._deadline = float("inf")
        self.max_candidates = max_candidates
        self.verbose = verbose

        self.value_tiles = {s: _tile_value(s) for s in config["tiles"] if _tile_value(s) is not None}
        self.points = {s: d["points"] for s, d in config["tiles"].items()}
        self.points["="] = config["equals_tile"]["points"]
        self.mult = {s: d.get("expr_multiplier", 1) for s, d in config["tiles"].items()}
        self.bad_pairs = None  # per-agent view of the shared pair cache, see _adjacency_blacklist

        # Per-turn counters (inspected by the stress test)
        self.stats = Counter()

    # ------------------------------------------------------------------
    # Entry point (same interface as AIAgent)
    # ------------------------------------------------------------------
    def handle_turn(self, game):
        move = self.find_best_move(game)
        if move is not None:
            self.stats["plays"] += 1
            return move
        player = game.players[game.current_turn_index]
        self.stats["swaps"] += 1
        return Move(tiles_to_swap=self._choose_swap(player))

    # Tiles that are only useful in small numbers: more than this many are dead weight.
    _KEEP_AT_MOST = {"C": 1, ")": 2, "exp(": 1, "d/dx(": 1, "int(": 1, "+": 2, "=": 1}

    def _choose_swap(self, player):
        """Throw back surplus tiles of types we can't use many of, plus
        duplicates beyond two; if the rack is already balanced, a random few."""
        seen = Counter()
        surplus = []
        for t in player.rack:
            seen[t.symbol] += 1
            if seen[t.symbol] > self._KEEP_AT_MOST.get(t.symbol, 2):
                surplus.append(t)
        if surplus:
            return surplus
        return random.sample(player.rack, random.randint(1, max(1, len(player.rack) // 3)))

    # ------------------------------------------------------------------
    # Engine-derived rules
    # ------------------------------------------------------------------
    def _adjacency_blacklist(self, game) -> set:
        """
        The engine builds equations by concatenating tile strings, so some
        neighbours don't mean "multiply" (2 then 3 is 23; 1/2 then 3 is 1/23).
        Ask the engine once which pairs of value tiles are *not* a plain
        product, and never generate them.
        """
        if self.bad_pairs is not None:
            return self.bad_pairs
        engine = game.math
        bad = set()
        for a, b in itertools.product(self.value_tiles, repeat=2):
            if pair_is_bad(engine, "p", a, b):
                bad.add((a, b))
        # exp( ... ) behaves like a value tile at its boundaries
        for s_ in self.value_tiles:
            if pair_is_bad(engine, "e<", s_):
                bad.add((s_, "exp("))
            if pair_is_bad(engine, "e>", None, s_):
                bad.add((")", s_))
        self.bad_pairs = bad
        return bad

    # ------------------------------------------------------------------
    # Step 1: the rack pool
    # ------------------------------------------------------------------
    def _build_pool(self, rack_symbols: Counter, bad_pairs):
        """Iterative deepening: keep deepening until the next level would blow the node budget."""
        best = None
        prev_nodes = last_nodes = 0
        for depth in range(1, self.max_len + 1):
            result, nodes = self._build_pool_to_depth(rack_symbols, bad_pairs, depth)
            best = result
            self.stats["pool_depth"] = depth
            growth = nodes / last_nodes if last_nodes else 8
            prev_nodes, last_nodes = last_nodes, nodes
            if nodes * max(growth, 2) > self.node_budget:
                break
            if depth >= 2 and self._over_budget(0.4):     # a slow host: stop deepening after 40% of the budget
                self.stats["budget_hits"] += 1
                break
        return best

    def _build_pool_to_depth(self, rack_symbols: Counter, bad_pairs, max_len: int):
        """
        pool[key] -> list of (tokens, vec, has_plus) for every grammar-valid
        expression the rack can spell, grouped by numeric fingerprint.
        Also returns by_len for the short anchor-companion expressions.
        """
        usable = {s: n for s, n in rack_symbols.items()
                  if s in self.value_tiles or s in ("+", "exp(", ")")}
        symbols = sorted(usable)
        pool: Dict[tuple, list] = defaultdict(list)
        short: List[tuple] = []
        counts = dict(usable)
        zero = (0.0,) * K
        frames: List[tuple] = []  # open exp( groups: (outer_total, outer_term)

        nodes = [0]

        def dfs(tokens, n, total, term, last_is_value, has_plus):
            nodes[0] += 1
            # n = non-bracket tiles used so far (the length budget).
            # total = sum of finished terms, term = product so far (None right
            # after '+' or 'exp('), both for the innermost open group.
            if last_is_value and not frames:
                vec = tuple(t + p for t, p in zip(total, term))
                k = _key(vec)
                if k is not None:
                    bucket = pool[k]
                    if len(bucket) < 12:
                        bucket.append((tuple(tokens), vec, has_plus))
                    if len(tokens) <= self.max_inner:
                        short.append((tuple(tokens), vec, has_plus))
            prev = tokens[-1] if tokens else None
            for s in symbols:
                if counts[s] == 0:
                    continue
                if s == ")":
                    if not (frames and last_is_value):
                        continue
                elif s == "+":
                    if not last_is_value or n >= max_len:
                        continue
                else:  # value tile or exp(
                    if n >= max_len or (last_is_value and (prev, s) in bad_pairs):
                        continue
                counts[s] -= 1
                tokens.append(s)
                if s == "+":
                    dfs(tokens, n + 1, tuple(t + p for t, p in zip(total, term)), None, False, True)
                elif s == "exp(":
                    frames.append((total, term))
                    dfs(tokens, n, zero, None, False, has_plus)
                    frames.pop()
                elif s == ")":
                    outer_total, outer_term = frames.pop()
                    inner = tuple(t + p for t, p in zip(total, term))
                    val = tuple(math.exp(v) if v < 50 else math.inf for v in inner)
                    new_term = val if outer_term is None else tuple(p * q for p, q in zip(outer_term, val))
                    dfs(tokens, n, outer_total, new_term, True, has_plus)
                    frames.append((outer_total, outer_term))
                else:
                    v = self.value_tiles[s]
                    new_term = v if term is None else tuple(p * q for p, q in zip(term, v))
                    dfs(tokens, n + 1, total, new_term, True, has_plus)
                tokens.pop()
                counts[s] += 1

        dfs([], 0, zero, None, False, False)
        return (pool, short), nodes[0]

    # ------------------------------------------------------------------
    # Symbolic helpers (cached): value of d/dx or integral of an expression
    # ------------------------------------------------------------------
    def _calculus_vec(self, game, kind: str, inner_tokens: tuple) -> Optional[Tuple[float, ...]]:
        cache_key = (kind, inner_tokens)
        if cache_key in _CALCULUS_CACHE:
            return _CALCULUS_CACHE[cache_key]
        if self._over_budget():
            self.stats["budget_skips"] += 1
            return None            # not cached: a later, less rushed turn can still compute it
        result = compute_calculus_vec(game.math, kind, inner_tokens)
        _CALCULUS_CACHE[cache_key] = result
        return result

    def _over_budget(self, fraction: float = 1.0) -> bool:
        return time.monotonic() > self._deadline - self.time_budget * (1.0 - fraction)

    # ------------------------------------------------------------------
    # Step 2: candidate sides built around an anchor
    # ------------------------------------------------------------------
    def _anchor_sides(self, game, t, vt, short, rack):
        """
        Yields (tokens, anchor_index, vec, kind) for one side of an equation
        containing the anchor tile `t`, wrapped in d/dx( ) or int( ). Plain
        equations are found by the pool join instead (see _join_candidates).
        """
        # calculus wrapped around the anchor alone, or anchor + one rack tile
        inners = [([t], 0)]
        for s in (v for v in self.value_tiles if rack[v] > 0):
            inners += [([t, "+", s], 0), ([s, "+", t], 2), ([t, s], 0), ([s, t], 1)]
        for inner, idx in inners:
            tup = tuple(inner)
            for kind, open_tile in (("d/dx", "d/dx("), ("int", "int(")):
                if rack[open_tile] == 0 or rack[")"] == 0 or (kind == "int" and (rack["C"] == 0 or rack["+"] == 0)):
                    continue
                vec = self._calculus_vec(game, kind, tup)
                if vec is not None:
                    yield [open_tile, *inner, ")"], idx + 1, vec, kind
                # second derivative: d/dx(d/dx(inner)) costs two openers and two closers
                if kind == "d/dx" and rack[open_tile] >= 2 and rack[")"] >= 2:
                    wrapped = (open_tile, *tup, ")")
                    vec2 = self._calculus_vec(game, kind, wrapped)
                    if vec2 is not None:
                        yield [open_tile, open_tile, *inner, ")", ")"], idx + 2, vec2, kind

    # ------------------------------------------------------------------
    # Step 3: placement and scoring
    # ------------------------------------------------------------------
    def _free_room(self, board, r, c, dr, dc) -> Tuple[int, int]:
        """
        How many new tiles fit before/after (r,c) along (dr,dc) without
        touching anything sideways and without running into another run.
        """
        pr, pc = dc, dr  # perpendicular

        def side_free(rr, cc):
            for sr, sc in ((rr + pr, cc + pc), (rr - pr, cc - pc)):
                if 0 <= sr < board.height and 0 <= sc < board.width and board.grid[sr][sc] is not None:
                    return False
            return True

        def scan(sign):
            n = 0
            rr, cc = r + sign * dr, c + sign * dc
            while 0 <= rr < board.height and 0 <= cc < board.width:
                if board.grid[rr][cc] is not None:
                    return max(n - 1, 0)  # last tile would touch that run and merge with it
                if not side_free(rr, cc):
                    return n
                n += 1
                rr, cc = rr + sign * dr, cc + sign * dc
            return n

        return scan(-1), scan(1)

    def _estimate_score(self, tokens, anchor_idx) -> int:
        pts, mult = 0, 1
        for i, s in enumerate(tokens):
            pts += self.points.get(s, 0)
            if i != anchor_idx:
                mult *= self.mult.get(s, 1)
        return pts * mult

    def find_best_move(self, game) -> Optional[Move]:
        self._deadline = time.monotonic() + self.time_budget
        player = game.players[game.current_turn_index]
        board = game.board
        bad_pairs = self._adjacency_blacklist(game)

        rack = Counter(t.symbol for t in player.rack)
        free_equals = player.equals_available and bool(game.equals_bag)
        if rack["="] == 0 and not free_equals:
            return None
        if rack["="] == 0:
            rack["="] = 1  # the one free '=' the engine will hand out on demand
        # Every board tile that a new line could pass through, with room around it
        anchors = []
        for r in range(board.height):
            for c in range(board.width):
                tile = board.grid[r][c]
                if tile is None or not (tile.symbol in self.value_tiles or tile.symbol == "="):
                    continue
                for direction, (dr, dc) in (("H", (0, 1)), ("V", (1, 0))):
                    # the anchor must stand alone in this direction
                    if any(0 <= r + s_ * dr < board.height and 0 <= c + s_ * dc < board.width
                           and board.grid[r + s_ * dr][c + s_ * dc] is not None for s_ in (-1, 1)):
                        continue
                    before, after = self._free_room(board, r, c, dr, dc)
                    if before + after >= 2:
                        anchors.append((r, c, direction, tile.symbol, before, after))

        # The pool is built from the rack plus one spare copy of each anchor
        # symbol (the anchor tile is already on the board, so it is free).
        pool_rack = Counter(rack)
        for symbol in {a[3] for a in anchors if a[3] in self.value_tiles}:
            pool_rack[symbol] += 1
        pool, short = self._build_pool(pool_rack, bad_pairs)
        self.stats["pool_size"] += sum(len(v) for v in pool.values())

        candidates: List[Tuple[tuple, Candidate]] = []
        seen = set()
        self._join_candidates(candidates, seen, pool, rack, anchors, bad_pairs)
        for r, c, direction, t, before, after in anchors:
            if t in self.value_tiles:
                self._collect(game, candidates, seen, (r, c, direction), t, self.value_tiles[t], short,
                              pool, rack, before, after, bad_pairs)

        if not candidates:
            return None
        candidates.sort(key=lambda cand: cand[1].score, reverse=True)
        self.stats["candidates"] += len(candidates)

        self.stats["verified_this_turn"] = 0
        for (r, c, direction), cand in candidates[: self.max_verify]:
            move = self._to_move(game, player, (r, c, direction), cand)
            if move is None:
                continue
            if self.stats["verified_this_turn"] >= 3 and self._over_budget():
                self.stats["budget_hits"] += 1
                break
            self.stats["verified"] += 1
            self.stats["verified_this_turn"] += 1
            _, error = game.evaluate_play(move.tiles_to_play, direction)
            if error is None:
                return move
            self.stats["rejected_by_engine"] += 1
        return None

    def _join_candidates(self, candidates, seen, pool, rack, anchors, bad_pairs):
        """
        Meet in the middle: any two pool expressions with the same fingerprint
        form an equation. It is playable if one tile of it can be an anchor
        already on the board and the rest comes from the rack.
        """
        by_symbol = defaultdict(list)
        for anchor in anchors:
            by_symbol[anchor[3]].append(anchor)
        eq_anchors = "=" in by_symbol
        counts_memo: Dict[tuple, dict] = {}

        def counts(tokens):
            d = counts_memo.get(tokens)
            if d is None:
                d = {}
                for tok in tokens:
                    d[tok] = d.get(tok, 0) + 1
                counts_memo[tokens] = d
            return d

        for bucket in pool.values():
            if len(candidates) >= self.max_candidates:
                return
            n = len(bucket)
            if n < 2:
                continue
            for i in range(n):
                e1 = bucket[i][0]
                c1 = counts(e1)
                for j in range(i + 1, n):
                    e2 = bucket[j][0]
                    c2 = counts(e2)
                    if c1 == c2:
                        continue  # same tiles reordered: not an interesting play
                    need = dict(c1)
                    for tok, k in c2.items():
                        need[tok] = need.get(tok, 0) + k
                    # Tiles the rack can't cover. At most one may be missing, and
                    # that one must be the anchor (it is already on the board).
                    short_of, deficit = [], 0
                    for tok, k in need.items():
                        d = k - rack.get(tok, 0)
                        if d > 0:
                            short_of.append(tok)
                            deficit += d
                    if deficit > 1:
                        continue
                    for left, right in ((e1, e2), (e2, e1)):
                        seq = list(left) + ["="] + list(right)
                        if not self._adjacency_ok(seq, bad_pairs):
                            continue
                        options = []
                        # The '=' is the anchor: every other tile must come from the rack.
                        if eq_anchors and not short_of:
                            options.append((len(left), "="))
                        # Otherwise the '=' comes from the rack/free pile, and one tile is the anchor.
                        if rack.get("=", 0) >= 1:
                            if short_of:
                                options += [(k, short_of[0]) for k, tok in enumerate(seq) if tok == short_of[0]]
                            else:
                                options += [(k, tok) for k, tok in enumerate(seq) if tok != "=" and tok in by_symbol]
                        for idx, sym in options:
                            if sym not in by_symbol:
                                continue
                            for (r, c, direction, _, before, after) in by_symbol[sym]:
                                if idx > before or len(seq) - 1 - idx > after:
                                    continue
                                sig = ((r, c, direction), tuple(seq), idx)
                                if sig in seen:
                                    continue
                                seen.add(sig)
                                candidates.append(((r, c, direction),
                                                   Candidate(seq, idx, self._estimate_score(seq, idx))))

    def _collect(self, game, candidates, seen, where, t, vt, short, pool, rack,
                 room_before, room_after, bad_pairs):
        r, c, direction = where
        for tokens, anchor_idx, vec, kind in self._anchor_sides(game, t, vt, short, rack):
            # geometry: tiles before/after the anchor must fit
            if anchor_idx > room_before or len(tokens) - 1 - anchor_idx > room_after:
                continue
            side_need = Counter(tokens)
            side_need[t] -= 1  # the anchor itself is already on the board
            if any(rack[s] < n for s, n in side_need.items() if n > 0):
                continue
            k = _key(vec)
            if k is None:
                continue
            matches = pool.get(k)
            if not matches:
                continue
            for b_tokens, _, _ in matches:
                other = list(b_tokens)
                if kind == "int":
                    other += ["+", "C"]
                total_need = side_need + Counter(other)
                total_need["="] += 1
                if any(rack[s] < n for s, n in total_need.items() if n > 0):
                    continue
                # trivial identities (same tiles, reordered) aren't interesting plays
                if Counter(tokens) == Counter(other):
                    continue
                for anchor_left in (True, False):
                    if anchor_left:
                        seq = tokens + ["="] + other
                        idx = anchor_idx
                    else:
                        seq = other + ["="] + tokens
                        idx = len(other) + 1 + anchor_idx
                    if idx > room_before or len(seq) - 1 - idx > room_after:
                        continue
                    if not self._adjacency_ok(seq, bad_pairs):
                        continue
                    sig = (where, tuple(seq), idx)
                    if sig in seen:
                        continue
                    seen.add(sig)
                    candidates.append((where, Candidate(seq, idx, self._estimate_score(seq, idx))))

    def _adjacency_ok(self, seq, bad_pairs) -> bool:
        return all((a, b) not in bad_pairs for a, b in zip(seq, seq[1:]))

    def _to_move(self, game, player, where, cand: Candidate) -> Optional[Move]:
        r, c, direction = where
        dr, dc = (0, 1) if direction == "H" else (1, 0)
        start_r, start_c = r - cand.anchor_idx * dr, c - cand.anchor_idx * dc
        available = list(player.rack)
        placed = []
        for i, symbol in enumerate(cand.tokens):
            if i == cand.anchor_idx:
                continue
            tr, tc = start_r + i * dr, start_c + i * dc
            if not (0 <= tr < game.board.height and 0 <= tc < game.board.width):
                return None
            match = next((t for t in available if t.symbol == symbol), None)
            if match is None:
                if symbol == "=":
                    match = make_tile("=", self.config)
                else:
                    return None
            else:
                available.remove(match)
            placed.append((tr, tc, match))
        return Move(tiles_to_play=placed, direction=direction) if placed else None
