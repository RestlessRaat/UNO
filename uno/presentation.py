"""Presentation adapters; importing this module is optional for headless hosts."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from .animation import action_timeline, opening


class AnimationTurnPacing:
    """Translate presentation timelines into host-side AI pacing delays."""

    def __init__(self, voice_duration: Callable[[str], float], ai_delay: float = 0.75):
        self.voice_duration = voice_duration
        self.ai_delay = ai_delay

    def opening_delay(self, view: Mapping[str, Any]) -> float:
        return opening(dict(view), self.voice_duration).duration

    def action_delay(self, before: Mapping[str, Any], after: Mapping[str, Any]) -> float:
        return action_timeline(dict(before), dict(after), self.voice_duration).duration + self.ai_delay


__all__ = ["AnimationTurnPacing"]
