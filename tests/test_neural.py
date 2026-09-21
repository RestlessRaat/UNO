"""Optional ML contract tests. Base-game installs can run without PyTorch."""
from copy import deepcopy
from pathlib import Path
import random

import pytest

torch = pytest.importorskip('torch')
np = pytest.importorskip('numpy')

from uno.ai_plugins import AiRegistry, build_context, run_policy
from uno.engine import BY_ID, GameConfig, new_game
from uno.neural.encoding import (N_TYPES, belief_targets, encode, grouped_actions,
                                 privileged)
from uno.neural.model import Actor, Critic, ModelConfig, tensor_batch
from uno.neural.policy import NeuralPolicy, load_actor
from uno.neural.training import (Collector, TrainConfig, load_checkpoint, save_run,
                                 terminal_rewards, update, vector_gae)

torch.set_num_threads(2)


def test_extended_plugin_observation_is_lossless_and_immutable():
    game = new_game(GameConfig(), 12)
    game.phase = 'choose_player'
    game.apply_action({'player_id': 0, 'type': 'choose_player', 'target': 1})
    view = game.view_for(0)
    context = build_context(view, 1, 2000)
    restored = context.game.to_dict(context.legal_actions)
    assert restored['history'] == view['history']
    assert restored['known_opponent_cards'] == view['known_opponent_cards']
    assert restored['known_opponent_cards']
    view['history'][-1]['type'] = 'changed'
    assert context.game.history[-1].type != 'changed'
    assert isinstance(context.game.known_opponent_cards[0][1], tuple)


def test_actor_encoding_cannot_observe_hidden_hand_or_deck_changes():
    game = new_game(GameConfig(), 17)
    alternate = deepcopy(game)
    alternate.hands[1][0], alternate.deck[0] = alternate.deck[0], alternate.hands[1][0]
    alternate.deck.reverse()
    first, second = encode(game.view_for(0))[0], encode(alternate.view_for(0))[0]
    for key in first:
        np.testing.assert_array_equal(first[key], second[key])
    assert not np.array_equal(privileged(game), privileged(alternate))
    torch.manual_seed(10)
    actor = Actor(ModelConfig(width=32, heads=4, layers=1, history_hidden=32)).eval()
    with torch.inference_mode():
        a = actor(tensor_batch([first], 'cpu'))[0]
        b = actor(tensor_batch([second], 'cpu'))[0]
    torch.testing.assert_close(a, b, rtol=0, atol=0)


def test_card_order_does_not_change_policy_and_duplicates_share_one_action():
    game = new_game(GameConfig(), 10)
    # Obtain a legal duplicate pair without assuming the initial shuffle.
    pair = [c.id for c in BY_ID.values() if c.color == game.color and c.value == '3']
    for cid in pair:
        for source in [game.deck, *game.hands[1:]]:
            if cid in source:
                source.remove(cid)
                game.hands[0].append(cid)
    view = game.view_for(0)
    shuffled = deepcopy(view)
    shuffled['hand'].reverse()
    shuffled['legal'].reverse()
    first, actions = encode(view)
    second, _ = encode(shuffled)
    for key in first:
        np.testing.assert_array_equal(first[key], second[key])
    assert sum(a['type'] == 'play' and a['card_id'] in pair for a in actions) == 1
    assert all(a in view['legal'] for a in actions)


@pytest.mark.parametrize('players', [2, 3, 4])
def test_encoder_covers_complete_games_and_privileged_targets(players):
    game = new_game(GameConfig(tuple(str(i) for i in range(players))), 3)
    rng = random.Random(3)
    for _ in range(4096):
        if game.winner is not None:
            break
        obs, actions = encode(game.view_for(game.current))
        assert np.isfinite(privileged(game)).all()
        assert obs['action_mask'].sum() == len(actions)
        targets, mask = belief_targets(game, game.current)
        np.testing.assert_allclose(targets[mask].sum(-1), 1, atol=1e-6)
        game.apply_action({'player_id': game.current, **rng.choice(actions)})
    assert game.winner is not None


def test_multiplayer_returns_preserve_eliminated_rewards_and_truncation_bootstrap():
    rewards = np.zeros((2, 1, 4), dtype=np.float32)
    rewards[-1, 0] = terminal_rewards(2, 4)
    values = np.zeros_like(rewards)
    boundary = np.asarray([[False], [True]])
    advantage, returns = vector_gae(rewards, values, values, boundary, 1, 1)
    # Seat 0 could have been eliminated before either step; it still gets loss.
    assert returns[0, 0, 0] == pytest.approx(-1 / 3)
    assert returns[0, 0, 2] == 1
    following = np.ones((1, 1, 4), dtype=np.float32) * 0.7
    _, truncated = vector_gae(values[:1], values[:1], following, boundary[1:], 0.9, 1)
    np.testing.assert_allclose(truncated, 0.63)
    assert abs(terminal_rewards(1, 3).sum()) < 1e-6


