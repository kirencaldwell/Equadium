from core.game_entities import make_tile
from core.game_config import CONFIG
from core.game_manager import EquadiumGame, run_autonomous_game
from core.search_agent import SearchAgent


def main(config=None, verbose=False):
    if config is None:
        config = CONFIG

    if verbose:
        print("==================================================")
        print("        AI VS AI: EQUADIUM GRANDMASTER MATCH      ")
        print("==================================================")

    # 2. Initialize the Game Engine
    players = ["Newton_Bot", "Leibniz_Bot"]

    # 3. Equip the bots (they search from their rack each turn; no precomputed playbook)
    agents = {name: SearchAgent(name, config) for name in players}

    game, end_reason, scores = run_autonomous_game(players, agents, config, verbose=True)

    # 6. Final results — always printed
    if verbose:
        print("\n==================================================")
        print("            MATCH SIMULATION COMPLETE             ")
        print("==================================================")
        game.display_game_state()

    results = game.get_final_results()
    print("\n" + "═" * 64)
    print(f"{'FINAL RESULTS & STATISTICS':^64}")
    print("═" * 64)
    integral = "\u222b"
    print(f"  {'Player':<16} {'Score':>6}  {'Turns':>5}  {'Exprs':>5}  {'Avg Len':>7}  {'d/dx':>5}  {integral:>5}  {'Swaps':>6}")
    print("  " + "\u2500" * 60)
    for name, stats in sorted(results.items(), key=lambda kv: kv[1]["score"], reverse=True):
        print(
            f"  {name:<16} {stats['score']:>6}  "
            f"{stats['turns_played']:>5}  "
            f"{stats['expressions_played']:>5}  "
            f"{stats['avg_expression_length']:>7.2f}  "
            f"{stats['derivatives_used']:>5}  "
            f"{stats['integrals_used']:>5}  "
            f"{stats['swaps_made']:>6}"
        )
    print("═" * 64)
    return results


if __name__ == "__main__":
    main()
