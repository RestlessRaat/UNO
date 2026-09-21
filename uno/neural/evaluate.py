"""Held-out, seat-balanced evaluation. No training or heuristic fallbacks."""
import argparse
import json
import math
from pathlib import Path
import random
import time

import torch

from uno.ai import choose_action, decision_view
from uno.engine import GameConfig, new_game
from .encoding import encode
from .model import tensor_batch
from .policy import load_actor


def wilson(wins, games):
    if not games:
        return [0.0, 1.0]
    z = 1.96
    fraction = wins / games
    denominator = 1 + z * z / games
    center = (fraction + z * z / (2 * games)) / denominator
    half = z * math.sqrt(fraction * (1 - fraction) / games + z * z / (4 * games * games)) / denominator
    return [max(0, center - half), min(1, center + half)]


@torch.inference_mode()
def evaluate(checkpoint, *, seeds=4, first_seed=60001, players=(2, 3, 4), opponents=('normal',),
             max_steps=4096, device='cpu', deterministic=False, objective='round'):
    actor = load_actor(str(Path(checkpoint).resolve()), device)
    cases = []
    for n in players:
        for opponent in opponents:
            for seed in range(first_seed, first_seed + seeds):
                for seat in range(n):
                    cases.append({'n': n, 'opponent': opponent, 'seed': seed, 'seat': seat,
                                  'game': new_game(GameConfig(tuple(f'P{i}' for i in range(n))), seed),
                                  'rng': random.Random(seed * 17 + seat), 'steps': 0, 'rounds': 0})
    finished = []
    started = time.perf_counter()
    inference_seconds, decisions = 0.0, 0
    while cases:
        neural = []
        for case in cases:
            game = case['game']
            if game.current == case['seat']:
                observation, actions = encode(game.view_for(game.current))
                neural.append((case, observation, actions))
            else:
                action = choose_action(decision_view(game, case['opponent']), case['rng'], case['opponent'])
                game.apply_action(action)
                case['steps'] += 1
        # Bound batch sizes for GPU memory and CPU latency.
        for start in range(0, len(neural), 128):
            group = neural[start:start + 128]
            tick = time.perf_counter()
            logits, _ = actor(tensor_batch([item[1] for item in group], device))
            probabilities = logits.softmax(-1).cpu().tolist()
            inference_seconds += time.perf_counter() - tick
            decisions += len(group)
            for (case, _, actions), probability in zip(group, probabilities):
                valid = probability[:len(actions)]
                index = (max(range(len(actions)), key=valid.__getitem__) if deterministic
                         else case['rng'].choices(range(len(actions)), weights=valid, k=1)[0])
                game = case['game']
                game.apply_action({'player_id': game.current, **actions[index]})
                case['steps'] += 1
        active = []
        for case in cases:
            game = case['game']
            winner = None
            if game.winner is not None:
                case['rounds'] += 1
                winner = game.winner if objective == 'round' else game.match_winner
            if winner is not None or case['steps'] >= max_steps:
                finished.append({key: case[key] for key in ('n', 'opponent', 'seed', 'seat', 'steps', 'rounds')}
                                | {'win': winner == case['seat'], 'truncated': winner is None})
            else:
                if game.winner is not None:
                    # Rematch seeds stay in the held-out seed interval.
                    seed = 60000 + ((case['seed'] - 60000 + 997 * case['rounds']) % 5536)
                    case['game'] = new_game(GameConfig(game.config.names, scores=tuple(game.scores)), seed)
                active.append(case)
        cases = active
    groups = []
    for n in players:
        for opponent in opponents:
            rows = [r for r in finished if r['n'] == n and r['opponent'] == opponent]
            wins = sum(r['win'] for r in rows)
            complete = sum(not r['truncated'] for r in rows)
            groups.append({'players': n, 'opponent': opponent, 'scheduled': len(rows), 'completed': complete,
                           'truncated': len(rows) - complete, 'wins': wins,
                           'win_rate_completed': wins / complete if complete else None,
                           'wilson95_descriptive': wilson(wins, complete),
                           'random_seat_baseline': 1 / n})
    return {'checkpoint': str(Path(checkpoint).resolve()), 'objective': objective,
            'deterministic': deterministic, 'device': device, 'seconds': time.perf_counter() - started,
            'batched_inference_ms_per_decision': 1000 * inference_seconds / max(1, decisions),
            'groups': groups, 'games': finished,
            'note': 'Small samples do not establish strength. Seat-swapped games share seeds; Wilson intervals '
                    'are descriptive and do not account for pairing. Truncations are reported, not called losses.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkpoint', type=Path)
    parser.add_argument('--output', type=Path, default=Path('build/neural/evaluation.json'))
    parser.add_argument('--seeds', type=int, default=4)
    parser.add_argument('--first-seed', type=int, default=60001)
    parser.add_argument('--players', nargs='+', type=int, choices=(2, 3, 4), default=[2, 3, 4])
    parser.add_argument('--opponents', nargs='+', choices=('normal', 'hard', 'devil'), default=['normal'])
    parser.add_argument('--max-steps', type=int, default=4096)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    parser.add_argument('--objective', choices=('round', 'match'), default='round')
    parser.add_argument('--deterministic', action='store_true')
    args = parser.parse_args(argv)
    if args.seeds < 1 or not 60000 <= args.first_seed <= 65535 or args.first_seed + args.seeds > 65536:
        parser.error('Evaluation seeds must stay in reserved interval [60000, 65535]')
    if args.max_steps < 1:
        parser.error('--max-steps must be positive')
    torch.set_num_threads(2)
    report = evaluate(args.checkpoint, seeds=args.seeds, first_seed=args.first_seed,
                      players=args.players, opponents=args.opponents, max_steps=args.max_steps,
                      device=args.device, deterministic=args.deterministic, objective=args.objective)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps({k: v for k, v in report.items() if k != 'games'}, indent=2))


if __name__ == '__main__':
    main()