def test_real_ppo_update_checkpoint_reload_and_plugin(tmp_path, monkeypatch):
    torch.manual_seed(2)
    config = TrainConfig(envs=2, horizon=8, epochs=1, minibatch=16)
    rng = random.Random(2)
    collector = Collector(config, rng)
    actor = Actor(ModelConfig(width=32, heads=4, layers=1, history_hidden=32))
    critic = Critic()
    optimizer = torch.optim.Adam([*actor.parameters(), *critic.parameters()], lr=0.001)
    before = actor.query.weight.detach().clone()
    data, stats = collector.collect(actor, critic, [], 'cpu')
    losses = update(actor, critic, optimizer, data, config, 'cpu')
    assert stats['policy_samples'] == 16
    assert all(np.isfinite(v) for v in losses.values())
    assert not torch.equal(before, actor.query.weight)
    save_run(tmp_path, actor, critic, optimizer, config, collector, rng, [], 1, 16)
    checkpoint = load_checkpoint(tmp_path / 'latest.pt')
    assert checkpoint['optimizer']['state']
    restored = Collector(config, random.Random(0))
    restored.load_state_dict(checkpoint['collector'])
    assert [g.replay() for g in restored.games] == [g.replay() for g in collector.games]
    policy = NeuralPolicy(tmp_path / 'actor.pt')
    assert not policy.actor.training
    monkeypatch.setenv('UNO_NEURAL_CHECKPOINT', str(tmp_path / 'actor.pt'))
    registry = AiRegistry(Path(__file__).resolve().parents[1] / 'examples/plugins')
    assert registry.has('local.neural')
    plugin = registry.create('local.neural')
    game = restored.games[0]
    context = build_context(game.view_for(game.current), 8, 10000)
    action = run_policy(plugin, context)
    assert action in game.legal_actions(game.current)
    assert action == run_policy(plugin, context)
    load_actor.cache_clear()


def test_frozen_opponents_are_excluded_from_policy_samples():
    config = TrainConfig(envs=2, horizon=8)
    collector = Collector(config, random.Random(12))
    collector.roles = [(1, 0), (1, 0)]
    actor = Actor(ModelConfig(width=32, heads=4, layers=1, history_hidden=32))
    opponent = deepcopy(actor).eval().requires_grad_(False)
    data, stats = collector.collect(actor, Critic(), [opponent], 'cpu')
    assert stats['historical_actions'] > 0
    assert stats['policy_samples'] + stats['historical_actions'] == 16
    assert data['policy_mask'].dtype == bool


@pytest.mark.parametrize('match_win', [False, True])
def test_match_training_carries_scores_and_only_rewards_match_winner(match_win):
    config = TrainConfig(envs=1, horizon=1, objective='match', players=(2,))
    collector = Collector(config, random.Random(5))
    game = collector.games[0]
    winning_card = next(c.id for c in BY_ID.values() if c.color == game.color and c.value == '3'
                        and c.id != game.discard[-1])
    pool = [cid for cid in BY_ID if cid not in (winning_card, game.discard[-1])]
    game.hands = [[winning_card], pool[:7]]
    game.deck = pool[7:]
    game.scores[0] = game.score_target - 1 if match_win else 0

    class FirstAction(torch.nn.Module):
        def forward(self, obs):
            logits = torch.full_like(obs['action_mask'], -1000, dtype=torch.float32)
            logits[:, 0] = 1000
            return logits, None

    class ZeroCritic(torch.nn.Module):
        def forward(self, state):
            return torch.zeros((state.shape[0], 4))

    data, stats = collector.collect(FirstAction(), ZeroCritic(), [], 'cpu')
    assert stats['rounds'] == 1
    assert stats['episodes'] == int(match_win)
    if match_win:
        np.testing.assert_array_equal(data['returns'][0], [1, -1, 0, 0])
        assert collector.games[0].scores == [0, 0]
    else:
        np.testing.assert_array_equal(data['returns'][0], [0, 0, 0, 0])
        assert collector.games[0].scores[0] > 0
        assert collector.games[0].winner is None
