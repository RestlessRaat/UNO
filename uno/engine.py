"""Deterministic No Mercy rules, independent of graphics and networking.

The supplied SB3 is the rules reference. The intentional rule change is the
inclusive 36-card mercy limit. Card IDs are physical cards, not card types.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

COLORS = ("red", "yellow", "green", "blue")
DRAW_VALUES = {"draw 2": 2, "draw 4": 4, "wild reverse draw 4": 4,
               "wild draw 6": 6, "wild draw 10": 10}


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Card:
    id: int
    color: str
    value: str

    @property
    def wild(self):
        return self.color == "wild"

    @property
    def points(self):
        return int(self.value) if self.value.isdigit() else 50 if self.wild else 20

    def public(self):
        return asdict(self)


def make_cards() -> tuple[Card, ...]:
    definitions = []
    for color in COLORS:
        for value in range(10):
            definitions.extend([(color, str(value))] * 2)
        for value, count in (("reverse", 3), ("draw 2", 3), ("skip", 3),
                             ("skip all", 2), ("draw 4", 2), ("discard all", 3)):
            definitions.extend([(color, value)] * count)
    for value, count in (("wild reverse draw 4", 8), ("wild draw 10", 4),
                         ("wild draw 6", 4), ("wild colour roulette", 8)):
        definitions.extend([("wild", value)] * count)
    return tuple(Card(i + 1, *definition) for i, definition in enumerate(definitions))


CARDS = make_cards()
BY_ID = {c.id: c for c in CARDS}


class XorShift16:
    """The SB3's (7, 9, 8) 16-bit shift generator, including seed zero."""
    def __init__(self, seed: int):
        self.state = int(seed) % 65536

    def next(self):
        self.state ^= (self.state << 7) & 65535
        self.state ^= self.state >> 9
        self.state ^= (self.state << 8) & 65535
        return self.state

    def index(self, length):
        return self.next() % length

    def shuffle(self, items):
        source = list(items)
        result = []
        while source:
            result.append(source.pop(self.index(len(source))))
        return result


@dataclass(frozen=True)
class GameConfig:
    names: tuple[str, ...] = ("You", "Ada", "Turing", "Grace")
    mercy_limit: int = 36
    scores: tuple[int, ...] = ()


