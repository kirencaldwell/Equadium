"""
session.py

A GameSession wraps an EquadiumGame with *seats* so the same engine can run
in any of three modes:

  - human_vs_agent : seat 0 is a human, seat 1 is the algorithmic agent
  - human_vs_human : both seats are humans
  - agent_vs_agent : both seats are agents (self-play / stress testing)

The web API and the stress-test harness both drive games through this class,
so what we stress test is what the web app actually runs.
"""

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.ai_agent import AIAgent
from core.search_agent import SearchAgent
from core.game_config import CONFIG
from core.game_entities import Move
from core.game_manager import EquadiumGame
from core.math_playbook import MathPlaybook

HUMAN = "human"
AGENT = "agent"

MODES = {
    "human_vs_agent": [("Human", HUMAN), ("AI_Opponent", AGENT)],
    "human_vs_human": [("Player1", HUMAN), ("Player2", HUMAN)],
    "agent_vs_agent": [("Newton_Bot", AGENT), ("Leibniz_Bot", AGENT)],
}

# The playbook is expensive to build, so every session shares one instance.
_playbook: Optional[MathPlaybook] = None


def get_playbook(config: dict = CONFIG, max_length: Optional[int] = None) -> MathPlaybook:
    global _playbook
    if _playbook is None:
        if max_length is None:
            max_length = int(os.environ.get("EQUADIUM_PLAYBOOK_LENGTH", "4"))
        _playbook = MathPlaybook(config, max_length=max_length)
    return _playbook


def make_agent(agent_type: str, name: str, config: dict, playbook: Optional[MathPlaybook] = None):
    if agent_type == "search":
        return SearchAgent(name, config)
    if agent_type == "playbook":
        return AIAgent(name, playbook or get_playbook(config), config, verbose=False)
    raise ValueError(f"Unknown agent type '{agent_type}' (use 'search' or 'playbook')")


@dataclass
class Seat:
    name: str
    kind: str  # HUMAN or AGENT
    agent: Optional[AIAgent] = None


@dataclass
class TurnRecord:
    """What happened on one turn; handy for logs and for the UI."""
    player: str
    action: str          # "play" | "swap" | "pass"
    ok: bool
    score_delta: int = 0
    error: Optional[str] = None
    tiles: Optional[List[str]] = None


