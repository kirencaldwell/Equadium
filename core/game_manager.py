from core.game_entities import Board, Move, Player, Tile, make_tile
from core.math_engine import MathEngine
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from core.math_playbook import MathPlaybook
import sys

import random

#def run_versus_game(player_names, config, playbook, verbose):
    
def run_autonomous_game(player_names, agents, config: dict,
              verbose: bool = False) -> list[dict]:

    game = EquadiumGame(player_names, config, verbose=verbose)

    # Count attempts (not completed turns) so a bot that keeps submitting
    # illegal moves can't spin forever.
    max_turns = config.get("max_turns", 75)
    attempts = 0
    while attempts < max_turns * 2 and not game.is_game_over:
        current_player = game.players[game.current_turn_index]
        bot = agents[current_player.name]

        move = bot.handle_turn(game)
        if not game.execute_move(current_player, move):
            game.execute_move(current_player, Move())  # illegal -> forced pass
        attempts += 1

    end_reason = game.end_reason or "game over"

    scores = "  |  ".join(f"{p.name}: {p.score} pts" for p in game.players)


    return game, end_reason, scores

@dataclass
class PlayerStats:
    """Per-player statistics tracked across the game."""
    expressions_played: int = 0
    total_expression_length: int = 0  # sum of tile counts in each expression
    derivatives_used: int = 0         # times d/dx( appeared in a played expression
    integrals_used: int = 0           # times int( appeared in a played expression
    swaps_made: int = 0               # times the player swapped tiles

    @property
    def avg_expression_length(self) -> float:
        if self.expressions_played == 0:
            return 0.0
        return self.total_expression_length / self.expressions_played

