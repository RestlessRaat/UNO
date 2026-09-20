"""Information-limited forward planning for Devil difficulty.

The simulator receives only the same view as a player. Unknown cards are
random samples, never the real hidden hands, draw order or shuffle seed.
"""
from collections import Counter
from functools import lru_cache
import random

from .engine import BY_ID, CARDS, COLORS, DRAW_VALUES, Game, GameConfig

ROLLOUT_SAMPLES = 48
ROLLOUT_STEPS = 160

def _forced_finish(view, budget=4000):
    """Search only guaranteed own turns; return a legal first step of a win."""
    from .ai import _playable
    cards = tuple(view['hand'])
    duel = sum(not p['eliminated'] for p in view['players']) == 2
    extra = {'skip all', 'skip', 'reverse', 'wild reverse draw 4'} if duel else {'skip all'}
    blockers = [c for c in cards if c['value'] not in extra]
    # Every non-extra-turn card must be the finisher or vanish in its discard-all.
    if len(blockers) > 1 and (len({c['color'] for c in blockers}) > 1
                              or not any(c['value'] == 'discard all' for c in blockers)):
        return None
    full = (1 << len(cards)) - 1
    nodes = 0
    color_masks = {color: sum(1 << i for i, c in enumerate(cards) if c['color'] == color)
                   for color in COLORS}

    @lru_cache(maxsize=budget)
    def solve(mask, color, top, pending, last):
        nonlocal nodes
        nodes += 1
        if nodes > budget:
            return None
        seen = set()
        for i, c in enumerate(cards):
            bit = 1 << i
            key = c['color'], c['value']
            if not mask & bit or key in seen or not _playable(c, color, top, pending, last):
                continue
            seen.add(key)
            rest = mask ^ bit
            value = c['value']
            if value == 'discard all':
                rest &= ~color_masks[c['color']]
            if not rest:
                return c['id']
            if value == 'skip all' or duel and value in ('skip','reverse'):
                if solve(rest, c['color'], value, 0, 0) is not None:
                    return c['id']
            elif duel and value == 'wild reverse draw 4':
                for next_color in COLORS:
                    if solve(rest, next_color, value, pending+4, 4) is not None:
                        return c['id']
        return None

    if view['phase'] == 'turn' and not view['penalty_started']:
        card_id = solve(full, view['color'], view['top']['value'], view['pending'], view['last_draw'])
        action = {'type':'play','card_id':card_id}
        return action if action in view['legal'] else None
    if view['phase'] == 'choose_color' and duel and view['top']['value'] == 'wild reverse draw 4':
        for action in view['legal']:
            if solve(full, action['color'], view['top']['value'], view['pending'], 4) is not None:
                return action
    return None


