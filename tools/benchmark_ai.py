"""Compare one AI difficulty with another across seeds and every seat position."""
import argparse
import json
from pathlib import Path
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from uno.ai import DIFFICULTIES, choose_action
from uno.ai_plugins import AiRegistry, legacy_name, normalize_plugin_id
from uno.application import AiController, AiRunner
from uno.engine import GameConfig, new_game


def _round(job):
    players, seed, challenger_seat, challenger, opponent, plugin_dir = job
    game = new_game(GameConfig(tuple(f"P{i}" for i in range(players))), seed)
    registry = AiRegistry(Path(plugin_dir) if plugin_dir else None, discover=bool(plugin_dir))
    runner = AiRunner(registry)
    rngs = [random.Random(seed * 101 + seat * 997) for seat in range(players)]
    max_decision_ms = 0.0
    for _ in range(10000):
        if game.winner is not None:
            break
        seat = game.current
        before = time.perf_counter()
        selected = normalize_plugin_id(challenger if seat == challenger_seat else opponent)
        legacy = legacy_name(selected)
        action = (choose_action(game.view_for(seat), rngs[seat], legacy) if legacy else
                  runner.choose_blocking(game, seat, AiController(selected, {}), rngs[seat].getrandbits(64)))
        elapsed = (time.perf_counter() - before) * 1000
        if seat == challenger_seat:
            max_decision_ms = max(max_decision_ms, elapsed)
        game.apply_action(action)
    if game.winner is None:
        raise RuntimeError(f"Unfinished game: players={players}, seed={seed}, challenger_seat={challenger_seat}")
    runner.close()
    return game.winner == challenger_seat, game.revision, max_decision_ms


def benchmark(players, seeds, start_seed, challenger="hard", opponent="normal", workers=1, plugin_dir=None):
    wins = actions = 0
    max_decision_ms = 0.0
    started = time.monotonic()
    jobs = [(players, seed, seat, challenger, opponent, str(plugin_dir) if plugin_dir else None)
            for seed in range(start_seed, start_seed + seeds) for seat in range(players)]
    pool = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
    try:
        results = pool.map(_round, jobs) if pool else map(_round, jobs)
        for won, count, elapsed in results:
            wins += won
            actions += count
            max_decision_ms = max(max_decision_ms, elapsed)
    finally:
        if pool:
            pool.shutdown(cancel_futures=True)
    games = seeds * players
    return {"players": players, "games": games, f"{challenger}_wins": wins,
            "challenger": challenger, "opponent": opponent,
            f"{challenger}_win_rate": round(wins / games, 4),
            "equal_seat_share": round(1 / players, 4),
            "mean_actions": round(actions / games, 1),
            f"max_{challenger}_decision_ms": round(max_decision_ms, 2),
            "seconds": round(time.monotonic() - started, 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=100, help="Seed count per seat position")
    parser.add_argument("--start-seed", type=int, default=1)
    parser.add_argument("--players", type=int, choices=(2, 3, 4), nargs="+", default=[2, 3, 4])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--challenger", default="builtin.hard", help="Plugin ID or legacy built-in name")
    parser.add_argument("--opponent", default="builtin.normal", help="Plugin ID or legacy built-in name")
    parser.add_argument("--plugin-dir", type=Path, help="Directory containing external plugin folders")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if args.workers < 1 or args.seeds < 1 or not 0 <= args.start_seed < 65536 or args.start_seed + args.seeds > 65536:
        parser.error("Use a positive seed count within the 16-bit seed range.")
    results = []
    for players in args.players:
        registry = AiRegistry(args.plugin_dir, discover=bool(args.plugin_dir))
        for selected in (args.challenger, args.opponent):
            if not registry.has(selected):
                parser.error(f"Unknown AI plugin: {selected}")
        result = benchmark(players, args.seeds, args.start_seed, args.challenger, args.opponent,
                           args.workers, args.plugin_dir)
        results.append(result)
        print(json.dumps(result), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"start_seed": args.start_seed,
                                          "seeds_per_seat": args.seeds, "results": results}, indent=2),
                               encoding="utf8")


if __name__ == "__main__":
    main()
