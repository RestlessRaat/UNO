from copy import deepcopy
import random

import pytest

from uno.ai import DIFFICULTIES, choose_action
from uno.engine import BY_ID, GameConfig, new_game


def position(hands, top=("red", "5"), direction=1):
    game = new_game(GameConfig(tuple(f"P{i}" for i in range(len(hands)))), 123)
    available = list(BY_ID)

    def take(spec):
        card_id = next(i for i in available if (BY_ID[i].color, BY_ID[i].value) == spec)
        available.remove(card_id)
        return card_id

    game.discard = [take(top)]
    game.hands = [[take(spec) for spec in hand] for hand in hands]
    game.deck = available
    game.color, game.direction = top[0], direction
    game.last_events = []
    return game


def hard(game, seed=0):
    return choose_action(game.view_for(game.current), random.Random(seed), "hard")


def chosen_value(game):
    action = hard(game)
    return BY_ID[action["card_id"]].value if action["type"] == "play" else action["type"]


def original_policy(view, rng):
    """Frozen legacy policy: protects saved-seed normal-game behaviour."""
    actions = view["legal"]
    if not actions:
        return None
    plays = [a for a in actions if a["type"] == "play"]
    if plays:
        chosen = rng.choice(plays)
    elif actions[0]["type"] == "choose_color":
        colors = {c["color"] for c in view["hand"] if c["color"] != "wild"}
        chosen = rng.choice([a for a in actions if a["color"] in colors] or actions)
    else:
        chosen = rng.choice(actions)
    return {"player_id": view["you"], **chosen}


def test_normal_preserves_legacy_actions_and_rng_state():
    for seed in range(8):
        game = new_game(GameConfig(), seed)
        expected_rng, default_rng, normal_rng = (random.Random(seed) for _ in range(3))
        for _ in range(2000):
            view = game.view_for(game.current)
            expected = original_policy(view, expected_rng)
            assert choose_action(view, default_rng) == expected
            assert choose_action(view, normal_rng, "normal") == expected
            assert default_rng.getstate() == normal_rng.getstate() == expected_rng.getstate()
            if expected is None:
                break
            game.apply_action(expected)
        assert game.winner is not None


def test_difficulties_validation_and_empty_legal_actions():
    assert DIFFICULTIES == ("normal", "hard", "devil", "god")
    with pytest.raises(ValueError, match="difficulty"):
        choose_action({"legal": []}, random.Random(0), "impossible")
    for difficulty in ("normal", "hard", "devil"):
        assert choose_action({"legal": []}, random.Random(0), difficulty) is None


def test_discard_all_finishes_immediately():
    game = position([[('red', '1'), ('red', 'discard all'), ('red', '2')], [('blue', '3')]])
    assert chosen_value(game) == "discard all"
    game.apply_action(hard(game))
    assert game.winner == 0


@pytest.mark.parametrize("card", [('red', '7'), ('red', '0'), ('wild', 'wild reverse draw 4')])
def test_last_card_wins_before_dangerous_effect(card):
    game = position([[card], [('blue', '3')]])
    game.apply_action(hard(game))
    assert game.winner == 0


@pytest.mark.parametrize("extra_turn,players", [("skip all", 3), ("reverse", 2), ("skip", 2)])
def test_extra_turn_then_last_seven_wins_without_swapping(extra_turn, players):
    game = position([[('red', extra_turn), ('red', '7')]] + [[('blue', '3')]] * (players - 1))
    assert chosen_value(game) == extra_turn
    game.apply_action(hard(game))
    assert game.current == 0
    game.apply_action(hard(game))
    assert game.winner == 0


def test_seven_trades_large_hand_for_smallest_public_count():
    game = position([[('red', '7'), ('red', '1'), ('blue', '3'), ('yellow', '4')],
                     [('green', '6')], [('blue', '4'), ('green', '8'), ('blue', '6')]])
    assert chosen_value(game) == "7"
    game.apply_action(hard(game))
    assert hard(game)["target"] == 1
    game.apply_action(hard(game))
    assert len(game.hands[0]) == 1


def test_seven_does_not_give_away_a_better_hand():
    game = position([[('red', '7'), ('red', '1')], [('blue', str(i)) for i in range(8)]])
    assert chosen_value(game) == "1"


@pytest.mark.parametrize("direction", [1, -1])
def test_zero_receives_hand_from_correct_neighbor(direction):
    small = [('green', '6')]
    large = [('blue', str(i)) for i in range(8)]
    own = [('red', '0'), ('red', '1'), ('yellow', '3'), ('yellow', '4')]
    opponents = [large, small] if direction == 1 else [small, large]
    game = position([own, *opponents], direction=direction)
    assert chosen_value(game) == "0"
    game.apply_action(hard(game))
    assert len(game.hands[0]) == 1
    opposite = position([own, *opponents], direction=-direction)
    assert chosen_value(opposite) != "0"


