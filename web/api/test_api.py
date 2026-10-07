import pytest
from fastapi.testclient import TestClient

from core.game_config import CONFIG
from web.api.main import app

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


def test_modes_listed():
    assert set(client.get("/modes").json()) == {"human_vs_agent", "human_vs_human", "agent_vs_agent"}


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


def test_human_vs_human_two_players_alternate():
    gid = create("human_vs_human")
    rig_rack(gid, 0, ["=", "x"])
    state = client.get(f"/games/{gid}").json()
    assert state["seats"] == {"Player1": "human", "Player2": "human"}
    # Player2 can't move first
    assert client.post(f"/games/{gid}/pass", params={"player": "Player2"}).status_code == 400
    r = client.post(f"/games/{gid}/play", params={"player": "Player1"}, json=equals_x_play())
    assert r.json()["status"] == "success" and r.json()["agent_moves"] == []
    assert client.get(f"/games/{gid}").json()["current_player"] == "Player2"
    assert client.post(f"/games/{gid}/pass", params={"player": "Player2"}).json()["status"] == "success"
    assert client.get(f"/games/{gid}").json()["current_player"] == "Player1"


def test_swap_in_human_vs_human_hot_seat():
    gid = create("human_vs_human")
    r = client.post(f"/games/{gid}/swap", params={"player": "Player1"}, json={"tile_indices": [0, 1, 2]})
    assert r.json()["status"] == "success"
    assert client.get(f"/games/{gid}").json()["current_player"] == "Player2"


def test_agent_vs_agent_step_and_autoplay(monkeypatch):
    monkeypatch.setitem(CONFIG, "max_turns", 6)
    gid = create("agent_vs_agent")
    step = client.post(f"/games/{gid}/agent_step").json()
    assert step["move"]["player"] == "Newton_Bot"
    out = client.post(f"/games/{gid}/autoplay").json()
    assert out["end_reason"] in ("turn limit", "stalled", "rack empty", "bag empty+stuck")
    assert client.get(f"/games/{gid}").json()["game_over"] is True


def test_cannot_autoplay_with_a_human_seat():
    gid = create("human_vs_agent")
    assert client.post(f"/games/{gid}/autoplay").status_code == 400


def test_join_turns_agent_seat_into_human():
    gid = create("human_vs_agent")
    assert client.post(f"/games/{gid}/join").status_code == 200
    state = client.get(f"/games/{gid}").json()
    assert state["mode"] == "human_vs_human"
    assert set(state["seats"].values()) == {"human"}


def test_unknown_game_404():
    assert client.get("/games/does-not-exist").status_code == 404
