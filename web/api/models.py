from pydantic import BaseModel
from typing import List, Optional, Tuple

class TileModel(BaseModel):
    symbol: str
    points: int
    expr_multiplier: int

class PlayerModel(BaseModel):
    name: str
    score: int
    rack: List[TileModel]
    equals_available: bool

class BoardModel(BaseModel):
    width: int
    height: int
    grid: List[List[Optional[TileModel]]]

class MoveModel(BaseModel):
    # Represent tiles_to_play as a list of (row, col, tile_model)
    # Using a list of tuples in Pydantic can be tricky, might need a model for the tile play
    tiles_to_play: List[dict] # Will handle conversion manually or define a sub-model
    direction: Optional[str] = None
    n_tiles_to_swap: int = 0
