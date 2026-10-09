"""Turn a GameSession into the JSON the frontend consumes (shared by the solo and online routes)."""
from core.game_entities import Tile
from web.api.models import PlayerModel, BoardModel, MoveModel, TileModel


def tile_to_model(tile):
    if tile is None:
        return None
    return TileModel(
        symbol=tile.symbol,
        points=tile.points,
        expr_multiplier=tile.expr_multiplier
    )

def game_to_model(session):
    game = session.game
    return {
        "board": BoardModel(
            width=game.board.width,
            height=game.board.height,
            grid=[
                [tile_to_model(tile) for tile in row]
                for row in game.board.grid
            ]
        ),
        "players": [
            PlayerModel(
                name=p.name,
                score=p.score,
                rack=[tile_to_model(t) for t in p.rack],
                equals_available=p.equals_available
            )
            for p in game.players
        ],
        "current_player": session.current_player.name,
        "equals_pile_count": len(game.equals_bag),
        "mode": session.mode,
        "seats": {name: seat.kind for name, seat in session.seats.items()},
        "game_over": game.is_game_over,
        "end_reason": game.end_reason,
        "winners": game.winners if game.is_game_over else [],
        "turns_played": game.turns_played,
        "bag_count": len(game.tile_bag),
        "last_move": session.history[-1].__dict__ if session.history else None,
    }


def _tiles_from_model(move: MoveModel):
    return [
        (p['r'], p['c'], Tile(symbol=p['tile']['symbol'], points=p['tile']['points'],
                              expr_multiplier=p['tile']['expr_multiplier']))
        for p in move.tiles_to_play
    ]
