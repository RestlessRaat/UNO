"""Application services shared by the local UI and authoritative LAN host."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import logging
import os
import hashlib
import json
from pathlib import Path
import random
import time
from typing import Any, Mapping, Protocol

from .ai import choose_action as choose_legacy_action, decision_view
from .ai_plugins import (AiRegistry, DEFAULT_PLUGIN_ID, PluginError, build_context,
                         legacy_name, normalize_plugin_id, run_policy)
from .engine import Game, GameConfig, new_game


log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HumanController:
    kind: str = "human"


@dataclass(frozen=True, slots=True)
class AiController:
    plugin_id: str = DEFAULT_PLUGIN_ID
    settings: Mapping[str, Any] = field(default_factory=dict)
    kind: str = "ai"


@dataclass(frozen=True, slots=True)
class DisconnectedController:
    kind: str = "disconnected"


SeatController = HumanController | AiController | DisconnectedController


@dataclass(frozen=True, slots=True)
class AiFailure:
    plugin_id: str
    version: str
    player_id: int
    revision: int
    reason: str


@dataclass(slots=True)
class _Pending:
    game: Game
    revision: int
    player_id: int
    plugin_id: str
    seed: int
    started: float
    budget_ms: int
    future: Future


class TurnPacing(Protocol):
    def opening_delay(self, view: Mapping[str, Any]) -> float:
        ...

    def action_delay(self, before: Mapping[str, Any], after: Mapping[str, Any]) -> float:
        ...


@dataclass(frozen=True, slots=True)
class FixedTurnPacing:
    opening_seconds: float = 2.5
    action_seconds: float = 0.0

    def opening_delay(self, _view: Mapping[str, Any]) -> float:
        return self.opening_seconds

    def action_delay(self, _before: Mapping[str, Any], _after: Mapping[str, Any]) -> float:
        return self.action_seconds


class AiRunner:
    """Own policy instances, run decisions off-thread, and reject stale results."""

    def __init__(self, registry: AiRegistry, *, max_workers: int = 4,
                 executor: ThreadPoolExecutor | None = None):
        self.registry = registry
        self.executor = executor or ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="UnoAI")
        self._owns_executor = executor is None
        self._instances: dict[tuple[int, int, str], object] = {}
        self._failed: set[tuple[int, int]] = set()
        self._pending: _Pending | None = None
        self.failures: list[AiFailure] = []

    @property
    def pending(self) -> bool:
        return self._pending is not None

    def effective_plugin(self, game: Game, player_id: int, requested: str) -> str:
        if (id(game), player_id) in self._failed:
            return DEFAULT_PLUGIN_ID
        requested = normalize_plugin_id(requested)
        return requested if self.registry.has(requested) else DEFAULT_PLUGIN_ID

    def tick(self, game: Game, player_id: int, controller: AiController | DisconnectedController,
             random_seed: int) -> dict[str, Any] | None:
        requested = DEFAULT_PLUGIN_ID if isinstance(controller, DisconnectedController) else controller.plugin_id
        settings = {} if isinstance(controller, DisconnectedController) else controller.settings
        plugin_id = self.effective_plugin(game, player_id, requested)
        if self._pending is not None:
            pending = self._pending
            if pending.game is not game or pending.revision != game.revision or pending.player_id != player_id:
                self.cancel_pending()
                return None
            if not pending.future.done():
                if (time.monotonic() - pending.started) * 1000 <= pending.budget_ms:
                    return None
                pending.future.cancel()
                self._pending = None
                return self._fallback(game, player_id, pending.plugin_id, pending.seed,
                                      f"decision exceeded {pending.budget_ms} ms")
            self._pending = None
            try:
                action = pending.future.result()
                if pending.game is not game or pending.revision != game.revision:
                    return None
                if action not in game.legal_actions(player_id):
                    raise PluginError("AI returned an action that is not legal now.")
                return {"player_id": player_id, **action}
            except Exception as exc:
                return self._fallback(game, player_id, pending.plugin_id, pending.seed, str(exc))

        spec = self.registry.spec(plugin_id)
        try:
            difficulty = legacy_name(plugin_id)
            if difficulty is not None:
                private_view = decision_view(game, difficulty)
                future = self.executor.submit(_run_builtin_legacy, private_view,
                                              random_seed, difficulty)
            elif spec.omniscient:
                private_view = decision_view(game, "god")
                future = self.executor.submit(_run_builtin_god, private_view, random_seed)
            else:
                normalized = self.registry.normalize_settings(plugin_id, settings)
                key = (id(game), player_id, plugin_id)
                policy = self._instances.get(key)
                if policy is None:
                    policy = self.registry.create(plugin_id, normalized)
                    self._instances[key] = policy
                context = build_context(game.view_for(player_id), random_seed, spec.time_budget_ms)
                future = self.executor.submit(run_policy, policy, context)
            self._pending = _Pending(game, game.revision, player_id, plugin_id, random_seed,
                                     time.monotonic(), spec.time_budget_ms, future)
        except Exception as exc:
            return self._fallback(game, player_id, plugin_id, random_seed, str(exc))
        return None

    def choose_blocking(self, game: Game, player_id: int,
                        controller: AiController | DisconnectedController,
                        random_seed: int) -> dict[str, Any]:
        """Blocking adapter for event loops that run it through ``to_thread``."""
        requested = DEFAULT_PLUGIN_ID if isinstance(controller, DisconnectedController) else controller.plugin_id
        settings = {} if isinstance(controller, DisconnectedController) else controller.settings
        plugin_id = self.effective_plugin(game, player_id, requested)
        spec = self.registry.spec(plugin_id)
        try:
            difficulty = legacy_name(plugin_id)
            if difficulty is not None:
                action = _run_builtin_legacy(decision_view(game, difficulty),
                                             random_seed, difficulty)
            elif spec.omniscient:
                action = _run_builtin_god(decision_view(game, "god"), random_seed)
            else:
                normalized = self.registry.normalize_settings(plugin_id, settings)
                key = (id(game), player_id, plugin_id)
                policy = self._instances.get(key)
                if policy is None:
                    policy = self.registry.create(plugin_id, normalized)
                    self._instances[key] = policy
                context = build_context(game.view_for(player_id), random_seed, spec.time_budget_ms)
                action = run_policy(policy, context)
            if action not in game.legal_actions(player_id):
                raise PluginError("AI returned an action that is not legal now.")
            return {"player_id": player_id, **action}
        except Exception as exc:
            return self._fallback(game, player_id, plugin_id, random_seed, str(exc))

    def _fallback(self, game: Game, player_id: int, plugin_id: str, seed: int, reason: str):
        spec = self.registry.spec(plugin_id)
        self._failed.add((id(game), player_id))
        self._instances = {key: value for key, value in self._instances.items()
                           if key[:2] != (id(game), player_id)}
        failure = AiFailure(plugin_id, spec.version, player_id, game.revision,
                            reason or "unknown plugin failure")
        self.failures.append(failure)
        log.warning("AI plugin failure: %s", failure)
        result = choose_legacy_action(game.view_for(player_id), random.Random(seed), "normal")
        return result

    def pop_failures(self) -> list[AiFailure]:
        result, self.failures = self.failures, []
        return result

    def cancel_pending(self):
        if self._pending is not None:
            self._pending.future.cancel()
            self._pending = None

    def reset(self):
        self.cancel_pending()
        self._instances.clear()
        self._failed.clear()
        self.failures.clear()

    def close(self):
        self.reset()
        if self._owns_executor:
            self.executor.shutdown(wait=False, cancel_futures=True)


def _run_builtin_god(view: dict[str, Any], seed: int) -> dict[str, Any]:
    result = choose_legacy_action(view, random.Random(seed), "god")
    if result is None:
        raise PluginError("God returned no action.")
    return {key: value for key, value in result.items() if key != "player_id"}


def _run_builtin_legacy(view: dict[str, Any], seed: int, difficulty: str) -> dict[str, Any]:
    result = choose_legacy_action(view, random.Random(seed), difficulty)
    if result is None:
        raise PluginError(f"{difficulty.title()} returned no action.")
    return {key: value for key, value in result.items() if key != "player_id"}


class GameSession:
    """Authoritative application boundary around a rules-engine game."""

    def __init__(self, game: Game, controllers: Mapping[int, SeatController] | None = None,
                 seat_ids: tuple[int, ...] | None = None):
        self.game = game
        self.seat_ids = seat_ids or tuple(range(len(game.hands)))
        if len(self.seat_ids) != len(game.hands):
            raise ValueError("Seat mapping does not match the game.")
        self.controllers = dict(controllers or {seat: HumanController() for seat in self.seat_ids})

    @classmethod
    def create(cls, config: GameConfig, seed: int, controllers: Mapping[int, SeatController] | None = None,
               seat_ids: tuple[int, ...] | None = None) -> "GameSession":
        return cls(new_game(config, seed), controllers, seat_ids)

    @property
    def current_seat_id(self) -> int:
        return self.seat_ids[self.game.current]

    def engine_player(self, seat_id: int) -> int:
        return self.seat_ids.index(seat_id)

    def controller(self, seat_id: int | None = None) -> SeatController:
        seat_id = self.current_seat_id if seat_id is None else seat_id
        return self.controllers.get(seat_id, HumanController())

    def set_controller(self, seat_id: int, controller: SeatController):
        if seat_id not in self.seat_ids:
            raise ValueError("Unknown seat.")
        self.controllers[seat_id] = controller

    def view_for_seat(self, seat_id: int) -> dict[str, Any]:
        return self.game.view_for(self.engine_player(seat_id))

    def submit(self, seat_id: int, action: Mapping[str, Any]):
        return self.game.apply_action({**dict(action), "player_id": self.engine_player(seat_id)})

    def replay(self, plugin_metadata: Mapping[int, Mapping[str, Any]] | None = None,
               ai_failures: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        old = self.game.replay()
        return {"version": 2, "config": old["config"], "seed": old["seed"],
                "actions": old["actions"],
                "participants": {str(key): dict(value) for key, value in (plugin_metadata or {}).items()},
                "ai_failures": [dict(value) for value in (ai_failures or [])]}


def settings_fingerprint(settings: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(settings), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf8")).hexdigest()


def plugin_directory(user_directory: Path) -> Path:
    return Path(os.environ.get("UNO_PLUGIN_DIR") or Path(user_directory) / "plugins")


__all__ = ["AiRunner", "AiFailure", "GameSession", "HumanController", "AiController",
           "DisconnectedController", "SeatController", "TurnPacing", "FixedTurnPacing",
           "plugin_directory", "settings_fingerprint"]