def test_blocks_next_players_last_card():
    game = position([[('red', '2'), ('red', 'skip'), ('yellow', '3')], [('red', '1')],
                     [('blue', '2'), ('green', '3'), ('blue', '4'), ('blue', '6')]])
    assert chosen_value(game) == "skip"
    game.apply_action(hard(game))
    assert game.current == 2


def test_wild_color_follows_own_largest_group():
    game = position([[('wild', 'wild draw 6'), ('blue', '1'), ('blue', '2'), ('red', '3')], [('green', '4')]])
    game.apply_action({'player_id': 0, 'type': 'play', 'card_id': game.hands[0][0]})
    assert hard(game)["color"] == "blue"


def test_two_player_reverse_draw_four_is_not_an_attack_on_opponent():
    game = position([[('wild', 'wild reverse draw 4'), ('red', '1'), ('blue', '3')], [('red', '2')]])
    assert chosen_value(game) == "1"


def test_reverse_draw_four_can_set_up_own_winning_stack():
    game = position([[('wild', 'wild reverse draw 4'), ('blue', 'draw 4')], [('red', '2')]])
    assert chosen_value(game) == "wild reverse draw 4"
    game.apply_action(hard(game))
    game.apply_action(hard(game))
    assert game.current == 0 and game.pending == 4
    game.apply_action(hard(game))
    assert game.winner == 0


def test_two_active_players_use_duel_rules_after_mercy_eliminations():
    game = position([[('wild', 'wild reverse draw 4'), ('red', '1'), ('blue', '3')],
                     [], [('red', '2')], []])
    game.eliminated = [False, True, False, True]
    game.assert_invariants()
    assert chosen_value(game) == '1'
    game = position([[('red', 'reverse'), ('red', '7')], [], [('blue', '3')], []])
    game.eliminated = [False, True, False, True]
    assert chosen_value(game) == 'reverse'
    game.apply_action(hard(game))
    game.apply_action(hard(game))
    assert game.winner == 0


def test_pending_stack_selects_legal_attack():
    game = position([[('blue', 'draw 4'), ('red', 'draw 4'), ('wild', 'wild draw 10'), ('red', '1')],
                     [('blue', '2')], [('green', '3')]])
    game.pending, game.last_draw = 6, 4
    game.color = 'red'
    assert chosen_value(game) == "wild draw 10"
    game.apply_action(hard(game))
    game.apply_action(hard(game))
    assert game.pending == 16 and game.current == 1


def test_roulette_victim_chooses_most_common_unseen_colour():
    game = position([[('wild', 'wild colour roulette'), ('red', '1')],
                     [('red', '2'), ('red', '3'), ('blue', '4'), ('yellow', '5')]])
    game.apply_action({'player_id': 0, 'type': 'play', 'card_id': game.hands[0][0]})
    assert game.current == 1
    assert hard(game)["color"] == "green"


def test_snapshot_is_not_mutated_and_hidden_cards_do_not_change_choice():
    game = new_game(GameConfig(), 348)
    before = game.view_for(0)
    original = deepcopy(before)
    choice = choose_action(before, random.Random(11), "hard")
    assert before == original
    # Deal different hidden cards while keeping all publicly visible facts equal.
    game.hands[1][0], game.deck[-1] = game.deck[-1], game.hands[1][0]
    assert game.view_for(0) == before
    assert choose_action(game.view_for(0), random.Random(11), "hard") == choice


@pytest.mark.parametrize("players", [2, 3, 4])
def test_complete_hard_and_mixed_games_are_legal_and_reproducible(players):
    for seed in range(12):
        game = new_game(GameConfig(tuple(f"P{i}" for i in range(players))), seed + 500)
        rng = random.Random(seed)
        for _ in range(10000):
            if game.winner is not None:
                break
            view = game.view_for(game.current)
            difficulty = "hard" if seed % 2 == 0 or game.current == seed % players else "normal"
            state = rng.getstate()
            action = choose_action(view, rng, difficulty)
            repeated = random.Random()
            repeated.setstate(state)
            assert choose_action(view, repeated, difficulty) == action
            assert {k: v for k, v in action.items() if k != 'player_id'} in view["legal"]
            game.apply_action(action)
        assert game.winner is not None, (players, seed)
        game.assert_invariants()
