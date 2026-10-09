from fastapi.testclient import TestClient

from core.game_config import CONFIG
from core.game_entities import make_tile
from web.api.main import app
from web.api import rooms

client = TestClient(app)
CR, CC = CONFIG["board_dimensions"][0] // 2, CONFIG["board_dimensions"][1] // 2


def tile(symbol, points=1):
    return {"symbol": symbol, "points": points, "expr_multiplier": 1}


def hdr(token):
    return {"X-Player-Token": token}


def new_game():
    host = client.post("/rooms", json={"name": "Ada"}).json()
    guest = client.post(f"/rooms/{host['code']}/join", json={"name": "Bo"}).json()
    return host, guest


def state(code, who):
    return client.get(f"/rooms/{code}", headers=hdr(who["token"])).json()


def mover_and_waiter(host, guest):
    st = state(host["code"], host)["state"]
    cur = st["current_player"]
    return (host, guest) if host["seat"] == cur else (guest, host)


def test_create_and_join_give_opposite_seats_and_tokens():
    host, guest = new_game()
    assert {host["seat"], guest["seat"]} == {"Player1", "Player2"}
    assert host["token"] != guest["token"]
    assert len(host["code"]) == rooms.CODE_LENGTH


def test_third_player_is_rejected_and_unknown_code_404s():
    host, _ = new_game()
    assert client.post(f"/rooms/{host['code']}/join").status_code == 409
    assert client.post("/rooms/NOPE0/join").status_code == 404


def test_state_hides_the_opponents_rack_but_shows_your_own():
    host, guest = new_game()
    for me, other in ((host, guest), (guest, host)):
        st = state(host["code"], me)["state"]
        mine = next(p for p in st["players"] if p["name"] == me["seat"])
        theirs = next(p for p in st["players"] if p["name"] == other["seat"])
        assert len(mine["rack"]) == mine["rack_count"] > 0
        assert theirs["rack"] == [] and theirs["rack_count"] > 0
        assert st["labels"] == {host["seat"]: "Ada", guest["seat"]: "Bo"}


def test_requests_need_a_valid_token():
    host, _ = new_game()
    assert client.get(f"/rooms/{host['code']}").status_code == 403
    assert client.get(f"/rooms/{host['code']}", headers=hdr("wrong")).status_code == 403
    assert client.post(f"/rooms/{host['code']}/pass").status_code == 403


def test_cannot_move_before_opponent_joins():
    host = client.post("/rooms", json={"name": "Ada"}).json()
    r = client.post(f"/rooms/{host['code']}/pass", headers=hdr(host["token"]))
    assert r.status_code == 409
    assert state(host["code"], host)["state"]["joined"] is False


def test_only_the_player_to_move_can_act():
    host, guest = new_game()
    mover, waiter = mover_and_waiter(host, guest)
    bad = client.post(f"/rooms/{host['code']}/pass", headers=hdr(waiter["token"]))
    assert bad.status_code == 400 and "turn" in bad.json()["detail"]
    ok = client.post(f"/rooms/{host['code']}/pass", headers=hdr(mover["token"]))
    assert ok.status_code == 200 and ok.json()["status"] == "success"
    assert state(host["code"], host)["state"]["current_player"] == waiter["seat"]


def test_play_updates_both_views_and_bumps_version():
    host, guest = new_game()
    mover, waiter = mover_and_waiter(host, guest)
    seat_idx = 0 if mover["seat"] == "Player1" else 1
    rooms._rooms[host["code"]].session.game.players[seat_idx].rack = [make_tile("x", CONFIG)]
    v0 = state(host["code"], waiter)["version"]
    body = {"tiles_to_play": [{"r": CR, "c": CC + 1, "tile": tile("=", 0)},
                              {"r": CR, "c": CC + 2, "tile": tile("x")}], "direction": "H"}
    r = client.post(f"/rooms/{host['code']}/play", json=body, headers=hdr(mover["token"]))
    assert r.json()["status"] == "success", r.text
    seen = state(host["code"], waiter)
    assert seen["version"] > v0
    assert seen["state"]["board"]["grid"][CR][CC + 2]["symbol"] == "x"


def test_polling_with_since_reports_no_change():
    host, guest = new_game()
    v = state(host["code"], host)["version"]
    r = client.get(f"/rooms/{host['code']}?since={v}", headers=hdr(host["token"])).json()
    assert r == {"changed": False, "version": v}


def test_invalid_play_does_not_change_version():
    host, guest = new_game()
    mover, _ = mover_and_waiter(host, guest)
    v = state(host["code"], mover)["version"]
    body = {"tiles_to_play": [{"r": 0, "c": 0, "tile": tile("x")}], "direction": "H"}
    r = client.post(f"/rooms/{host['code']}/play", json=body, headers=hdr(mover["token"]))
    assert r.json()["status"] == "failed"
    assert state(host["code"], mover)["version"] == v


def test_solo_games_endpoint_cannot_see_rooms():
    host, _ = new_game()
    assert client.get(f"/games/{host['code']}").status_code == 404
