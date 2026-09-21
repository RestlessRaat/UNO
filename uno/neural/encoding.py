"""Versioned, fair-information observations and equivalent-action grouping."""
from __future__ import annotations

import numpy as np

from uno.engine import BY_ID, CARDS, COLORS

ENCODING_VERSION = 1
CARD_TYPES = tuple(dict.fromkeys((c.color, c.value) for c in CARDS))
TYPE_INDEX = {value: i for i, value in enumerate(CARD_TYPES)}
CARD_INDEX = {c.id: TYPE_INDEX[(c.color, c.value)] for c in CARDS}
N_TYPES = len(CARD_TYPES)
PHASES = ('turn', 'choose_color', 'choose_player', 'roulette', 'finished')
EVENTS = ('start', 'play', 'draw', 'discard_all', 'swap', 'rotate', 'mercy',
          'color', 'reverse', 'uno', 'turn', 'shuffle', 'win', 'unknown')
ACTION_TYPES = ('play', 'draw', 'end_turn', 'choose_color', 'choose_player')
HAND_SIZE = 36
HISTORY_SIZE = 64
MAX_ACTIONS = 40
PLAYER_DIM = 7 + N_TYPES
GLOBAL_DIM = 23 + 2 * N_TYPES
EVENT_DIM = len(EVENTS) + 4 + 4 + 5 + N_TYPES + 5
ACTION_DIM = len(ACTION_TYPES) + N_TYPES + 4 + 4
CRITIC_DIM = 4 * (N_TYPES + 4) + N_TYPES + GLOBAL_DIM + 4


def relative_seat(pid, you, count):
    return (int(pid) - you) % count


def histogram(ids):
    result = np.zeros(N_TYPES, dtype=np.float32)
    for cid in ids:
        result[CARD_INDEX[int(cid)]] += 1
    return result


def grouped_actions(view):
    """Keep one engine action per semantically equivalent card type."""
    hand = {c['id']: c for c in view['hand']}
    unique = {}
    for action in view['legal']:
        kind = action['type']
        if kind == 'play':
            card = hand[action['card_id']]
            key = (0, TYPE_INDEX[(card['color'], card['value'])])
        elif kind == 'choose_color':
            key = (3, COLORS.index(action['color']))
        elif kind == 'choose_player':
            key = (4, relative_seat(action['target'], view['you'], len(view['players'])))
        else:
            key = (ACTION_TYPES.index(kind), 0)
        unique.setdefault(key, dict(action))
    return [unique[key] for key in sorted(unique)]


