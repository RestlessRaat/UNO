from copy import deepcopy
import random

import pytest

from uno.ai import choose_action, decision_view
from uno.devil import (_Belief, _forced_finish, _rollout, _Simulation,
                       choose_devil)
from uno.engine import BY_ID, Game, GameConfig, new_game
from test_ai import position


def test_proves_long_combo_before_seven_without_exchanging():
    game = position([[('red', 'skip')] * 3 + [('red', 'reverse')] * 3 + [('red', '7')],
                     [('blue', '1')]])
    for _ in range(7):
        assert game.current == 0
        action = _forced_finish(game.view_for(0))
        assert action is not None
        game.apply_action({'player_id': 0, **action})
    assert game.winner == 0


def test_no_forced_finish_when_regular_cards_cannot_share_a_finisher():
    game = position([[('red', 'skip'), ('blue', '1'), ('green', '2')], [('yellow', '3')]])
    assert _forced_finish(game.view_for(0)) is None


def test_forced_finish_uses_discard_all_after_extra_turns():
    game = position([[('red', 'skip'), ('blue', 'skip'), ('blue', '1'), ('blue', 'discard all')],
                     [('yellow', '2')]])
    for _ in range(3):
        game.apply_action({'player_id': 0, **_forced_finish(game.view_for(0))})
    assert game.winner == 0


def test_reverse_four_finish_respects_self_penalty_and_color_phase():
    game = position([[('wild', 'wild reverse draw 4'), ('blue', 'draw 4')], [('red', '2')]])
    for _ in range(3):
        action = _forced_finish(game.view_for(0))
        assert action in game.legal_actions(0)
        game.apply_action({'player_id': 0, **action})
    assert game.winner == 0


def test_no_hidden_state_dependency_or_snapshot_mutation():
    game = new_game(GameConfig(), 82)
    view = game.view_for(0)
    original = deepcopy(view)
    first = choose_devil(view, random.Random(53), samples=2)
    game.hands[1][0], game.deck[-1] = game.deck[-1], game.hands[1][0]
    game.seed = 999
    assert game.view_for(0) == original
    assert choose_devil(game.view_for(0), random.Random(53), samples=2) == first
    assert view == original


def test_sample_conserves_cards_and_keeps_public_discards_out_of_hands():
    game = position([[('red', 'discard all'), ('red', '1'), ('blue', '2')], [('green', '3')]])
    game.apply_action({'player_id': 0, 'type': 'play', 'card_id': game.hands[0][0]})
    view = game.view_for(1)
    assert len(view['known_discards']) >= 2
    sample = _Simulation.sample(view, random.Random(6))
    cards = sample.deck + sample.discard + [c for hand in sample.hands for c in hand]
    assert len(cards) == len(set(cards)) == 168
    assert sample.hands[1] == game.hands[1]
    assert set(view['known_discards']) <= set(sample.discard)
    assert len(sample.deck) == view['deck_count']


def test_sampling_preserves_revealed_roulette_cards():
    game = new_game(GameConfig(), 2)
    game.current = 1
    game.phase = 'roulette'
    game.color = next(c for c in ('red', 'blue', 'yellow', 'green') if c != BY_ID[game.deck[-1]].color)
    game.apply_action({'player_id': 1, 'type': 'draw'})
    view = game.view_for(0)
    sample = _Simulation.sample(view, random.Random(8))
    assert set(game.roulette_revealed) <= set(sample.hands[1])
    assert len(set(sample.deck + sample.discard + sum(sample.hands, []))) == 168


def test_seven_and_zero_create_private_exact_hand_memory():
    game = position([[('red', '7'), ('blue', '1')],
                     [('wild', 'wild draw 10'), ('green', '2'), ('green', '3')]])
    old_own, old_other = set(game.hands[0][1:]), set(game.hands[1])
    game.apply_action({'player_id': 0, 'type': 'play', 'card_id': game.hands[0][0]})
    game.apply_action({'player_id': 0, 'type': 'choose_player', 'target': 1})
    assert set(game.view_for(0)['known_opponent_cards']['1']) == old_own
    assert set(game.view_for(1)['known_opponent_cards']['0']) == old_other

    game = position([[('red', '0'), ('blue', '4')],
                     [('wild', 'wild draw 6'), ('green', '5')]])
    passed = set(game.hands[0][1:])
    game.apply_action({'player_id': 0, 'type': 'play', 'card_id': game.hands[0][0]})
    assert set(game.view_for(0)['known_opponent_cards']['1']) == passed


def test_particle_sample_honors_remembered_cards():
    game = position([[('red', '7'), ('blue', '1')],
                     [('wild', 'wild draw 10'), ('green', '2'), ('green', '3')]])
    game.apply_action({'player_id': 0, 'type': 'play', 'card_id': game.hands[0][0]})
    game.apply_action({'player_id': 0, 'type': 'choose_player', 'target': 1})
    view = game.view_for(0)
    remembered = set(view['known_opponent_cards']['1'])
    for seed in range(8):
        sample = _Simulation.sample(view, random.Random(seed), _Belief(view))
        assert remembered <= set(sample.hands[1])


