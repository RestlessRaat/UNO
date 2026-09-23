"""Synchronous batched league self-play and PPO with exact windowed GRU gradients.

Returns are computed on the global action clock for every seat, including seats
that were eliminated. Frozen opponents contribute critic data but no policy loss.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import random
import time

import numpy as np
import torch
from torch.distributions import Categorical

from uno.engine import GameConfig, new_game, replay_game
from .encoding import ENCODING_VERSION, belief_targets, encode, privileged
from .model import Actor, Critic, ModelConfig, choose_device, config_dict, tensor_batch

FORMAT_VERSION = 1


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


@dataclass
class TrainConfig:
    seed: int = 20260921
    envs: int = 16
    horizon: int = 128
    players: tuple[int, ...] = (2, 3, 4)
    objective: str = 'round'
    max_episode_steps: int = 4096
    epochs: int = 3
    minibatch: int = 256
    learning_rate: float = 0.0003
    gamma: float = 0.997
    gae_lambda: float = 0.95
    clip: float = 0.2
    entropy: float = 0.01
    belief_weight: float = 0.1
    target_kl: float = 0.03
    snapshot_every: int = 5
    league_size: int = 4
    historical_probability: float = 0.5


def rules_hash():
    return hashlib.sha256((Path(__file__).resolve().parents[1] / 'engine.py').read_bytes()).hexdigest()


def terminal_rewards(winner, players):
    rewards = np.zeros(4, dtype=np.float32)
    rewards[:players] = -1 / (players - 1)
    rewards[winner] = 1
    return rewards


def vector_gae(rewards, values, next_values, boundaries, gamma, lam):
    """T x E x 4 arrays. next_values=0 only at true terminals, not truncations."""
    advantage = np.zeros_like(rewards)
    carry = np.zeros_like(rewards[0])
    for t in reversed(range(len(rewards))):
        delta = rewards[t] + gamma * next_values[t] - values[t]
        carry = delta + gamma * lam * (~boundaries[t, :, None]) * carry
        advantage[t] = carry
    return advantage, advantage + values


class Collector:
    def __init__(self, config, rng):
        self.config, self.rng = config, rng
        self.games, self.steps, self.roles = [], [], []
        for _ in range(config.envs):
            self.games.append(self.fresh_game())
            self.steps.append(0)
            self.roles.append(None)

    def fresh_game(self, players=None, scores=()):
        n = players or self.rng.choice(self.config.players)
        return new_game(GameConfig(tuple(f'P{i}' for i in range(n)), scores=tuple(scores)),
                        self.rng.randrange(1, 60000))

    def reset(self, index, league_count):
        game = self.fresh_game()
        self.games[index], self.steps[index] = game, 0
        self.roles[index] = ((self.rng.randrange(len(game.hands)), self.rng.randrange(league_count))
                             if league_count and self.rng.random() < self.config.historical_probability else None)

    def state_dict(self):
        return {'games': [g.replay() for g in self.games], 'steps': self.steps, 'roles': self.roles}

    def load_state_dict(self, state):
        self.games = [replay_game(g) for g in state['games']]
        self.steps = list(state['steps'])
        self.roles = list(state['roles'])

    @torch.inference_mode()
    def collect(self, actor, critic, league, device):
        actor.eval()
        critic.eval()
        observations, states, masks, choices, logprobs, seats = [], [], [], [], [], []
        values, following, rewards, boundaries, policy_masks, beliefs, belief_masks = [], [], [], [], [], [], []
        completed, truncated, rounds, historical_actions = 0, 0, 0, 0
        wins = [0, 0, 0, 0]
        for _ in range(self.config.horizon):
            encoded = [encode(game.view_for(game.current)) for game in self.games]
            obs, legal = zip(*encoded)
            batch = tensor_batch(obs, device)
            logits, _ = actor(batch)
            learn = np.ones(len(self.games), dtype=bool)
            for league_index, opponent in enumerate(league):
                indices = [i for i, role in enumerate(self.roles)
                           if role and role[1] == league_index and self.games[i].current != role[0]]
                if indices:
                    subset = {key: value[indices] for key, value in batch.items()}
                    logits[indices] = opponent(subset)[0]
                    learn[indices] = False
            distribution = Categorical(logits=logits)
            actions = distribution.sample()
            probabilities = distribution.log_prob(actions).cpu().numpy()
            action_indices = actions.cpu().tolist()
            state = np.stack([privileged(game) for game in self.games])
            value = critic(torch.as_tensor(state, device=device)).cpu().numpy()
            targets = [belief_targets(game, game.current) for game in self.games]
            player_masks = np.asarray([[i < len(g.hands) for i in range(4)] for g in self.games])
            actor_seats = np.asarray([g.current for g in self.games])
            reward = np.zeros_like(value)
            boundary = np.zeros(len(self.games), dtype=bool)
            terminal = np.zeros(len(self.games), dtype=bool)
            for i, game in enumerate(self.games):
                game.apply_action({'player_id': game.current, **legal[i][action_indices[i]]})
                self.steps[i] += 1
                if game.winner is not None:
                    rounds += 1
                    winner = game.winner if self.config.objective == 'round' else game.match_winner
                    if winner is not None:
                        reward[i] = terminal_rewards(winner, len(game.hands))
                        boundary[i] = terminal[i] = True
                        completed += 1
                        wins[winner] += 1
                    else:
                        self.games[i] = self.fresh_game(len(game.hands), game.scores)
                if not terminal[i] and self.steps[i] >= self.config.max_episode_steps:
                    boundary[i] = True
                    truncated += 1
            next_state = np.stack([privileged(g) for g in self.games])
            next_value = critic(torch.as_tensor(next_state, device=device)).cpu().numpy()
            next_value[terminal] = 0
            for i in np.flatnonzero(boundary):
                self.reset(int(i), len(league))
            observations.extend(obs)
            states.append(state)
            masks.append(player_masks)
            choices.append(np.asarray(action_indices))
            logprobs.append(probabilities)
            seats.append(actor_seats)
            values.append(value)
            following.append(next_value)
            rewards.append(reward)
            boundaries.append(boundary)
            policy_masks.append(learn)
            beliefs.extend(t[0] for t in targets)
            belief_masks.extend(t[1] for t in targets)
            historical_actions += int((~learn).sum())
        advantages, returns = vector_gae(np.stack(rewards), np.stack(values), np.stack(following),
                                         np.stack(boundaries), self.config.gamma, self.config.gae_lambda)
        actor_seats = np.stack(seats)
        actor_advantages = np.take_along_axis(advantages, actor_seats[..., None], axis=-1).squeeze(-1)
        data = {'obs': observations, 'states': np.concatenate(states), 'seat_mask': np.concatenate(masks),
                'actions': np.concatenate(choices), 'old_logprobs': np.concatenate(logprobs),
                'advantages': actor_advantages.reshape(-1), 'returns': returns.reshape(-1, 4),
                'policy_mask': np.concatenate(policy_masks), 'beliefs': np.stack(beliefs),
                'belief_mask': np.stack(belief_masks)}
        stats = {'episodes': completed, 'rounds': rounds, 'truncations': truncated,
                 'historical_actions': historical_actions, 'wins_by_absolute_seat': wins,
                 'policy_samples': int(data['policy_mask'].sum())}
        return data, stats


def update(actor, critic, optimizer, data, config, device):
    actor.train()
    critic.train()
    batch = tensor_batch(data['obs'], device)
    tensors = {key: torch.as_tensor(value, device=device) for key, value in data.items() if key != 'obs'}
    valid = tensors['policy_mask']
    advantages = tensors['advantages']
    if valid.any():
        selection = advantages[valid]
        advantages = (advantages - selection.mean()) / selection.std(unbiased=False).clamp_min(1e-6)
    n = len(advantages)
    totals = []
    for _ in range(config.epochs):
        for indices in torch.randperm(n, device=device).split(config.minibatch):
            logits, belief = actor({k: v[indices] for k, v in batch.items()})
            distribution = Categorical(logits=logits)
            new_logprob = distribution.log_prob(tensors['actions'][indices])
            log_ratio = new_logprob - tensors['old_logprobs'][indices]
            ratio = log_ratio.exp()
            keep = valid[indices]
            surrogate = torch.minimum(ratio * advantages[indices],
                                      ratio.clamp(1 - config.clip, 1 + config.clip) * advantages[indices])
            policy_loss = -(surrogate * keep).sum() / keep.sum().clamp_min(1)
            entropy = (distribution.entropy() * keep).sum() / keep.sum().clamp_min(1)
            value = critic(tensors['states'][indices])
            seat_mask = tensors['seat_mask'][indices]
            value_loss = ((value - tensors['returns'][indices]).square() * seat_mask).sum() / seat_mask.sum()
            belief_mask = tensors['belief_mask'][indices] & keep.unsqueeze(-1)
            cross_entropy = -(tensors['beliefs'][indices] * belief.log_softmax(-1)).sum(-1)
            belief_loss = (cross_entropy * belief_mask).sum() / belief_mask.sum().clamp_min(1)
            loss = policy_loss + 0.5 * value_loss - config.entropy * entropy + config.belief_weight * belief_loss
            if not torch.isfinite(loss):
                raise FloatingPointError('Non-finite PPO loss; checkpoint was not overwritten')
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            gradient = torch.nn.utils.clip_grad_norm_(list(actor.parameters()) + list(critic.parameters()), 1.0,
                                                       error_if_nonfinite=True)
            optimizer.step()
            with torch.no_grad():
                kl = (((ratio - 1) - log_ratio) * keep).sum() / keep.sum().clamp_min(1)
            totals.append([float(v.detach()) for v in (policy_loss, value_loss, belief_loss, entropy, kl, gradient)])
            if float(kl) > config.target_kl:
                break
        if totals[-1][4] > config.target_kl:
            break
    return dict(zip(('policy_loss', 'value_loss', 'belief_loss', 'entropy', 'approx_kl', 'gradient_norm'),
                    np.mean(totals, axis=0).tolist()))


def cpu_state(model):
    return {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}


def atomic_save(payload, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    torch.save(payload, temporary)
    temporary.replace(path)


def load_checkpoint(path):
    payload = torch.load(path, map_location='cpu', weights_only=True)
    if payload.get('format_version') != FORMAT_VERSION or payload.get('encoding_version') != ENCODING_VERSION:
        raise ValueError('Incompatible neural checkpoint format/encoding')
    return payload


def save_run(output, actor, critic, optimizer, config, collector, rng, league, step, total_steps):
    actor_payload = {'format_version': FORMAT_VERSION, 'encoding_version': ENCODING_VERSION,
                     'model_config': config_dict(actor), 'actor': cpu_state(actor),
                     'rules_hash': rules_hash(), 'update': step, 'environment_steps': total_steps,
                     'objective': config.objective}
    atomic_save(actor_payload, output / 'actor.pt')
    full = {**actor_payload, 'critic': cpu_state(critic), 'optimizer': optimizer.state_dict(),
            'train_config': asdict(config), 'collector': collector.state_dict(),
            'python_rng': rng.getstate(), 'torch_rng': torch.get_rng_state(),
            'cuda_rng': torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
            'league': [cpu_state(opponent) for opponent in league]}
    atomic_save(full, output / 'latest.pt')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('build/neural/local'))
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--device', choices=('auto', 'cpu', 'cuda'), default='auto')
    parser.add_argument('--updates', type=int, default=20, help='Additional updates (also on resume)')
    parser.add_argument('--minutes', type=float, default=0, help='Stop after a completed update; zero disables')
    parser.add_argument('--envs', type=int, default=16)
    parser.add_argument('--horizon', type=int, default=128)
    parser.add_argument('--minibatch', type=int, default=256)
    parser.add_argument('--epochs', type=int, default=3)
    parser.add_argument('--players', type=int, nargs='+', choices=(2, 3, 4), default=[2, 3, 4])
    parser.add_argument('--objective', choices=('round', 'match'), default='round')
    parser.add_argument('--seed', type=int, default=20260921)
    parser.add_argument('--max-episode-steps', type=int, default=4096)
    parser.add_argument('--snapshot-every', type=int, default=5)
    parser.add_argument('--threads', type=int, default=2)
    args = parser.parse_args(argv)
    if min(args.updates, args.envs, args.horizon, args.minibatch, args.epochs,
           args.max_episode_steps, args.snapshot_every, args.threads) < 1 or args.minutes < 0:
        parser.error('Counts must be positive and minutes non-negative')
    torch.set_num_threads(args.threads)
    device = choose_device(args.device)
    config = TrainConfig(seed=args.seed, envs=args.envs, horizon=args.horizon, players=tuple(args.players),
                         objective=args.objective, minibatch=args.minibatch, epochs=args.epochs,
                         max_episode_steps=args.max_episode_steps, snapshot_every=args.snapshot_every)
    saved = load_checkpoint(args.resume) if args.resume else None
    if saved:
        if saved['rules_hash'] != rules_hash():
            raise ValueError('Rules engine changed since checkpoint; start a new run')
        config = TrainConfig(**saved['train_config'])
    elif (args.output / 'latest.pt').exists():
        parser.error('Output already contains a run; use --resume or a different --output')
    torch.manual_seed(config.seed)
    rng = random.Random(config.seed)
    model_config = ModelConfig(**saved['model_config']) if saved else ModelConfig()
    actor, critic = Actor(model_config).to(device), Critic().to(device)
    optimizer = torch.optim.Adam(list(actor.parameters()) + list(critic.parameters()), lr=config.learning_rate)
    collector = Collector(config, rng)
    league = []
    start_update, total_steps = 0, 0
    if saved:
        actor.load_state_dict(saved['actor'])
        critic.load_state_dict(saved['critic'])
        optimizer.load_state_dict(saved['optimizer'])
        collector.load_state_dict(saved['collector'])
        rng.setstate(saved['python_rng'])
        torch.set_rng_state(saved['torch_rng'])
        if saved['cuda_rng'] and device.type == 'cuda':
            torch.cuda.set_rng_state_all(saved['cuda_rng'])
        for state in saved['league']:
            opponent = Actor(model_config).to(device).eval()
            opponent.load_state_dict(state)
            opponent.requires_grad_(False)
            league.append(opponent)
        # Model construction consumed RNG above; restore after every construction.
        torch.set_rng_state(saved['torch_rng'])
        if saved['cuda_rng'] and device.type == 'cuda':
            torch.cuda.set_rng_state_all(saved['cuda_rng'])
        start_update, total_steps = saved['update'], saved['environment_steps']
    args.output.mkdir(parents=True, exist_ok=True)
    metadata = {'train_config': asdict(config), 'model_config': asdict(model_config),
                'device': str(device), 'torch': str(torch.__version__), 'cuda': torch.version.cuda,
                'gpu': torch.cuda.get_device_name(device) if device.type == 'cuda' else None,
                'actor_parameters': sum(p.numel() for p in actor.parameters()),
                'critic_parameters': sum(p.numel() for p in critic.parameters()), 'rules_hash': rules_hash(),
                'resumed_from': str(args.resume) if args.resume else None}
    (args.output / 'run.json').write_text(json.dumps(metadata, indent=2), encoding='utf8')
    print(json.dumps(metadata), flush=True)
    started = time.monotonic()
    if not saved:
        atomic_save({'format_version': FORMAT_VERSION, 'encoding_version': ENCODING_VERSION,
                     'model_config': asdict(model_config), 'actor': cpu_state(actor), 'rules_hash': rules_hash(),
                     'update': 0, 'environment_steps': 0, 'objective': config.objective}, args.output / 'initial-actor.pt')
    for step in range(start_update + 1, start_update + args.updates + 1):
        completed = step - start_update - 1
        elapsed = time.monotonic() - started
        average_update = elapsed / completed if completed else 0
        eta = average_update * (args.updates - completed)
        print(f'[训练] 更新 {completed + 1}/{args.updates} 开始'
              f' | 已用 {format_duration(elapsed)}'
              f' | 预计剩余 {format_duration(eta) if completed else "计算中"}', flush=True)
        tick = time.monotonic()
        data, stats = collector.collect(actor, critic, league, device)
        collected = time.monotonic()
        losses = update(actor, critic, optimizer, data, config, device)
        total_steps += config.envs * config.horizon
        if step % config.snapshot_every == 0:
            opponent = Actor(model_config).to(device).eval()
            opponent.load_state_dict(actor.state_dict())
            opponent.requires_grad_(False)
            if len(league) == config.league_size:
                # Replace a slot only after assigning affected episodes to self-play.
                for i, role in enumerate(collector.roles):
                    if role and role[1] == 0:
                        collector.roles[i] = None
                    elif role:
                        collector.roles[i] = (role[0], role[1] - 1)
                league.pop(0)
            league.append(opponent)
        save_run(args.output, actor, critic, optimizer, config, collector, rng, league, step, total_steps)
        elapsed = time.monotonic() - tick
        metrics = {'update': step, 'environment_steps': total_steps, 'seconds': elapsed,
                   'collect_seconds': collected - tick, 'steps_per_second': config.envs * config.horizon / elapsed,
                   'league_size': len(league), **stats, **losses}
        with (args.output / 'metrics.jsonl').open('a', encoding='utf8') as stream:
            stream.write(json.dumps(metrics) + '\n')
        print(f'[训练] 更新 {completed + 1}/{args.updates} 完成'
              f' | 本轮 {format_duration(elapsed)}'
              f' | 总用时 {format_duration(time.monotonic() - started)}'
              f' | 完成局数 {stats.get("rounds", 0)}', flush=True)
        print(json.dumps(metrics), flush=True)
        if args.minutes and time.monotonic() - started >= args.minutes * 60:
            break
    print(f'Checkpoint: {(args.output / "latest.pt").resolve()}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped. Resume latest.pt from the last completed update; partial update discarded.', flush=True)
