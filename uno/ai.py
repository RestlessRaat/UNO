"""AI policies; only God receives an explicit private omniscient snapshot."""
from collections import Counter
import random

from .engine import CARDS, DRAW_VALUES


DIFFICULTIES = ("normal", "hard", "devil", "god")
_WIN = 100_000.0
_LOOKAHEAD = 3  # Extra turns only: opponents' unknown hands are never simulated.


def choose_action(view: dict, rng: random.Random, difficulty: str = "normal") -> dict | None:
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"Unknown AI difficulty: {difficulty!r}")
    actions = view["legal"]
    if not actions:
        return None
    if difficulty == "god":
        if len(actions) == 1:
            return {"player_id": view["you"], **actions[0]}
        from .god import choose_god
        return {"player_id": view["you"], **choose_god(view, rng)}
    if difficulty == "devil":
        from .devil import choose_devil
        return {"player_id": view["you"], **choose_devil(view, rng)}
    if difficulty == "hard":
        scores = _HardPolicy(view).scores(actions)
        best = max(scores)
        chosen = rng.choice([a for a, score in zip(actions, scores) if abs(score - best) < 1e-9])
    else:
        # Keep the original random policy, including its RNG consumption.
        plays = [a for a in actions if a["type"] == "play"]
        if plays:
            chosen = rng.choice(plays)
        elif actions[0]["type"] == "choose_color":
            colors = {c["color"] for c in view["hand"] if c["color"] != "wild"}
            choices = [a for a in actions if a["color"] in colors] or actions
            chosen = rng.choice(choices)
        else:
            chosen = rng.choice(actions)
    return {"player_id": view["you"], **chosen}


def _playable(card, color, top_value, pending=0, last_draw=0):
    amount = DRAW_VALUES.get(card["value"], 0)
    wild = card["color"] == "wild"
    if pending:
        return amount > 0 and amount >= last_draw and (card["color"] == color or amount == last_draw or wild)
    return (wild or card["color"] == color or card["value"] == top_value
            or amount > 0 and amount == DRAW_VALUES.get(top_value))


