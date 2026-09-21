"""Minimal third-party AI example. It imports only the stable public API."""
import random

from uno.ai_api import DecisionContext


class RandomPlusPolicy:
    def __init__(self, settings):
        self.prefer_play = settings["prefer_play"]
        self.style = settings["style"]

    def choose_action(self, context: DecisionContext):
        choices = context.legal_actions
        if self.prefer_play:
            plays = tuple(action for action in choices if action.type == "play")
            choices = plays or choices
        if self.style == "first":
            return choices[0]
        return random.Random(context.random_seed).choice(choices)


def create_policy(settings):
    return RandomPlusPolicy(settings)
