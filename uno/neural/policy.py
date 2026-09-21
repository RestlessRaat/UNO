"""CPU inference shared by the optional game plugin and evaluation tools."""
from functools import lru_cache
from pathlib import Path
import random

import torch

from uno.ai_api import action_from_dict
from .encoding import encode
from .model import Actor, ModelConfig, tensor_batch
from .training import load_checkpoint, rules_hash


@lru_cache(maxsize=4)
def load_actor(path, device='cpu'):
    checkpoint = load_checkpoint(Path(path))
    if checkpoint['rules_hash'] != rules_hash():
        raise ValueError('This neural model was trained with a different rules engine')
    actor = Actor(ModelConfig(**checkpoint['model_config'])).to(device).eval()
    actor.load_state_dict(checkpoint['actor'])
    actor.requires_grad_(False)
    return actor


class NeuralPolicy:
    def __init__(self, checkpoint, *, deterministic=False):
        self.actor = load_actor(str(Path(checkpoint).resolve()))
        self.deterministic = deterministic

    @torch.inference_mode()
    def decide(self, view, seed):
        observation, actions = encode(view)
        if len(actions) == 1:
            return actions[0]
        logits, _ = self.actor(tensor_batch([observation], 'cpu'))
        probabilities = logits[0, :len(actions)].softmax(-1).tolist()
        index = (max(range(len(actions)), key=probabilities.__getitem__) if self.deterministic
                 else random.Random(seed).choices(range(len(actions)), weights=probabilities, k=1)[0])
        return actions[index]

    def choose_action(self, context):
        return action_from_dict(self.decide(context.game.to_dict(context.legal_actions), context.random_seed))
