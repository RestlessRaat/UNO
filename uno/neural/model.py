"""Small set-attention actor, history GRU, and separately parameterised critic."""
from dataclasses import asdict, dataclass
import math

import numpy as np
import torch
from torch import nn

from .encoding import (ACTION_DIM, CRITIC_DIM, EVENT_DIM, GLOBAL_DIM, N_TYPES,
                       PLAYER_DIM)


@dataclass(frozen=True)
class ModelConfig:
    width: int = 128
    heads: int = 4
    layers: int = 2
    history_hidden: int = 256


def tensor_batch(observations, device):
    return {key: torch.as_tensor(np.stack([o[key] for o in observations]), device=device)
            for key in observations[0]}


class SetBlock(nn.Module):
    def __init__(self, width, heads):
        super().__init__()
        self.norm1 = nn.LayerNorm(width)
        self.attention = nn.MultiheadAttention(width, heads, batch_first=True)
        self.norm2 = nn.LayerNorm(width)
        self.ff = nn.Sequential(nn.Linear(width, width * 2), nn.GELU(), nn.Linear(width * 2, width))

    def forward(self, x, mask):
        normal = self.norm1(x)
        x = x + self.attention(normal, normal, normal, key_padding_mask=~mask,
                               need_weights=False)[0]
        return x + self.ff(self.norm2(x))


class Actor(nn.Module):
    def __init__(self, config=ModelConfig()):
        super().__init__()
        self.config = config
        d = config.width
        self.card_embedding = nn.Embedding(N_TYPES + 1, d, padding_idx=0)
        self.hand_token = nn.Parameter(torch.zeros(1, 1, d))
        self.hand_blocks = nn.ModuleList(SetBlock(d, config.heads) for _ in range(config.layers))
        self.player_projection = nn.Linear(PLAYER_DIM, d)
        self.global_projection = nn.Linear(GLOBAL_DIM, d)
        self.public_blocks = nn.ModuleList(SetBlock(d, config.heads) for _ in range(config.layers))
        self.event_projection = nn.Sequential(nn.Linear(EVENT_DIM, 64), nn.GELU())
        self.history = nn.GRU(64, config.history_hidden, batch_first=True)
        self.fusion = nn.Sequential(nn.Linear(2 * d + config.history_hidden, 256), nn.GELU(),
                                    nn.Linear(256, d), nn.LayerNorm(d))
        self.action_projection = nn.Sequential(nn.Linear(ACTION_DIM, d), nn.GELU(), nn.Linear(d, d))
        self.query = nn.Linear(d, d)
        self.belief = nn.Linear(d, 4 * N_TYPES)

    def forward(self, observation):
        batch = observation['hand'].shape[0]
        valid = torch.ones((batch, 1), device=observation['hand'].device, dtype=torch.bool)
        hand = torch.cat((self.hand_token.expand(batch, -1, -1),
                          self.card_embedding(observation['hand'])), dim=1)
        mask = torch.cat((valid, observation['hand'] != 0), dim=1)
        for block in self.hand_blocks:
            hand = block(hand, mask)
        public = torch.cat((self.global_projection(observation['global_state']).unsqueeze(1),
                            self.player_projection(observation['players'])), dim=1)
        mask = torch.cat((valid, observation['player_mask']), dim=1)
        for block in self.public_blocks:
            public = block(public, mask)
        events, _ = self.history(self.event_projection(observation['history']))
        # Right padding occurs AFTER this selected state; it cannot affect it.
        history = events[torch.arange(batch, device=events.device), observation['history_length'] - 1]
        state = self.fusion(torch.cat((hand[:, 0], public[:, 0], history), dim=-1))
        candidates = self.action_projection(observation['actions'])
        logits = (candidates * self.query(state).unsqueeze(1)).sum(-1) / math.sqrt(self.config.width)
        logits = logits.masked_fill(~observation['action_mask'], -1e9)
        return logits, self.belief(state).reshape(batch, 4, N_TYPES)


class Critic(nn.Module):
    def __init__(self):
        super().__init__()
        self.network = nn.Sequential(nn.Linear(CRITIC_DIM, 256), nn.GELU(),
                                     nn.Linear(256, 256), nn.GELU(), nn.Linear(256, 4))

    def forward(self, state):
        return self.network(state)


def choose_device(request):
    if request == 'auto':
        return torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    if request == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA requested but unavailable; use --device cpu explicitly')
    return torch.device(request)


def config_dict(actor):
    return asdict(actor.config)