class EquadiumGame:
    def __init__(self, player_names, config, verbose: bool = False, seed_center: bool = True):
        self.config = config
        self.verbose = verbose
        self.board = Board(*self.config["board_dimensions"])
        self.math = MathEngine(self.config)
        self.players = [Player(name) for name in player_names]
        self.current_turn_index = 0

        # Per-player stats
        self.stats: Dict[str, PlayerStats] = {name: PlayerStats() for name in player_names}

        # Game-level counters
        self.turns_played = 0
        self.consecutive_non_plays = 0  # resets on any successful tile play
        # Set when a play draws the last tile from the bag: the game is over once this many turns have been played,
        # i.e. after every other player has had one final turn.
        self.final_turn_count: Optional[int] = None
        
        # Initialize both distinct bags
        self.tile_bag = self._initialize_normal_bag()
        self.equals_bag = self._initialize_equals_bag()
        
        # Deal initial hands
        for player in self.players:
            self.draw_tiles(player)

        # Every game starts from a seed tile in the middle so that plays have
        # something to connect to.
        if seed_center:
            self.board.grid[self.board.height // 2][self.board.width // 2] = make_tile("x", config)

        # Human-readable reason the most recent move was rejected (or None)
        self.last_error: Optional[str] = None

        # Name of the player who gave up, if anyone did. A forfeit ends the game at once and the
        # other player wins no matter what the scores say.
        self.forfeited_by: Optional[str] = None

    @property
    def is_game_over(self) -> bool:
        """
        The game ends when:
          1. A play draws the last tile from the bag. The player who drew it has had their turn, and every other
             player then gets one final "rebuttal" turn, after which the game is over, OR
          2. Nobody has played for `stall_rounds` full rounds, OR
          3. The turn limit (`max_turns`) is reached, OR
          4. A player forfeits.
        """
        return self.end_reason is not None

    @property
    def end_reason(self) -> Optional[str]:
        """Why the game is over, or None if it is still in progress."""
        if self.forfeited_by is not None:
            return "forfeit"
        if self.final_turn_count is not None and self.turns_played >= self.final_turn_count:
            return "last tile drawn"
        # With a few tiles left in the bag, players can swap them back and forth forever.
        stall_rounds = self.config.get("stall_rounds", 3)
        if stall_rounds and self.consecutive_non_plays >= stall_rounds * len(self.players):
            return "stalled"
        if self.turns_played >= self.config.get("max_turns", 75):
            return "turn limit"
        return None

    @property
    def winners(self) -> List[str]:
        """Names of the winner(s); more than one means a tie. After a forfeit that is everyone else,
        whatever the scores were."""
        if self.forfeited_by is not None:
            return [p.name for p in self.players if p.name != self.forfeited_by]
        top = max(p.score for p in self.players)
        return [p.name for p in self.players if p.score == top]

    # ------------------------------------------------------------------
    # Placement rules (geometry only; the math is checked separately)
    # ------------------------------------------------------------------
    def check_placement(self, move_tiles, direction) -> Optional[str]:
        """
        Returns an error string if the tiles can't legally be placed on the
        board, else None. move_tiles is a list of (row, col, tile).
        """
        board = self.board
        if not move_tiles:
            return "No tiles were placed"
        if direction not in ("H", "V"):
            return "Direction must be 'H' or 'V'"

        coords = [(r, c) for r, c, _ in move_tiles]
        if len(set(coords)) != len(coords):
            return "Two tiles were placed on the same square"
        for r, c in coords:
            if not (0 <= r < board.height and 0 <= c < board.width):
                return "A tile was placed off the board"
            if board.grid[r][c] is not None:
                return "A tile was placed on an occupied square"

        rows = {r for r, _ in coords}
        cols = {c for _, c in coords}
        if direction == "H" and len(rows) > 1:
            return "Tiles must all be in one row"
        if direction == "V" and len(cols) > 1:
            return "Tiles must all be in one column"

        # Contiguity: gaps between placed tiles must be filled by existing tiles
        dr, dc = (0, 1) if direction == "H" else (1, 0)
        placed = set(coords)
        lo, hi = min(coords), max(coords)
        r, c = lo
        while (r, c) != (hi[0] + dr, hi[1] + dc):
            if (r, c) not in placed and board.grid[r][c] is None:
                return "Tiles must form one unbroken line"
            r, c = r + dr, c + dc

        # Connectivity: must touch at least one tile already on the board
        def touches_board(r, c):
            for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if 0 <= nr < board.height and 0 <= nc < board.width and board.grid[nr][nc] is not None:
                    return True
            return False

        board_empty = all(t is None for row in board.grid for t in row)
        if not board_empty and not any(touches_board(r, c) for r, c in coords):
            return "Play must connect to tiles already on the board"
        return None

    @staticmethod
    def score_play(equations_data, move_tiles) -> int:
        """Points for a play: each equation's tile points, times the multipliers of newly placed tiles in it."""
        total = 0
        for _, eq_tiles in equations_data:
            multiplier = 1
            for _, _, placed_tile in move_tiles:
                if placed_tile in eq_tiles:
                    multiplier *= placed_tile.expr_multiplier
            total += sum(t.points for t in eq_tiles) * multiplier
        return total

    def evaluate_play(self, move_tiles, direction) -> Tuple[Optional[list], Optional[str]]:
        """
        Dry-runs a play without changing any state. Returns
        (equations_data, None) if legal or (None, reason) if not.
        """
        self.failed_tiles = None
        err = self.check_placement(move_tiles, direction)
        if err:
            return None, err

        self.board.place_tiles_temporarily(move_tiles)
        try:
            equations_data = self.board.get_all_new_equations(move_tiles, direction)
            if not equations_data:
                return None, "Play does not form any equation"
            for eq_str, eq_tiles in equations_data:
                valid, msg = self.math.validate_equation(eq_str)
                if not valid:
                    self.failed_tiles = [t.symbol for t in eq_tiles]    # lets the UI show the failing line tile by tile
                    return None, f"'{eq_str}' is not a valid equation ({msg})"
        finally:
            self._rollback(move_tiles)
        return equations_data, None

    # ------------------------------------------------------------------
    # Persistence. Tiles are stored with their own points/multipliers (not
    # looked up in CONFIG), so a saved game stays valid if tile values are
    # retuned later.
    # ------------------------------------------------------------------
    @staticmethod
    def _tile_to_dict(t: Tile) -> dict:
        return {"s": t.symbol, "p": t.points, "m": t.expr_multiplier}

    @staticmethod
    def _tile_from_dict(d: dict) -> Tile:
        return Tile(d["s"], d["p"], d.get("m", 1))

    def to_dict(self) -> dict:
        td = self._tile_to_dict
        return {
            "players": [{"name": p.name, "score": p.score, "equals_available": p.equals_available,
                         "rack": [td(t) for t in p.rack]} for p in self.players],
            "board": [[r, c, td(t)] for r, row in enumerate(self.board.grid) for c, t in enumerate(row) if t],
            "tile_bag": [td(t) for t in self.tile_bag],
            "equals_bag": [td(t) for t in self.equals_bag],
            "current_turn_index": self.current_turn_index,
            "turns_played": self.turns_played,
            "consecutive_non_plays": self.consecutive_non_plays,
            "stats": {name: vars(st).copy() for name, st in self.stats.items()},
            "forfeited_by": self.forfeited_by,
            "final_turn_count": self.final_turn_count,
        }

    @classmethod
    def from_dict(cls, d: dict, config: dict, verbose: bool = False) -> "EquadiumGame":
        game = cls([p["name"] for p in d["players"]], config, verbose=verbose, seed_center=False)
        fd = cls._tile_from_dict
        for player, pd in zip(game.players, d["players"]):
            player.score = pd["score"]
            player.equals_available = pd["equals_available"]
            player.rack = [fd(t) for t in pd["rack"]]
        game.board.grid = [[None] * game.board.width for _ in range(game.board.height)]
        for r, c, t in d["board"]:
            game.board.grid[r][c] = fd(t)
        game.tile_bag = [fd(t) for t in d["tile_bag"]]
        game.equals_bag = [fd(t) for t in d["equals_bag"]]
        game.current_turn_index = d["current_turn_index"]
        game.turns_played = d["turns_played"]
        game.consecutive_non_plays = d["consecutive_non_plays"]
        game.stats = {name: PlayerStats(**st) for name, st in d["stats"].items()}
        game.forfeited_by = d.get("forfeited_by")      # absent in games saved before forfeits existed
        game.final_turn_count = d.get("final_turn_count")
        if game.final_turn_count is None and not game.tile_bag and game.forfeited_by is None:
            # a game saved before this rule whose bag is already empty: everyone gets one more turn
            game.final_turn_count = game.turns_played + len(game.players)
        return game

    def display_scoreboard(self):
        """Prints a clean status update of the current game standings."""
        print("\n" + "─" * 40)
        print(f"{'CURRENT STANDINGS':^40}")
        print("─" * 40)
        # Sort players so the leader is shown first!
        for ranked_player in sorted(self.players, key=lambda p: p.score, reverse=True):
            print(f"  🏆 {ranked_player.name:<15} : {ranked_player.score:>4} pts")
        print("─" * 40)

    def display_game_state(self):
        """Prints a comprehensive snapshot of the entire game's current status."""
        # 1. Render the 15x15 board matrix
        self.board.render()

        # 2. Print Standings & Bag Metrics
        print("\n" + "═" * 50)
        print(f"{'GAME STATUS DASHBOARD':^50}")
        print("═" * 50)
        print(f" 📦 Normal Bag: {len(self.tile_bag)} tiles left  |  🟰 Equals Pile: {len(self.equals_bag)} tiles left")
        print("─" * 50)
        print(" PLAYER STANDINGS & RACKS:")
        
        # 3. Print each player's live score and exact rack inventory
        for player in self.players:
            rack_symbols = [tile.symbol for tile in player.rack]
            # Highlight whose turn it is next
            active_marker = "➡️" if player == self.players[self.current_turn_index] else "  "
            
            print(f" {active_marker} {player.name:<10} | Score: {player.score:>3} pts | Rack: {rack_symbols}")
        print("═" * 50 + "\n")

    def _initialize_normal_bag(self):
        bag = []
        for symbol, data in self.config["tiles"].items():
            bag.extend([make_tile(symbol, self.config) for _ in range(data["count"])])
        random.shuffle(bag)
        return bag

    def _initialize_equals_bag(self):
        # The separate free pile for '=' tiles
        return [make_tile("=", self.config) for _ in range(self.config["equals_tile"]["count"])]

    def draw_tiles(self, player):
        """
        Replenishes the player's hand up to max_rack_size using NORMAL tiles.
        Any '=' tiles they are holding are ignored during this calculation.
        """
        normal_tiles_in_rack = sum(1 for t in player.rack if t.symbol != "=")
        tiles_needed = self.config["max_rack_size"] - normal_tiles_in_rack
        
        if tiles_needed > 0 and self.tile_bag:
            drawn = [self.tile_bag.pop() for _ in range(min(tiles_needed, len(self.tile_bag)))]
            player.add_tiles(drawn)

    def draw_equals_tile(self, player):
        """
        Action called by the CLI when a player explicitly requests an '=' tile 
        from the free pile.
        """
        if self.equals_bag:
            tile = self.equals_bag.pop()
            player.add_tiles([tile])
            if self.verbose:
                print(f"📥 {player.name} drew an [=] tile from the free pile.")
            return True
        else:
            if self.verbose:
                print("⚠️ The equals pile is completely empty!")
            return False

    def _advance_turn(self):
        """Advances the turn, increments turns_played, resets '=' resource."""
        self.turns_played += 1
        self.current_turn_index = (self.current_turn_index + 1) % len(self.players)
        current_player = self.players[self.current_turn_index]
        current_player.equals_available = True

        if self.verbose:
            print(f"🔄 Turn transitioned to {current_player.name}. '=' resource replenished.")

    def execute_move(self, player, move, tiles_to_swap=None):
        """Executes a Move object on behalf of the player."""
        self.last_error = None
        if player != self.players[self.current_turn_index]:
            self.last_error = f"It is not {player.name}'s turn"
            if self.verbose:
                print(f"⚠️ It is not {player.name}'s turn!")
            return False

        if move.is_pass:
            return self.pass_turn(player)

        if move.is_swap:
            tiles_to_swap = tiles_to_swap or move.tiles_to_swap
            if tiles_to_swap:
                self.swap_tiles(player, tiles_to_swap)
                return True
            # Fallback to old random behavior if no specific tiles provided
            num_to_swap = min(move.n_tiles_to_swap, len(player.rack))
            if num_to_swap == 0:
                return self.pass_turn(player)
            tiles_to_swap_random = random.sample(player.rack, num_to_swap)
            self.swap_tiles(player, tiles_to_swap_random)
            return True

        
        if move.is_play:
            return self.play_tiles(player, move)

        return False

    def pass_turn(self, player):
        """
        Allows a player to pass their turn if they have no valid moves.
        This will trigger an automatic turn swap and display the new state.
        """
        # Ensure the player acting is actually the one whose turn it is
        if player != self.players[self.current_turn_index]:
            if self.verbose:
                print(f"⚠️ It is not {player.name}'s turn to pass!")
            return False

        if self.verbose:
            print(f"🏳️  {player.name} has elected to pass their turn.")
        
        ## Penalty tile for passing
        #if self.tile_bag:
        #    penalty_tile = self.tile_bag.pop()
        #    player.add_tiles([penalty_tile])
        #    if self.verbose:
        #        print(f"📥 {player.name} drew a penalty tile for passing.")

        self.consecutive_non_plays += 1
        self._advance_turn()
        if self.verbose:
            self.display_game_state()
        return True

    def play_tiles(self, player, move):
        # 1. Check rack availability for normal and equals tiles
        rack_symbols = [t.symbol for t in player.rack]
        needed_symbols = [t.symbol for _, _, t in move.tiles_to_play]

        equals_needed = needed_symbols.count("=")
        equals_in_rack = rack_symbols.count("=")

        if equals_needed > equals_in_rack:
            extra_equals_needed = equals_needed - equals_in_rack
            if extra_equals_needed == 1 and player.equals_available:
                if not self.draw_equals_tile(player):
                    self.last_error = "The equals pile is empty"
                    if self.verbose:
                        print("⚠️ Cannot execute play: Equals pile is empty!")
                    return False
            else:
                self.last_error = "No '=' tile available (only one free '=' per turn)"
                if self.verbose:
                    print("⚠️ Cannot execute play: Missing '=' tile or resource already used!")
                return False

        # 2. Match needed symbols to actual Tile objects in player's rack
        actual_move_tiles = []
        temp_rack = list(player.rack)
        can_play = True
        for r, c, tile in move.tiles_to_play:
            match = next((t for t in temp_rack if t.symbol == tile.symbol), None)
            if match:
                temp_rack.remove(match)
                actual_move_tiles.append((r, c, match))
            else:
                can_play = False
                break

        if not can_play:
            self.last_error = "Your rack does not contain all of those tiles"
            if self.verbose:
                print("⚠️ Cannot execute play: Rack does not contain all required tiles!")
            return False

        # 3. Deduct tiles from player's rack
        for _, _, match in actual_move_tiles:
            player.rack.remove(match)

        # 4. Attempt play validation
        success = self.attempt_play(player, actual_move_tiles, move.direction)
        if success:
            return True
        else:
            # Rollback tiles to rack
            for _, _, match in actual_move_tiles:
                player.rack.append(match)
            return False

    def attempt_play(self, player, move_tiles, direction, silent=False):
        # Enforce turn order
        if player != self.players[self.current_turn_index]:
            return False

        equations_data, error = self.evaluate_play(move_tiles, direction)
        all_valid = error is None
        if not all_valid:
            self.last_error = error
        if all_valid:
            # Check if this specific play included an '=' sign
            # If the user/AI placed an '=' tile, consume the resource
            used_equals = any(t.symbol == "=" for _, _, t in move_tiles)
            if used_equals:
                player.equals_available = False
            
            play_score = self.score_play(equations_data, move_tiles)
            player.score += play_score

            # --- Record statistics ---
            pstats = self.stats[player.name]
            for eq_str, eq_tiles in equations_data:
                pstats.expressions_played += 1
                pstats.total_expression_length += len(eq_tiles)
                pstats.derivatives_used += eq_str.count("d/dx(")
                pstats.integrals_used += eq_str.count("int(")

            if self.verbose:
                print(f"🎉 {player.name} played: {[t.symbol for _, _, t in move_tiles]} ({direction})")
                print(f"   Equations formed: {[eq_str for eq_str, _ in equations_data]}")
                print(f"   Scored {play_score} points! Total score: {player.score} pts")
            
            # Draw replacements
            self.draw_tiles(player)
            self.consecutive_non_plays = 0  # successful play resets the stuck counter
            if not self.tile_bag and self.final_turn_count is None:
                # This play drew the last tile. It is turn turns_played + 1; every other player then gets one more.
                self.final_turn_count = self.turns_played + len(self.players)
            self._advance_turn()
            if self.verbose:
                self.display_game_state()
            # Tiles go on the board only once the play is accepted
            self.board.place_tiles_temporarily(move_tiles)
            return True
        return False

    def _rollback(self, move_tiles):
        coords_to_remove = [(r, c) for r, c, _ in move_tiles]
        self.board.remove_tiles(coords_to_remove)

    def get_final_results(self) -> dict:
        """Returns final scores and gameplay statistics for all players."""
        results = {}
        for player in self.players:
            pstats = self.stats[player.name]
            results[player.name] = {
                "score": player.score,
                "expressions_played": pstats.expressions_played,
                "avg_expression_length": round(pstats.avg_expression_length, 2),
                "total_expression_length": pstats.total_expression_length,
                "derivatives_used": pstats.derivatives_used,
                "integrals_used": pstats.integrals_used,
                "swaps_made": pstats.swaps_made,
                "turns_played": self.turns_played,
            }
        return results

    def forfeit(self, player) -> bool:
        """The player gives up. Allowed at any time (it need not be their turn) until the game is over."""
        if self.is_game_over:
            self.last_error = "The game is already over"
            return False
        self.forfeited_by = player.name
        if self.verbose:
            print(f"🏳️  {player.name} forfeited the game.")
        return True

    def swap_tiles(self, player, tiles_to_swap):
        """Returns chosen tiles to the bag and draws replacements."""
        if self.verbose:
            print(f"🔄 {player.name} is swapping {len(tiles_to_swap)} tiles...")

        # Record the swap
        self.stats[player.name].swaps_made += 1
        self.consecutive_non_plays += 1

        # 1. Return tiles to bag
        for tile in tiles_to_swap:
            self.tile_bag.append(tile)
            player.rack.remove(tile)
        
        # 2. Shuffle bag for randomness
        random.shuffle(self.tile_bag)
        
        # 3. Fill up to max_rack_size
        self.draw_tiles(player)
        
        # 4. End the turn
        self._advance_turn()
        if self.verbose:
            self.display_game_state()
