"""
train_bot.py

Trains a SearchAgent policy (core/bot_policy.py) for ONE game config by
self-play: a mutated challenger plays the current champion over paired,
seat-swapped games (same seed, each bot gets both seats, which cancels the
first-player edge). A challenger replaces the champion only if it wins by
a clear margin; at the end the champion is measured against the untuned bot
and saved to core/bot_policies/<config_id>.json, where the game picks it up.

Usage (from the repo root):
    python -m core.train_bot --iters 30 --pairs 6 --save
    python -m core.train_bot --config my_config.json --iters 30 --save
    python -m core.train_bot --eval --pairs 20            # trained policy vs untuned bot
    python -m core.train_bot --config-id                  # print the id of the current config

--config takes a JSON file with the same shape as core.game_config.CONFIG
(board_dimensions may be a list), so configs from a config-exploration system
can be trained without touching game_config.py.
"""
import argparse
import json
import math
import random
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor

from core.bot_policy import BotPolicy, config_id, load_policy, save_policy
from core.game_config import CONFIG
from core.search_agent import SearchAgent
from core.session import GameSession

_config = CONFIG


def normalize_config(cfg: dict) -> dict:
    cfg = dict(cfg)
    cfg["board_dimensions"] = tuple(cfg["board_dimensions"])
    return cfg


def _init(config):
    global _config
    _config = config


def play_game(config: dict, first: BotPolicy, second: BotPolicy, seed: int) -> dict:
    """One bot-vs-bot game; returns the seats' scores."""
    random.seed(seed)
    session = GameSession("agent_vs_agent", config, agent_types=["search", "search"])
    for seat, pol in zip(session.seats.values(), (first, second)):
        seat.agent = SearchAgent(seat.name, config, policy=pol)
    steps, max_steps = 0, config.get("max_turns", 75) * 2
    while not session.is_over and steps < max_steps:
        session.step_agent()
        steps += 1
    a, b = (p.score for p in session.game.players)
    return {"first": a, "second": b, "turns": session.game.turns_played}


def _pair_task(task):
    """Challenger vs champion on one seed, both seatings. Returns challenger margin per game."""
    chal, champ, seed = task
    chal, champ = BotPolicy.from_dict(chal), BotPolicy.from_dict(champ)
    g1 = play_game(_config, chal, champ, seed)
    g2 = play_game(_config, champ, chal, seed + 500_000)
    return [g1["first"] - g1["second"], g2["second"] - g2["first"]]


class Arena:
    def __init__(self, config, workers):
        self.pool = ProcessPoolExecutor(workers, initializer=_init, initargs=(config,))

    def match(self, chal: BotPolicy, champ: BotPolicy, pairs: int, seed: int) -> dict:
        margins = []
        for m in self.pool.map(_pair_task, [(chal.to_dict(), champ.to_dict(), seed + i) for i in range(pairs)]):
            margins += m
        n = len(margins)
        mean = statistics.mean(margins)
        sd = statistics.pstdev(margins) if n > 1 else 0.0
        return {"games": n, "margin": mean, "z": mean / (sd / math.sqrt(n)) if sd else 0.0,
                "win_rate": (sum(m > 0 for m in margins) + 0.5 * sum(m == 0 for m in margins)) / n}


def mutate(policy: BotPolicy, symbols, rng: random.Random, scale: float) -> BotPolicy:
    new = policy.copy()
    for _ in range(rng.randint(1, 3)):
        r = rng.random()
        if r < 0.15:
            new.play_bonus = max(-3.0, min(6.0, new.play_bonus + rng.gauss(0, 1.0 * scale)))
        elif r < 0.85:
            s = rng.choice(symbols)
            new.keep_value[s] = max(-8.0, min(8.0, new.keep_value.get(s, 0.0) + rng.gauss(0, 2.0 * scale)))
        else:
            s = rng.choice(symbols)
            cur = new.swap_keep.get(s, SearchAgent._KEEP_AT_MOST.get(s, 2))
            new.swap_keep[s] = max(0, min(6, cur + rng.choice([-1, 1])))
    return new


def fmt(m):
    return f"margin={m['margin']:+.1f} win={m['win_rate']:.0%} z={m['z']:+.2f} ({m['games']} games)"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="JSON game config (default: core/game_config.py)")
    ap.add_argument("--config-id", action="store_true")
    ap.add_argument("--eval", action="store_true", help="measure the saved policy vs the untuned bot and exit")
    ap.add_argument("--iters", type=int, default=20)
    ap.add_argument("--pairs", type=int, default=6, help="seed pairs (2 games each) per challenger test")
    ap.add_argument("--final-pairs", type=int, default=20, help="pairs for the final check vs the untuned bot")
    ap.add_argument("--accept-z", type=float, default=1.0, help="z-score a challenger needs to replace the champion")
    ap.add_argument("--save-z", type=float, default=1.0, help="z-score vs the untuned bot required to save")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--fresh", action="store_true", help="start from the untuned bot, not the saved policy")
    ap.add_argument("--save", action="store_true", help="write the result to core/bot_policies/")
    ap.add_argument("--out", help="also write a JSON training log here")
    args = ap.parse_args(argv)

    config = normalize_config(json.load(open(args.config))) if args.config else CONFIG
    cid = config_id(config)
    if args.config_id:
        print(cid)
        return 0
    baseline = BotPolicy()
    arena = Arena(config, args.workers)

    if args.eval:
        m = arena.match(load_policy(config), baseline, args.final_pairs, 10_000 + args.seed)
        print(f"config {cid}: trained vs untuned: {fmt(m)}")
        return 0

    champion = baseline if args.fresh else load_policy(config)
    symbols = [s for s, d in config["tiles"].items()]
    rng = random.Random(args.seed)
    history = []
    t0 = time.time()
    print(f"training config {cid} with {len(symbols)} tile types, {args.pairs * 2} games per test", flush=True)
    for i in range(1, args.iters + 1):
        scale = 1.0 if i <= args.iters // 2 else 0.5  # finer steps in the second half
        chal = mutate(champion, symbols, rng, scale)
        m = arena.match(chal, champion, args.pairs, args.seed + 1000 * i)
        accepted = m["z"] >= args.accept_z and m["margin"] > 0
        if accepted:
            champion = chal
        history.append({"iter": i, "accepted": accepted, **m, "policy": chal.to_dict()})
        print(f"[{i}/{args.iters}] {'ACCEPT' if accepted else 'reject'} {fmt(m)} ({time.time() - t0:.0f}s)", flush=True)

    # fresh seeds the search never saw, so lucky acceptances show up as a weak final number
    final = arena.match(champion, baseline, args.final_pairs, 10_000_000 + args.seed)
    print(f"\nFINAL trained vs untuned bot: {fmt(final)}")
    print(json.dumps(champion.to_dict(), indent=1))
    meta = {"final_vs_untuned": final, "iters": args.iters, "pairs": args.pairs, "seed": args.seed}
    if args.save:
        if final["margin"] > 0 and final["z"] >= args.save_z:
            print("saved", save_policy(config, champion, meta))
        else:
            print(f"NOT saved: trained policy did not beat the untuned bot clearly (needs z >= {args.save_z})")
    if args.out:
        json.dump({"config_id": cid, "policy": champion.to_dict(), "final": final, "history": history},
                  open(args.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
