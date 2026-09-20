import json
import random
from collections import Counter
from pathlib import Path

import pytest

from uno.ai import choose_action
from uno.engine import BY_ID, CARDS, GameConfig, RuleError, XorShift16, new_game, replay_game


def scenario(hands, top=("red", "5"), count=None):
    """Build full-deck positions so edge tests still check conservation."""
    count = count or len(hands)
    game = new_game(GameConfig(tuple(f"P{i}" for i in range(count))), 123)
    pool = list(BY_ID)
    def take(spec):
        c = next(i for i in pool if (BY_ID[i].color, BY_ID[i].value) == spec)
        pool.remove(c)
        return c
    game.discard = [take(top)]
    game.hands = [[take(c) for c in hand] for hand in hands]
    game.deck = pool
    game.color = top[0]
    game.last_events = []
    return game


def play(game, value):
    card = next(i for i in game.hands[game.current] if BY_ID[i].value == value)
    return game.apply_action({"player_id": game.current, "type": "play", "card_id": card})


def test_deck_matches_source():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from inspect_sb3 import project
    target = next(t for t in project()["targets"] if t["name"] == "uno")
    expected = next(v[1] for v in target["lists"].values() if v[0] == "_initial deck")
    assert [f'{"default" if c.wild else c.color}:{c.value}' for c in CARDS] == expected
    assert len(CARDS) == len(BY_ID) == 168


def test_xorshift_and_zero():
    rng = XorShift16(1)
    assert [rng.next() for _ in range(3)] == [33153, 24609, 59801]
    assert XorShift16(0).next() == 0


@pytest.mark.parametrize("players", [2, 3, 4])
def test_seeded_full_games(players):
    for seed in range(25):
        game = new_game(GameConfig(tuple(f"P{i}" for i in range(players))), seed)
        rng = random.Random(seed)
        for _ in range(10000):
            game.assert_invariants()
            if game.winner is not None:
                break
            game.apply_action(choose_action(game.view_for(game.current), rng))
        assert game.winner is not None, seed
        replay = replay_game(game.replay())
        assert replay.view_for(0) == game.view_for(0)


@pytest.mark.parametrize("mode", ["normal", "penalty", "roulette", "ai"])
def test_mercy_36(mode):
    game = new_game(GameConfig(("A", "B", "C")), 42)
    while len(game.hands[0]) < 35:
        game.hands[0].append(game.deck.pop())
    if mode == "penalty":
        game.pending, game.last_draw = 20, 10
    if mode == "roulette":
        game.phase = "roulette"
    assert not game.eliminated[0]
    action = {"player_id": 0, "type": "draw"}
    if mode == "ai":
        game.phase = "roulette"  # forced draw goes through the AI's common entry point
        action = choose_action(game.view_for(0), random.Random(2))
    game.apply_action(action)
    assert game.eliminated[0] and game.hands[0] == []
    assert game.current == 1 and game.pending == 0
    assert game.last_events[0]["type"] == "draw"
    assert game.last_events[1]["type"] == "mercy"
    game.assert_invariants()


def test_stack_requires_matching_color_equal_amount_or_wild():
    game = scenario([[('blue', 'draw 4'), ('red', 'draw 4'), ('blue', 'draw 2'),
                      ('wild', 'wild draw 6'), ('red', '1')], [('yellow', '4')]])
    game.pending, game.last_draw = 2, 2
    legal = {a.get('card_id') for a in game.legal_actions(0)}
    assert game.hands[0][0] not in legal
    assert set(game.hands[0][1:4]) <= legal
    game.apply_action({'player_id': 0, 'type': 'draw'})
    assert game.pending == 1 and game.penalty_started
    assert not any(a['type'] == 'play' for a in game.legal_actions(0))
    game.apply_action({'player_id': 0, 'type': 'draw'})
    assert game.current == 1 and game.pending == 0


