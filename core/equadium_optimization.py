"""
equadium_optimization.py

Contains:
  - run_games(): run N games with a given config, returns per-game results
  - aggregate_results(): roll up per-game results into per-player averages
  - sample_config(): draw a Monte Carlo config from distributions around a base config
  - run_monte_carlo(): iterate over MC draws, run games for each, collect results
  - main(): entry point — runs the Monte Carlo loop and saves results to JSON
"""

import copy
import json
import math
import random
import sys
from datetime import datetime

from game_config import CONFIG
from game_manager import EquadiumGame, run_autonomous_game
from game_entities import make_tile
from ai_agent import AIAgent
from math_playbook import MathPlaybook


# ---------------------------------------------------------------------------
# Sampling parameters
# These control how far each MC draw can stray from the base config values.
# Tune these to widen or narrow your exploration:
#
#   count_lambda_scale: Poisson draws. lambda = base_count * scale.
#                       1.0 = centered on base, higher = wider spread.
#   points_sigma:       Gaussian std-dev applied to point values.
#                       Higher = wider exploration of point values.
#   count_min:          Minimum tile count (prevents degenerate configs).
#   points_min:         Minimum point value (can be 0).
# ---------------------------------------------------------------------------
SAMPLING_PARAMS = {
    "count_lambda_scale": 1.0,   # Poisson lambda = base_count * this
    "points_sigma": 4.5,         # Gaussian sigma for point values
    "count_min": 1,              # Floor for tile counts
    "points_min": 0,             # Floor for point values
}


def _poisson_sample(lam: float) -> int:
    """Pure-Python Poisson sample using Knuth's algorithm."""
    lam = max(lam, 1e-6)
    L = math.exp(-lam)
    k, p = 0, 1.0
    while p > L:
        k += 1
        p *= random.random()
    return k - 1


def sample_config(base_config: dict, sampling_params: dict | None = None) -> dict:
    """
    Draws a single Monte Carlo config from distributions centred on base_config.

    For each tile:
      - count  ~ Poisson(lambda = base_count * count_lambda_scale), clamped to count_min
      - points ~ round(Gauss(base_points, points_sigma)), clamped to points_min

    The equals_tile, board_dimensions, max_rack_size, require_plus_c, and
    max_turns are kept fixed (not sampled) so the game structure stays valid.
    """
    sp = sampling_params or SAMPLING_PARAMS
    count_lambda_scale = sp.get("count_lambda_scale", 1.0)
    points_sigma       = sp.get("points_sigma", 1.5)
    count_min          = sp.get("count_min", 1)
    points_min         = sp.get("points_min", 0)

    new_config = copy.deepcopy(base_config)

    # for symbol, data in new_config["tiles"].items():
    #     base_count  = base_config["tiles"][symbol]["count"]
    #     base_points = base_config["tiles"][symbol]["points"]

    #     lam = max(base_count * count_lambda_scale, count_min)
    #     new_count  = max(count_min,  _poisson_sample(lam))
    #     new_points = max(points_min, round(random.gauss(base_points, points_sigma)))

    #     new_config["tiles"][symbol]["count"]  = new_count
    #     new_config["tiles"][symbol]["points"] = new_points

    return new_config


def config_snapshot(config: dict) -> dict:
    """Extract just the tile counts and points for storage alongside metrics."""
    return {
        symbol: {
            "count":  data["count"],
            "points": data["points"],
        }
        for symbol, data in config["tiles"].items()
    }


# ---------------------------------------------------------------------------
# Core game running
# ---------------------------------------------------------------------------

