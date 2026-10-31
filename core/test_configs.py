import pytest

from core import configs
from core.configs import ConfigError, build_config, get_config, list_configs
from core.game_config import CONFIG
from core.session import GameSession


def test_standard_is_the_live_config_and_betas_are_listed():
    assert get_config("standard") is CONFIG
    ids = [c["id"] for c in list_configs()]
    assert ids[0] == "standard" and len(ids) > 1
    assert all(c["beta"] for c in list_configs()[1:])


def test_overrides_merge_without_touching_the_standard_config():
    cfg = build_config({"max_rack_size": 12, "tiles": {"d/dx(": {"count": 9}}})
    assert cfg["max_rack_size"] == 12
    assert cfg["tiles"]["d/dx("]["count"] == 9 and cfg["tiles"]["d/dx("]["points"] == CONFIG["tiles"]["d/dx("]["points"]
    assert CONFIG["max_rack_size"] == 16 and CONFIG["tiles"]["d/dx("]["count"] == 5


@pytest.mark.parametrize("bad", [
    {"board_dimensions": [9, 9]}, {"tiles": {"zzz": {"count": 1}}}, {"tiles": {"x": {"color": 1}}},
    {"max_rack_size": "big"}, {"max_rack_size": True}, {"require_plus_c": 1}, {"tiles": {"x": {"count": -1}}},
    {"max_rack_size": 41}, {"equals_tile": {"count": 1}},
])
def test_bad_overrides_are_rejected(bad):
    with pytest.raises(ConfigError):
        build_config(bad)


def test_fingerprint_changes_with_the_rules():
    assert configs.fingerprint(build_config({"max_turns": 40})) != configs.fingerprint(CONFIG)


def test_every_registered_config_plays_a_full_bot_game():
    for entry in list_configs()[1:]:
        s = GameSession("agent_vs_agent", get_config(entry["id"]), config_id=entry["id"])
        s.run_to_completion()
        assert s.is_over, entry["id"]


def test_session_remembers_its_config_across_save_and_load():
    s = GameSession("human_vs_human", get_config("beta-small-rack"), config_id="beta-small-rack")
    back = GameSession.from_dict(s.to_dict())
    assert back.config_id == "beta-small-rack" and back.config["max_rack_size"] == 12
    legacy = s.to_dict(); del legacy["config_id"]
    assert GameSession.from_dict(legacy).config is CONFIG