class GameSession:
    def __init__(self, mode: str = "human_vs_agent", config: dict = CONFIG,
                 playbook: Optional[MathPlaybook] = None, verbose: bool = False,
                 agent_types: Optional[List[str]] = None):
        """
        agent_types: which algorithm drives each agent seat, in seat order.
        "search" (default) searches from the rack each turn; "playbook" is the
        original precomputed-table agent, kept as a baseline.
        """
        if mode not in MODES:
            raise ValueError(f"Unknown mode '{mode}'. Choose from: {', '.join(MODES)}")
        self.mode = mode
        self.config = config
        self.playbook = playbook
        self.game = EquadiumGame([n for n, _ in MODES[mode]], config, verbose=verbose)
        self.seats: Dict[str, Seat] = {}
        types = iter(agent_types or [])
        for name, kind in MODES[mode]:
            agent = None
            if kind == AGENT:
                agent = make_agent(next(types, "search"), name, config, playbook)
            self.seats[name] = Seat(name, kind, agent)
        self.history: List[TurnRecord] = []
        self.illegal_agent_moves = 0

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    @property
    def current_player(self):
        return self.game.players[self.game.current_turn_index]

    @property
    def current_seat(self) -> Seat:
        return self.seats[self.current_player.name]

    @property
    def is_over(self) -> bool:
        return self.game.is_game_over

    def player(self, name: str):
        for p in self.game.players:
            if p.name == name:
                return p
        raise KeyError(f"No such player '{name}'")

    def human_seats(self) -> List[str]:
        return [s.name for s in self.seats.values() if s.kind == HUMAN]

    def make_seat_human(self, seat_name: str, new_name: str) -> None:
        """Hand an agent's seat to a human (e.g. a second player joining)."""
        self.seats.pop(seat_name)
        player = self.player(seat_name)
        player.name = new_name
        self.game.stats[new_name] = self.game.stats.pop(seat_name)
        self.seats[new_name] = Seat(new_name, HUMAN)
        if len(self.human_seats()) == len(self.seats):
            self.mode = "human_vs_human"

    # ------------------------------------------------------------------
    # Human actions. Each returns a TurnRecord and then lets any agent
    # opponents respond so the caller always gets back a human-to-move state.
    # ------------------------------------------------------------------
    def _require_turn(self, player_name: str, kind: str = HUMAN):
        if self.is_over:
            raise ValueError("The game is over")
        seat = self.seats.get(player_name)
        if seat is None:
            raise KeyError(f"No such player '{player_name}'")
        if seat.kind != kind:
            raise ValueError(f"'{player_name}' is not a {kind} seat")
        if self.current_player.name != player_name:
            raise ValueError(f"It is not {player_name}'s turn")
        return self.player(player_name)

    def play(self, player_name: str, tiles_to_play, direction: str) -> TurnRecord:
        player = self._require_turn(player_name)
        move = Move(tiles_to_play=tiles_to_play, direction=direction)
        return self._execute(player, move, "play")

    def swap(self, player_name: str, tile_indices: List[int]) -> TurnRecord:
        player = self._require_turn(player_name)
        if not tile_indices:
            raise ValueError("Pick at least one tile to swap")
        if len(set(tile_indices)) != len(tile_indices) or any(
                not 0 <= i < len(player.rack) for i in tile_indices):
            raise ValueError("Invalid tile selection")
        tiles = [player.rack[i] for i in tile_indices]
        before = self.game.turns_played
        ok = self.game.execute_move(player, Move(n_tiles_to_swap=len(tiles)), tiles_to_swap=tiles)
        return self._record(player, "swap", ok, 0, before)

    def pass_turn(self, player_name: str) -> TurnRecord:
        player = self._require_turn(player_name)
        return self._execute(player, Move(), "pass")

    def _execute(self, player, move: Move, action: str) -> TurnRecord:
        score_before = player.score
        turns_before = self.game.turns_played
        ok = self.game.execute_move(player, move)
        return self._record(player, action, ok, score_before, turns_before,
                            tiles=[t.symbol for _, _, t in move.tiles_to_play] or None)

    def _record(self, player, action, ok, score_before, turns_before, tiles=None) -> TurnRecord:
        rec = TurnRecord(
            player=player.name, action=action, ok=ok,
            score_delta=(player.score - score_before) if action == "play" else 0,
            error=None if ok else self.game.last_error, tiles=tiles,
        )
        if ok:
            self.history.append(rec)
        return rec

    # ------------------------------------------------------------------
    # Agent actions
    # ------------------------------------------------------------------
    def step_agent(self) -> Optional[TurnRecord]:
        """Plays one turn for the agent whose turn it is, if it is an agent's."""
        if self.is_over or self.current_seat.kind != AGENT:
            return None
        player = self.current_player
        move = self.current_seat.agent.handle_turn(self.game)
        action = "play" if move.is_play else "swap" if move.is_swap else "pass"
        score_before, turns_before = player.score, self.game.turns_played
        ok = self.game.execute_move(player, move)
        if not ok:
            # The agent proposed something illegal. Count it (the stress test
            # reports this) and pass so the game keeps moving.
            self.illegal_agent_moves += 1
            error = self.game.last_error
            self.game.execute_move(player, Move())
            rec = TurnRecord(player.name, "pass", True, 0, error=f"agent move rejected: {error}")
            self.history.append(rec)
            return rec
        return self._record(player, action, True, score_before, turns_before,
                            tiles=[t.symbol for _, _, t in move.tiles_to_play] or None)

    def advance_agents(self, limit: int = 10) -> List[TurnRecord]:
        """Runs agent turns until a human is to move or the game ends."""
        records = []
        while not self.is_over and self.current_seat.kind == AGENT and len(records) < limit:
            rec = self.step_agent()
            if rec is None:
                break
            records.append(rec)
        return records

    def run_to_completion(self, max_steps: Optional[int] = None) -> None:
        """Agent-vs-agent only: plays the whole game."""
        if self.human_seats():
            raise ValueError("run_to_completion needs every seat to be an agent")
        max_steps = max_steps or self.config.get("max_turns", 75) * 2
        steps = 0
        while not self.is_over and steps < max_steps:
            self.step_agent()
            steps += 1
