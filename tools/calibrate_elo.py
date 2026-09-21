"""Calibrate a challenger against Normal with each seed and both seat positions."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from uno.ai import DIFFICULTIES, choose_action, decision_view
from uno.elo import DuelElo, save_calibration
from uno.engine import GameConfig, new_game


def play_duel(seed, hard_seat, challenger="hard"):
    names = (challenger.title(), "Normal") if hard_seat == 0 else ("Normal", challenger.title())
    game = new_game(GameConfig(names), seed)
    rngs = [random.Random(seed * 101 + seat * 997) for seat in range(2)]
    for _ in range(10000):
        if game.winner is not None:
            return challenger if game.winner == hard_seat else "normal", game.revision
        seat = game.current
        difficulty = challenger if seat == hard_seat else "normal"
        view = decision_view(game, difficulty) if difficulty == "devil" else game.view_for(seat)
        game.apply_action(choose_action(view, rngs[seat], difficulty))
    raise RuntimeError(f"Round did not finish: seed={seed}, hard_seat={hard_seat}")


def _paired_seed(job):
    seed, challenger = job
    return [play_duel(seed, seat, challenger) for seat in range(2)]


def calibrate(start_seed, seeds, progress=None, challenger="hard", workers=1):
    if (type(seeds) is not int or seeds < 1 or type(start_seed) is not int
            or start_seed < 0 or start_seed + seeds > 65536):
        raise ValueError("Use a positive seed count within the 16-bit seed range.")
    if type(workers) is not int or workers < 1:
        raise ValueError("Workers must be a positive integer.")
    ratings = DuelElo(challenger=challenger)
    seats = [DuelElo(challenger=challenger), DuelElo(challenger=challenger)]
    actions = 0
    jobs = [(seed, challenger) for seed in range(start_seed, start_seed + seeds)]
    pool = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
    try:
        results = pool.map(_paired_seed, jobs) if pool else map(_paired_seed, jobs)
        for index, pair in enumerate(results, 1):
            for seat, (winner, count) in enumerate(pair):
                ratings.record(winner)
                seats[seat].record(winner)
                actions += count
            if progress and (index % (10 if challenger == "devil" else 100) == 0 or index == seeds):
                progress(index, ratings)
    finally:
        if pool:
            pool.shutdown(cancel_futures=True)
    return {**ratings.report(), "start_seed": start_seed, "seeds": seeds,
            "paired_seats": True, "rng": "Random(seed * 101 + seat * 997)",
            f"{challenger}_seat_results": [{"seat": i, "games": r.games, f"{challenger}_wins": r.hard_wins}
                                  for i, r in enumerate(seats)],
            "actions": actions,
            "source_sha256": {name: hashlib.sha256((ROOT / "uno" / name).read_bytes()).hexdigest()
                              for name in (("ai.py", "engine.py", "devil.py") if challenger == "devil" else ("ai.py", "engine.py"))}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=2000)
    parser.add_argument("--start-seed", type=int, default=1)
    parser.add_argument("--output", type=Path, default=ROOT / "assets/ai_elo.json")
    parser.add_argument("--challenger", choices=("hard", "devil"), default="hard")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if args.workers < 1 or args.seeds < 1 or args.start_seed < 0 or args.start_seed + args.seeds > 65536:
        parser.error("Use a positive seed count within the 16-bit seed range.")
    started = time.monotonic()

    def progress(index, rating):
        print(f"{index}/{args.seeds} seeds; {rating.games} rounds; "
              f"Normal {rating.ratings()['normal']:.0f}, {args.challenger.title()} {rating.ratings()[args.challenger]:.0f}", flush=True)

    report = calibrate(args.start_seed, args.seeds, progress, args.challenger, args.workers)
    report["seconds"] = round(time.monotonic() - started, 2)
    save_calibration(report, args.output)
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
