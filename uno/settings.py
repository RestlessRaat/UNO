"""Settings migration kept outside Pygame and the rules engine."""
from __future__ import annotations

from typing import Any, Mapping

from .ai_plugins import AiRegistry, DEFAULT_PLUGIN_ID, LEGACY_TO_PLUGIN, normalize_plugin_id


def load_ai_seats(settings: Mapping[str, Any], registry: AiRegistry) -> dict[int, dict[str, Any]]:
    legacy = settings.get("ai_difficulty", "normal")
    legacy_id = LEGACY_TO_PLUGIN.get(legacy, DEFAULT_PLUGIN_ID) if isinstance(legacy, str) else DEFAULT_PLUGIN_ID
    raw = settings.get("local_ai_seats")
    result = {}
    for seat in (1, 2, 3):
        entry = raw.get(str(seat), {}) if isinstance(raw, dict) else {}
        plugin_id = normalize_plugin_id(entry.get("plugin_id", legacy_id)) if isinstance(entry, dict) else legacy_id
        original = plugin_id
        if not registry.has(plugin_id):
            plugin_id = DEFAULT_PLUGIN_ID
        values = entry.get("settings", {}) if isinstance(entry, dict) and isinstance(entry.get("settings", {}), dict) else {}
        try:
            values = dict(registry.normalize_settings(plugin_id, values))
            reset = False
        except ValueError:
            values = dict(registry.normalize_settings(plugin_id, {}))
            reset = True
        result[seat] = {"plugin_id": plugin_id, "settings": values,
                        "requested_plugin_id": original, "config_reset": reset}
    return result


def dump_ai_seats(seats: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    return {str(seat): {"plugin_id": value["plugin_id"],
                        "settings": dict(value.get("settings", {}))}
            for seat, value in seats.items() if seat in (1, 2, 3)}


__all__ = ["load_ai_seats", "dump_ai_seats"]