class _HardPolicy:
    def __init__(self, view):
        self.view = view
        self.you = view["you"]
        self.active = tuple(p["id"] for p in view["players"] if not p["eliminated"])
        self.counts = {p["id"]: p["count"] for p in view["players"] if not p["eliminated"]}
        self.hand = tuple(view["hand"])
        self.direction = view["direction"]
        self.mercy = view["mercy_limit"]

    def next(self, direction, steps=1, player=None):
        index = self.active.index(self.you if player is None else player)
        return self.active[(index + direction * steps) % len(self.active)]

    def value(self, hand, count, counts, next_player, known_next=None, color=None, top=None):
        if count >= self.mercy:
            return -_WIN
        score = -10 * count + 12 / (count + 1)
        if hand is not None:
            colors = Counter(c["color"] for c in hand if c["color"] != "wild")
            # Concentrated colours and flexible wilds make the remaining hand easier.
            score += 2 * max(colors.values(), default=0) + 2.5 * sum(c["color"] == "wild" for c in hand)
        else:
            score += count  # Average flexibility; exchanged faces remain unknown.
        for player, size in counts.items():
            if player == self.you:
                continue
            score -= 18 / max(size, 1)
        if next_player != self.you:
            size = counts[next_player]
            danger = 45 / max(size, 1) ** 2
            if known_next is not None:
                # A passed hand is ours to know, including whether its last card wins.
                danger *= 2 if any(_playable(c, color, top) for c in known_next) else 0
            score -= danger
        return score

    def swap_score(self, target, hand, counts, direction, color, top):
        updated = dict(counts)
        updated[self.you], updated[target] = counts[target], len(hand)
        next_player = self.next(direction)
        return self.value(None, updated[self.you], updated, next_player,
                          hand if next_player == target else None, color, top)

    def score_card(self, card, hand, counts, direction, pending, depth):
        remaining = tuple(c for c in hand if c["id"] != card["id"])
        value, color = card["value"], card["color"]
        if value == "discard all":
            remaining = tuple(c for c in remaining if c["color"] != color)
        # The engine checks victory BEFORE effects, including sevens and wilds.
        if not remaining:
            return _WIN + depth
        updated = dict(counts)
        updated[self.you] = len(remaining)
        if value == "7":
            return max(self.swap_score(p, remaining, updated, direction, color, value)
                       for p in self.active if p != self.you)
        if value == "0":
            rotated = {self.next(direction, player=p): size for p, size in updated.items()}
            return self.value(None, rotated[self.you], rotated, self.next(direction), remaining, color, value)
        next_player = self.next(direction)
        if value in ("reverse", "wild reverse draw 4"):
            direction *= -1
            next_player = self.you if len(self.active) == 2 else self.next(direction)
        elif value == "skip":
            next_player = self.next(direction, 2)
        elif value == "skip all":
            next_player = self.you
        amount = DRAW_VALUES.get(value, 0)
        if next_player == self.you:
            # Bound search to our guaranteed extra turns; no guessed opponent cards.
            total = pending + amount if amount else 0
            options = [c for c in remaining if _playable(c, color, value, total, amount)]
            if color == "wild":
                # We choose the colour before playing a legal coloured stack.
                options = [c for c in remaining if DRAW_VALUES.get(c["value"], 0) >= amount]
            fallback_count = len(remaining) + (total if total else 1)
            fallback = self.value(None, fallback_count, updated, self.next(direction))
            if depth and options:
                return max(fallback, max(self.score_card(c, remaining, updated, direction, total, depth - 1)
                                         for c in self.unique(options)))
            return fallback if total else self.value(remaining, len(remaining), updated, self.you) + 3
        attack = pending + amount if amount else 0
        if value == "wild colour roulette":
            attack = 3  # Victim chooses its colour; an average estimate, not deck knowledge.
        pressure = 0
        if attack:
            target_count = updated[next_player]
            # Stacks can return: expected pressure is deliberately below guaranteed damage.
            updated[next_player] += attack * 0.65
            pressure = attack * 0.7
            if target_count + attack >= self.mercy:
                pressure += 20
        return self.value(remaining, len(remaining), updated, next_player) + pressure

    @staticmethod
    def unique(cards):
        seen = set()
        for card in cards:
            key = card["color"], card["value"]
            if key not in seen:
                seen.add(key)
                yield card

    def color_score(self, color):
        top = self.view["top"]["value"]
        if top == "wild colour roulette":
            # Roulette belongs to the victim. More unseen cards in this colour means
            # a shorter expected draw. The unseen pool is not the hidden draw pile.
            known = {c["id"]: c for c in (*self.hand, self.view["top"], *self.view.get("roulette_revealed", []))}
            return sum(c.color == color for c in CARDS) - sum(c["color"] == color for c in known.values())
        if top == "wild reverse draw 4" and len(self.active) == 2:
            choices = [c for c in self.hand if _playable(c, color, top, self.view["pending"], 4)]
            if choices:
                return max(self.score_card(c, self.hand, self.counts, -self.direction,
                                           self.view["pending"], _LOOKAHEAD) for c in self.unique(choices))
            return -_WIN
        matching = [c for c in self.hand if c["color"] == color]
        return 4 * len(matching) + 2 * sum(c["value"] == "discard all" for c in matching)

    def scores(self, actions):
        cards = {c["id"]: c for c in self.hand}
        play_scores = {}
        results = []
        for action in actions:
            kind = action["type"]
            if kind == "play":
                card = cards[action["card_id"]]
                key = card["color"], card["value"]
                if key not in play_scores:
                    play_scores[key] = self.score_card(card, self.hand, self.counts,
                                                      self.direction, self.view["pending"], _LOOKAHEAD)
                score = play_scores[key]
            elif kind == "choose_color":
                score = self.color_score(action["color"])
            elif kind == "choose_player":
                score = self.swap_score(action["target"], self.hand, self.counts, self.direction,
                                        self.view["color"], self.view["top"]["value"])
            else:
                penalty = self.view["pending"]
                score = self.value(None, len(self.hand) + (penalty or 1), self.counts, self.next(self.direction)) - 3
            results.append(score)
        return results


def decision_view(game, difficulty):
    """Private host-side AI input; never send this snapshot to clients."""
    view = game.view_for(game.current)
    if difficulty == "devil":
        # Opaque identity is host-local and reveals no game information.  It
        # only lets Devil retain its own belief model between decisions.
        view["_devil_key"] = (id(game), game.current)
    if difficulty == "god" and len(view["legal"]) > 1:
        from .god import GodSimulation
        view["_god_state"] = GodSimulation.from_game(game)
    return view