def encode(view):
    """Whitelist visible fields; identities/seeds/names never become features."""
    you, n = view['you'], len(view['players'])
    hand = np.zeros(HAND_SIZE, dtype=np.int64)
    # Canonicalisation also makes floating-point inference invariant to UI sorting.
    types = sorted(TYPE_INDEX[(c['color'], c['value'])] + 1 for c in view['hand'])
    if len(types) > HAND_SIZE:
        raise ValueError('Observation exceeds the 36-card rules contract')
    hand[:len(types)] = types
    players = np.zeros((4, PLAYER_DIM), dtype=np.float32)
    player_mask = np.zeros(4, dtype=bool)
    for p in view['players']:
        relative = relative_seat(p['id'], you, n)
        player_mask[relative] = True
        players[relative, :7] = (1, p['count'] / 36, float(p['eliminated']),
                                 p['score'] / view['score_target'],
                                 float(p['id'] == view['current']),
                                 relative / 3, float(p['id'] == you))
        known = view.get('known_opponent_cards', {}).get(str(p['id']), ())
        players[relative, 7:] = histogram(known) / 8
    global_state = np.zeros(GLOBAL_DIM, dtype=np.float32)
    global_state[PHASES.index(view['phase'])] = 1
    global_state[5 + COLORS.index(view['color'])] = 1
    global_state[9:19] = (view['direction'], view['pending'] / 168,
                          view['last_draw'] / 10, float(view['penalty_started']),
                          view['deck_count'] / 168, view['discard_count'] / 168,
                          n / 4, view['mercy_limit'] / 36,
                          view['score_target'] / 1000,
                          relative_seat(view['current'], you, n) / 3)
    # Reserved slots support future scalar features without exposing hidden state.
    top = view['top']
    global_state[23 + TYPE_INDEX[(top['color'], top['value'])]] = 1
    global_state[23 + N_TYPES:] = histogram(view.get('known_discards', ())) / 8

    events = view.get('history') or view.get('events') or [{'type': 'start'}]
    events = events[-HISTORY_SIZE:]
    history = np.zeros((HISTORY_SIZE, EVENT_DIM), dtype=np.float32)
    for i, event in enumerate(events):
        kind = event['type']
        history[i, EVENTS.index(kind) if kind in EVENTS else len(EVENTS) - 1] = 1
        offset = len(EVENTS)
        for key in ('player', 'target'):
            if key in event and isinstance(event[key], int):
                history[i, offset + relative_seat(event[key], you, n)] = 1
            offset += 4
        color = event.get('color', event.get('color_before'))
        if color in (*COLORS, 'wild'):
            history[i, offset + (*COLORS, 'wild').index(color)] = 1
        offset += 5
        card = event.get('card')
        if card:
            history[i, offset + TYPE_INDEX[(card['color'], card['value'])]] = 1
        for card in event.get('cards', ()):
            history[i, offset + TYPE_INDEX[(card['color'], card['value'])]] += 1
        offset += N_TYPES
        history[i, offset:] = (event.get('pending_before', 0) / 168,
                               event.get('direction', 0), float(event.get('revealed', False)),
                               event.get('points', 0) / 1000,
                               (len(events) - 1 - i) / HISTORY_SIZE)

    actions = grouped_actions(view)
    if not actions or len(actions) > MAX_ACTIONS:
        raise ValueError('Policy needs between 1 and 40 legal action groups')
    action_features = np.zeros((MAX_ACTIONS, ACTION_DIM), dtype=np.float32)
    action_mask = np.zeros(MAX_ACTIONS, dtype=bool)
    hand_by_id = {c['id']: c for c in view['hand']}
    for i, action in enumerate(actions):
        action_mask[i] = True
        kind = action['type']
        action_features[i, ACTION_TYPES.index(kind)] = 1
        if kind == 'play':
            c = hand_by_id[action['card_id']]
            action_features[i, 5 + TYPE_INDEX[(c['color'], c['value'])]] = 1
        elif kind == 'choose_color':
            action_features[i, 5 + N_TYPES + COLORS.index(action['color'])] = 1
        elif kind == 'choose_player':
            action_features[i, 9 + N_TYPES + relative_seat(action['target'], you, n)] = 1
    return {'hand': hand, 'players': players, 'player_mask': player_mask,
            'global_state': global_state, 'history': history,
            'history_length': np.asarray(len(events), dtype=np.int64),
            'actions': action_features, 'action_mask': action_mask}, actions


def privileged(game):
    """Training-only critic features in absolute seat order, never actor inputs."""
    result = np.zeros(CRITIC_DIM, dtype=np.float32)
    offset = 0
    for pid in range(4):
        if pid < len(game.hands):
            result[offset:offset + N_TYPES] = histogram(game.hands[pid]) / 8
            result[offset + N_TYPES:offset + N_TYPES + 4] = (
                1, len(game.hands[pid]) / 36, float(game.eliminated[pid]),
                game.scores[pid] / game.score_target)
        offset += N_TYPES + 4
    result[offset:offset + N_TYPES] = histogram(game.deck) / 8
    offset += N_TYPES
    # encode() requires legal actions; terminal critic values are always zero.
    view = game.view_for(game.current)
    view['legal'] = view['legal'] or [{'type': 'end_turn'}]
    result[offset:offset + GLOBAL_DIM] = encode(view)[0]['global_state']
    result[-4 + game.current] = 1
    return result


def belief_targets(game, you):
    """Relative opponents (3 slots), then deck, as categorical count densities."""
    target = np.zeros((4, N_TYPES), dtype=np.float32)
    mask = np.zeros(4, dtype=bool)
    for relative in range(1, len(game.hands)):
        target[relative - 1] = histogram(game.hands[(you + relative) % len(game.hands)])
    target[3] = histogram(game.deck)
    counts = target.sum(-1)
    mask[counts > 0] = True
    target /= np.maximum(counts[:, None], 1)
    return target, mask
