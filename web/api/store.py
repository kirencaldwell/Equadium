"""
Persistence for saved games, player stats and profiles.

`get_store()` picks the backend from the environment:
  FIREBASE_PROJECT_ID + credentials    -> FirestoreStore (credentials = FIREBASE_SERVICE_ACCOUNT json,
                                          GOOGLE_APPLICATION_CREDENTIALS file, or FIRESTORE_EMULATOR_HOST)
  otherwise                            -> MemoryStore (lost on restart; fine for local dev)

Game records are plain dicts:
  id, kind ('solo'|'room'), code, mode, status ('waiting'|'active'|'finished'),
  state (GameSession.to_dict()), summary, meta, user_ids, created_at, updated_at
"""
import copy
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger("equadium_store")

ACTIVE = ("waiting", "active")


class StoreError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    enabled = False

    def save_game(self, rec: dict) -> None: raise NotImplementedError
    def get_game(self, game_id: Optional[str] = None, code: Optional[str] = None) -> Optional[dict]: raise NotImplementedError
    def list_user_games(self, user_id: str, statuses=ACTIVE, limit: int = 20) -> List[dict]: raise NotImplementedError
    def save_results(self, rows: List[dict]) -> None: raise NotImplementedError
    def list_results(self, user_id: str, limit: int = 1000) -> List[dict]: raise NotImplementedError
    def upsert_profile(self, user_id: str, display_name: Optional[str], avatar_url: Optional[str]) -> None: raise NotImplementedError
    def get_profile(self, user_id: str) -> Optional[dict]: raise NotImplementedError


class MemoryStore(Store):
    enabled = True
    name = "memory"

    def __init__(self):
        self._games: Dict[str, dict] = {}
        self._results: Dict[tuple, dict] = {}
        self._profiles: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def save_game(self, rec):
        with self._lock:
            old = self._games.get(rec["id"])
            rec = copy.deepcopy(rec)
            rec.setdefault("created_at", old["created_at"] if old else now_iso())
            rec["updated_at"] = now_iso()
            self._games[rec["id"]] = rec

    def get_game(self, game_id=None, code=None):
        with self._lock:
            if game_id is not None:
                rec = self._games.get(game_id)
            else:
                rec = next((g for g in self._games.values() if g.get("code") == code), None)
            return copy.deepcopy(rec) if rec else None

    def list_user_games(self, user_id, statuses=ACTIVE, limit=20):
        with self._lock:
            games = [g for g in self._games.values() if user_id in g["user_ids"] and g["status"] in statuses]
            games.sort(key=lambda g: g["updated_at"], reverse=True)
            return copy.deepcopy(games[:limit])

    def save_results(self, rows):
        with self._lock:
            for r in rows:
                self._results[(r["game_id"], r["user_id"])] = {**r, "finished_at": r.get("finished_at", now_iso())}

    def list_results(self, user_id, limit=1000):
        with self._lock:
            rows = [r for r in self._results.values() if r["user_id"] == user_id]
            rows.sort(key=lambda r: r["finished_at"], reverse=True)
            return copy.deepcopy(rows[:limit])

    def upsert_profile(self, user_id, display_name, avatar_url):
        with self._lock:
            self._profiles[user_id] = {"user_id": user_id, "display_name": display_name, "avatar_url": avatar_url}

    def get_profile(self, user_id):
        with self._lock:
            return copy.deepcopy(self._profiles.get(user_id))