def run_games(config: dict, num_runs: int, playbook: MathPlaybook | None = None,
              verbose: bool = False) -> list[dict]:
    """
    Runs `num_runs` independent games with the given config.
    If a pre-built playbook is provided it is reused; otherwise one is built.
    Returns a list of per-game result dicts from EquadiumGame.get_final_results().
    """
    if playbook is None:
        playbook = MathPlaybook(config, max_length=4)

    player_names = ["Newton_Bot", "Leibniz_Bot"]
    agents = {
        name: AIAgent(name, playbook, config, verbose=verbose)
        for name in player_names
    }
    all_results = []

    for i in range(num_runs):
        print(f"\r  [Game {i + 1}/{num_runs}] Starting...          ", end="", flush=True)
        game, end_reason, scores = run_autonomous_game(player_names, agents, config, playbook, verbose = True)
        print(f"\r  [Game {i + 1}/{num_runs}] Done ({game.turns_played} turns, {end_reason})  |  {scores}          ")

        all_results.append(game.get_final_results())

    return all_results


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_results(all_results: list[dict]) -> dict:
    """
    Aggregates per-game results into per-player averages/totals.
    Returns a dict keyed by player name.
    """
    if not all_results:
        return {}

    player_names = list(all_results[0].keys())
    n = len(all_results)
    aggregated = {
        name: {
            "games_played": n,
            "total_score": 0,
            "avg_score": 0.0,
            "wins": 0,
            "win_rate": 0.0,
            "total_expressions": 0,
            "avg_expressions": 0.0,
            "total_expression_length": 0,
            "avg_expression_length": 0.0,
            "total_derivatives": 0,
            "total_integrals": 0,
            "total_swaps": 0,
            "total_turns": 0,
            "avg_turns": 0.0,
        }
        for name in player_names
    }

    for result in all_results:
        top_score = max(result[nm]["score"] for nm in player_names)
        for name in player_names:
            stats = result[name]
            agg   = aggregated[name]
            agg["total_score"]             += stats["score"]
            agg["total_expressions"]       += stats["expressions_played"]
            agg["total_expression_length"] += stats["total_expression_length"]
            agg["total_derivatives"]       += stats["derivatives_used"]
            agg["total_integrals"]         += stats["integrals_used"]
            agg["total_swaps"]             += stats["swaps_made"]
            agg["total_turns"]             += stats["turns_played"]
            if stats["score"] == top_score:
                agg["wins"] += 1

    for name in player_names:
        agg = aggregated[name]
        agg["avg_score"] = round(agg["total_score"] / n, 2)
        total_exprs = agg["total_expressions"]
        agg["avg_expressions"] = round(total_exprs / n, 2)
        agg["avg_expression_length"] = (
            round(agg["total_expression_length"] / total_exprs, 2)
            if total_exprs > 0 else 0.0
        )
        agg["win_rate"] = round(agg["wins"] / n, 3)
        # turns_played is game-level (same for all players); avg across games
        agg["avg_turns"] = round(agg["total_turns"] / n, 1)

    return aggregated


def combined_metrics(aggregated: dict) -> dict:
    """
    Collapses per-player aggregates into a single combined-game view.
    Useful for storing a single row of metrics per MC iteration.
    """
    player_names = list(aggregated.keys())
    n_players = len(player_names)
    if n_players == 0:
        return {}

    combined = {
        "avg_score":             round(sum(aggregated[n]["avg_score"]             for n in player_names) / n_players, 2),
        "total_expressions":     sum(aggregated[n]["total_expressions"]            for n in player_names),
        "avg_expressions":       round(sum(aggregated[n]["avg_expressions"]        for n in player_names) / n_players, 2),
        "avg_expression_length": round(sum(aggregated[n]["avg_expression_length"]  for n in player_names) / n_players, 2),
        "total_derivatives":     sum(aggregated[n]["total_derivatives"]            for n in player_names),
        "total_integrals":       sum(aggregated[n]["total_integrals"]              for n in player_names),
        "total_swaps":           sum(aggregated[n]["total_swaps"]                  for n in player_names),
        # turns_played is the same for both players; just take from first
        "avg_turns":             aggregated[player_names[0]]["avg_turns"],
        # Win rate imbalance — 0.0 = perfectly balanced, 1.0 = one player always wins
        "win_imbalance":         round(abs(aggregated[player_names[0]]["win_rate"] - 0.5) * 2, 3),
    }
    return combined


# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------

