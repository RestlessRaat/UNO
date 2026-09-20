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
from uno.engine import GameConfig, new_game


def _round(job):
    players, seed, challenger_seat, challenger, opponent = job
    game = new_game(GameConfig(tuple(f"P{i}" for i in range(players))), seed)
    rngs = [random.Random(seed * 101 + seat * 997) for seat in range(players)]
    max_decision_ms = 0.0
    for _ in range(10000):
        if game.winner is not None:
            break
        seat = game.current
        view = game.view_for(seat)
        before = time.perf_counter()
        action = choose_action(view, rngs[seat], challenger if seat == challenger_seat else opponent)
        elapsed = (time.perf_counter() - before) * 1000
        if seat == challenger_seat:
            max_decision_ms = max(max_decision_ms, elapsed)
        game.apply_action(action)
    if game.winner is None:
        raise RuntimeError(f"Unfinished game: players={players}, seed={seed}, challenger_seat={challenger_seat}")
    return game.winner == challenger_seat, game.revision, max_decision_ms


def benchmark(players, seeds, start_seed, challenger="hard", opponent="normal", workers=1):
    wins = actions = 0
    max_decision_ms = 0.0
    started = time.monotonic()
    jobs = [(players, seed, seat, challenger, opponent)
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
    parser.add_argument("--challenger", choices=("normal", "hard", "devil"), default="hard")
    parser.add_argument("--opponent", choices=("normal", "hard", "devil"), default="normal")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if args.workers < 1 or args.seeds < 1 or not 0 <= args.start_seed < 65536 or args.start_seed + args.seeds > 65536:
        parser.error("Use a positive seed count within the 16-bit seed range.")
    results = []
    for players in args.players:
        result = benchmark(players, args.seeds, args.start_seed, args.challenger, args.opponent, args.workers)
        results.append(result)
        print(json.dumps(result), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"start_seed": args.start_seed,
                                          "seeds_per_seat": args.seeds, "results": results}, indent=2),
                               encoding="utf8")


if __name__ == "__main__":
    main()
