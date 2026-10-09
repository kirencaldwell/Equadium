"""Sign-in, saved games and stats. Uses an in-memory store and locally minted Firebase-style ID tokens."""
import datetime
import time

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from core.game_config import CONFIG
from core.game_entities import make_tile
from web.api import auth, main, persistence, rooms
from web.api.store import FirestoreStore, MemoryStore, set_store

PID = "equadium-test"
CERTS_URL = "https://certs.test/securetoken"
ALICE, BOB = "alice-uid-0001", "bob-uid-0002"

client = TestClient(main.app)


def make_key(kid):
    """An RSA key plus a self-signed X.509 cert, like the ones Google publishes for Firebase."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30)).sign(key, hashes.SHA256()))
    return kid, key, cert.public_bytes(serialization.Encoding.PEM).decode()


KID, KEY, CERT_PEM = make_key("key-1")


class FakeCertsResponse:
    def __init__(self, certs, max_age=3600):
        self._certs, self.headers = certs, {"Cache-Control": f"public, max-age={max_age}, must-revalidate"}

    def raise_for_status(self): pass
    def json(self): return self._certs


def token(uid=ALICE, name="Alice", exp=3600, aud=PID, iss=None, key=KEY, kid=KID, alg="RS256", sub=None):
    now = int(time.time())
    claims = {"sub": uid if sub is None else sub, "aud": aud, "iss": iss or f"https://securetoken.google.com/{PID}",
              "iat": now - 5, "exp": now + exp, "email": f"{name.lower()}@x.com", "name": name, "picture": "http://a/v.png"}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def H(uid=ALICE, name="Alice", **kw):
    return {"Authorization": f"Bearer {token(uid, name, **kw)}"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PID)
    monkeypatch.setenv("FIREBASE_CERTS_URL", CERTS_URL)
    fetches = []
    published = {KID: CERT_PEM}

    def fake_get(url, timeout=None):
        assert url == CERTS_URL
        fetches.append(url)
        return FakeCertsResponse(dict(published))
    monkeypatch.setattr(auth.requests, "get", fake_get)
    auth.reset_cert_cache()
    store = MemoryStore()
    set_store(store)
    main.sessions.clear()
    rooms._rooms.clear()
    store.cert_fetches, store.published = fetches, published
    yield store
    set_store(None)
    auth.reset_cert_cache()


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


OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.mark.parametrize("kw", [
    {"exp": -10},                                                     # expired
    {"aud": "another-project"},                                       # token for a different Firebase project
    {"iss": "https://securetoken.google.com/another-project"},
    {"key": OTHER_KEY},                                               # signed by someone else
    {"kid": "no-such-key"},                                           # unknown signing key
    {"sub": ""},                                                      # no subject
])
def test_bad_tokens_rejected(kw):
    assert client.get("/me", headers=H(**kw)).status_code == 401


def test_forged_hs256_token_using_public_cert_as_secret_is_rejected():
    """The classic algorithm-confusion attack: sign with HMAC using the (public) certificate as the secret.
    Built by hand because PyJWT itself refuses to do this."""
    import base64, hashlib, hmac, json as _json
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=")
    header = b64(_json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
    body = b64(_json.dumps({"sub": ALICE, "aud": PID, "iss": f"https://securetoken.google.com/{PID}",
                            "iat": int(time.time()), "exp": int(time.time()) + 600}).encode())
    sig = b64(hmac.new(CERT_PEM.encode(), header + b"." + body, hashlib.sha256).digest())
    forged = (header + b"." + body + b"." + sig).decode()
    assert client.get("/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_unsigned_token_is_rejected():
    unsigned = jwt.encode({"sub": ALICE, "aud": PID, "exp": int(time.time()) + 600}, None, algorithm="none")
    assert client.get("/me", headers={"Authorization": f"Bearer {unsigned}"}).status_code == 401


def test_garbage_token_is_rejected():
    assert client.get("/me", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_bad_token_is_not_silently_a_guest():
    # a broken session must surface as 401 so the app can ask the user to sign in again
    assert client.post("/games/create", json={"mode": "human_vs_agent"}, headers=H(exp=-10)).status_code == 401


def test_certs_are_cached_and_rotated_keys_are_picked_up(env):
    for _ in range(3):
        assert client.get("/me", headers=H()).status_code == 200
    assert len(env.cert_fetches) == 1                       # not one fetch per request

    new_kid, new_key, new_pem = make_key("key-2")           # Google rotates keys
    env.published[new_kid] = new_pem
    auth._certs._fetched -= auth.CERT_REFRESH_MIN_SECONDS   # let the unknown-kid refresh through
    assert client.get("/me", headers=H(key=new_key, kid=new_kid)).status_code == 200
    assert len(env.cert_fetches) == 2


def test_unknown_key_ids_cannot_force_a_fetch_per_request(env):
    client.get("/me", headers=H())
    before = len(env.cert_fetches)
    for _ in range(5):
        assert client.get("/me", headers=H(kid="nope")).status_code == 401
    assert len(env.cert_fetches) == before                   # rate limited


def test_keys_survive_a_certificate_endpoint_outage(monkeypatch):
    assert client.get("/me", headers=H()).status_code == 200
    auth._certs._expires = 0                                  # force a refresh...
    monkeypatch.setattr(auth.requests, "get", lambda *a, **k: (_ for _ in ()).throw(auth.requests.ConnectionError("down")))
    assert client.get("/me", headers=H()).status_code == 200  # ...which fails; last good keys still work


def test_auth_off_means_everyone_is_a_guest(monkeypatch):
    monkeypatch.delenv("FIREBASE_PROJECT_ID")
    r = client.post("/games/create", json={"mode": "human_vs_agent"}, headers={"Authorization": "Bearer junk"})
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


# ── Firestore store, against a fake that enforces Firestore's real restrictions ──
class FakeSnap:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        import copy
        return copy.deepcopy(self._data)


def _check_firestore_value(v, in_array=False):
    """Firestore rejects arrays nested directly in arrays and documents over 1 MiB."""
    if isinstance(v, (list, tuple)):
        assert not in_array, "Firestore does not allow nested arrays"
        for x in v:
            _check_firestore_value(x, True)
    elif isinstance(v, dict):
        for k, x in v.items():
            assert isinstance(k, str) and k, "map keys must be non-empty strings"
            _check_firestore_value(x)


class FakeDoc:
    def __init__(self, col, id):
        self.col, self.id = col, id

    def get(self, timeout=None):
        return FakeSnap(self.col.docs.get(self.id))

    def set(self, data, merge=False, timeout=None):
        import copy, json as _json
        _check_firestore_value(data)
        assert len(_json.dumps(data)) < 1_000_000
        data = copy.deepcopy(data)
        self.col.docs[self.id] = {**self.col.docs.get(self.id, {}), **data} if merge else data

    def update(self, data, timeout=None):
        from google.api_core.exceptions import NotFound
        if self.id not in self.col.docs:
            raise NotFound("no document to update")
        _check_firestore_value(data)
        self.col.docs[self.id] = {**self.col.docs[self.id], **data}


class FakeQuery:
    def __init__(self, col, filters=(), fields=None, n=None):
        self.col, self.filters, self.fields, self.n = col, tuple(filters), fields, n

    def where(self, filter):
        return FakeQuery(self.col, self.filters + (filter,), self.fields, self.n)

    def select(self, fields):
        return FakeQuery(self.col, self.filters, fields, self.n)

    def limit(self, n):
        return FakeQuery(self.col, self.filters, self.fields, n)

    def stream(self, timeout=None):
        out = []
        for data in self.col.docs.values():
            ok = True
            for f in self.filters:
                v = data.get(f.field_path)
                ok &= (v == f.value) if f.op_string == "==" else (f.value in (v or [])) if f.op_string == "array_contains" else False
            if ok:
                out.append(FakeSnap({k: x for k, x in data.items() if self.fields is None or k in self.fields}))
        return out[: self.n]


class FakeCollection(FakeQuery):
    def __init__(self):
        self.docs = {}
        super().__init__(self)

    def document(self, id):
        return FakeDoc(self, id)


class FakeFirestore:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, FakeCollection())


@pytest.fixture(params=["memory", "firestore-fake", "firestore-emulator"])
def any_store(request):
    if request.param == "memory":
        return MemoryStore()
    if request.param == "firestore-fake":
        return FirestoreStore(client=FakeFirestore())
    # The real thing: `firebase emulators:start --only firestore`, then FIRESTORE_EMULATOR_HOST=127.0.0.1:8080
    import os, requests
    host = os.getenv("FIRESTORE_EMULATOR_HOST")
    if not host:
        pytest.skip("set FIRESTORE_EMULATOR_HOST to run against the Firestore emulator")
    requests.delete(f"http://{host}/emulator/v1/projects/{PID}/databases/(default)/documents", timeout=5)   # clean slate
    return FirestoreStore(project_id=PID)


def game_rec(gid, users, status="active", code=None, kind="solo"):
    return {"id": gid, "kind": kind, "code": code, "mode": "human_vs_agent", "status": status,
            "state": {"board": [[12, 12, {"s": "x"}]], "history": [{"cells": [[1, 2], [3, 4]]}]},   # nested arrays, like a real snapshot
            "summary": {"current_player": "Human", "players": [{"name": "Human", "score": 3}]},
            "meta": {"seat_users": {"Human": users[0]}}, "user_ids": users}


def test_store_contract_games(any_store):
    any_store.save_game(game_rec("g1", [ALICE]))
    any_store.save_game(game_rec("g2", [ALICE, BOB], code="ABCDE", kind="room"))
    any_store.save_game(game_rec("g3", [BOB], status="finished"))
    assert any_store.get_game(game_id="g1")["state"]["board"] == [[12, 12, {"s": "x"}]]    # snapshot round-trips exactly
    assert any_store.get_game(code="ABCDE")["id"] == "g2"
    assert any_store.get_game(game_id="nope") is None and any_store.get_game(code="ZZZZZ") is None
    assert {g["id"] for g in any_store.list_user_games(ALICE)} == {"g1", "g2"}
    assert {g["id"] for g in any_store.list_user_games(BOB)} == {"g2"}          # g3 is finished
    any_store.save_game({**game_rec("g1", [ALICE]), "status": "abandoned"})     # saving again replaces
    assert {g["id"] for g in any_store.list_user_games(ALICE)} == {"g2"}
    any_store.save_game({**game_rec("g2", [ALICE, BOB], code="ABCDE", kind="room"), "status": "finished"})
    assert any_store.list_user_games(ALICE) == []


def test_store_list_cards_leave_out_the_snapshot_and_internal_fields():
    store = FirestoreStore(client=FakeFirestore())
    store.save_game(game_rec("g1", [ALICE]))
    card = store.list_user_games(ALICE)[0]
    assert "state" not in card and "state_json" not in card and "active_user_ids" not in card
    assert card["summary"]["players"][0]["score"] == 3
    full = store.get_game(game_id="g1")
    assert "state_json" not in full and "active_user_ids" not in full and "created_at" in full


def test_store_contract_results_and_profiles(any_store):
    row = lambda gid, uid, t: {"game_id": gid, "user_id": uid, "mode": "online", "opponent": "human", "outcome": "win",
                               "score": 10, "opp_score": 5, "plays": 1, "swaps": 0, "passes": 0, "best_play": 10,
                               "turns": 2, "finished_at": t}
    any_store.save_results([row("a", ALICE, "2026-01-01"), row("b", ALICE, "2026-02-01"), row("a", BOB, "2026-01-01")])
    any_store.save_results([row("a", ALICE, "2026-01-01")])                     # idempotent
    assert [r["game_id"] for r in any_store.list_results(ALICE)] == ["b", "a"]  # newest first
    assert len(any_store.list_results(BOB)) == 1
    any_store.upsert_profile(ALICE, "Alice", None)
    any_store.upsert_profile(ALICE, "Alice B", "http://a")
    assert any_store.get_profile(ALICE)["display_name"] == "Alice B"
    assert any_store.get_profile(BOB) is None


def test_firestore_is_chosen_only_when_fully_configured(monkeypatch):
    from web.api import store as store_mod
    for var in ("FIREBASE_PROJECT_ID", "FIREBASE_SERVICE_ACCOUNT", "GOOGLE_APPLICATION_CREDENTIALS", "FIRESTORE_EMULATOR_HOST"):
        monkeypatch.delenv(var, raising=False)
    store_mod.set_store(None)
    assert store_mod.get_store().name == "memory"
    store_mod.set_store(None)
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PID)                              # project but no credentials: don't guess
    assert store_mod.get_store().name == "memory"


# ── resilience: a broken backend must never look like a CORS problem or break a game ──
def test_store_that_fails_to_initialise_never_breaks_play(monkeypatch):
    """E.g. FIREBASE_SERVICE_ACCOUNT / GOOGLE_APPLICATION_CREDENTIALS pointing at a missing file."""
    from web.api import store as store_mod
    set_store(None)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/nonexistent/firebase.json")
    safe = TestClient(main.app, raise_server_exceptions=False)
    r = safe.post("/games/create", json={"mode": "human_vs_agent"}, headers=H())
    assert r.status_code == 200, r.text
    gid = r.json()["game_id"]
    assert safe.post(f"/games/{gid}/pass", headers=H()).json()["status"] == "success"
    assert store_mod.get_store().name == "memory"          # degraded loudly (logged), but still playing


def test_server_errors_still_carry_cors_headers(monkeypatch):
    """Without this, any 500 shows up in the browser as a misleading 'blocked by CORS policy'."""
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(main, "GameSession", boom)
    safe = TestClient(main.app, raise_server_exceptions=False)
    r = safe.post("/games/create", json={"mode": "human_vs_agent"}, headers={"Origin": "https://equadium.vercel.app"})
    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") in ("*", "https://equadium.vercel.app")
    assert "kaboom" not in r.text                           # no internals leaked to the client


def test_allowed_origins_tolerate_trailing_slashes_and_spaces():
    assert main._cors_origins("https://a.vercel.app/, http://localhost:3000 ,") == ["https://a.vercel.app", "http://localhost:3000"]
    assert main._cors_origins("") == ["*"] and main._cors_origins(None) == ["*"]
