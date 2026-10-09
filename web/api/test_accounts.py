"""Sign-in, saved games and stats. Uses an in-memory store and locally minted Supabase-style JWTs."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from core.game_config import CONFIG
from core.game_entities import make_tile
from web.api import auth, main, persistence, rooms
from web.api.store import MemoryStore, SupabaseStore, set_store

SECRET = "test-secret-test-secret-test-secret-123"
URL = "https://proj.supabase.co"
ALICE, BOB = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"

client = TestClient(main.app)


def token(uid=ALICE, name="Alice", secret=SECRET, alg="HS256", exp=3600, aud="authenticated", iss=None, key=None):
    claims = {"sub": uid, "aud": aud, "exp": int(time.time()) + exp, "email": f"{name.lower()}@x.com",
              "user_metadata": {"full_name": name, "avatar_url": "http://a/v.png"}}
    if iss:
        claims["iss"] = iss
    return jwt.encode(claims, key or secret, algorithm=alg)


def H(uid=ALICE, name="Alice", **kw):
    return {"Authorization": f"Bearer {token(uid, name, **kw)}"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    store = MemoryStore()
    set_store(store)
    main.sessions.clear()
    rooms._rooms.clear()
    yield store
    set_store(None)


def restart():
    """Simulate a server restart: everything in memory is gone, only the store remains."""
    main.sessions.clear()
    main._locks.clear()
    rooms._rooms.clear()


def rig(session, seat_index, symbols):
    session.game.players[seat_index].rack = [make_tile(s, CONFIG) for s in symbols]


def eq_play():
    r, c = CONFIG["board_dimensions"][0] // 2, CONFIG["board_dimensions"][1] // 2
    t = lambda sym, pts: {"symbol": sym, "points": pts, "expr_multiplier": 1}
    return {"tiles_to_play": [{"r": r, "c": c + 1, "tile": t("=", 0)}, {"r": r, "c": c + 2, "tile": t("x", 1)}],
            "direction": "H"}


# ── token verification ──────────────────────────────────────────────────────
def test_me_requires_sign_in():
    assert client.get("/me").status_code == 401


def test_valid_token_identifies_user():
    r = client.get("/me", headers=H())
    assert r.status_code == 200
    assert r.json()["id"] == ALICE and r.json()["name"] == "Alice" and r.json()["avatar_url"] == "http://a/v.png"


@pytest.mark.parametrize("kw", [{"secret": "x" * 40}, {"exp": -10}, {"aud": "anon"}])
def test_bad_tokens_rejected(kw):
    assert client.get("/me", headers=H(**kw)).status_code == 401


def test_bad_token_is_not_silently_a_guest():
    # a broken session must surface as 401 so the app can ask the user to sign in again
    assert client.post("/games/create", json={"mode": "human_vs_agent"}, headers=H(exp=-10)).status_code == 401


def test_asymmetric_tokens_verified_via_jwks(monkeypatch):
    private = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET")

    class FakeJwks:
        def get_signing_key_from_jwt(self, _):
            return type("K", (), {"key": private.public_key()})()
    monkeypatch.setitem(auth._jwks_clients, URL, FakeJwks())

    good = token(alg="ES256", key=private, iss=f"{URL}/auth/v1")
    assert client.get("/me", headers={"Authorization": f"Bearer {good}"}).json()["id"] == ALICE
    other = ec.generate_private_key(ec.SECP256R1())
    forged = token(alg="ES256", key=other, iss=f"{URL}/auth/v1")
    assert client.get("/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    wrong_iss = token(alg="ES256", key=private, iss="https://evil.example/auth/v1")
    assert client.get("/me", headers={"Authorization": f"Bearer {wrong_iss}"}).status_code == 401


def test_auth_off_means_everyone_is_a_guest(monkeypatch):
    monkeypatch.delenv("SUPABASE_JWT_SECRET")
    r = client.post("/games/create", json={"mode": "human_vs_agent"}, headers=H(secret="whatever" * 6))
    assert r.status_code == 200
    assert client.get("/me").status_code == 401


# ── saved solo games ────────────────────────────────────────────────────────
def new_game(headers=None, mode="human_vs_agent"):
    r = client.post("/games/create", json={"mode": mode}, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()["game_id"]


def test_game_ids_are_not_guessable():
    ids = {new_game() for _ in range(5)}
    assert len(ids) == 5 and not any(i.isdigit() for i in ids)


def test_signed_in_game_survives_restart_and_opens_anywhere():
    gid = new_game(H())
    rig(main.sessions[gid], 0, ["=", "x", "2"])
    assert client.post(f"/games/{gid}/play", json=eq_play(), headers=H()).json()["status"] == "success"
    before = client.get(f"/games/{gid}", headers=H()).json()

    restart()
    after = client.get(f"/games/{gid}", headers=H()).json()   # "another device": loads from the store
    assert after["board"] == before["board"] and after["players"] == before["players"]
    assert after["turns_played"] == before["turns_played"] == 2
    # and it is still playable
    assert client.post(f"/games/{gid}/pass", headers=H()).json()["status"] == "success"


def test_saved_game_is_private_to_its_owner():
    gid = new_game(H())
    restart()
    assert client.get(f"/games/{gid}").status_code == 401                       # guest
    assert client.get(f"/games/{gid}", headers=H(BOB, "Bob")).status_code == 404  # someone else
    assert client.post(f"/games/{gid}/pass", headers=H(BOB, "Bob")).status_code == 404
    assert client.get(f"/games/{gid}", headers=H()).status_code == 200


def test_guest_games_are_not_saved(env):
    gid = new_game()
    assert env.get_game(gid) is None
    assert client.post(f"/games/{gid}/pass").json()["status"] == "success"      # still playable by id
    assert env.list_user_games(ALICE) == []


def test_watching_bots_is_never_saved(env):
    gid = new_game(H(), mode="agent_vs_agent")
    assert env.get_game(gid) is None


def test_continue_list_and_delete():
    a = new_game(H())
    b = new_game(H())
    games = client.get("/me/games", headers=H()).json()["games"]
    assert {g["id"] for g in games} == {a, b}
    g = next(g for g in games if g["id"] == a)
    assert g["kind"] == "solo" and g["seat"] == "Human" and g["your_turn"] is True
    assert [p["name"] for p in g["players"]] == ["Human", "AI_Opponent"]
    assert client.get("/me/games", headers=H(BOB, "Bob")).json()["games"] == []

    assert client.delete(f"/games/{a}", headers=H()).json() == {"deleted": True}
    restart()
    assert [g["id"] for g in client.get("/me/games", headers=H()).json()["games"]] == [b]
    assert client.get(f"/games/{a}", headers=H()).status_code == 200   # still loadable by id, just not listed


# ── results & stats ─────────────────────────────────────────────────────────
def finish_solo(monkeypatch, headers):
    monkeypatch.setitem(CONFIG, "max_turns", 2)       # human passes, bot moves -> game over
    gid = new_game(headers)
    assert client.post(f"/games/{gid}/pass", headers=headers).json()["game_over"] is True
    return gid


def test_finished_game_records_stats_once(monkeypatch):
    gid = finish_solo(monkeypatch, H())
    stats = client.get("/me/stats", headers=H()).json()
    assert stats["games"] == 1 and stats["wins"] + stats["losses"] + stats["ties"] == 1
    assert stats["vs_computer"]["games"] == 1 and stats["recent"][0]["game_id"] == gid
    # finished games leave the Continue list, and re-finalising never double counts
    assert client.get("/me/games", headers=H()).json()["games"] == []
    restart()
    client.get(f"/games/{gid}", headers=H())
    assert client.get("/me/stats", headers=H()).json()["games"] == 1


def test_failed_result_write_is_retried_on_next_load(monkeypatch, env):
    real = env.save_results
    env.save_results = lambda rows: (_ for _ in ()).throw(RuntimeError("db down"))
    gid = finish_solo(monkeypatch, H())
    assert client.get("/me/stats", headers=H()).json()["games"] == 0
    assert env.get_game(gid)["status"] == "active"          # not marked finished until results are in
    env.save_results = real
    restart()
    client.get(f"/games/{gid}", headers=H())
    assert client.get("/me/stats", headers=H()).json()["games"] == 1
    assert env.get_game(gid)["status"] == "finished"


def test_storage_outage_never_breaks_a_move(env):
    gid = new_game(H())
    env.save_game = lambda rec: (_ for _ in ()).throw(RuntimeError("db down"))
    assert client.post(f"/games/{gid}/pass", headers=H()).json()["status"] == "success"


def test_pass_and_play_is_saved_but_not_counted(monkeypatch):
    monkeypatch.setitem(CONFIG, "max_turns", 2)
    gid = new_game(H(), mode="human_vs_human")
    client.post(f"/games/{gid}/pass", params={"player": "Player1"}, headers=H())
    client.post(f"/games/{gid}/pass", params={"player": "Player2"}, headers=H())
    assert client.get("/me/stats", headers=H()).json()["games"] == 0


def test_compute_stats():
    row = lambda o, score, best, opp="agent": {"game_id": "g", "user_id": "u", "mode": "human_vs_agent", "opponent": opp,
                                               "outcome": o, "score": score, "opp_score": 10, "plays": 4,
                                               "best_play": best, "finished_at": "t"}
    st = persistence.compute_stats([row("win", 50, 20), row("win", 30, 9), row("loss", 10, 30), row("tie", 40, 5, "human")])
    assert (st["games"], st["wins"], st["losses"], st["ties"]) == (4, 2, 1, 1)
    assert st["win_rate"] == 0.5 and st["avg_score"] == 32.5 and st["best_score"] == 50 and st["best_play"] == 30
    assert st["win_streak"] == 2 and st["vs_computer"] == {"games": 3, "wins": 2} and st["vs_humans"] == {"games": 1, "wins": 0}
    empty = persistence.compute_stats([])
    assert empty["games"] == 0 and empty["win_rate"] == 0.0 and empty["recent"] == []


# ── online rooms with accounts ──────────────────────────────────────────────
def make_room():
    host = client.post("/rooms", json={}, headers=H()).json()
    guest = client.post(f"/rooms/{host['code']}/join", json={}, headers=H(BOB, "Bob")).json()
    return host, guest


def test_room_seats_are_bound_to_accounts_and_resume_without_token():
    host, guest = make_room()
    assert {host["seat"], guest["seat"]} == {"Player1", "Player2"}
    restart()
    # Alice opens the game on a new device: no token, just her account
    r = client.get(f"/rooms/{host['code']}", headers=H())
    assert r.status_code == 200 and r.json()["seat"] == host["seat"]
    assert client.get(f"/rooms/{host['code']}", headers=H(BOB, "Bob")).json()["seat"] == guest["seat"]
    assert client.get(f"/rooms/{host['code']}", headers=H("33333333-3333-3333-3333-333333333333", "Eve")).status_code == 403
    assert client.get(f"/rooms/{host['code']}").status_code == 403


def test_my_games_returns_a_usable_room_token():
    host, _ = make_room()
    restart()
    entry = next(g for g in client.get("/me/games", headers=H()).json()["games"] if g["kind"] == "room")
    assert entry["code"] == host["code"] and entry["seat"] == host["seat"] and entry["token"] == host["token"]
    r = client.get(f"/rooms/{entry['code']}", headers={"X-Player-Token": entry["token"]})   # guest-style access works too
    assert r.status_code == 200
    # Bob's list never exposes Alice's token
    bob_entry = next(g for g in client.get("/me/games", headers=H(BOB, "Bob")).json()["games"] if g["kind"] == "room")
    assert bob_entry["token"] != host["token"]


def test_rejoining_your_own_room_returns_your_seat():
    host, _ = make_room()
    again = client.post(f"/rooms/{host['code']}/join", json={}, headers=H()).json()
    assert again["seat"] == host["seat"] and again["token"] == host["token"]


def test_room_play_is_saved_and_finish_records_both_players(monkeypatch):
    host, guest = make_room()
    room = rooms._rooms[host["code"]]
    for i in (0, 1):
        rig(room.session, i, ["=", "x", "2", "x"])
    mover = {host["seat"]: H(), guest["seat"]: H(BOB, "Bob")}[room.session.current_player.name]
    assert client.post(f"/rooms/{host['code']}/play", json=eq_play(), headers=mover).json()["status"] == "success"
    restart()
    state = client.get(f"/rooms/{host['code']}", headers=H()).json()["state"]
    assert state["turns_played"] == 1

    monkeypatch.setitem(CONFIG, "max_turns", 2)
    room = rooms._get_room(host["code"])
    mover = {host["seat"]: H(), guest["seat"]: H(BOB, "Bob")}[room.session.current_player.name]
    assert client.post(f"/rooms/{host['code']}/pass", headers=mover).json()["game_over"] is True
    a, b = (client.get("/me/stats", headers=H(u, n)).json() for u, n in ((ALICE, "Alice"), (BOB, "Bob")))
    assert a["games"] == b["games"] == 1
    assert a["vs_humans"]["games"] == 1
    assert a["wins"] + b["wins"] == 1 or a["ties"] == b["ties"] == 1   # exactly one winner, or a tie for both
    assert a["wins"] + a["losses"] + a["ties"] == 1
    assert {r["mode"] for r in a["recent"]} == {"online"}


def test_guest_rooms_survive_a_restart_too():
    host = client.post("/rooms", json={"name": "Zed"}).json()
    restart()
    r = client.get(f"/rooms/{host['code']}", headers={"X-Player-Token": host["token"]})
    assert r.status_code == 200 and r.json()["seat"] == host["seat"]


def test_room_codes_do_not_collide_with_saved_rooms(env, monkeypatch):
    host = client.post("/rooms", json={}).json()
    restart()                                              # memory forgets the code, the store remembers
    seq = iter(list(host["code"]) + list("ABCDE"))
    monkeypatch.setattr(rooms.secrets, "choice", lambda alphabet: next(seq))
    assert rooms._new_code() == "ABCDE"


# ── Supabase store, against an emulated PostgREST ───────────────────────────
class FakePostgrest:
    """Just enough of PostgREST for the queries SupabaseStore issues."""
    def __init__(self):
        self.tables = {"games": {}, "game_results": {}, "profiles": {}}
        self.calls = []
        self.keys = {"games": ("id",), "game_results": ("game_id", "user_id"), "profiles": ("user_id",)}

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        table = url.rsplit("/", 1)[1]
        self.calls.append((method, table, params, headers))
        rows = self.tables[table]
        params = params or {}
        if method == "POST":
            assert params.get("on_conflict") == ",".join(self.keys[table]), "upsert must name its conflict columns"
            assert "resolution=merge-duplicates" in headers["Prefer"]
            for r in (json if isinstance(json, list) else [json]):
                key = tuple(r[k] for k in self.keys[table])
                defaults = {"finished_at": "2026-01-01T00:00:00Z"} if table == "game_results" else {}   # column defaults
                rows[key] = {**defaults, **rows.get(key, {}), **r}
            return self._resp(201, None)
        out = list(rows.values())
        for col, cond in params.items():
            if col in ("select", "order", "limit"):
                continue
            op, _, val = cond.partition(".")
            if op == "eq":
                out = [r for r in out if str(r.get(col)) == val]
            elif op == "cs":
                out = [r for r in out if set(val.strip("{}").split(",")) <= set(r.get(col) or [])]
            elif op == "in":
                out = [r for r in out if r.get(col) in val.strip("()").split(",")]
        if "order" in params:
            col, _, direction = params["order"].partition(".")
            out.sort(key=lambda r: r[col], reverse=direction == "desc")
        return self._resp(200, out[: int(params.get("limit", 1000))])

    @staticmethod
    def _resp(status, body):
        import json as _json
        return type("R", (), {"status_code": status, "content": b"x" if body is not None else b"",
                              "text": "", "json": lambda self: body})()


@pytest.fixture(params=["memory", "supabase"])
def any_store(request):
    if request.param == "memory":
        return MemoryStore()
    store = SupabaseStore(URL, "eyJ-service-key")
    store.http = FakePostgrest()
    return store


def game_rec(gid, users, status="active", code=None, kind="solo"):
    return {"id": gid, "kind": kind, "code": code, "mode": "human_vs_agent", "status": status, "state": {"x": 1},
            "summary": {"current_player": "Human"}, "meta": {"seat_users": {"Human": users[0]}}, "user_ids": users}


def test_store_contract_games(any_store):
    any_store.save_game(game_rec("g1", [ALICE]))
    any_store.save_game(game_rec("g2", [ALICE, BOB], code="ABCDE", kind="room"))
    any_store.save_game(game_rec("g3", [BOB], status="finished"))
    assert any_store.get_game(game_id="g1")["id"] == "g1"
    assert any_store.get_game(code="ABCDE")["id"] == "g2"
    assert any_store.get_game(game_id="nope") is None
    assert {g["id"] for g in any_store.list_user_games(ALICE)} == {"g1", "g2"}
    assert {g["id"] for g in any_store.list_user_games(BOB)} == {"g2"}          # g3 is finished
    any_store.save_game({**game_rec("g1", [ALICE]), "status": "abandoned"})     # upsert replaces
    assert {g["id"] for g in any_store.list_user_games(ALICE)} == {"g2"}


def test_store_contract_results_and_profiles(any_store):
    row = lambda gid, uid: {"game_id": gid, "user_id": uid, "mode": "online", "opponent": "human", "outcome": "win",
                            "score": 10, "opp_score": 5, "plays": 1, "swaps": 0, "passes": 0, "best_play": 10, "turns": 2}
    any_store.save_results([row("a", ALICE), row("b", ALICE), row("a", BOB)])
    any_store.save_results([row("a", ALICE)])                                   # idempotent
    assert len(any_store.list_results(ALICE)) == 2 and len(any_store.list_results(BOB)) == 1
    any_store.upsert_profile(ALICE, "Alice", None)
    any_store.upsert_profile(ALICE, "Alice B", "http://a")
    assert any_store.get_profile(ALICE)["display_name"] == "Alice B"
    assert any_store.get_profile(BOB) is None


def test_supabase_key_headers():
    legacy = SupabaseStore(URL, "eyJhbGciOi.service.role")
    assert legacy.http.headers["apikey"] == legacy.http.headers["Authorization"].split()[1]
    new_style = SupabaseStore(URL, "sb_secret_abc123")
    assert new_style.http.headers["apikey"] == "sb_secret_abc123" and "Authorization" not in new_style.http.headers
