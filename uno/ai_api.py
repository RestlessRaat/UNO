"""Stable, dependency-free API exposed to external UNO AI plugins.

External plugins should import only this module.  The objects passed to a
policy are immutable snapshots; plugins never receive the live rules engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol, TypeAlias, runtime_checkable


API_VERSION = 1
JSONValue: TypeAlias = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]


@dataclass(frozen=True, slots=True)
class CardView:
    id: int
    color: str
    value: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CardView":
        return cls(int(value["id"]), str(value["color"]), str(value["value"]))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "color": self.color, "value": self.value}


@dataclass(frozen=True, slots=True)
class PlayerView:
    id: int
    name: str
    count: int
    eliminated: bool
    score: int

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PlayerView":
        return cls(int(value["id"]), str(value["name"]), int(value["count"]),
                   bool(value["eliminated"]), int(value["score"]))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "count": self.count,
                "eliminated": self.eliminated, "score": self.score}


@dataclass(frozen=True, slots=True)
class GameEvent:
    type: str
    data: tuple[tuple[str, Any], ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GameEvent":
        return cls(str(value["type"]), tuple((str(k), _freeze(v)) for k, v in value.items() if k != "type"))

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **{key: _thaw(value) for key, value in self.data}}


@dataclass(frozen=True, slots=True)
class PlayAction:
    card_id: int
    type: str = "play"


@dataclass(frozen=True, slots=True)
class DrawAction:
    type: str = "draw"


@dataclass(frozen=True, slots=True)
class EndTurnAction:
    type: str = "end_turn"


@dataclass(frozen=True, slots=True)
class ChooseColorAction:
    color: str
    type: str = "choose_color"


@dataclass(frozen=True, slots=True)
class ChoosePlayerAction:
    target: int
    type: str = "choose_player"


Action: TypeAlias = PlayAction | DrawAction | EndTurnAction | ChooseColorAction | ChoosePlayerAction


def action_from_dict(value: Mapping[str, Any]) -> Action:
    """Strictly convert a wire action; actor IDs are intentionally rejected."""
    if not isinstance(value, Mapping) or "player_id" in value:
        raise ValueError("AI actions must not contain player_id.")
    kind = value.get("type")
    if kind == "play" and type(value.get("card_id")) is int and set(value) == {"type", "card_id"}:
        return PlayAction(value["card_id"])
    if kind == "draw" and set(value) == {"type"}:
        return DrawAction()
    if kind == "end_turn" and set(value) == {"type"}:
        return EndTurnAction()
    if kind == "choose_color" and isinstance(value.get("color"), str) and set(value) == {"type", "color"}:
        return ChooseColorAction(value["color"])
    if kind == "choose_player" and type(value.get("target")) is int and set(value) == {"type", "target"}:
        return ChoosePlayerAction(value["target"])
    raise ValueError("Invalid AI action.")


def action_to_dict(action: Action) -> dict[str, Any]:
    if isinstance(action, PlayAction):
        return {"type": "play", "card_id": action.card_id}
    if isinstance(action, DrawAction):
        return {"type": "draw"}
    if isinstance(action, EndTurnAction):
        return {"type": "end_turn"}
    if isinstance(action, ChooseColorAction):
        return {"type": "choose_color", "color": action.color}
    if isinstance(action, ChoosePlayerAction):
        return {"type": "choose_player", "target": action.target}
    raise TypeError("Unknown action object.")


@dataclass(frozen=True, slots=True)
class PublicGameView:
    revision: int
    you: int
    current: int
    phase: str
    color: str
    direction: int
    pending: int
    last_draw: int
    penalty_started: bool
    turn_serial: int
    players: tuple[PlayerView, ...]
    hand: tuple[CardView, ...]
    top: CardView
    deck_count: int
    discard_count: int
    known_discards: tuple[int, ...]
    winner: int | None
    round_points: int
    match_winner: int | None
    score_target: int
    mercy_limit: int
    events: tuple[GameEvent, ...]
    roulette_revealed: tuple[CardView, ...]
    # Additive API v1 extension: older plugins can ignore these immutable fields.
    history: tuple[GameEvent, ...] = ()
    known_opponent_cards: tuple[tuple[int, tuple[int, ...]], ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PublicGameView":
        return cls(
            revision=int(value["revision"]), you=int(value["you"]), current=int(value["current"]),
            phase=str(value["phase"]), color=str(value["color"]), direction=int(value["direction"]),
            pending=int(value["pending"]), last_draw=int(value["last_draw"]),
            penalty_started=bool(value["penalty_started"]), turn_serial=int(value["turn_serial"]),
            players=tuple(PlayerView.from_dict(p) for p in value["players"]),
            hand=tuple(CardView.from_dict(c) for c in value["hand"]),
            top=CardView.from_dict(value["top"]),
            deck_count=int(value["deck_count"]), discard_count=int(value["discard_count"]),
            known_discards=tuple(int(card) for card in value.get("known_discards", ())),
            winner=value.get("winner"), round_points=int(value["round_points"]),
            match_winner=value.get("match_winner"), score_target=int(value["score_target"]),
            mercy_limit=int(value["mercy_limit"]),
            events=tuple(GameEvent.from_dict(event) for event in value.get("events", ())),
            roulette_revealed=tuple(CardView.from_dict(c) for c in value.get("roulette_revealed", ())),
            history=tuple(GameEvent.from_dict(event) for event in value.get("history", ())),
            known_opponent_cards=tuple((int(pid), tuple(int(cid) for cid in cards))
                                      for pid, cards in value.get("known_opponent_cards", {}).items()),
        )

    def to_dict(self, legal_actions: tuple[Action, ...] = ()) -> dict[str, Any]:
        return {
            "revision": self.revision, "you": self.you, "current": self.current,
            "phase": self.phase, "color": self.color, "direction": self.direction,
            "pending": self.pending, "last_draw": self.last_draw,
            "penalty_started": self.penalty_started, "turn_serial": self.turn_serial,
            "players": [p.to_dict() for p in self.players], "hand": [c.to_dict() for c in self.hand],
            "top": self.top.to_dict(), "deck_count": self.deck_count,
            "discard_count": self.discard_count, "known_discards": list(self.known_discards),
            "winner": self.winner, "round_points": self.round_points,
            "match_winner": self.match_winner, "score_target": self.score_target,
            "mercy_limit": self.mercy_limit, "legal": [action_to_dict(a) for a in legal_actions],
            "events": [event.to_dict() for event in self.events],
            "roulette_revealed": [card.to_dict() for card in self.roulette_revealed],
            "history": [event.to_dict() for event in self.history],
            "known_opponent_cards": {str(pid): list(cards) for pid, cards in self.known_opponent_cards},
        }


@dataclass(frozen=True, slots=True)
class DecisionContext:
    api_version: int
    game: PublicGameView
    legal_actions: tuple[Action, ...]
    random_seed: int
    time_budget_ms: int


@runtime_checkable
class Policy(Protocol):
    def choose_action(self, context: DecisionContext) -> Action:
        ...


def readonly_settings(value: Mapping[str, JSONValue]) -> Mapping[str, JSONValue]:
    return MappingProxyType(dict(value))


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((str(k), _freeze(v)) for k, v in value.items())
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, tuple):
        if value and all(isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], str) for v in value):
            return {k: _thaw(v) for k, v in value}
        return [_thaw(v) for v in value]
    return value


__all__ = [
    "API_VERSION", "JSONValue", "Action", "CardView", "PlayerView", "GameEvent",
    "PublicGameView", "DecisionContext", "Policy", "PlayAction", "DrawAction",
    "EndTurnAction", "ChooseColorAction", "ChoosePlayerAction", "action_from_dict",
    "action_to_dict", "readonly_settings",
]
