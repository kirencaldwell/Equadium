from fastapi.testclient import TestClient
from web.api.main import app

client = TestClient(app)

def test_create_game():
    response = client.post("/games/create", json=["Player1", "Player2"])
    assert response.status_code == 200
    assert "game_id" in response.json()

def test_get_game():
    response = client.post("/games/create", json=["Player1", "Player2"])
    game_id = response.json()["game_id"]
    response = client.get(f"/games/{game_id}")
    assert response.status_code == 200
    assert "board" in response.json()
    assert "players" in response.json()
    assert "current_player" in response.json()