class _Simulation(Game):
    def _emit(self, *args, **kwargs):
        pass

    def _uno(self, player):
        pass

    def _finish(self, player):
        self.winner = player
        self.phase = 'finished'

    def _recycle(self):
        if not self.deck and len(self.discard) > 1:
            self.deck = self.discard[:-1]
            self.rng.shuffle(self.deck)
            self.discard = self.discard[-1:]

    @classmethod
    def sample(cls, view, rng):
        game = cls.__new__(cls)
        game.config = GameConfig(tuple(p['name'] for p in view['players']))
        game.current, game.direction = view['current'], view['direction']
        game.color, game.phase = view['color'], view['phase']
        game.pending, game.last_draw = view['pending'], view['last_draw']
        game.penalty_started = view['penalty_started']
        game.color_reason = ('roulette' if view['top']['value'] == 'wild colour roulette'
                             else view['top']['value']) if game.phase == 'choose_color' else ''
        game.turn_serial, game.winner = 0, None
        game.rng = rng
        game.eliminated = [p['eliminated'] for p in view['players']]
        own = [c['id'] for c in view['hand']]
        top = view['top']['id']
        discards = set(view.get('known_discards', (top,))) | {top}
        revealed = {}
        if view['phase'] == 'roulette':
            revealed[view['current']] = [c['id'] for c in view.get('roulette_revealed', ())]
        else:
            for event in view.get('events', ()):
                if event['type'] == 'draw' and event.get('revealed'):
                    revealed[event['player']] = [c['id'] for c in view.get('roulette_revealed', ())]
        for player in view['players']:
            if player['eliminated']:
                discards.update(revealed.pop(player['id'], ()))
        known = set(own) | discards | {c for hand in revealed.values() for c in hand}
        unknown = [c.id for c in CARDS if c.id not in known]
        rng.shuffle(unknown)
        game.hands = []
        for p in view['players']:
            if p['id'] == view['you']:
                game.hands.append(list(own))
            else:
                visible = revealed.get(p['id'], [])
                n = p['count'] - len(visible)
                game.hands.append(unknown[:n] + visible)
                del unknown[:n]
        game.deck = unknown[:view['deck_count']]
        game.discard = unknown[view['deck_count']:] + sorted(discards - {top}) + [top]
        game.roulette_revealed = []
        return game

    def fork(self):
        result = type(self).__new__(type(self))
        result.__dict__ = self.__dict__.copy()
        result.hands = [list(hand) for hand in self.hands]
        result.deck, result.discard = list(self.deck), list(self.discard)
        result.eliminated = list(self.eliminated)
        result.roulette_revealed = list(self.roulette_revealed)
        result.rng = random.Random(0)
        result.rng.setstate(self.rng.getstate())
        return result

    def act(self, action):
        kind = action['type']
        if kind == 'play':
            self._play(action['card_id'])
        elif kind == 'draw':
            self._draw()
        elif kind == 'choose_color':
            self._choose_color(action['color'])
        elif kind == 'choose_player':
            p, t = self.current, action['target']
            self.hands[p], self.hands[t] = self.hands[t], self.hands[p]
            self._advance()
        else:
            self.pending = self.last_draw = 0
            self._advance()

    def rollout_action(self, smart):
        hand = self.hands[self.current]
        if self.phase == 'choose_player':
            targets = [p for p in self.active if p != self.current]
            target = min(targets, key=lambda p:len(self.hands[p])) if smart else self.rng.choice(targets)
            return {'type':'choose_player','target':target}
        if self.phase == 'choose_color':
            colors = Counter(BY_ID[c].color for c in hand if BY_ID[c].color != 'wild')
            if smart:
                color = min(COLORS, key=lambda c: colors[c]) if self.color_reason == 'roulette' else max(COLORS, key=lambda c: colors[c])
            else:
                color = self.rng.choice([c for c in COLORS if c in colors] or list(COLORS))
            return {'type':'choose_color','color':color}
        cards = [] if self.phase == 'roulette' else [c for c in hand if self._playable(c)]
        if not cards:
            return {'type':'draw'} if self.deck or len(self.discard)>1 else {'type':'end_turn'}
        if not smart:
            return {'type':'play','card_id':self.rng.choice(cards)}
        if len(hand) <= 8:
            finish = _forced_finish({
                'hand': [{'id': i, 'color': BY_ID[i].color, 'value': BY_ID[i].value} for i in hand],
                'players': [{'eliminated': out} for out in self.eliminated],
                'phase': self.phase, 'penalty_started': self.penalty_started,
                'color': self.color, 'top': {'value': BY_ID[self.discard[-1]].value},
                'pending': self.pending, 'last_draw': self.last_draw,
                'legal': [{'type':'play', 'card_id': i} for i in cards]}, budget=150)
            if finish:
                return finish
        other = self._next()
        n, target_n = len(hand), len(self.hands[other])
        colors = Counter(BY_ID[c].color for c in hand)
        duel = len(self.active)==2
        def score(i):
            c = BY_ID[i]
            value = c.value
            left = n - (colors[c.color] if value == 'discard all' else 1)
            if not left:
                return 10000
            if value in ('0','7'):
                take = min(len(self.hands[p]) for p in self.active if p != self.current) if value=='7' else len(self.hands[self._next(steps=len(self.active)-1)])
                return (n-take)*10 - (35 if left==1 else 0)
            bonus = (n-left)*10 + colors[c.color]
            if value == 'skip all' or duel and value in ('skip','reverse'):
                bonus += 12
            amount = DRAW_VALUES.get(value,0)
            if value == 'wild reverse draw 4' and duel:
                bonus -= 35
            elif amount:
                bonus += amount*2 + (30 if target_n+amount+self.pending>=self.config.mercy_limit else 0)
            if c.wild:
                bonus -= 4
            next_player = other
            if value == 'skip all' or duel and value in ('skip', 'reverse'):
                next_player = self.current
            elif value == 'skip':
                next_player = self._next(steps=2)
            elif value in ('reverse', 'wild reverse draw 4'):
                next_player = self.current if duel else self._next(steps=len(self.active)-1)
            if next_player != self.current:
                expected_count = len(self.hands[next_player]) + 0.65 * (self.pending+amount if amount else 3 if value=='wild colour roulette' else 0)
                bonus -= 45 / max(1, expected_count)**2
            return bonus
        return {'type':'play','card_id':max(cards,key=score)}


