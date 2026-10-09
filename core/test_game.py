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
