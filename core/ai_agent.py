import random
import sympy as sp
from core.game_entities import Move, Tile, make_tile

class AIAgent:
    def __init__(self, name, playbook, config, verbose: bool = False):
        self.name = name
        self.playbook = playbook
        self.config = config
        self.verbose = verbose

    def handle_turn(self, game):
        """Main entry point for the game loop."""
        if self.verbose:
            print(f"🤔 {self.name} is calculating moves...")
        move = self.find_best_move(game)
        if move is not None:
            return move

        # Fallback: Tile Swapping
        player = game.players[game.current_turn_index]
        num_to_swap = random.randint(1, max(1, len(player.rack) // 2))
        return Move(n_tiles_to_swap=num_to_swap)

    def find_best_move(self, game):
        """Scans board, consults playbook, finds the optimal Move or returns None."""
        player = game.players[game.current_turn_index]
        import sympy as sp
        
        # 1. Check the player's resource
        if not player.equals_available:
            if self.verbose:
                print(f"   [DEBUG] {self.name} has already used their '=' resource this turn.")
            return None

        # 2. Get buildable expressions using the current rack
        buildable_dict = self._get_buildable_expressions(player.rack)
        
        if not buildable_dict:
            if self.verbose:
                print(f"   [DEBUG] {self.name} cannot build any math from the Playbook.")
            return None

        anchors = self._get_prioritized_anchors(game.board)

        non_tautology_moves = []
        tautology_moves = []

        rack_symbols = [t.symbol for t in player.rack]
        x = sp.Symbol('x')

        for r, c, anchor_val in anchors:
            try:
                # Parse anchor value
                anchor_expr = game.math._parse_expression(str(anchor_val))
            except Exception:
                continue
                
            if self.verbose:
                print(f"   [DEBUG] Checking Anchor at ({r},{c}) -> Value: {anchor_val}")

            # Define templates: (name, target_expr_fn, extra_symbols, sequence_fn, anchor_index_fn)
            templates = [
                # Template 1: recipe = anchor
                (
                    "recipe = anchor",
                    lambda a: sp.simplify(a),
                    [],
                    lambda rec, anc: rec + ["="] + [anc],
                    lambda rec: len(rec) + 1
                ),
                # Template 2: anchor = recipe
                (
                    "anchor = recipe",
                    lambda a: sp.simplify(a),
                    [],
                    lambda rec, anc: [anc] + ["="] + rec,
                    lambda rec: 0
                ),
                # Template 3: d/dx(anchor) = recipe
                (
                    "d/dx(anchor) = recipe",
                    lambda a: sp.simplify(sp.diff(a, x)),
                    ["d/dx(", ")"],
                    lambda rec, anc: ["d/dx(", anc, ")", "="] + rec,
                    lambda rec: 1
                ),
                # Template 4: recipe = d/dx(anchor)
                (
                    "recipe = d/dx(anchor)",
                    lambda a: sp.simplify(sp.diff(a, x)),
                    ["d/dx(", ")"],
                    lambda rec, anc: rec + ["=", "d/dx(", anc, ")"],
                    lambda rec: len(rec) + 2
                ),
                # Template 5: int(anchor) = recipe + C
                (
                    "int(anchor) = recipe + C",
                    lambda a: sp.simplify(sp.integrate(a, x)),
                    ["int(", ")", "+", "C"],
                    lambda rec, anc: ["int(", anc, ")", "="] + rec + ["+", "C"],
                    lambda rec: 1
                ),
                # Template 6: recipe + C = int(anchor)
                (
                    "recipe + C = int(anchor)",
                    lambda a: sp.simplify(sp.integrate(a, x)),
                    ["int(", ")", "+", "C"],
                    lambda rec, anc: rec + ["+", "C", "=", "int(", anc, ")"],
                    lambda rec: len(rec) + 4
                ),
                # Template 7: 2 * anchor = recipe
                (
                    "2 * anchor = recipe",
                    lambda a: sp.simplify(2 * a),
                    ["2", "*"],
                    lambda rec, anc: ["2", "*", anc, "="] + rec,
                    lambda rec: 2
                ),
                # Template 8: recipe = 2 * anchor
                (
                    "recipe = 2 * anchor",
                    lambda a: sp.simplify(2 * a),
                    ["2", "*"],
                    lambda rec, anc: rec + ["=", "2", "*", anc],
                    lambda rec: len(rec) + 3
                ),
            ]

            for name, target_expr_fn, extra_symbols, sequence_fn, anchor_index_fn in templates:
                try:
                    target_expr = target_expr_fn(anchor_expr)
                except Exception:
                    continue

                if target_expr not in buildable_dict:
                    continue

                for recipe in buildable_dict[target_expr]:
                    recipe_str = "".join(recipe)
                    
                    # Check if we have the needed tiles for this template + recipe
                    temp_rack_symbols = list(rack_symbols)
                    has_equals = "=" in temp_rack_symbols
                    if not has_equals:
                        temp_rack_symbols.append("=")
                        
                    if not self._can_build_with_extras(recipe, extra_symbols, temp_rack_symbols):
                        continue

                    # Tautology check: compare LHS and RHS expressions
                    full_seq = sequence_fn(recipe, str(anchor_val))
                    eq_str = "".join(full_seq)
                    try:
                        left_str, right_str = eq_str.split("=", 1)
                        left_expr = game.math._parse_expression(left_str.replace("+C", "").replace("+ C", "").strip())
                        right_expr = game.math._parse_expression(right_str.replace("+C", "").replace("+ C", "").strip())
                        is_tautology = left_expr == right_expr
                    except Exception:
                        is_tautology = False

                    anchor_idx = anchor_index_fn(recipe)

                    for direction in ["H", "V"]:
                        # Prepare a temporary rack for alignment check.
                        rack_copy = list(player.rack)
                        has_equals = any(t.symbol == "=" for t in rack_copy)
                        if not has_equals:
                            rack_copy.append(make_tile("=", self.config))
                            
                        placed_tiles = self._align_sequence_to_board(
                            r, c, full_seq, direction, game.board, rack_copy, anchor_index=anchor_idx
                        )
                        if placed_tiles:
                            # Verify if the play is mathematically valid
                            game.board.place_tiles_temporarily(placed_tiles)
                            equations_data = game.board.get_all_new_equations(placed_tiles, direction)
                            
                            all_valid = True
                            for eq_str_check, eq_tiles in equations_data:
                                valid, _ = game.math.validate_equation(eq_str_check)
                                if not valid:
                                    all_valid = False
                                    break
                            
                            # Rollback
                            game.board.remove_tiles([(tr, tc) for tr, tc, _ in placed_tiles])
                            
                            if all_valid:
                                move_obj = Move(tiles_to_play=placed_tiles, direction=direction)
                                if is_tautology:
                                    if self.verbose:
                                        print(f"   [DEBUG] {self.name} identified '{''.join(full_seq)}' ({direction}) as a tautology.")
                                    tautology_moves.append(move_obj)
                                else:
                                    if self.verbose:
                                        print(f"   [DEBUG] Found valid non-tautology play: {''.join(full_seq)} ({direction})")
                                    non_tautology_moves.append(move_obj)

        if non_tautology_moves:
            best_move = max(non_tautology_moves, key=lambda m: self._estimate_move_score(m, game))
            return best_move
        elif tautology_moves:
            best_move = max(tautology_moves, key=lambda m: self._estimate_move_score(m, game))
            return best_move
            
        if self.verbose:
            print(f"   [DEBUG] {self.name} exhausted all anchors.")
        return None

    def _can_build_with_extras(self, recipe, extra_symbols, rack_symbols):
        rack_copy = list(rack_symbols)
        for symbol in recipe + extra_symbols:
            if symbol in rack_copy:
                rack_copy.remove(symbol)
            else:
                return False
        return True

    def _estimate_move_score(self, move, game):
        game.board.place_tiles_temporarily(move.tiles_to_play)
        equations_data = game.board.get_all_new_equations(move.tiles_to_play, move.direction)
        
        score = 0
        for eq_str, eq_tiles in equations_data:
            eq_points = sum(tile.points for tile in eq_tiles)
            multiplier = 1
            for _, _, placed_tile in move.tiles_to_play:
                if placed_tile in eq_tiles:
                    multiplier *= placed_tile.expr_multiplier
            score += eq_points * multiplier
            
        game.board.remove_tiles([(r, c) for r, c, _ in move.tiles_to_play])
        return score

    # ------------------------------------------------------------------
    # HELPER METHODS
    # ------------------------------------------------------------------

    def _get_buildable_expressions(self, rack_tiles):
        """Filters the master catalog down to what we can afford."""
        rack_symbols = [t.symbol for t in rack_tiles]
        buildable = {}
        for result, recipes in self.playbook.catalog.items():
            valid_recipes = [r for r in recipes if self._can_build(r, rack_symbols)]
            if valid_recipes:
                buildable[result] = valid_recipes
        return buildable

    def _can_build(self, recipe, rack_symbols):
        """Checks if a recipe can be built with current rack inventory."""
        rack_copy = list(rack_symbols)
        for symbol in recipe:
            if symbol in rack_copy:
                rack_copy.remove(symbol)
            else:
                return False
        return True

    def _find_linear_combinations(self, buildable_dict, target_expr, rack_tiles):
        """Exploits linearity to combine known blocks (e.g. A + B = Target)."""
        valid_combinations = []
        rack_symbols = [t.symbol for t in rack_tiles]
        
        # 1. Exact Playbook Match
        if target_expr in buildable_dict:
            valid_combinations.extend(buildable_dict[target_expr])
            
        # 2. Additive Combinations (Using Linearity)
        if '+' in rack_symbols:
            for expr_a, recipes_a in buildable_dict.items():
                expr_b = sp.simplify(target_expr - expr_a)
                if expr_b in buildable_dict:
                    # We found a combination! Check if we have enough tiles for BOTH recipes
                    for ra in recipes_a:
                        for rb in buildable_dict[expr_b]:
                            combined_recipe = ra + ['+'] + rb
                            if self._can_build(combined_recipe, rack_symbols):
                                valid_combinations.append(combined_recipe)
                                
        return valid_combinations

    def _get_prioritized_anchors(self, board):
        """Returns board tiles. Prioritizes 'ends' of equations."""
        anchors = []
        for r in range(board.height):
            for c in range(board.width):
                if board.grid[r][c] is not None:
                    # You would eventually add logic here to prioritize 
                    # edge tiles over tightly packed middle tiles
                    anchors.append((r, c, board.grid[r][c].symbol))
        return anchors

    def _align_sequence_to_board(self, anchor_r, anchor_c, sequence, direction, board, rack, anchor_index=None):
        """Maps the chosen string sequence to actual board coordinates/tiles."""
        if anchor_index is None:
            anchor_index = len(sequence) - 1
            
        move = []
        # Create a temporary pool of available tiles to pull from
        available_tiles = list(rack)
        
        # Calculate starting coordinates based on anchor_index.
        dr, dc = (0, 1) if direction == "H" else (1, 0)
        
        start_r = anchor_r - (anchor_index * dr)
        start_c = anchor_c - (anchor_index * dc)
        
        for i, symbol in enumerate(sequence):
            target_r = start_r + (i * dr)
            target_c = start_c + (i * dc)
            
            # 1. Out of bounds check
            if not (0 <= target_r < board.height and 0 <= target_c < board.width):
                return [] 
                
            # 2. Cell is empty: pull the exact tile object from the rack pool
            if board.grid[target_r][target_c] is None:
                matching_tile = next((t for t in available_tiles if t.symbol == symbol), None)
                if not matching_tile:
                    return [] # Rack lacks the physical tile needed (should be rare due to _can_build)
                
                available_tiles.remove(matching_tile)
                move.append((target_r, target_c, matching_tile))
                
            # 3. Cell is occupied: verify it matches the symbol we need (crossover play!)
            elif board.grid[target_r][target_c].symbol == symbol:
                pass 
                
            # 4. Cell is occupied by something else: collision, invalid placement
            else:
                return []
                
        return move