class FirestoreStore(Store):
    """Google Cloud Firestore, accessed server-side with a service account (which bypasses security rules).

    Collections: games/{id}, game_results/{game_id}__{user_id}, profiles/{user_id}.
    Two Firestore quirks shape the layout:
      * arrays can't contain arrays, and the game snapshot does, so it is stored as a JSON string;
      * listing "my unfinished games" must not need a composite index, so each game carries
        `active_user_ids` (= user_ids while unfinished, empty afterwards) and is queried with a
        single array-contains filter.
    """
    enabled = True
    name = "firestore"

    def __init__(self, project_id: Optional[str] = None, credentials_json: Optional[str] = None,
                 client=None, timeout: float = 8.0):
        if client is None:
            from google.cloud import firestore
            creds = None
            if credentials_json:
                from google.oauth2 import service_account
                creds = service_account.Credentials.from_service_account_info(json.loads(credentials_json))
            client = firestore.Client(project=project_id, credentials=creds)
        self.db = client
        self.timeout = timeout

    # -- encoding -----------------------------------------------------------
    @staticmethod
    def _encode(rec: dict) -> dict:
        data = {k: v for k, v in rec.items() if k != "state"}
        data["state_json"] = json.dumps(rec["state"], separators=(",", ":"))
        data["active_user_ids"] = list(rec["user_ids"]) if rec["status"] in ACTIVE else []
        return data

    @staticmethod
    def _decode(data: dict) -> dict:
        rec = {k: v for k, v in data.items() if k not in ("state_json", "active_user_ids")}
        if "state_json" in data:
            rec["state"] = json.loads(data["state_json"])
        return rec

    @staticmethod
    def _where(collection, field: str, op: str, value):
        from google.cloud.firestore_v1.base_query import FieldFilter
        return collection.where(filter=FieldFilter(field, op, value))

    # -- games --------------------------------------------------------------
    def save_game(self, rec):
        from google.api_core.exceptions import NotFound
        ref = self.db.collection("games").document(rec["id"])
        data = {**self._encode(rec), "updated_at": now_iso()}
        try:
            ref.update(data, timeout=self.timeout)          # replaces the listed fields
        except NotFound:
            ref.set({**data, "created_at": data["updated_at"]}, timeout=self.timeout)

    def get_game(self, game_id=None, code=None):
        games = self.db.collection("games")
        if game_id is not None:
            snap = games.document(game_id).get(timeout=self.timeout)
            return self._decode(snap.to_dict()) if snap.exists else None
        for snap in self._where(games, "code", "==", code).limit(1).stream(timeout=self.timeout):
            return self._decode(snap.to_dict())
        return None

    def list_user_games(self, user_id, statuses=ACTIVE, limit=20):
        query = self._where(self.db.collection("games"), "active_user_ids", "array_contains", user_id)
        # `select` leaves out the (large) snapshot; the Continue list only needs the summary card.
        query = query.select(["id", "kind", "code", "mode", "status", "summary", "user_ids", "meta", "updated_at"])
        recs = [self._decode(s.to_dict()) for s in query.stream(timeout=self.timeout)]
        recs = [r for r in recs if r["status"] in statuses]
        recs.sort(key=lambda r: r["updated_at"], reverse=True)
        return recs[:limit]

    # -- results & profiles -------------------------------------------------
    def save_results(self, rows):
        for r in rows:
            self.db.collection("game_results").document(f"{r['game_id']}__{r['user_id']}").set(r, timeout=self.timeout)

    def list_results(self, user_id, limit=1000):
        query = self._where(self.db.collection("game_results"), "user_id", "==", user_id)
        rows = [s.to_dict() for s in query.limit(limit).stream(timeout=self.timeout)]
        rows.sort(key=lambda r: r["finished_at"], reverse=True)
        return rows

    def upsert_profile(self, user_id, display_name, avatar_url):
        self.db.collection("profiles").document(user_id).set(
            {"user_id": user_id, "display_name": display_name, "avatar_url": avatar_url}, merge=True, timeout=self.timeout)

    def get_profile(self, user_id):
        snap = self.db.collection("profiles").document(user_id).get(timeout=self.timeout)
        return snap.to_dict() if snap.exists else None


_store: Optional[Store] = None


def get_store() -> Store:
    global _store
    if _store is None:
        pid = os.getenv("FIREBASE_PROJECT_ID")
        creds = os.getenv("FIREBASE_SERVICE_ACCOUNT")
        if pid and (creds or os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("FIRESTORE_EMULATOR_HOST")):
            _store = FirestoreStore(pid, creds)
            logger.info("Saving games to Firestore (project %s)", pid)
        else:
            _store = MemoryStore()
            logger.info("Firestore not configured; saved games live in memory only")
    return _store


def set_store(store: Optional[Store]) -> None:
    """Test hook / explicit wiring."""
    global _store
    _store = store