def test_belief_downweights_stack_cards_after_penalty_is_accepted():
    game = new_game(GameConfig(('A', 'B')), 19)
    game.current = 1
    game.pending, game.last_draw = 6, 6
    game.apply_action({'player_id': 1, 'type': 'draw'})
    view = game.view_for(0)
    belief = _Belief(view)
    stack = next(c.id for c in BY_ID.values() if c.value == 'wild draw 10')
    ordinary = next(c.id for c in BY_ID.values() if c.value == '1')
    assert belief.weights[1][stack] < belief.weights[1][ordinary]


def test_rollout_can_draw_repeatedly_but_stops_at_plan_limit():
    game = new_game(GameConfig(('A', 'B')), 29)
    sim = _Simulation.sample(game.view_for(0), random.Random(3))
    weak = [c.id for c in BY_ID.values() if c.value.isdigit() and c.value not in ('0', '7')][:10]
    sim.hands[0] = weak[:2]
    sim.hands[1] = weak[2:6]
    sim.current, sim.phase, sim.pending = 0, 'turn', 0
    sim.color = BY_ID[sim.hands[0][0]].color
    sim.discard[-1] = sim.hands[0][0]
    sim.deck = sim.deck + weak[6:]
    for expected in range(3):
        action = sim.rollout_action(True)
        assert action == {'type': 'draw'}
        sim.act(action)
        assert sim.voluntary_draws[0] == expected + 1
    assert sim.rollout_action(True)['type'] == 'play'


def test_rollout_models_opponents_as_strategic(monkeypatch):
    game = new_game(GameConfig(('A', 'B')), 61)
    world = _Simulation.sample(game.view_for(0), random.Random(8))
    seen = []
    original = _Simulation.rollout_action
    def record(self, smart):
        seen.append((self.current, smart))
        return original(self, smart)
    monkeypatch.setattr(_Simulation, 'rollout_action', record)
    monkeypatch.setattr('uno.devil.ROLLOUT_STEPS', 12)
    _rollout(world, game.legal_actions(0)[0], 0)
    assert any(player == 1 and smart for player, smart in seen)


def test_mercy_keeps_concealed_hands_private_and_sample_valid():
    game = new_game(GameConfig(), 6)
    game.current = 1
    while len(game.hands[1]) < 35:
        game.hands[1].append(game.deck.pop())
    hidden = set(game.hands[1])
    game.phase = 'roulette'
    game.apply_action({'player_id': 1, 'type': 'draw'})
    view = game.view_for(2)
    assert not hidden.intersection(view['known_discards'])
    assert set(game.roulette_revealed) <= set(view['known_discards'])
    sample = _Simulation.sample(view, random.Random(7))
    all_cards = sample.deck + sample.discard + sum(sample.hands, [])
    assert len(all_cards) == len(set(all_cards)) == 168
    assert sample.hands[1] == []


def test_fork_is_independent_and_replays_identically():
    sample = _Simulation.sample(new_game(GameConfig(), 41).view_for(0), random.Random(9))
    clone = sample.fork()
    before = deepcopy(sample.hands)
    clone.act({'type': 'draw'})
    assert sample.hands == before
    sample.act({'type': 'draw'})
    assert sample.hands == clone.hands and sample.deck == clone.deck


@pytest.mark.parametrize('players', [2, 3, 4])
def test_simulated_rules_match_real_engine(players):
    game = new_game(GameConfig(tuple(f'P{i}' for i in range(players))), 74)
    sim = _Simulation.sample(game.view_for(0), random.Random(26))
    actual = new_game(game.config, 74)
    fields = ('hands', 'deck', 'discard', 'current', 'direction', 'color', 'phase', 'pending',
              'last_draw', 'penalty_started', 'color_reason', 'turn_serial', 'eliminated', 'winner',
              'roulette_revealed')
    for field in fields:
        setattr(actual, field, deepcopy(getattr(sim, field)))
    actual.known_discards = {actual.discard[-1]}

    class ShuffleAdapter:
        def __init__(self, state):
            self.rng = random.Random()
            self.rng.setstate(state)
        def shuffle(self, items):
            result = list(items)
            self.rng.shuffle(result)
            return result

    for _ in range(3000):
        if sim.winner is not None:
            break
        action = sim.rollout_action(smart=sim.current == 0)
        assert action in actual.legal_actions(actual.current)
        actual.rng = ShuffleAdapter(sim.rng.getstate())
        actual.apply_action({'player_id': actual.current, **action})
        sim.act(action)
        for field in fields:
            assert getattr(actual, field) == getattr(sim, field), field
    assert sim.winner is not None


@pytest.mark.parametrize('players', [2, 3, 4])
def test_devil_full_games_use_only_legal_actions(monkeypatch, players):
    monkeypatch.setattr('uno.devil.ROLLOUT_SAMPLES', 2)
    game = new_game(GameConfig(tuple(f'P{i}' for i in range(players))), 240 + players)
    rng = random.Random(13)
    for _ in range(10000):
        if game.winner is not None:
            break
        action = choose_action(game.view_for(game.current), rng, 'devil')
        game.apply_action(action)
    assert game.winner is not None
    game.assert_invariants()
