"""AI plugin discovery, metadata validation, and built-in adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib.util
import json
from pathlib import Path
import random
import re
import sys
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .ai import choose_action as choose_legacy_action
from .ai_api import (API_VERSION, Action, DecisionContext, Policy, PublicGameView,
                     action_from_dict, action_to_dict, readonly_settings)


DEFAULT_PLUGIN_ID = "builtin.normal"
LEGACY_TO_PLUGIN = {name: f"builtin.{name}" for name in ("normal", "hard", "devil", "god")}
PLUGIN_TO_LEGACY = {value: key for key, value in LEGACY_TO_PLUGIN.items()}
_ID = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")


class PluginError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    type: str
    name: str
    description: str
    default: bool | int | float | str
    minimum: int | float | None = None
    maximum: int | float | None = None
    step: int | float | None = None
    options: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginSpec:
    id: str
    name: str
    version: str
    description: str = ""
    author: str = ""
    time_budget_ms: int = 2000
    settings_schema: tuple[SettingSpec, ...] = ()
    builtin: bool = False
    omniscient: bool = False

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "version": self.version,
                "description": self.description, "author": self.author,
                "time_budget_ms": self.time_budget_ms,
                "settings_schema": [setting_to_dict(item) for item in self.settings_schema]}


@dataclass(frozen=True, slots=True)
class PluginDiagnostic:
    path: str
    message: str


class _LegacyPolicy:
    def __init__(self, difficulty: str):
        self.difficulty = difficulty

    def choose_action(self, context: DecisionContext) -> Action:
        view = context.game.to_dict(context.legal_actions)
        result = choose_legacy_action(view, random.Random(context.random_seed), self.difficulty)
        if result is None:
            raise PluginError("AI returned no action while legal actions exist.")
        result = {key: value for key, value in result.items() if key != "player_id"}
        return action_from_dict(result)


Factory = Callable[[Mapping[str, Any]], Policy]


class AiRegistry:
    """A startup-only registry. External plugins never receive privileged APIs."""

    def __init__(self, plugin_dir: Path | None = None, *, discover: bool = True):
        self.plugin_dir = Path(plugin_dir) if plugin_dir is not None else None
        self._specs: dict[str, PluginSpec] = {}
        self._factories: dict[str, Factory] = {}
        self.diagnostics: list[PluginDiagnostic] = []
        self._register_builtins()
        if discover and self.plugin_dir is not None:
            self.discover()

    def _register_builtins(self):
        descriptions = {
            "normal": "Original random policy.",
            "hard": "Public-information tactical policy.",
            "devil": "Public-information sampled search policy.",
            "god": "Built-in omniscient search policy.",
        }
        budgets = {"normal": 2000, "hard": 2000, "devil": 5000, "god": 10000}
        versions = {"normal": "1.0.0", "hard": "1.0.0", "devil": "1.1.0", "god": "1.0.0"}
        for difficulty in ("normal", "hard", "devil", "god"):
            plugin_id = LEGACY_TO_PLUGIN[difficulty]
            spec = PluginSpec(plugin_id, difficulty.title(), versions[difficulty], descriptions[difficulty],
                              "UNO No Mercy", budgets[difficulty], builtin=True,
                              omniscient=difficulty == "god")
            factory = (lambda _settings, value=difficulty: _LegacyPolicy(value))
            self.register(spec, factory)

    def register(self, spec: PluginSpec, factory: Factory):
        if spec.id in self._specs:
            raise PluginError(f"Duplicate plugin id: {spec.id}")
        if not callable(factory):
            raise PluginError("Plugin entry point is not callable.")
        self._specs[spec.id] = spec
        self._factories[spec.id] = factory

    @property
    def specs(self) -> tuple[PluginSpec, ...]:
        builtins = [self._specs[key] for key in LEGACY_TO_PLUGIN.values()]
        external = sorted((value for value in self._specs.values() if not value.builtin),
                          key=lambda item: (item.name.casefold(), item.id))
        return tuple(builtins + external)

    def has(self, plugin_id: str) -> bool:
        return normalize_plugin_id(plugin_id) in self._specs

    def spec(self, plugin_id: str) -> PluginSpec:
        plugin_id = normalize_plugin_id(plugin_id)
        try:
            return self._specs[plugin_id]
        except KeyError as exc:
            raise PluginError(f"Unknown AI plugin: {plugin_id}") from exc

    def normalize_settings(self, plugin_id: str, settings: Mapping[str, Any] | None) -> Mapping[str, Any]:
        spec = self.spec(plugin_id)
        raw = dict(settings or {})
        allowed = {item.key for item in spec.settings_schema}
        unknown = set(raw) - allowed
        if unknown:
            raise PluginError(f"Unknown setting(s): {', '.join(sorted(unknown))}")
        result = {}
        for item in spec.settings_schema:
            value = raw.get(item.key, item.default)
            _validate_setting_value(item, value)
            result[item.key] = value
        return MappingProxyType(result)

    def create(self, plugin_id: str, settings: Mapping[str, Any] | None = None) -> Policy:
        plugin_id = normalize_plugin_id(plugin_id)
        normalized = self.normalize_settings(plugin_id, settings)
        try:
            policy = self._factories[plugin_id](readonly_settings(normalized))
        except Exception as exc:
            raise PluginError(f"Cannot create {plugin_id}: {exc}") from exc
        if not callable(getattr(policy, "choose_action", None)):
            raise PluginError(f"{plugin_id} did not create a Policy.")
        return policy

    def discover(self):
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        for manifest_path in sorted(self.plugin_dir.glob("*/plugin.json")):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf8"))
                spec, entry_point = parse_manifest(data)
                factory = _load_factory(manifest_path.parent, entry_point, spec.id)
                self.register(spec, factory)
            except Exception as exc:
                self.diagnostics.append(PluginDiagnostic(str(manifest_path), str(exc)))


def normalize_plugin_id(value: Any) -> str:
    if isinstance(value, str) and value in LEGACY_TO_PLUGIN:
        return LEGACY_TO_PLUGIN[value]
    if not isinstance(value, str):
        return DEFAULT_PLUGIN_ID
    return value


def legacy_name(plugin_id: str) -> str | None:
    return PLUGIN_TO_LEGACY.get(normalize_plugin_id(plugin_id))


def setting_to_dict(item: SettingSpec) -> dict[str, Any]:
    result = {"key": item.key, "type": item.type, "name": item.name,
              "description": item.description, "default": item.default}
    if item.minimum is not None:
        result["min"] = item.minimum
    if item.maximum is not None:
        result["max"] = item.maximum
    if item.step is not None:
        result["step"] = item.step
    if item.options:
        result["options"] = list(item.options)
    return result


def parse_manifest(value: Any) -> tuple[PluginSpec, str]:
    if not isinstance(value, dict):
        raise PluginError("plugin.json must contain an object.")
    if value.get("api_version") != API_VERSION:
        raise PluginError(f"Unsupported AI API version: {value.get('api_version')!r}")
    if "omniscient" in value or "trusted" in value or value.get("information_access") not in (None, "public"):
        raise PluginError("External plugins may only request public information.")
    plugin_id = value.get("id")
    if not isinstance(plugin_id, str) or not _ID.fullmatch(plugin_id) or plugin_id.startswith("builtin."):
        raise PluginError("Plugin id must be a namespaced lowercase id and may not use builtin.*")
    name, version, entry = value.get("name"), value.get("version"), value.get("entry_point")
    if not all(isinstance(item, str) and item.strip() for item in (name, version, entry)):
        raise PluginError("name, version and entry_point are required strings.")
    budget = value.get("time_budget_ms", 2000)
    if type(budget) is not int or not 100 <= budget <= 10000:
        raise PluginError("time_budget_ms must be an integer from 100 to 10000.")
    schema_value = value.get("settings_schema", [])
    if not isinstance(schema_value, list):
        raise PluginError("settings_schema must be a list.")
    schema = tuple(_parse_setting(item) for item in schema_value)
    if len({item.key for item in schema}) != len(schema):
        raise PluginError("Setting keys must be unique.")
    spec = PluginSpec(plugin_id, name.strip(), version.strip(), str(value.get("description", "")),
                      str(value.get("author", "")), budget, schema)
    return spec, entry


def _parse_setting(value: Any) -> SettingSpec:
    if not isinstance(value, dict):
        raise PluginError("Each setting schema item must be an object.")
    key, kind = value.get("key"), value.get("type")
    if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
        raise PluginError("Setting keys must be lowercase identifiers.")
    if kind not in ("boolean", "integer", "number", "enum"):
        raise PluginError(f"Unsupported setting type for {key}.")
    if "default" not in value:
        raise PluginError(f"Setting {key} needs a default.")
    options = value.get("options", ())
    if kind == "enum" and (not isinstance(options, list) or not options
                            or any(not isinstance(item, str) for item in options)):
        raise PluginError(f"Enum setting {key} needs string options.")
    spec = SettingSpec(key, kind, str(value.get("name", key.replace("_", " ").title())),
                       str(value.get("description", "")), value["default"],
                       value.get("min"), value.get("max"), value.get("step"), tuple(options))
    _validate_setting_value(spec, spec.default)
    return spec


def _validate_setting_value(spec: SettingSpec, value: Any):
    if spec.type == "boolean":
        valid = type(value) is bool
    elif spec.type == "integer":
        valid = type(value) is int
    elif spec.type == "number":
        valid = type(value) in (int, float)
    else:
        valid = isinstance(value, str) and value in spec.options
    if not valid:
        raise PluginError(f"Invalid value for setting {spec.key}.")
    if spec.type in ("integer", "number"):
        if spec.minimum is not None and value < spec.minimum:
            raise PluginError(f"Setting {spec.key} is below its minimum.")
        if spec.maximum is not None and value > spec.maximum:
            raise PluginError(f"Setting {spec.key} is above its maximum.")
        if spec.step is not None and (type(spec.step) not in (int, float) or spec.step <= 0):
            raise PluginError(f"Setting {spec.key} has an invalid step.")


def _load_factory(root: Path, entry_point: str, plugin_id: str) -> Factory:
    if entry_point.count(":") != 1:
        raise PluginError("entry_point must use module:function syntax.")
    module_part, attribute = entry_point.split(":")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", module_part) or not attribute.isidentifier():
        raise PluginError("Invalid entry_point.")
    source = root.joinpath(*module_part.split(".")).with_suffix(".py")
    if not source.is_file():
        raise PluginError(f"Entry module does not exist: {module_part}")
    module_name = "_uno_ai_plugin_" + re.sub(r"[^a-zA-Z0-9_]", "_", plugin_id)
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise PluginError("Cannot create plugin module loader.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(root))
    try:
        spec.loader.exec_module(module)
    finally:
        try:
            sys.path.remove(str(root))
        except ValueError:
            pass
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise PluginError("Plugin entry point is not callable.")
    return factory


def build_context(view: Mapping[str, Any], random_seed: int, time_budget_ms: int) -> DecisionContext:
    legal = tuple(action_from_dict(action) for action in view.get("legal", ()))
    return DecisionContext(API_VERSION, PublicGameView.from_dict(view), legal,
                           int(random_seed), int(time_budget_ms))


def run_policy(policy: Policy, context: DecisionContext) -> dict[str, Any]:
    action = policy.choose_action(context)
    result = action_to_dict(action)
    if result not in [action_to_dict(item) for item in context.legal_actions]:
        raise PluginError("AI returned an action that is not legal now.")
    return result


__all__ = ["AiRegistry", "PluginSpec", "SettingSpec", "PluginDiagnostic", "PluginError",
           "DEFAULT_PLUGIN_ID", "LEGACY_TO_PLUGIN", "PLUGIN_TO_LEGACY", "normalize_plugin_id",
           "legacy_name", "parse_manifest", "build_context", "run_policy"]
