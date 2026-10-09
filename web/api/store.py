"""
Persistence for saved games, player stats and profiles.

`get_store()` picks the backend from the environment:
  SUPABASE_URL + SUPABASE_SERVICE_KEY  -> SupabaseStore (Postgres via PostgREST)
  otherwise                            -> MemoryStore (lost on restart; fine for local dev)

Game records are plain dicts:
  id, kind ('solo'|'room'), code, mode, status ('waiting'|'active'|'finished'),
  state (GameSession.to_dict()), summary, meta, user_ids, created_at, updated_at
"""
import copy
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

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


class SupabaseStore(Store):
    """Talks to Supabase's PostgREST API with the service-role key (server-side only)."""
    enabled = True
    name = "supabase"

    def __init__(self, url: str, service_key: str, timeout: float = 8.0):
        self.base = url.rstrip("/") + "/rest/v1"
        self.timeout = timeout
        self.http = requests.Session()
        self.http.headers.update({"apikey": service_key, "Content-Type": "application/json"})
        # Legacy service_role keys are JWTs and go in Authorization too; the newer
        # sb_secret_... keys must only be sent as the apikey header.
        if service_key.startswith("eyJ"):
            self.http.headers["Authorization"] = f"Bearer {service_key}"

    def _request(self, method: str, table: str, params=None, json=None, prefer: Optional[str] = None):
        headers = {"Prefer": prefer} if prefer else {}
        try:
            res = self.http.request(method, f"{self.base}/{table}", params=params, json=json,
                                    headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise StoreError(f"Supabase request failed: {exc}") from exc
        if res.status_code >= 300:
            raise StoreError(f"Supabase {method} {table} -> {res.status_code}: {res.text[:300]}")
        return res.json() if method == "GET" else None

    def _upsert(self, table: str, rows, conflict: str):
        self._request("POST", table, params={"on_conflict": conflict}, json=rows,
                      prefer="resolution=merge-duplicates,return=minimal")

    def save_game(self, rec):
        row = {**rec, "updated_at": now_iso()}
        self._upsert("games", row, "id")

    def get_game(self, game_id=None, code=None):
        key, value = ("id", game_id) if game_id is not None else ("code", code)
        rows = self._request("GET", "games", params={key: f"eq.{value}", "select": "*", "limit": 1})
        return rows[0] if rows else None

    def list_user_games(self, user_id, statuses=ACTIVE, limit=20):
        return self._request("GET", "games", params={
            "user_ids": "cs.{" + user_id + "}",
            "status": "in.(" + ",".join(statuses) + ")",
            "order": "updated_at.desc", "limit": limit,
            "select": "id,kind,code,mode,status,summary,user_ids,meta,updated_at",
        })

    def save_results(self, rows):
        if rows:
            self._upsert("game_results", rows, "game_id,user_id")

    def list_results(self, user_id, limit=1000):
        return self._request("GET", "game_results", params={
            "user_id": f"eq.{user_id}", "order": "finished_at.desc", "limit": limit, "select": "*"})

    def upsert_profile(self, user_id, display_name, avatar_url):
        self._upsert("profiles", {"user_id": user_id, "display_name": display_name, "avatar_url": avatar_url}, "user_id")

    def get_profile(self, user_id):
        rows = self._request("GET", "profiles", params={"user_id": f"eq.{user_id}", "select": "*", "limit": 1})
        return rows[0] if rows else None


_store: Optional[Store] = None


def get_store() -> Store:
    global _store
    if _store is None:
        url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
        if url and key:
            _store = SupabaseStore(url, key)
            logger.info("Saving games to Supabase")
        else:
            _store = MemoryStore()
            logger.info("Supabase not configured; saved games live in memory only")
    return _store


def set_store(store: Optional[Store]) -> None:
    """Test hook / explicit wiring."""
    global _store
    _store = store
