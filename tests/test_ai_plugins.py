import json
from dataclasses import FrozenInstanceError
from concurrent.futures import Future
from pathlib import Path

import pytest

from uno.ai_api import DrawAction
from uno.ai_plugins import (AiRegistry, DEFAULT_PLUGIN_ID, PluginError,
                            build_context, parse_manifest, run_policy)
from uno.application import AiController, AiRunner, GameSession, HumanController
from uno.engine import GameConfig, new_game, replay_game
from uno.settings import load_ai_seats


def write_plugin(root: Path, *, plugin_id="test.first", policy=None, manifest=None):
    folder = root / plugin_id.replace(".", "-")
    folder.mkdir(parents=True)
    data = manifest or {
        "api_version": 1, "id": plugin_id, "name": "First", "version": "1.0.0",
        "entry_point": "policy:create_policy", "time_budget_ms": 500,
        "settings_schema": [{"key": "mode", "type": "enum", "default": "first",
                             "options": ["first", "last"]}],
    }
    (folder / "plugin.json").write_text(json.dumps(data), encoding="utf8")
    (folder / "policy.py").write_text(policy or """
class Policy:
    def __init__(self, settings): self.settings = settings
    def choose_action(self, context):
        return context.legal_actions[0 if self.settings['mode'] == 'first' else -1]
def create_policy(settings): return Policy(settings)
""", encoding="utf8")
    return folder


def test_public_context_is_immutable_and_has_no_hidden_deck():
    game = new_game(GameConfig(("A", "B")), 12)
    context = build_context(game.view_for(0), 99, 2000)
    with pytest.raises(FrozenInstanceError):
        context.game.current = 1
    assert not hasattr(context.game, "deck")
    assert not hasattr(context.game, "hands")
    assert context.game.hand[0].id == game.hands[0][0]


def test_external_plugin_discovery_configuration_and_decision(tmp_path):
    write_plugin(tmp_path)
    registry = AiRegistry(tmp_path)
    assert registry.has("test.first")
    spec = registry.spec("test.first")
    assert spec.name == "First" and not spec.omniscient
    game = new_game(GameConfig(("A", "B")), 7)
    context = build_context(game.view_for(0), 3, spec.time_budget_ms)
    action = run_policy(registry.create("test.first", {"mode": "last"}), context)
    assert action == game.legal_actions(0)[-1]
    with pytest.raises(PluginError, match="Unknown setting"):
        registry.create("test.first", {"extra": True})


@pytest.mark.parametrize("claim", [{"omniscient": True}, {"trusted": True},
                                     {"information_access": "private"}])
def test_external_manifest_cannot_request_private_state(claim):
    manifest = {"api_version": 1, "id": "test.private", "name": "Private",
                "version": "1", "entry_point": "policy:create_policy", **claim}
    with pytest.raises(PluginError, match="public information"):
        parse_manifest(manifest)


def test_broken_plugins_are_isolated(tmp_path):
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "plugin.json").write_text("{bad", encoding="utf8")
    write_plugin(tmp_path, plugin_id="test.good")
    registry = AiRegistry(tmp_path)
    assert registry.has("test.good") and len(registry.diagnostics) == 1


def test_runner_rejects_illegal_action_and_falls_back_to_normal(tmp_path):
    write_plugin(tmp_path, plugin_id="test.illegal", policy="""
from uno.ai_api import DrawAction
class Policy:
    def choose_action(self, context): return DrawAction() if all(a.type != 'draw' for a in context.legal_actions) else object()
def create_policy(settings): return Policy()
""", manifest={"api_version": 1, "id": "test.illegal", "name": "Illegal",
                 "version": "1", "entry_point": "policy:create_policy"})
    registry = AiRegistry(tmp_path)
    runner = AiRunner(registry)
    game = new_game(GameConfig(("A", "B")), 4)
    action = runner.choose_blocking(game, 0, AiController("test.illegal", {}), 8)
    assert {k: v for k, v in action.items() if k != "player_id"} in game.legal_actions(0)
    assert runner.failures and runner.effective_plugin(game, 0, "test.illegal") == DEFAULT_PLUGIN_ID
    runner.close()


def test_runner_soft_timeout_falls_back_and_ignores_late_result(monkeypatch):
    class Executor:
        def __init__(self):
            self.future = Future()
            self.future.set_running_or_notify_cancel()

        def submit(self, *args, **kwargs):
            return self.future

    now = [10.0]
    monkeypatch.setattr("uno.application.time.monotonic", lambda: now[0])
    registry = AiRegistry(discover=False)
    executor = Executor()
    runner = AiRunner(registry, executor=executor)
    game = new_game(GameConfig(("A", "B")), 5)
    assert runner.tick(game, 0, AiController("builtin.hard", {}), 7) is None
    now[0] += 3
    action = runner.tick(game, 0, AiController("builtin.hard", {}), 99)
    assert {k: v for k, v in action.items() if k != "player_id"} in game.legal_actions(0)
    assert runner.failures[0].plugin_id == "builtin.hard"
    executor.future.set_result(game.legal_actions(0)[-1])
    assert not runner.pending  # the timed-out future is detached even if it later completes
    runner.close()


def test_per_seat_session_and_v2_replay_remain_action_based():
    game = new_game(GameConfig(("Human", "A", "B")), 9)
    session = GameSession(game, {0: HumanController(), 1: AiController("builtin.hard", {}),
                                 2: AiController("builtin.devil", {})})
    action = game.legal_actions(0)[0]
    session.submit(0, action)
    data = session.replay({1: {"plugin_id": "builtin.hard", "version": "1.0.0"}})
    assert data["version"] == 2 and data["participants"]["1"]["plugin_id"] == "builtin.hard"
    assert replay_game(data).view_for(0) == game.view_for(0)


def test_old_settings_migrate_to_all_ai_seats():
    registry = AiRegistry(discover=False)
    seats = load_ai_seats({"ai_difficulty": "hard"}, registry)
    assert {entry["plugin_id"] for entry in seats.values()} == {"builtin.hard"}


def test_dependency_boundaries_have_no_forbidden_imports():
    root = Path(__file__).resolve().parents[1] / "uno"
    api = (root / "ai_api.py").read_text(encoding="utf8")
    engine = (root / "engine.py").read_text(encoding="utf8")
    network = (root / "network.py").read_text(encoding="utf8")
    assert "pygame" not in api and "websockets" not in api
    assert "pygame" not in engine and "websockets" not in engine
    assert "from .animation" not in network and "import pygame" not in network
