
class Move:
    def __init__(self, tiles_to_play=None, direction=None, n_tiles_to_swap=0):
        self.tiles_to_play = tiles_to_play if tiles_to_play is not None else []
        self.direction = direction
        self.n_tiles_to_swap = n_tiles_to_swap

    @property
    def is_play(self):
        return len(self.tiles_to_play) > 0

    @property
    def is_swap(self):
        return self.n_tiles_to_swap > 0

    @property
    def is_pass(self):
        return not self.is_play and not self.is_swap

class Tile:
    def __init__(self, symbol, points, expr_multiplier=1):
        self.symbol = symbol
        self.points = points
        self.expr_multiplier = expr_multiplier # 2 = Double, 3 = Triple, etc.

def make_tile(symbol, config):
    """Helper to generate a Tile dynamically utilizing CONFIG/config data."""
    if symbol == "=":
        data = config["equals_tile"]
    else:
        data = config["tiles"][symbol]
    
    # Grab the multiplier if it exists, otherwise default to 1
    multiplier = data.get("expr_multiplier", 1)
    return Tile(symbol, data["points"], multiplier)

class Player:
    def __init__(self, name):
        self.name = name
        self.rack = []
        self.score = 0
        self.equals_available = True  # The special resource

    def add_tiles(self, tiles):
        self.rack.extend(tiles)

    def play_tiles(self, tiles_to_play):
        for tile in tiles_to_play:
            if tile in self.rack:
                self.rack.remove(tile)

class Board:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.grid = [[None for _ in range(width)] for _ in range(height)]

    def _get_contiguous_string(self, row, col, dr, dc):
        """Scans and returns the full string AND the list of Tile objects."""
        r, c = row, col
        while 0 <= r - dr < self.height and 0 <= c - dc < self.width and self.grid[r - dr][c - dc] is not None:
            r -= dr
            c -= dc
            
        expr_str = ""
        tiles_list = []
        while 0 <= r < self.height and 0 <= c < self.width and self.grid[r][c] is not None:
            expr_str += self.grid[r][c].symbol
            tiles_list.append(self.grid[r][c])
            r += dr
            c += dc
            
        return expr_str, tiles_list

    def get_all_new_equations(self, placed_tiles, direction):
        if not placed_tiles:
            return []
            
        equations_data = [] # Will hold tuples: (equation_string, list_of_tiles)
        main_dr, main_dc = (0, 1) if direction == "H" else (1, 0)
        cross_dr, cross_dc = (1, 0) if direction == "H" else (0, 1)

        # 1. Main Equation
        first_r, first_c, _ = placed_tiles[0]
        main_word, main_tiles = self._get_contiguous_string(first_r, first_c, main_dr, main_dc)
        if len(main_word) > 1:
            equations_data.append((main_word, main_tiles))

        # 2. Orthogonal Cross Equations
        for r, c, tile in placed_tiles:
            cross_word, cross_tiles = self._get_contiguous_string(r, c, cross_dr, cross_dc)
            if len(cross_word) > len(tile.symbol):
                equations_data.append((cross_word, cross_tiles))

        return equations_data

    def place_tiles_temporarily(self, tiles):
        for r, c, tile in tiles:
            self.grid[r][c] = tile

    def remove_tiles(self, coords):
        for r, c in coords:
            self.grid[r][c] = None
    def render(self):
        """Prints a beautifully aligned ASCII representation of the board."""
        cell_width = 7  # Wide enough to accommodate "sin(x)" or "d/dx(" comfortably
        
        print("\n" + "=" * (self.width * cell_width + 6))
        print(f"{'EQUADIUM BOARD':^{self.width * cell_width + 6}}")
        print("=" * (self.width * cell_width + 6) + "\n")

        # 1. Print Column Headers (0, 1, 2...)
        col_header = "     " + "".join(f"{c:^{cell_width}}" for c in range(self.width))
        print(col_header)
        
        # Divider line
        print("     " + "-" * (self.width * cell_width))

        # 2. Print Rows
        for r in range(self.height):
            # Row header label (e.g., "0  |", "10 |")
            row_str = f"{r:<3} |"
            
            for c in range(self.width):
                tile = self.grid[r][c]
                if tile:
                    # Center the tile symbol inside brackets like [ x**2 ]
                    display_text = f"[{tile.symbol}]"
                    row_str += f"{display_text:^{cell_width}}"
                else:
                    # Empty space placeholder
                    row_str += f"{'.':^{cell_width}}"
                    
            print(row_str)
        print("\n" + "=" * (self.width * cell_width + 6))