def run_monte_carlo(
    base_config: dict,
    n_iterations: int = 20,
    games_per_iteration: int = 10,
    sampling_params: dict | None = None,
    output_file: str | None = None,
) -> list[dict]:
    """
    Runs a Monte Carlo sweep over tile config parameters.

    For each iteration:
      1. Sample a new config from distributions around base_config.
      2. Build the math playbook (reused across games in the same iteration).
      3. Run `games_per_iteration` games.
      4. Aggregate metrics and store alongside the sampled config snapshot.

    Returns a list of iteration records, each containing:
      {
        "iteration": int,
        "config_snapshot": { symbol: {count, points}, ... },
        "sampling_params": { ... },
        "per_player": { player: { aggregated stats } },
        "combined": { combined metrics },
      }

    Results are also saved to `output_file` (JSON) after every iteration so
    progress is preserved if the run is interrupted.
    """
    sp = sampling_params or SAMPLING_PARAMS

    if output_file is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"/Users/kirencaldwell/Documents/Equadium/mc_results_{ts}.json"

    print(f"\n{'═' * 60}")
    print(f"  MONTE CARLO OPTIMIZATION")
    print(f"  Iterations : {n_iterations}")
    print(f"  Games/iter : {games_per_iteration}")
    print(f"  Output     : {output_file}")
    print(f"  Sampling   :")
    print(f"    count_lambda_scale = {sp.get('count_lambda_scale', 1.0)}")
    print(f"    points_sigma       = {sp.get('points_sigma', 1.5)}")
    print(f"{'═' * 60}\n")

    # The playbook is symbol-independent (only uses tile symbols, not
    # counts/points), so we build it once and reuse across all iterations.
    print("Building shared playbook (once)...")
    shared_playbook = MathPlaybook(base_config, max_length=4)

    all_records = []

    for i in range(n_iterations):
        print(f"\n{'─' * 60}")
        print(f"  Iteration {i + 1}/{n_iterations}")

        sampled_config = sample_config(base_config, sp)
        snapshot = config_snapshot(sampled_config)

        # Print the sampled tile params compactly
        tile_summary = "  Tiles: " + "  ".join(
            f"{sym}(n={d['count']},pts={d['points']})"
            for sym, d in snapshot.items()
        )
        print(tile_summary)

        game_results = run_games(
            sampled_config,
            num_runs=games_per_iteration,
            playbook=shared_playbook,
        )

        aggregated = aggregate_results(game_results)
        combined   = combined_metrics(aggregated)

        record = {
            "iteration":       i + 1,
            "config_snapshot": snapshot,
            "sampling_params": sp,
            "per_player":      aggregated,
            "combined":        combined,
        }
        all_records.append(record)

        # Print iteration summary
        print(
            f"  → avg_score={combined['avg_score']:.1f}  "
            f"exprs/game={combined['avg_expressions']:.1f}  "
            f"avg_len={combined['avg_expression_length']:.2f}  "
            f"d/dx={combined['total_derivatives']}  "
            f"∫={combined['total_integrals']}  "
            f"swaps={combined['total_swaps']}  "
            f"imbalance={combined['win_imbalance']:.2f}"
        )

        # Save incrementally so progress isn't lost
        with open(output_file, "w") as f:
            json.dump(all_records, f, indent=2)

    print(f"\n{'═' * 60}")
    print(f"  Monte Carlo complete. {n_iterations} iterations saved to {output_file}")
    print(f"{'═' * 60}\n")

    return all_records


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def print_aggregate_report(aggregated: dict) -> None:
    """Prints a formatted per-player summary table."""
    print("\n" + "═" * 86)
    print(f"{'AGGREGATE REPORT':^86}")
    print("═" * 86)
    integral = "\u222b"
    print(
        f"  {'Player':<16} {'Wins':>5}  {'Win%':>6}  {'Avg Score':>9}  "
        f"{'Avg Turns':>9}  {'Avg Exprs':>9}  {'Avg Len':>7}  {'d/dx':>5}  {integral:>5}  {'Swaps':>6}"
    )
    print("  " + "\u2500" * 82)
    for name, agg in sorted(aggregated.items(), key=lambda kv: kv[1]["avg_score"], reverse=True):
        print(
            f"  {name:<16} {agg['wins']:>5}  {agg['win_rate']:>6.1%}  "
            f"{agg['avg_score']:>9.1f}  "
            f"{agg['avg_turns']:>9.1f}  "
            f"{agg['avg_expressions']:>9.1f}  "
            f"{agg['avg_expression_length']:>7.2f}  "
            f"{agg['total_derivatives']:>5}  "
            f"{agg['total_integrals']:>5}  "
            f"{agg['total_swaps']:>6}"
        )
    print("\u2550" * 86)


def _print_game_summary(game_num: int, results: dict) -> None:
    winner = max(results, key=lambda n: results[n]["score"])
    print(f"  Result: {winner} wins with {results[winner]['score']} pts")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # ---- Single-run mode (used when called directly) ----
    # Swap to run_monte_carlo(...) for the full MC sweep.

    run_monte_carlo(
        base_config=CONFIG,
        n_iterations=20,
        games_per_iteration=1,
        sampling_params=SAMPLING_PARAMS,
    )


if __name__ == "__main__":
    main()
