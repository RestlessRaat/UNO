import random
from copy import deepcopy
import pytest
from uno.ai import decision_view, choose_action
from uno.engine import new_game, GameConfig
from uno.god import GodSimulation


def test_private_snapshot_and_exact_deck():
    game = new_game(GameConfig(), 51)
    ordinary = game.view_for(0)
    view = decision_view(game, "god")
    sim = view["_god_state"]
    assert sim.hands == game.hands and sim.deck == game.deck and sim.discard == game.discard
    assert "_god_state" not in ordinary
    for mode in ("normal", "hard", "devil"):
        assert "_god_state" not in decision_view(game, mode)
    sim.act({"type": "draw"})
    assert sim.hands[0][-1] == game.deck[-1]
    assert game.view_for(0) == ordinary


@pytest.mark.parametrize("players", [2, 3, 4])
def test_exact_simulation_matches_engine(players):
    game = new_game(GameConfig(tuple(str(i) for i in range(players))), 98)
    sim = GodSimulation.from_game(game)
    rng = random.Random(24)
    for _ in range(4000):
        if game.winner is not None:
            break
        action = choose_action(game.view_for(game.current), rng)
        sim.act(action)
        game.apply_action(action)
        for field in ("hands", "deck", "discard", "current", "phase", "pending", "direction", "color", "winner", "eliminated"):
            assert getattr(sim, field) == getattr(game, field)
        assert sim.rng.state == game.rng.state
    assert game.winner is not None


def test_god_search_is_legal_and_does_not_mutate():
    game = new_game(GameConfig(("A", "B")), 31)
    before = deepcopy(game.__dict__)
    action = choose_action(decision_view(game, "god"), random.Random(9), "god")
    assert {k:v for k,v in action.items() if k != "player_id"} in game.legal_actions(0)
    assert game.hands == before["hands"] and game.deck == before["deck"]
    assert game.rng.state == before["rng"].state
    with pytest.raises(ValueError, match="private"):
        choose_action(game.view_for(0), random.Random(9), "god")


@pytest.mark.parametrize("color", ["red", "yellow", "green", "blue"])
def test_roulette_uses_first_actual_nonwild_card(color):
    from uno.engine import BY_ID
    game = new_game(GameConfig(), 22)
    game.phase, game.color_reason = "choose_color", "roulette"
    colored = next(i for i in game.deck if BY_ID[i].color == color)
    wild = next(i for i in game.deck if BY_ID[i].wild)
    game.deck.remove(colored); game.deck.remove(wild)
    game.deck.extend([colored, wild])
    action = choose_action(decision_view(game, "god"), random.Random(0), "god")
    assert action["color"] == color
    game.apply_action(action)
    game.apply_action({"player_id": 0, "type": "draw"})
    game.apply_action({"player_id": 0, "type": "draw"})
    assert game.current == 1 and game.phase == "turn"


def test_roulette_predicts_recycled_deck():
    from uno.engine import BY_ID
    game = new_game(GameConfig(), 87)
    game.discard = game.deck + game.discard
    game.deck = []
    game.phase, game.color_reason = "choose_color", "roulette"
    sim = GodSimulation.from_game(game)
    sim._recycle()
    expected = next(BY_ID[i].color for i in reversed(sim.deck) if not BY_ID[i].wild)
    assert choose_action(decision_view(game, "god"), random.Random(0), "god")["color"] == expected


def test_attack_resolves_entire_penalty_to_force_mercy():
    from uno.engine import BY_ID, DRAW_VALUES
    game = new_game(GameConfig(("A", "B")), 22)
    available = list(BY_ID)
    def take(color, value):
        card = next(i for i in available if (BY_ID[i].color, BY_ID[i].value) == (color, value))
        available.remove(card)
        return card
    game.discard = [take("red", "5")]
    attack = take("wild", "wild draw 10")
    game.hands = [[attack, take("red", "1"), take("blue", "2")], []]
    for i in list(available):
        if BY_ID[i].value not in DRAW_VALUES and len(game.hands[1]) < 33:
            game.hands[1].append(i); available.remove(i)
    game.deck = available
    game.color = "red"
    action = choose_action(decision_view(game, "god"), random.Random(0), "god")
    assert action == {"player_id": 0, "type": "play", "card_id": attack}
    game.apply_action(action)
    action = choose_action(decision_view(game, "god"), random.Random(0), "god")
    game.apply_action(action)
    while game.winner is None:
        game.apply_action({"player_id": 1, "type": "draw"})
    assert game.winner == 0



def test_deck_order_cached_until_live_shuffle():
    from uno.god import _DECK_CACHE
    game = new_game(GameConfig(), 314)
    first = GodSimulation.from_game(game)
    order = first.deck.order
    original = list(game.deck)
    game.apply_action({"player_id": 0, "type": "draw"})
    second = GodSimulation.from_game(game)
    assert second.deck.order is order
    assert second.deck == original[:-1] and first.deck == original
    branch = second.fork()
    assert branch.deck.order is order
    branch.act({"type": "draw"})
    assert branch.deck == original[:-2] and second.deck == original[:-1]
    game.discard = game.deck + game.discard
    game.deck.clear()
    game._recycle()
    shuffled = GodSimulation.from_game(game)
    assert shuffled.deck.order is not order
    assert shuffled.deck == game.deck
    assert _DECK_CACHE[game][0] is game.deck


def test_simulated_shuffle_does_not_invalidate_live_cache():
    game = new_game(GameConfig(), 27)
    sim = GodSimulation.from_game(game)
    order = sim.deck.order
    sim.discard = list(sim.deck) + sim.discard
    while sim.deck:
        sim.deck.pop()
    sim._recycle()
    assert sim.deck.order is not order
    assert GodSimulation.from_game(game).deck.order is order


def test_forced_draw_does_not_request_full_snapshot(monkeypatch):
    game = new_game(GameConfig(), 23)
    game.phase = "roulette"
    def forbidden(*args):
        raise AssertionError("forced draw must not copy or search the game")
    monkeypatch.setattr(GodSimulation, "from_game", forbidden)
    view = decision_view(game, "god")
    assert "_god_state" not in view
    assert choose_action(view, random.Random(1), "god") == {"player_id": 0, "type": "draw"}