@pytest.mark.parametrize('count, expected', [(2, 0), (3, 2), (4, 3)])
def test_reverse_draw_four(count, expected):
    hands = [[('wild', 'wild reverse draw 4'), ('red', '1')]] + [[('blue', str(i))] for i in range(count - 1)]
    game = scenario(hands)
    play(game, 'wild reverse draw 4')
    assert game.phase == 'choose_color'
    game.apply_action({'player_id': 0, 'type': 'choose_color', 'color': 'green'})
    assert game.direction == -1 and game.current == expected and game.pending == 4


def test_last_seven_wins_before_swap_and_last_wild_before_penalty():
    for card in [('red', '7'), ('wild', 'wild draw 10')]:
        game = scenario([[card], [('red', '3'), ('wild', 'wild draw 6')]])
        play(game, card[1])
        assert game.winner == 0 and game.phase == 'finished'
        assert game.pending == 0 and game.scores[0] == 53


def test_swap_and_rotation():
    game = scenario([[('red', '7'), ('red', '1')], [('blue', '2')], [('green', '3')]])
    play(game, '7')
    assert game.phase == 'choose_player'
    game.apply_action({'player_id': 0, 'type': 'choose_player', 'target': 2})
    assert BY_ID[game.hands[0][0]].value == '3' and game.current == 1
    game = scenario([[('red', '0'), ('red', '1')], [('blue', '2')], [('green', '3')]])
    play(game, '0')
    assert [BY_ID[h[0]].value for h in game.hands] == ['3', '1', '2']


def test_discard_all_stays_on_top_and_can_win():
    game = scenario([[('red', 'discard all'), ('red', '1'), ('red', '2')], [('blue', '3')]])
    play(game, 'discard all')
    assert game.winner == 0
    assert BY_ID[game.discard[-1]].value == 'discard all'


def test_roulette_chooser_and_public_draw():
    game = scenario([[('wild', 'wild colour roulette'), ('red', '1')], [('blue', '2')]])
    play(game, 'wild colour roulette')
    assert game.current == 1 and game.phase == 'choose_color'
    game.apply_action({'player_id': 1, 'type': 'choose_color', 'color': 'green'})
    assert game.phase == 'roulette'
    green = next(i for i in game.deck if BY_ID[i].color == 'green')
    game.deck.remove(green)
    game.deck.append(green)
    game.apply_action({'player_id': 1, 'type': 'draw'})
    assert game.current == 0
    assert game.view_for(0)['events'][0]['card']['id'] == green


def test_hidden_draw_is_not_sent_to_other_player():
    game = new_game(GameConfig(), 12)
    game.apply_action({'player_id': 0, 'type': 'draw'})
    assert 'card' in game.view_for(0)['events'][0]
    assert 'card' not in game.view_for(1)['events'][0]
    assert 'deck' not in game.view_for(1) and 'seed' not in game.view_for(1)


def test_invalid_move_leaves_state_unchanged():
    game = new_game(GameConfig(), 12)
    before = game.replay().copy()
    with pytest.raises(RuleError):
        game.apply_action({'player_id': 1, 'type': 'draw'})
    with pytest.raises(RuleError):
        game.apply_action({'player_id': 0, 'type': 'play', 'card_id': True})
    assert game.replay() == before and game.revision == 0


def test_recycling_keeps_top_and_conserves_cards():
    game = new_game(GameConfig(), 12)
    game.discard[0:0] = game.deck
    game.deck = []
    top = game.discard[-1]
    game.apply_action({'player_id': 0, 'type': 'draw'})
    assert game.discard == [top]
    game.assert_invariants()


def test_35_cards_survive():
    game = new_game(GameConfig(), 42)
    while len(game.hands[0]) < 34:
        game.hands[0].append(game.deck.pop())
    game.apply_action({"player_id": 0, "type": "draw"})
    assert len(game.hands[0]) == 35 and not game.eliminated[0]
    game.assert_invariants()