def _rollout(world, action, you):
    sim = world.fork()
    sim.act(action)
    for _ in range(ROLLOUT_STEPS):
        if sim.winner is not None:
            break
        sim.act(sim.rollout_action(sim.current == you))
    if sim.winner is not None:
        return 1.0 if sim.winner == you else 0.0
    if sim.eliminated[you]:
        return 0.0
    own = len(sim.hands[you])
    other = min(len(sim.hands[p]) for p in sim.active if p != you)
    return other / (own + other + 0.001)


def choose_devil(view, rng, samples=None):
    """Bounded paired-world rollouts, with more evidence for close decisions."""
    from .ai import _HardPolicy, _WIN
    samples = ROLLOUT_SAMPLES if samples is None else samples
    if type(samples) is not int or samples < 1:
        raise ValueError("A positive rollout count is required.")
    legal = view['legal']
    if len(legal)==1:
        return legal[0]
    finish = _forced_finish(view)
    if finish is not None:
        return finish
    policy = _HardPolicy(view)
    scores = policy.scores(legal)
    best = max(scores)
    if best >= _WIN:
        return legal[scores.index(best)]
    if view['phase'] == 'choose_color':
        return legal[scores.index(best)]
    # Keep physically duplicate cards from wasting equivalent rollout branches.
    options = []
    seen = set()
    for action, score in sorted(zip(legal, scores), key=lambda x: x[1], reverse=True):
        if action['type'] == 'play':
            card = BY_ID[action['card_id']]
            key = action['type'], card.color, card.value
        else:
            key = tuple(action.items())
        if key not in seen:
            seen.add(key)
            options.append((action, score))
    options = options[:8]
    results = [0.0] * len(options)
    counts = [samples] * len(options)
    for _ in range(samples):
        world = _Simulation.sample(view, random.Random(rng.getrandbits(64)))
        for index, (action, _) in enumerate(options):
            results[index] += _rollout(world, action, view['you'])

    def estimate(i):
        prior = 0.06 * max(-1, min(0, (options[i][1] - best) / 100))
        draw_cost = 0.05 if options[i][0]['type'] == 'draw' and not view['pending'] else 0
        return results[i] / counts[i] + prior - draw_cost

    finalists = sorted(range(len(options)), key=lambda i: (estimate(i), options[i][1]), reverse=True)[:2]
    if len(finalists) == 2 and estimate(finalists[0]) - estimate(finalists[1]) < 0.2:
        for _ in range(samples):
            world = _Simulation.sample(view, random.Random(rng.getrandbits(64)))
            for i in finalists:
                results[i] += _rollout(world, options[i][0], view['you'])
                counts[i] += 1
    best_index = max(finalists, key=lambda i: (estimate(i), options[i][1]))
    return options[best_index][0]
