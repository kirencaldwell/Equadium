"""Engine, session and agent tests. Run from the repo root: pytest core web"""
import copy
import random

import pytest

from core.game_config import CONFIG
from core.game_entities import Move, make_tile
from core.search_agent import SearchAgent
from core.session import GameSession, HUMAN


def tiles(*symbols):
    return [make_tile(s, CONFIG) for s in symbols]


def fresh_session(mode="human_vs_human", rack0=None, rack1=None):
    random.seed(0)
    s = GameSession(mode, CONFIG)
    if rack0 is not None:
        s.game.players[0].rack = tiles(*rack0)
    if rack1 is not None:
        s.game.players[1].rack = tiles(*rack1)
    return s


CENTER = (CONFIG["board_dimensions"][0] // 2, CONFIG["board_dimensions"][1] // 2)


def place(game, symbols, start, direction="H"):
    dr, dc = (0, 1) if direction == "H" else (1, 0)
    return [(start[0] + i * dr, start[1] + i * dc, make_tile(sym, CONFIG)) for i, sym in enumerate(symbols)]


# ── placement rules ─────────────────────────────────────────────────────────
def test_game_starts_with_seed_tile():
    g = fresh_session().game
    assert g.board.grid[CENTER[0]][CENTER[1]].symbol == "x"


def test_valid_play_connected_to_seed():
    s = fresh_session(rack0=["=", "x", "2"])
    rec = s.play("Player1", place(s.game, ["=", "x"], (CENTER[0], CENTER[1] + 1)), "H")
    assert rec.ok, rec.error
    assert s.current_player.name == "Player2"
    assert s.game.players[0].score > 0


@pytest.mark.parametrize("why,start,direction,symbols,expect", [
    ("disconnected", (0, 0), "H", ["x", "=", "x"], "connect"),
    ("gap in line", (CENTER[0], CENTER[1] + 2), "H", ["=", "x"], "connect"),
    ("on occupied square", CENTER, "H", ["x", "=", "x"], "occupied"),
    ("off board", (CENTER[0], CONFIG["board_dimensions"][1] - 1), "H", ["=", "x"], "off the board"),
])
def test_illegal_placements_rejected(why, start, direction, symbols, expect):
    s = fresh_session(rack0=["=", "x", "x", "x"])
    rec = s.play("Player1", place(s.game, symbols, start, direction), direction)
    assert not rec.ok, why
    assert expect in rec.error
    # a rejected play changes nothing
    assert len(s.game.players[0].rack) == 4
    assert s.current_player.name == "Player1"
    assert s.game.players[0].score == 0


def test_tiles_must_be_in_one_line():
    s = fresh_session(rack0=["=", "x", "x"])
    tl = [(CENTER[0], CENTER[1] + 1, make_tile("=", CONFIG)), (CENTER[0] + 1, CENTER[1] + 2, make_tile("x", CONFIG))]
    rec = s.play("Player1", tl, "H")
    assert not rec.ok and "one row" in rec.error


def test_wrong_math_rejected():
    s = fresh_session(rack0=["=", "x", "2"])
    rec = s.play("Player1", place(s.game, ["=", "2"], (CENTER[0], CENTER[1] + 1)), "H")
    assert not rec.ok and "not a valid equation" in rec.error


def test_single_multichar_tile_not_treated_as_equation():
    # regression: a lone "x**2" in the cross direction used to count as an equation
    g = fresh_session().game
    eqs = g.board.get_all_new_equations([(CENTER[0] + 1, CENTER[1], make_tile("x**2", CONFIG))], "H")
    assert all(len(t) > 1 for _, t in eqs)


# ── turn order / modes ──────────────────────────────────────────────────────
def test_human_vs_human_enforces_turn_order():
    s = fresh_session(rack0=["=", "x"], rack1=["=", "x"])
    with pytest.raises(ValueError):
        s.play("Player2", place(s.game, ["=", "x"], (CENTER[0], CENTER[1] + 1)), "H")


def test_human_vs_agent_agent_replies():
    s = fresh_session("human_vs_agent", rack0=["=", "x"])
    assert s.seats["Human"].kind == HUMAN
    rec = s.play("Human", place(s.game, ["=", "x"], (CENTER[0], CENTER[1] + 1)), "H")
    assert rec.ok
    replies = s.advance_agents()
    assert len(replies) == 1 and replies[0].player == "AI_Opponent"
    assert s.current_player.name == "Human"


def test_agent_vs_agent_runs_to_completion(monkeypatch):
    monkeypatch.setitem(CONFIG, "max_turns", 8)
    random.seed(1)
    s = GameSession("agent_vs_agent", CONFIG)
    s.run_to_completion()
    assert s.is_over and s.game.turns_played <= 8
    assert s.illegal_agent_moves == 0


def test_human_seat_cannot_run_to_completion():
    with pytest.raises(ValueError):
        fresh_session("human_vs_agent").run_to_completion()


def test_unknown_mode():
    with pytest.raises(ValueError):
        GameSession("nope", CONFIG)


def test_swap_and_pass():
    s = fresh_session()
    assert s.swap("Player1", [0, 1]).ok
    assert s.current_player.name == "Player2"
    assert s.pass_turn("Player2").ok
    with pytest.raises(ValueError):
        s.swap("Player1", [999])


def test_stalled_game_ends():
    s = fresh_session()
    for _ in range(CONFIG["stall_rounds"] * 2):
        assert not s.is_over
        s.pass_turn(s.current_player.name)
    assert s.is_over and s.game.end_reason == "stalled"


def test_game_over_blocks_moves():
    s = fresh_session()
    for _ in range(CONFIG["stall_rounds"] * 2):
        s.pass_turn(s.current_player.name)
    with pytest.raises(ValueError):
        s.pass_turn(s.current_player.name)


# ── search agent ────────────────────────────────────────────────────────────
def test_search_agent_finds_obvious_play():
    # With "=" and "x" in hand next to the seed "x", x = x is the only trivial
    # option, but with 2, x, x there is x + x = 2x style play available.
    s = fresh_session("agent_vs_agent", rack0=["2", "x", "x", "=", "+"])
    agent = SearchAgent("t", CONFIG)
    move = agent.find_best_move(s.game)
    assert move is not None
    _, err = s.game.evaluate_play(move.tiles_to_play, move.direction)
    assert err is None


def test_search_agent_swaps_when_stuck():
    s = fresh_session("agent_vs_agent", rack0=[")", ")", ")", "C", "C", "C"])
    move = SearchAgent("t", CONFIG).handle_turn(s.game)
    assert move.is_swap and move.tiles_to_swap


def test_fingerprints_agree_with_engine():
    """Every equation the agent's numeric search proposes must be accepted by SymPy."""
    random.seed(3)
    agent = SearchAgent("t", CONFIG)
    checked = 0
    for _ in range(4):
        s = GameSession("agent_vs_agent", CONFIG)
        for _ in range(6):
            player = s.current_player
            move = agent.find_best_move(s.game)
            if move is None:
                s.pass_turn  # noqa: B018 - nothing to check this turn
                s.game.execute_move(player, Move())
                continue
            assert s.game.execute_move(player, move), s.game.last_error
            checked += 1
    assert checked > 0


# ---- calculus tiles inside bigger expressions ----
@pytest.mark.parametrize("equation,expected", [
    ("d/dx(x**2)+x=3x", True),                          # a derivative as one term of a sum
    ("d/dx(x**2)x=2x**2", True),                        # ...or a factor of a product
    ("d/dx(x**2)+x=2x", False),
    ("sin(x)int(x**2)=1/3x**3sin(x)+C", True),          # an integral as a factor (its constant chosen as 0)
    ("sin(x)21/6xx**2+C=sin(x)int(x**2)", True),        # the play that used to be rejected
    ("int(x)x=1/2x**3+C", True),
    ("int(x)+int(x)=x**2+C", True),                     # every integral has its own constant
    ("2int(x)=x**2+C", True),
    ("int(x)=1/2x**3+C", False),                        # the antiderivative still has to be right
    ("int(x)x=1/2x**2+C", False),
    ("int(d/dx(x**2))=x**2+C", True),                   # groups nest in either order
    ("d/dx(int(x))=x+C", True),
    ("kint(x)=x**2+C", True),                           # works together with the wild constant k
    ("d/dx(x**2=3", False),                             # an unclosed group is an error, not a crash
])
def test_calculus_inside_bigger_expressions(equation, expected):
    ok, _ = fresh_session().game.math.validate_equation(equation)
    assert ok is expected, equation


# ---- k, the wild constant ----
@pytest.mark.parametrize("equation,expected", [
    ("2k=3", True),                    # k = 3/2
    ("kx=3x", True),                   # k = 3
    ("k+k=3", True),                   # every k on the line is the same number
    ("kk=4", True),                    # k = 2
    ("kk+kk=0", True),                 # k = 0
    ("d/dx(kx)=k", True),              # true for every k
    ("int(k)=3x+C", True),             # k = 3
    ("int(k)=kx+C", True),
    ("2k=3=k+k", True),                # a chain needs one k that satisfies every link...
    ("2k=3=4", False),                 # ...and 3 = 4 is false whatever k is
    ("k=3=2k", False),                 # k = 3 for the first link, but then 2k = 6
    ("k=x", False),                    # no constant equals x
    ("kx=3", False),                   # k would have to be 3/x
    ("k=a", False),                    # nor may it depend on a or b
    ("kk+4=0", False),                 # only complex numbers solve it: k must be real
    ("x=x", True),
    ("x=2x", False),                   # ordinary equations are unchanged
])
def test_k_is_a_wild_constant(equation, expected):
    ok, _ = fresh_session().game.math.validate_equation(equation)
    assert ok is expected, equation


def test_k_is_worth_nothing():
    assert CONFIG["tiles"]["k"]["points"] == 0


# ---- the constant of integration: +C or -C ----
@pytest.mark.parametrize("equation,expected", [
    ("int(x)=1/2x**2+C", True),
    ("int(x)=1/2x**2-C", True),                 # -C is just as good a constant
    ("int(sin(x))=-cos(x)-C", True),
    ("int(x)=1/2x**2-C=1/2x**2+C", True),       # in a chain too
    ("int(x)=1/2x**2", False),                  # no constant at all
    ("int(x)=1/2x**2+Cx", False),               # "Cx" is a product, not a constant term
    ("int(1)=x+Cx", False),                     # ...so it can't be used to smuggle an extra x past the check
    ("int(x)=1/2x**3-C", False),                # still has to be the right antiderivative
    ("int(x)=C+1/2x**2", False),                # the constant is written as +C or -C after the function
])
def test_integral_constant(equation, expected):
    ok, _ = fresh_session().game.math.validate_equation(equation)
    assert ok is expected


# ---- fraction tiles next to numbers/variables read as products of tiles ----
@pytest.mark.parametrize("equation", [
    "2+21/xx=4", "2x1/x=2", "31/2=3/2", "x1/x=1",
    "xx**3=x**421/2",       # a power tile followed by a digit tile is a product, not a bigger exponent
    "x2=2x", "kx**22=2kx**2", "a2=2a",
    "22=4", "23=6", "4e^x=22e^x",     # digit tiles multiply: 2, 2 is 2*2, not twenty-two
])
def test_fraction_tile_is_its_own_factor(equation):
    ok, msg = fresh_session().game.math.validate_equation(equation)
    assert ok, msg


# ---- chained equalities (a = b = c) ----
@pytest.mark.parametrize("equation,expected", [
    ("41/xx=2+2=4", True),
    ("x=x=x", True),
    ("x=x=2x", False),          # the second link fails
    ("2+2=4=x", False),
    ("int(x)=1/2x**2+C=int(x)", True),
    ("x==x", False),             # empty part
])
def test_chained_equalities(equation, expected):
    ok, _ = fresh_session().game.math.validate_equation(equation)
    assert ok is expected


def test_chained_equality_can_be_played_on_the_board():
    s = fresh_session(rack0=["4", "1/x", "=", "2", "+", "2", "=", "4"])
    cells = place(s.game, ["4", "1/x"], (CENTER[0], CENTER[1] - 2)) + place(s.game, ["=", "2", "+", "2", "=", "4"], (CENTER[0], CENTER[1] + 1))
    rec = s.play("Player1", cells, "H")
    assert rec.ok, rec.error


# ---- subtraction ("-") tile ----
@pytest.mark.parametrize("equation", [
    "x**2-x=x**2-x",
    "d/dx(cos(x))=-sin(x)",
    "int(sin(x))=-cos(x)+C",
    "d/dx(x**2-x)=2x-1",
    "-x+x=x-x",
])
def test_minus_tile_valid_equations(equation):
    ok, msg = fresh_session().game.math.validate_equation(equation)
    assert ok, msg


@pytest.mark.parametrize("equation", ["x-=x", "x--x=x", "x+-x=0", "x-)=x", "x*-x=-x**2"])
def test_minus_tile_grammar_rejects_misplaced_signs(equation):
    ok, _ = fresh_session().game.math.validate_equation(equation)
    assert not ok


def test_minus_tile_is_in_the_bag():
    assert CONFIG["tiles"]["-"]["count"] > 0


def test_agent_plays_the_minus_tile_legally():
    used = 0
    for seed in range(2):
        random.seed(seed)
        g = GameSession("agent_vs_agent", CONFIG)
        g.run_to_completion()
        assert g.illegal_agent_moves == 0
        used += sum(1 for r in g.history if r.action == "play" and "-" in (r.tiles or []))
    assert used > 0


# ---- ln(x), 1/x, k and stacked calculus tiles ----
@pytest.mark.parametrize("equation", [
    "d/dx(ln(x))=1/x",
    "int(1/x)=ln(x)+C",
    "d/dx(kx)=k",
    "d/dx(d/dx(x**3))=6x",
    "d/dx(int(x))=x+C",
])
def test_new_tiles_and_stacked_calculus_are_valid(equation):
    ok, msg = fresh_session().game.math.validate_equation(equation)
    assert ok, msg


def test_second_derivative_scores_times_four():
    game = fresh_session().game
    assert game.config["tiles"]["d/dx("]["expr_multiplier"] ** 2 == 4


# ── persistence ─────────────────────────────────────────────────────────────
def _roundtrip(session):
    import json
    return GameSession.from_dict(json.loads(json.dumps(session.to_dict())), CONFIG)


def test_session_roundtrip_mid_game_and_keeps_playing():
    random.seed(4)
    s = GameSession("agent_vs_agent", CONFIG)
    for _ in range(14):
        s.step_agent()
    loaded = _roundtrip(s)
    assert loaded.to_dict() == s.to_dict()
    assert loaded.current_player.name == s.current_player.name
    assert [p.score for p in loaded.game.players] == [p.score for p in s.game.players]
    # the restored game is fully playable: agents move, board stays consistent
    assert loaded.step_agent() is not None
    assert loaded.illegal_agent_moves == 0


def test_roundtrip_preserves_human_seats_and_history():
    s = fresh_session("human_vs_agent", rack0=["=", "x"])
    s.play("Human", place(s.game, ["=", "x"], (CENTER[0], CENTER[1] + 1)), "H")
    s.advance_agents()
    loaded = _roundtrip(s)
    assert {n: seat.kind for n, seat in loaded.seats.items()} == {"Human": "human", "AI_Opponent": "agent"}
    assert loaded.seats["AI_Opponent"].agent is not None and loaded.seats["Human"].agent is None
    assert [r.player for r in loaded.history] == [r.player for r in s.history]
    assert loaded.history[0].cells == s.history[0].cells


def test_roundtrip_keeps_tile_values_even_if_config_changes():
    s = fresh_session("human_vs_human")
    data = s.to_dict()
    retuned = copy.deepcopy(CONFIG)
    for t in retuned["tiles"].values():
        t["points"] += 100
    loaded = GameSession.from_dict(data, retuned)
    assert loaded.to_dict() == data


# ── search agent speed: shared caches, precomputed file, time budget ─────────
def test_precomputed_search_cache_is_current_and_covers_every_tile():
    """If this fails, tiles or math_engine.py changed: run `python -m core.precompute_search_cache`."""
    import json
    from core import search_agent as sa
    with open(sa.CACHE_FILE) as f:
        data = json.load(f)
    assert data["header"] == sa.cache_header(), "search_cache.json is stale; regenerate it"
    tiles = list(SearchAgent("t", CONFIG).value_tiles)
    sep = sa._SEP
    assert all(sep.join(("p", a, b)) in data["pairs"] for a in tiles for b in tiles)
    assert all(sep.join(("e<", s)) in data["pairs"] and sep.join(("e>", s)) in data["pairs"] for s in tiles)
    for t in tiles:
        assert sep.join(("d/dx", t)) in data["calculus"] and sep.join(("int", t)) in data["calculus"]


def test_precomputed_values_match_fresh_computation():
    import json
    from core import search_agent as sa
    from core.math_engine import MathEngine
    engine = MathEngine(CONFIG)
    with open(sa.CACHE_FILE) as f:
        data = json.load(f)
    rng = random.Random(7)
    for key in rng.sample(sorted(data["calculus"]), 25):
        kind, *toks = key.split(sa._SEP)
        fresh, stored = sa.compute_calculus_vec(engine, kind, tuple(toks)), data["calculus"][key]
        assert (fresh is None) == (stored is None), key
        if fresh is not None:
            assert all(abs(a - b) < 1e-9 * max(1, abs(a)) for a, b in zip(fresh, stored)), key
    for key in rng.sample(sorted(data["pairs"]), 25):
        kind, *toks = key.split(sa._SEP)
        a, b = (toks[0], toks[1]) if kind == "p" else (toks[0], None) if kind == "e<" else (None, toks[0])
        sa._PAIR_CACHE.pop((kind, *toks), None)                       # force a real recomputation
        assert sa.pair_is_bad(engine, kind, a, b) == data["pairs"][key], key


def test_blacklist_is_computed_once_and_shared_between_agents():
    import time
    s = fresh_session("agent_vs_agent")
    SearchAgent("a", CONFIG)._adjacency_blacklist(s.game)          # warms the shared cache (or loads from file)
    start = time.time()
    second = SearchAgent("b", CONFIG)
    # Every pair of adjacent tiles now reads as the product of the two (digits included), which is what the agent
    # assumes, so no pair needs blacklisting any more.
    assert second._adjacency_blacklist(s.game) == set()
    assert time.time() - start < 0.5                                  # a brand-new agent/game does not recompute


def test_stale_cache_file_is_ignored(tmp_path):
    import json
    from core import search_agent as sa
    stale = tmp_path / "c.json"
    stale.write_text(json.dumps({"header": {**sa.cache_header(), "engine": "deadbeef"},
                                 "pairs": {sa._SEP.join(("p", "zzz", "yyy")): True}, "calculus": {}}))
    assert sa.load_cache_file(str(stale)) is False
    assert ("p", "zzz", "yyy") not in sa._PAIR_CACHE                  # nothing from a stale file leaks in
    assert sa.load_cache_file(str(tmp_path / "missing.json")) is False


def test_time_budget_bounds_a_cold_turn(monkeypatch):
    """On a slow host the agent must give a (possibly weaker) answer quickly rather than grind through SymPy."""
    import time
    from core import search_agent as sa
    random.seed(5)
    s = GameSession("agent_vs_agent", CONFIG)
    for _ in range(14):                                               # a realistic mid-game board
        s.step_agent()
    s.current_player.rack = tiles("d/dx(", "int(", ")", "C", "+", "=", "x", "x**2", "2", "3", "sin(x)", "cos(x)", "(x+a)", "a", "b")
    monkeypatch.setattr(sa, "_CALCULUS_CACHE", {})                    # cold: every integral would need computing
    agent = SearchAgent("slow", CONFIG, time_budget=0.3)
    start = time.time()
    move = agent.find_best_move(s.game)
    elapsed = time.time() - start
    assert elapsed < 3.0, f"cold turn took {elapsed:.1f}s despite a 0.3s budget"
    assert move is None or move.is_play
    assert agent.stats["budget_skips"] > 0 or agent.stats["budget_hits"] > 0


# ── forfeit ─────────────────────────────────────────────────────────────────
def test_forfeit_is_a_loss_for_the_forfeiter_whatever_the_scores():
    s = fresh_session()
    s.game.players[0].score, s.game.players[1].score = 90, 3        # the forfeiter is far ahead
    rec = s.forfeit("Player1")
    assert rec.ok and rec.action == "forfeit"
    g = s.game
    assert s.is_over and g.end_reason == "forfeit" and g.forfeited_by == "Player1"
    assert g.winners == ["Player2"]                                  # the other player wins despite fewer points
    assert [r.action for r in s.history] == ["forfeit"]


def test_forfeit_works_on_the_opponents_turn():
    s = fresh_session()
    assert s.current_player.name == "Player1"
    s.forfeit("Player2")                                              # not Player2's turn, still allowed
    assert s.game.winners == ["Player1"]


def test_nothing_can_be_done_after_a_forfeit():
    s = fresh_session(rack0=["=", "x"])
    s.forfeit("Player1")
    for act in (lambda: s.play("Player1", place(s.game, ["=", "x"], (CENTER[0], CENTER[1] + 1)), "H"),
                lambda: s.swap("Player1", [0]), lambda: s.pass_turn("Player1"), lambda: s.forfeit("Player2")):
        with pytest.raises(ValueError, match="over"):
            act()


def test_only_humans_can_forfeit():
    s = fresh_session("human_vs_agent")
    with pytest.raises(ValueError):
        s.forfeit("AI_Opponent")
    with pytest.raises(KeyError):
        s.forfeit("Nobody")
    with pytest.raises(ValueError):
        fresh_session("agent_vs_agent").forfeit("Newton_Bot")
    assert not s.is_over                                              # the refused attempts changed nothing


def test_forfeit_survives_saving_and_old_saves_still_load():
    s = fresh_session()
    s.forfeit("Player2")
    loaded = _roundtrip(s)
    assert loaded.is_over and loaded.game.forfeited_by == "Player2" and loaded.game.winners == ["Player1"]
    legacy = fresh_session().to_dict()
    del legacy["game"]["forfeited_by"]                                # a game saved before forfeits existed
    assert GameSession.from_dict(legacy, CONFIG).game.forfeited_by is None
