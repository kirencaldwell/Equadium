from core.game_entities import Board, Player, Tile, make_tile
from core.math_engine import MathEngine
from dataclasses import dataclass, field
from typing import Dict
from core.math_playbook import MathPlaybook
import sys

import random

#def run_versus_game(player_names, config, playbook, verbose):
    
def run_autonomous_game(player_names, agents, config: dict,
              verbose: bool = False) -> list[dict]:

    game = EquadiumGame(player_names, config, verbose=verbose)

    center_r = config["board_dimensions"][0] // 2
    center_c = config["board_dimensions"][1] // 2
    game.board.grid[center_r][center_c] = make_tile("x", config)

    max_turns = config.get("max_turns", 75)
    turn = 0
    while turn < max_turns:
        if game.is_game_over:
            break
        current_player = game.players[game.current_turn_index]
        bot = agents[current_player.name]

        move = bot.handle_turn(game)
        game.execute_move(current_player, move)
        turn += 1

    # Determine end reason
    if any(len(p.rack) == 0 for p in game.players):
        end_reason = "rack empty"
    elif not game.tile_bag and game.consecutive_non_plays >= len(game.players):
        end_reason = "bag empty+stuck"
    else:
        end_reason = "game over"

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
    def __init__(self, player_names, config, verbose: bool = False):
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
        
        # Initialize both distinct bags
        self.tile_bag = self._initialize_normal_bag()
        self.equals_bag = self._initialize_equals_bag()
        
        # Deal initial hands
        for player in self.players:
            self.draw_tiles(player)

    @property
    def is_game_over(self) -> bool:
        """
        The game ends when:
          1. Any player's rack is empty (they've exhausted their tiles), OR
          2. The bag is empty AND all players are stuck
             (consecutive non-play turns >= number of players).
        """
        if any(len(p.rack) == 0 for p in self.players):
            return True
        if not self.tile_bag and self.consecutive_non_plays >= len(self.players):
            return True
        return False

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
        if player != self.players[self.current_turn_index]:
            if self.verbose:
                print(f"⚠️ It is not {player.name}'s turn!")
            return False

        if move.is_pass:
            return self.pass_turn(player)

        if move.is_swap:
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
                    if self.verbose:
                        print("⚠️ Cannot execute play: Equals pile is empty!")
                    return False
            else:
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

        self.board.place_tiles_temporarily(move_tiles)
        equations_data = self.board.get_all_new_equations(move_tiles, direction)
        
        # Math verification
        all_valid = True
        for eq_str, eq_tiles in equations_data:
            if not self.math.validate_equation(eq_str)[0]:
                all_valid = False
                break
        if all_valid:
            # Check if this specific play included an '=' sign
            # If the user/AI placed an '=' tile, consume the resource
            used_equals = any(t.symbol == "=" for _, _, t in move_tiles)
            if used_equals:
                player.equals_available = False
            
            # Calculate score for the play
            play_score = 0
            for eq_str, eq_tiles in equations_data:
                eq_points = sum(tile.points for tile in eq_tiles)
                # Multipliers from newly placed tiles in this equation
                multiplier = 1
                for _, _, placed_tile in move_tiles:
                    if placed_tile in eq_tiles:
                        multiplier *= placed_tile.expr_multiplier
                play_score += eq_points * multiplier
            
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
            self._advance_turn()
            if self.verbose:
                self.display_game_state()
            return True
        else:
            self._rollback(move_tiles)
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
