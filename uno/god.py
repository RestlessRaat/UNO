"""Omniscient, bounded adversarial search on exact host-side state."""
import copy
from collections import Counter
from functools import lru_cache
from itertools import islice
from weakref import WeakKeyDictionary
from .engine import Game, BY_ID
from .devil import _Simulation, _forced_finish


class _DeckOrder:
    """Immutable shuffle result with a hash computed once per order."""
    __slots__ = ('cards', '_hash')

    def __init__(self, cards):
        self.cards = tuple(cards)
        self._hash = hash(self.cards)

    def __hash__(self):
        return self._hash

    def __eq__(self, other):
        return isinstance(other, _DeckOrder) and self.cards == other.cards


class _DeckCursor:
    """Branches share an immutable order and copy only a remaining-card index."""
    __slots__ = ('order', 'remaining')

    def __init__(self, order, remaining=None):
        self.order = order
        self.remaining = len(order.cards) if remaining is None else remaining

    def __len__(self):
        return self.remaining

    def __iter__(self):
        return islice(self.order.cards, self.remaining)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return self.order.cards[:self.remaining][index]
        index = index + self.remaining if index < 0 else index
        if not 0 <= index < self.remaining:
            raise IndexError('deck index out of range')
        return self.order.cards[index]

    def __eq__(self, other):
        return list(self) == list(other)

    def pop(self):
        if not self.remaining:
            raise IndexError('pop from empty deck')
        self.remaining -= 1
        return self.order.cards[self.remaining]

    def fork(self):
        return _DeckCursor(self.order, self.remaining)

    @property
    def key(self):
        return self.order, self.remaining


# The live engine only pops from a draw pile; recycling replaces the list.
# Weak keys release a finished game's cache, and identity detects every shuffle.
_DECK_CACHE = WeakKeyDictionary()
_PUBLIC_CARDS = {i: card.public() for i, card in BY_ID.items()}


class GodSimulation(_Simulation):
    @classmethod
    def from_game(cls, game):
        result = cls.__new__(cls)
        result.__dict__ = game.__dict__.copy()
        cached = _DECK_CACHE.get(game)
        if cached is None or cached[0] is not game.deck:
            cached = game.deck, _DeckOrder(game.deck)
            _DECK_CACHE[game] = cached
        result.deck = _DeckCursor(cached[1], len(game.deck))
        return result.fork()

    def fork(self):
        result = type(self).__new__(type(self))
        result.__dict__ = self.__dict__.copy()
        for key in ("discard", "eliminated", "roulette_revealed", "scores"):
            setattr(result, key, list(getattr(self, key)))
        result.deck = self.deck.fork()
        result.hands = [list(hand) for hand in self.hands]
        result.rng = copy.copy(self.rng)
        result.known_discards = set(self.known_discards)
        result.events, result.last_events, result.log = [], [], []
        return result

    def _recycle(self):
        if not self.deck and len(self.discard) > 1:
            Game._recycle(self)
            self.deck = _DeckCursor(_DeckOrder(self.deck))


WIN = 1_000_000
SEARCH_NODES = 12000
MAX_DEPTH = 10


def _settle(game):
    """Forced draws are consequences, not independent strategic plies."""
    for _ in range(256):
        if game.winner is not None:
            break
        legal = game.legal_actions(game.current)
        if len(legal) != 1:
            break
        game.act(legal[0])
    return game


@lru_cache(maxsize=8192)
def _hand_strength(ids):
    hand = [BY_ID[i] for i in ids]
    colors = Counter(c.color for c in hand)
    shed = max((colors[c.color] - 1 for c in hand if c.value == 'discard all'), default=0)
    size = len(hand) - 0.7 * shed
    flexibility = sum(c.wild or c.value in ('skip', 'reverse', 'skip all') for c in hand)
    return -30 * size + 100 / (size + 1) + 4 * flexibility


