import pytest
from fastapi.testclient import TestClient

from core.game_config import CONFIG
from web.api.main import app
from web.api import main

client = TestClient(app)

CENTER_R = CONFIG["board_dimensions"][0] // 2
CENTER_C = CONFIG["board_dimensions"][1] // 2


def tile(symbol, points=1, mult=1):
    return {"symbol": symbol, "points": points, "expr_multiplier": mult}


def create(mode=None):
    resp = client.post("/games/create", json={"mode": mode} if mode else None)
    assert resp.status_code == 200, resp.text
    return resp.json()["game_id"]


def rig_rack(game_id, player_index, symbols):
    """Test hook: give a player a known rack so plays are deterministic."""
    from core.game_entities import make_tile
    from web.api.main import sessions
    sessions[game_id].game.players[player_index].rack = [make_tile(s, CONFIG) for s in symbols]


def equals_x_play(player=None):
    body = {"tiles_to_play": [{"r": CENTER_R, "c": CENTER_C + 1, "tile": tile("=", 0)},
                              {"r": CENTER_R, "c": CENTER_C + 2, "tile": tile("x")}],
            "direction": "H"}
    return body


def test_default_create_is_human_vs_agent():
    gid = create()
    state = client.get(f"/games/{gid}").json()
    assert state["mode"] == "human_vs_agent"
    assert state["seats"] == {"Human": "human", "AI_Opponent": "agent"}
    assert state["board"]["grid"][CENTER_R][CENTER_C]["symbol"] == "x"


def test_unknown_mode_rejected():
    assert client.post("/games/create", json={"mode": "nope"}).status_code == 400


def test_pass_and_play_no_longer_exists():
    r = client.post("/games/create", json={"mode": "human_vs_human"})
    assert r.status_code == 400 and "online" in r.json()["detail"]


def test_modes_listed():
    assert set(client.get("/modes").json()) == {"human_vs_agent", "agent_vs_agent"}   # two-player games are online rooms


def test_human_vs_agent_play_then_agent_replies():
    gid = create("human_vs_agent")
    rig_rack(gid, 0, ["=", "x"])
    resp = client.post(f"/games/{gid}/play", json=equals_x_play())
    assert resp.json()["status"] == "success"
    assert len(resp.json()["agent_moves"]) == 1
    state = client.get(f"/games/{gid}").json()
    assert state["current_player"] == "Human"   # agent already answered
    assert state["turns_played"] == 2


def test_illegal_play_returns_reason_and_keeps_turn():
    gid = create("human_vs_agent")
    rig_rack(gid, 0, ["=", "2"])
    body = {"tiles_to_play": [{"r": CENTER_R, "c": CENTER_C + 1, "tile": tile("=", 0)},
                              {"r": CENTER_R, "c": CENTER_C + 2, "tile": tile("2")}], "direction": "H"}
    resp = client.post(f"/games/{gid}/play", json=body).json()
    assert resp["status"] == "failed" and "not a valid equation" in resp["error"]
    assert client.get(f"/games/{gid}").json()["current_player"] == "Human"


def test_validate_move_reports_reason():
    gid = create("human_vs_agent")
    far = {"tiles_to_play": [{"r": 0, "c": 0, "tile": tile("x")}, {"r": 0, "c": 1, "tile": tile("=", 0)}],
           "direction": "H"}
    out = client.post(f"/games/{gid}/validate_move", json=far).json()
    assert out["valid"] is False and "connect" in out["reason"]
    ok = client.post(f"/games/{gid}/validate_move", json=equals_x_play()).json()
    assert ok["valid"] is True






def test_agent_vs_agent_step_and_autoplay(monkeypatch):
    monkeypatch.setitem(CONFIG, "max_turns", 6)
    gid = create("agent_vs_agent")
    step = client.post(f"/games/{gid}/agent_step").json()
    assert step["move"]["player"] == "Newton_Bot"
    out = client.post(f"/games/{gid}/autoplay").json()
    assert out["end_reason"] in ("turn limit", "stalled", "last tile drawn")
    assert client.get(f"/games/{gid}").json()["game_over"] is True


def test_cannot_autoplay_with_a_human_seat():
    gid = create("human_vs_agent")
    assert client.post(f"/games/{gid}/autoplay").status_code == 400


def test_unknown_game_404():
    assert client.get("/games/does-not-exist").status_code == 404


# ── forfeit ─────────────────────────────────────────────────────────────────
def test_forfeiting_a_game_against_the_computer_is_a_loss():
    gid = create("human_vs_agent")
    r = client.post(f"/games/{gid}/forfeit").json()
    assert r["status"] == "success" and r["game_over"] is True and r["agent_moves"] == []
    state = client.get(f"/games/{gid}").json()
    assert state["game_over"] and state["end_reason"] == "forfeit"
    assert state["forfeited_by"] == "Human" and state["winners"] == ["AI_Opponent"]
    assert state["last_move"]["action"] == "forfeit"


def test_no_moves_or_second_forfeit_after_forfeiting():
    gid = create("human_vs_agent")
    client.post(f"/games/{gid}/forfeit")
    assert client.post(f"/games/{gid}/forfeit").status_code == 400
    assert client.post(f"/games/{gid}/pass").status_code == 400
    rig_rack(gid, 0, ["=", "x"])
    assert client.post(f"/games/{gid}/play", json=equals_x_play()).status_code == 400




def test_bots_cannot_forfeit():
    gid = create("agent_vs_agent")
    assert client.post(f"/games/{gid}/forfeit").status_code == 400


def test_validate_move_reports_the_equation_tile_by_tile():
    from core.game_config import CONFIG
    gid = client.post("/games/create", json={"mode": "human_vs_agent"}).json()["game_id"]
    game = main.sessions[gid].game
    from core.game_entities import make_tile
    game.players[0].rack = [make_tile(sym, CONFIG) for sym in ("=", "x", "2", "x")]
    r, c = CONFIG["board_dimensions"][0] // 2, CONFIG["board_dimensions"][1] // 2
    t = lambda sym, pts: {"symbol": sym, "points": pts, "expr_multiplier": 1}
    good = {"tiles_to_play": [{"r": r, "c": c + 1, "tile": t("=", 0)}, {"r": r, "c": c + 2, "tile": t("x", 1)}], "direction": "H"}
    body = client.post(f"/games/{gid}/validate_move", json=good).json()
    assert body["valid"] and body["equation_tiles"] == [["x", "=", "x"]] and body["reason_tiles"] is None
    bad = {"tiles_to_play": [{"r": r, "c": c + 1, "tile": t("=", 0)}, {"r": r, "c": c + 2, "tile": t("2", 1)}], "direction": "H"}
    body = client.post(f"/games/{gid}/validate_move", json=bad).json()
    assert not body["valid"] and body["reason_tiles"] == ["x", "=", "2"] and body["equation_tiles"] == []
