"""
bot_policy.py

The tunable part of the search bot. A BotPolicy holds a handful of numbers that
change *which* legal play the SearchAgent picks (not which plays are legal),
so a bot can be trained separately for each game configuration.

  play_bonus : points of credit per tile spent in a play (>0 favours dumping tiles, <0 favours small plays)
  keep_value : per tile symbol, how much it is worth holding on to (it is subtracted from a play's
               ranking for every such tile the play uses; negative = prefer to get rid of it)
  swap_keep  : per tile symbol, how many copies to keep when the bot has to swap (overrides the built-in table)

The all-zero policy reproduces the original untuned bot exactly. Trained policies
live in core/bot_policies/<config_id>.json, keyed by a fingerprint of the game
config, so changing tile counts/points/rack size/etc. invalidates the policy
(the bot falls back to the default until it is retrained with core.train_bot).
"""

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Dict, Optional

POLICY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_policies")


@dataclass
class BotPolicy:
    play_bonus: float = 0.0
    keep_value: Dict[str, float] = field(default_factory=dict)
    swap_keep: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"play_bonus": self.play_bonus, "keep_value": dict(self.keep_value), "swap_keep": dict(self.swap_keep)}

    @classmethod
    def from_dict(cls, d: dict) -> "BotPolicy":
        return cls(float(d.get("play_bonus", 0.0)),
                   {k: float(v) for k, v in d.get("keep_value", {}).items()},
                   {k: int(v) for k, v in d.get("swap_keep", {}).items()})

    def copy(self) -> "BotPolicy":
        return BotPolicy.from_dict(self.to_dict())


def config_id(config: dict) -> str:
    """Stable fingerprint of everything in a config that changes how the game plays."""
    blob = json.dumps(config, sort_keys=True, default=list)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def policy_path(config: dict, directory: str = POLICY_DIR) -> str:
    return os.path.join(directory, f"{config_id(config)}.json")


def save_policy(config: dict, policy: BotPolicy, meta: Optional[dict] = None, directory: str = POLICY_DIR) -> str:
    os.makedirs(directory, exist_ok=True)
    path = policy_path(config, directory)
    with open(path, "w") as f:
        json.dump({"config_id": config_id(config), "policy": policy.to_dict(), "meta": meta or {}}, f, indent=2)
    return path


def load_policy(config: dict, directory: str = POLICY_DIR) -> BotPolicy:
    """The trained policy for this config, or the neutral default if none exists."""
    try:
        with open(policy_path(config, directory)) as f:
            return BotPolicy.from_dict(json.load(f)["policy"])
    except (OSError, ValueError, KeyError):
        return BotPolicy()