def _value(game, you):
    if game.winner is not None:
        return WIN if game.winner == you else -WIN
    if game.eliminated[you]:
        return -WIN
    others = [p for p in game.active if p != you]
    threats = sorted((_hand_strength(tuple(game.hands[p])) for p in others), reverse=True)
    value = _hand_strength(tuple(game.hands[you])) - threats[0] - 0.15 * sum(threats[1:])
    value += 900 * sum(game.eliminated[p] for p in range(len(game.hands)) if p != you)
    # Knowing the exact next hand lets us deliberately deny its playable colours.
    from .ai import _playable
    current = game.current
    if game.phase == 'turn':
        playable = sum(_playable(_PUBLIC_CARDS[i], game.color, BY_ID[game.discard[-1]].value,
                                game.pending, game.last_draw) for i in game.hands[current])
        pressure = 35 if not playable else -20 / playable
        value += -pressure if current == you else pressure
    return value


def _roulette(game):
    """Choose the actual nearest non-wild colour, including exact recycling."""
    best = None
    for action in game.legal_actions(game.current):
        sim = game.fork()
        player = sim.current
        sim.act(action)
        drawn = 0
        while sim.winner is None and sim.phase == 'roulette' and sim.current == player:
            sim.act({'type': 'draw'})
            drawn += 1
        key = (sim.eliminated[player], drawn, -_value(sim, player))
        if best is None or key < best[0]:
            best = key, action
    return best[1]


def choose_god(view, rng):
    game = view.get('_god_state')
    if not isinstance(game, GodSimulation):
        raise ValueError('God requires a private omniscient snapshot.')
    legal = view['legal']
    if len(legal) == 1:
        return legal[0]
    if game.phase == 'choose_color' and game.color_reason == 'roulette':
        return _roulette(game)
    finish = _forced_finish(view, budget=20000)
    if finish:
        return finish
    you = view['you']
    nodes = 0
    table = {}
    ordering = {}

    class Exhausted(Exception):
        pass

    def key(state):
        return (tuple(tuple(sorted(h)) for h in state.hands), state.deck.key, tuple(state.discard),
                tuple(state.eliminated), state.current, state.direction, state.color, state.phase,
                state.pending, state.last_draw, state.penalty_started, state.color_reason, state.rng.state)

    def branches(state):
        nonlocal nodes
        actions = state.legal_actions(state.current)
        if state.phase == 'choose_color' and state.color_reason == 'roulette':
            actions = [_roulette(state)]
        seen, children = set(), []
        for action in actions:
            card = BY_ID.get(action.get('card_id'))
            identity = (card.color, card.value) if card else tuple(action.items())
            if identity in seen:
                continue
            seen.add(identity)
            nodes += 1
            if nodes > SEARCH_NODES:
                raise Exhausted
            child = state.fork()
            child.act(action)
            _settle(child)
            children.append((action, child))
        preferred = ordering.get(key(state))
        children.sort(key=lambda pair: (pair[0] == preferred,
                      _value(pair[1], you) * (1 if state.current == you else -1)), reverse=True)
        return children

    def search(state, depth, alpha, beta):
        if state.winner is not None or state.eliminated[you]:
            return _value(state, you), None
        if depth == 0:
            # Detect forced finishing chains even beyond the nominal horizon.
            finish = _forced_finish(state.view_for(state.current), budget=300)
            if finish:
                return (WIN - 100 if state.current == you else -WIN + 100), finish
            return _value(state, you), None
        position = key(state)
        cached = table.get((position, depth))
        if cached is not None:
            return cached
        original_alpha, original_beta = alpha, beta
        maximizing = state.current == you
        best, chosen = (-float('inf') if maximizing else float('inf')), None
        cutoff = False
        for action, child in branches(state):
            value, _ = search(child, depth - 1, alpha, beta)
            if chosen is None or (value > best if maximizing else value < best):
                best, chosen = value, action
            if maximizing:
                alpha = max(alpha, best)
            else:
                beta = min(beta, best)
            if beta <= alpha:
                cutoff = True
                break
        ordering[position] = chosen
        # Only store exact values; cutoff bounds must not masquerade as scores.
        if not cutoff and original_alpha < best < original_beta:
            table[(position, depth)] = best, chosen
        return best, chosen

    # Never compare one deeply searched move with another unfinished move.
    chosen = legal[0]
    for depth in range(1, MAX_DEPTH + 1):
        try:
            score, candidate = search(game, depth, -float('inf'), float('inf'))
        except Exhausted:
            break
        chosen = candidate
        if abs(score) > WIN / 2:
            break
    return chosen
