"""
configs.py

A registry of named game configurations.

"standard" is the live game (core/game_config.CONFIG). Every other entry is a *beta*: a
small set of overrides on top of the standard config, listed in core/beta_configs.json.
Online rooms can be created with any registered config, bots can play any of them, and
the offline tools (core/beta_playtest.py, core/tune_tiles.py) take a config id too.

To try a new idea, add an entry to beta_configs.json:

    {"id": "beta-short-game", "name": "Short game", "description": "...",
     "overrides": {"max_turns": 40}}

`overrides` is deep-merged onto the standard config, so a tile only needs the fields you
change ({"tiles": {"d/dx(": {"count": 8}}}). What may be changed is limited to things the
engine, the bots and the UI all handle (see validate_overrides): tile counts, points and
multipliers for the existing tile symbols, the equals pile, rack size, the "+C" rule,
the turn limit and the stall rule. New tile symbols and the board size need engine and
art changes first, so they are rejected.
"""
import copy
import hashlib
import json
import os
from typing import Dict, List

from core.game_config import CONFIG

STANDARD_ID = "standard"
BETA_FILE = os.path.join(os.path.dirname(__file__), "beta_configs.json")

SCALAR_KEYS = {"max_rack_size": (2, 40), "max_turns": (2, 1000), "stall_rounds": (0, 20)}
BOOL_KEYS = {"require_plus_c"}
TILE_FIELDS = {"count": (0, 60), "points": (0, 100), "expr_multiplier": (1, 10)}


class ConfigError(ValueError):
    pass


def _deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _int_in(value, lo, hi, what):
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ConfigError(f"{what} must be a whole number from {lo} to {hi}")


def validate_overrides(overrides: dict) -> None:
    """Raise ConfigError unless `overrides` only touches things a config may change."""
    if not isinstance(overrides, dict):
        raise ConfigError("overrides must be an object")
    for key, value in overrides.items():
        if key in SCALAR_KEYS:
            _int_in(value, *SCALAR_KEYS[key], key)
        elif key in BOOL_KEYS:
            if not isinstance(value, bool):
                raise ConfigError(f"{key} must be true or false")
        elif key == "tiles":
            if not isinstance(value, dict):
                raise ConfigError("tiles must be an object")
            for symbol, fields in value.items():
                if symbol not in CONFIG["tiles"]:
                    raise ConfigError(f"unknown tile '{symbol}' (new tile symbols need engine and art support first)")
                _check_tile_fields(f"tiles['{symbol}']", fields)
        elif key == "equals_tile":
            _check_tile_fields("equals_tile", value, allowed={"count", "points"})
        else:
            raise ConfigError(f"'{key}' can't be overridden")


def _check_tile_fields(where, fields, allowed=None):
    if not isinstance(fields, dict):
        raise ConfigError(f"{where} must be an object")
    for f, v in fields.items():
        if f not in (allowed or TILE_FIELDS):
            raise ConfigError(f"{where}.{f} can't be overridden")
        _int_in(v, *TILE_FIELDS[f], f"{where}.{f}")


def validate_config(cfg: dict) -> None:
    """Whole-config sanity: the game must be able to deal a rack and have an equals pile."""
    normal = sum(d["count"] for d in cfg["tiles"].values())
    if normal < 2 * cfg["max_rack_size"]:
        raise ConfigError(f"the bag has {normal} tiles, too few to deal two racks of {cfg['max_rack_size']}")
    if cfg["equals_tile"]["count"] < 4:
        raise ConfigError("need at least 4 equals tiles")
    if cfg["tiles"]["x"]["count"] < 1:
        raise ConfigError("the 'x' tile is needed to seed the board")


def build_config(overrides: dict) -> dict:
    """Standard config + overrides, validated. Returns a fresh dict the caller may keep."""
    validate_overrides(overrides)
    cfg = _deep_merge(CONFIG, overrides)
    validate_config(cfg)
    return cfg


def fingerprint(cfg: dict) -> str:
    """Short stable hash of a config's rules, so results and trained bots can say which rules they belong to."""
    return hashlib.sha1(json.dumps(cfg, sort_keys=True, default=list).encode()).hexdigest()[:10]


def _load() -> Dict[str, dict]:
    entries = {STANDARD_ID: {"id": STANDARD_ID, "name": "Standard", "beta": False,
                             "description": "The regular game.", "overrides": {}}}
    try:
        with open(BETA_FILE) as f:
            raw = json.load(f)
    except FileNotFoundError:
        raw = []
    for e in raw:
        if not e.get("id") or e["id"] == STANDARD_ID or e["id"] in entries:
            raise ConfigError(f"bad or duplicate config id {e.get('id')!r} in {BETA_FILE}")
        build_config(e.get("overrides", {}))   # fail loudly at import time rather than mid-game
        entries[e["id"]] = {"id": e["id"], "name": e.get("name", e["id"]), "beta": True,
                            "description": e.get("description", ""), "overrides": e.get("overrides", {})}
    return entries


_ENTRIES = _load()
_built: Dict[str, dict] = {}


def get_entry(config_id: str) -> dict:
    try:
        return _ENTRIES[config_id]
    except KeyError:
        raise ConfigError(f"Unknown config '{config_id}'. Choose from: {', '.join(_ENTRIES)}")


def get_config(config_id: str = STANDARD_ID) -> dict:
    """The full config dict for a registered id. The standard id returns the live CONFIG object itself.
    Treat the result as read-only: it is shared."""
    if config_id == STANDARD_ID:
        return CONFIG
    if config_id not in _built:
        _built[config_id] = build_config(get_entry(config_id)["overrides"])
    return _built[config_id]


def describe(config_id: str) -> dict:
    e = get_entry(config_id)
    return {"id": e["id"], "name": e["name"], "beta": e["beta"], "description": e["description"],
            "fingerprint": fingerprint(get_config(config_id)), "overrides": e["overrides"]}


def list_configs() -> List[dict]:
    return [describe(i) for i in _ENTRIES]
