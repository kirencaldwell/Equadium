
def make_tile(symbol):
    """Helper to generate a Tile using points defined in CONFIG."""
    if symbol == "=":
        points = CONFIG["equals_tile"]["points"]
    else:
        points = CONFIG["tiles"][symbol]["points"]
    return Tile(symbol, points)