class Game:
    def __init__(self, config: GameConfig, seed: int):
        if not 2 <= len(config.names) <= 4:
            raise RuleError("A game needs 2 to 4 players.")
        if config.mercy_limit != 36:
            raise RuleError("This edition uses the 36-card mercy rule.")
        self.config = config
        self.seed = int(seed) % 65536
        self.rng = XorShift16(self.seed)
        self.deck = self.rng.shuffle(range(168, 0, -1))
        self.hands: list[list[int]] = [[] for _ in config.names]
        self.eliminated = [False] * len(config.names)
        self.scores = list(config.scores or (0,) * len(config.names))
        if len(self.scores) != len(self.hands):
            raise RuleError("Score count does not match the seats.")
        self.current = 0
        self.direction = 1
        self.phase = "turn"
        self.pending = 0
        self.last_draw = 0
        self.penalty_started = False
        self.color_reason = ""
        self.winner: int | None = None
        self.turn_serial = 1
        self.revision = 0
        self.log: list[dict] = []
        self.events: list[dict] = []
        self.last_events: list[dict] = []
        self.round_points = 0
        self.score_target = len(config.names) * 250
        self.match_winner = None
        self.roulette_revealed: list[int] = []
        for _ in range(7):
            for hand in self.hands:
                hand.append(self.deck.pop())
        # Source scans downwards without discarding/reordering the skipped actions.
        first = next(i for i in range(len(self.deck) - 1, -1, -1)
                     if BY_ID[self.deck[i]].value.isdigit())
        self.discard = [self.deck.pop(first)]
        self.color = BY_ID[self.discard[-1]].color
        self._emit("start", current=self.current)
        self.last_events = list(self.events)

    @property
    def active(self):
        return [i for i, out in enumerate(self.eliminated) if not out]

    def _emit(self, kind, **data):
        # Remember only faces already revealed to everyone, never mercy hands.
        if kind in ("start", "shuffle"):
            self.known_discards = {self.discard[-1]}
        elif kind == "play":
            self.known_discards.add(data["card"]["id"])
        elif kind == "discard_all":
            self.known_discards.update(card["id"] for card in data["cards"])
        elif kind == "mercy" and self.events and self.events[-1].get("revealed"):
            self.known_discards.update(self.roulette_revealed)
        self.events.append({"type": kind, **data})

    def _next(self, player=None, steps=1):
        player = self.current if player is None else player
        for _ in range(steps):
            player = (player + self.direction) % len(self.hands)
            while self.eliminated[player]:
                player = (player + self.direction) % len(self.hands)
        return player

    def _advance(self, steps=1):
        self.current = self._next(steps=steps)
        self.turn_serial += 1
        self.phase = "turn"
        self.penalty_started = False
        self._emit("turn", player=self.current)

    def _playable(self, card_id):
        c = BY_ID[card_id]
        top = BY_ID[self.discard[-1]]
        if self.phase != "turn":
            return False
        if self.pending:
            amount = DRAW_VALUES.get(c.value, 0)
            return (not self.penalty_started and amount >= self.last_draw
                    and amount > 0 and (c.color == self.color or
                                        amount == self.last_draw or c.wild))
        return (c.color == self.color or c.value == top.value or c.wild or
                DRAW_VALUES.get(c.value, 0) > 0 and
                DRAW_VALUES.get(c.value) == DRAW_VALUES.get(top.value))

    def legal_actions(self, player_id: int) -> list[dict]:
        if self.winner is not None or player_id != self.current or self.eliminated[player_id]:
            return []
        if self.phase == "choose_color":
            return [{"type": "choose_color", "color": c} for c in COLORS]
        if self.phase == "choose_player":
            return [{"type": "choose_player", "target": i} for i in self.active if i != player_id]
        if self.phase == "roulette":
            return [{"type": "draw"}] if self.deck or len(self.discard) > 1 else [{"type": "end_turn"}]
        actions = [{"type": "play", "card_id": i} for i in self.hands[player_id] if self._playable(i)]
        if self.deck or len(self.discard) > 1:
            # SB3 permits voluntarily drawing even with a playable card, repeatedly.
            actions.append({"type": "draw"})
        if not actions:
            actions.append({"type": "end_turn"})
        return actions

    def apply_action(self, action: dict[str, Any]) -> list[dict]:
        if not isinstance(action, dict):
            raise RuleError("Invalid action.")
        player = action.get("player_id")
        if type(player) is not int or not 0 <= player < len(self.hands):
            raise RuleError("Invalid player.")
        kind = action.get("type")
        allowed = self.legal_actions(player)
        keys = {"play": ("card_id",), "draw": (), "end_turn": (),
                "choose_color": ("color",), "choose_player": ("target",)}
        if kind not in keys:
            raise RuleError("Unknown action.")
        candidate = {"type": kind, **{key: action.get(key) for key in keys[kind]}}
        if any(type(candidate.get(key)) is not int for key in ("card_id", "target") if key in candidate):
            raise RuleError("Invalid card or player.")
        if candidate not in allowed:
            raise RuleError("That move isn't legal now.")
        self.events = []
        if kind == "play":
            self._play(candidate["card_id"])
        elif kind == "draw":
            self._draw()
        elif kind == "choose_color":
            self._choose_color(candidate["color"])
        elif kind == "choose_player":
            other = candidate["target"]
            self.hands[player], self.hands[other] = self.hands[other], self.hands[player]
            self._emit("swap", player=player, target=other)
            self._uno(player)
            self._uno(other)
            self._advance()
        elif kind == "end_turn":
            self.pending = self.last_draw = 0
            self._advance()
        self.revision += 1
        self.log.append({"player_id": player, **candidate})
        self.last_events = list(self.events)
        self.assert_invariants()
        return list(self.events)

    def _play(self, card_id):
        player = self.current
        c = BY_ID[card_id]
        hand_index = self.hands[player].index(card_id)
        self.hands[player].remove(card_id)
        self.discard.append(card_id)
        self.roulette_revealed = []
        if not c.wild:
            self.color = c.color
        self._emit("play", player=player, card=c.public(), hand_index=hand_index)
        self._uno(player)
        # Source play.game tests a zero hand BEFORE prepare-for-next-turn effects.
        if not self.hands[player]:
            self._finish(player)
            return
        amount = DRAW_VALUES.get(c.value, 0)
        if amount:
            self.pending += amount
            self.last_draw = amount
            self.penalty_started = False
            if c.wild:
                self.phase, self.color_reason = "choose_color", c.value
            else:
                self._advance()
        elif c.value == "wild colour roulette":
            self.pending = self.last_draw = 0
            self._advance()
            self.phase, self.color_reason = "choose_color", "roulette"
        elif c.value == "reverse":
            self.direction *= -1
            self._emit("reverse", direction=self.direction)
            self._advance(0 if len(self.active) == 2 else 1)
        elif c.value == "skip":
            self._advance(2)
        elif c.value == "skip all":
            self._advance(0)
        elif c.value == "discard all":
            removed = [i for i in self.hands[player] if BY_ID[i].color == c.color]
            hand_indices = [self.hands[player].index(i) for i in removed]
            self.hands[player] = [i for i in self.hands[player] if i not in removed]
            self.discard[-1:-1] = removed
            self._emit("discard_all", player=player, cards=[BY_ID[i].public() for i in removed], hand_indices=hand_indices)
            self._uno(player)
            if not self.hands[player]:
                self._finish(player)
            else:
                self._advance()
        elif c.value == "7":
            self.phase = "choose_player"
        elif c.value == "0":
            old = [list(hand) for hand in self.hands]
            for i in self.active:
                self.hands[self._next(i)] = old[i]
            self._emit("rotate", direction=self.direction)
            for i in self.active:
                self._uno(i)
            self._advance()
        else:
            self._advance()

    def _choose_color(self, color):
        self.color = color
        self._emit("color", color=color, player=self.current)
        reason = self.color_reason
        self.color_reason = ""
        if reason == "roulette":
            self.phase = "roulette"
        else:
            if reason == "wild reverse draw 4":
                self.direction *= -1
                self._emit("reverse", direction=self.direction)
                self._advance(0 if len(self.active) == 2 else 1)
            else:
                self._advance()

    def _recycle(self):
        if not self.deck and len(self.discard) > 1:
            self.deck = self.rng.shuffle(self.discard[:-1])
            self.discard = self.discard[-1:]
            self._emit("shuffle")

    def _draw(self):
        self._recycle()
        player = self.current
        card = self.deck.pop()
        self.hands[player].append(card)
        roulette = self.phase == "roulette"
        if roulette:
            self.roulette_revealed.append(card)
        self._emit("draw", player=player, card=BY_ID[card].public(), revealed=roulette)
        if len(self.hands[player]) >= self.config.mercy_limit:
            self._eliminate(player)
            return
        if roulette:
            if BY_ID[card].color == self.color:
                self._advance()
        elif self.pending:
            self.penalty_started = True
            self.pending -= 1
            if self.pending == 0:
                self.last_draw = 0
                self._advance()
        self._recycle()

    def _eliminate(self, player):
        self.eliminated[player] = True
        # Source puts the eliminated hand underneath the existing discard pile.
        self.discard[0:0] = self.hands[player]
        self.hands[player] = []
        self.pending = self.last_draw = 0
        self.penalty_started = False
        self._emit("mercy", player=player)
        if len(self.active) == 1:
            self._finish(self.active[0])
        else:
            self._advance()

    def _uno(self, player):
        if len(self.hands[player]) == 1:
            self._emit("uno", player=player)

    def _finish(self, player):
        self.winner = player
        self.phase = "finished"
        self.round_points = sum(250 if self.eliminated[i] else sum(BY_ID[c].points for c in h)
                                for i, h in enumerate(self.hands) if i != player)
        self.scores[player] += self.round_points
        if self.scores[player] >= self.score_target:
            self.match_winner = player
        self._emit("win", player=player, points=self.round_points,
                   scoring_hands={str(i): [BY_ID[c].public() for c in h]
                                  for i, h in enumerate(self.hands) if i != player})

    def view_for(self, player_id):
        if type(player_id) is not int or not 0 <= player_id < len(self.hands):
            raise RuleError("Invalid viewer.")
        events = []
        for event in self.last_events:
            visible = dict(event)
            if event["type"] == "draw" and event["player"] != player_id and not event["revealed"]:
                visible.pop("card", None)
            events.append(visible)
        return {"revision": self.revision, "you": player_id, "current": self.current,
                "phase": self.phase, "color": self.color, "direction": self.direction,
                "pending": self.pending, "last_draw": self.last_draw,
                "penalty_started": self.penalty_started, "turn_serial": self.turn_serial,
                "players": [{"id": i, "name": name, "count": len(self.hands[i]),
                             "eliminated": self.eliminated[i], "score": self.scores[i]}
                            for i, name in enumerate(self.config.names)],
                "hand": [BY_ID[i].public() for i in self.hands[player_id]],
                "top": BY_ID[self.discard[-1]].public(), "deck_count": len(self.deck), "discard_count": len(self.discard),
                "known_discards": sorted(self.known_discards.intersection(self.discard)),
                "winner": self.winner, "round_points": self.round_points,
                "match_winner": self.match_winner, "score_target": self.score_target,
                "mercy_limit": self.config.mercy_limit,
                "legal": self.legal_actions(player_id), "events": events,
                "roulette_revealed": [BY_ID[i].public() for i in self.roulette_revealed]}

    def replay(self):
        return {"version": 1, "config": asdict(self.config), "seed": self.seed, "actions": self.log}

    def assert_invariants(self):
        all_ids = self.deck + self.discard + [c for h in self.hands for c in h]
        assert len(all_ids) == 168 and set(all_ids) == set(BY_ID), "Card conservation failed"
        assert self.discard
        assert self.winner is not None or not self.eliminated[self.current]
        assert all(not self.hands[i] for i in range(len(self.hands)) if self.eliminated[i])
        assert all(len(h) < self.config.mercy_limit for h in self.hands)


def new_game(config: GameConfig | dict, seed: int) -> Game:
    return Game(config if isinstance(config, GameConfig) else GameConfig(**config), seed)


def replay_game(data):
    if data.get("version") != 1:
        raise RuleError("Unsupported replay version.")
    game = new_game(data["config"], data["seed"])
    for action in data["actions"]:
        game.apply_action(action)
    return game
