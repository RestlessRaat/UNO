"""Scratch's display frames, played on a 1.5x presentation timeline.

Rules and networking never wait for this timeline. A transfer moves one real
card, reflows both hands linearly, and cross-fades its back in the same frames.
The No Mercy source uses 15 frames for play/draw, 6 for hand transfers and
mercy, 10 for scoring, and a 20-frame sine pulse at the start of each turn.
"""
from copy import deepcopy
from dataclasses import dataclass
import math

from .layout import hand_layout

SOURCE_FPS = 30
ANIMATION_SPEED = 1.5
FPS = SOURCE_FPS * ANIMATION_SPEED
CARD_WIDTH, CARD_HEIGHT = 56.7, 88.55


def pile_position(deck, count):
    offset = max(0, count - 1) / 14
    return (272 + offset, 180 - offset, 0, CARD_WIDTH, CARD_HEIGHT) if deck else (208 + offset, 180 - offset, 0, CARD_WIDTH, CARD_HEIGHT)


def interpolate(start, end, progress):
    delta = (end[2] - start[2] + 180) % 360 - 180
    # Scratch's -180-degree tie becomes +180 in pygame's opposite axis.
    if delta == -180:
        delta = 180
    angle = start[2] + delta * progress
    return (start[0] + (end[0] - start[0]) * progress,
            start[1] + (end[1] - start[1]) * progress, angle,
            start[3] + (end[3] - start[3]) * progress,
            start[4] + (end[4] - start[4]) * progress)


def hover_layout(layout, index, timer):
    """Source jiggle, with right-hand neighbours opened to a 75% card gap."""
    result = list(layout)
    if index is None or not 0 <= index < len(layout):
        return result
    timer *= ANIMATION_SPEED
    gap = layout[1][0] - layout[0][0] if len(layout) > 1 else CARD_WIDTH * 0.75
    for i in range(index + 1, len(layout)):
        x, y, a, w, h = result[i]
        result[i] = (x + CARD_WIDTH * 0.75 - gap, y, a, w, h)
    x, y, _, w, h = result[index]
    result[index] = (x + 4 * math.sin(math.radians(timer * 180)),
                     y - 8 * math.cos(math.radians(timer * 320)),
                     -6 * math.sin(math.radians(timer * 240)), w, h)
    return result


@dataclass
class Tile:
    key: str
    card: dict | None
    revealed: bool = False


@dataclass
class Scene:
    view: dict
    hands: list[list[Tile]]
    top: dict | None
    deck_count: int
    discard_count: int
    wheel: bool = True

    @classmethod
    def from_view(cls, view):
        hands = [[Tile(f'{view["revision"]}:{p["id"]}:{i}',
                       view['hand'][i] if p['id'] == view['you'] else None)
                  for i in range(p['count'])] for p in view['players']]
        if view['phase'] == 'roulette' and view['current'] != view['you']:
            revealed = view.get('roulette_revealed', [])
            hand = hands[view['current']]
            for tile, card in zip(hand[max(0, len(hand) - len(revealed)):], revealed):
                tile.card, tile.revealed = card, True
        return cls(deepcopy(view), hands, view['top'], view['deck_count'], view.get('discard_count', 1))

    def poses(self):
        result = {}
        for pid, hand in enumerate(self.hands):
            for tile, placement in zip(hand, hand_layout(pid, self.view['you'], len(self.hands), len(hand))):
                result[tile.key] = (tile, pid, placement)
        return result

    def visible_view(self):
        result = deepcopy(self.view)
        for p, hand in zip(result['players'], self.hands):
            p['count'] = len(hand)
        result['hand'] = [t.card for t in self.hands[result['you']] if t.card]
        result['top'] = self.top
        result['deck_count'] = self.deck_count
        return result

    def cards(self):
        return [(tile.card if owner == self.view['you'] or tile.revealed else None, pos, None)
                for tile, owner, pos in self.poses().values()]


@dataclass
class Segment:
    before: Scene
    after: Scene
    frames: int
    kind: str
    moving: Tile | None = None
    source: tuple | None = None
    target: tuple | None = None
    source_owner: int | None = None
    target_owner: int | None = None
    sounds: tuple = ()
    end_sounds: tuple = ()

    def sample(self, elapsed):
        frame = min(self.frames, max(1, int(elapsed * FPS) + 1))
        progress = frame / self.frames
        starts, ends = self.before.poses(), self.after.poses()
        cards = []
        you = self.before.view['you']
        for key in dict.fromkeys([*starts, *ends]):
            if self.moving and key == self.moving.key:
                continue
            tile, owner, end = ends.get(key, starts.get(key))
            start = starts.get(key, (tile, owner, end))[2]
            face = tile.card if owner == you or tile.revealed else None
            placement = interpolate(start, end, progress)
            if self.kind == 'pulse' and owner == self.after.view['current']:
                scale = 1 + 0.15 * math.sin(math.radians(frame * 9))
                placement = (*placement[:3], placement[3] * scale, placement[4] * scale)
            cards.append((face, placement, None))
        if self.moving:
            face = self.moving.card
            front_start = self.source_owner == you
            front_end = self.target_owner == you or self.target_owner == 'discard' or self.moving.revealed
            if face is None:
                back_alpha = None
            elif front_start and front_end:
                back_alpha = 0
            elif front_start:
                back_alpha = round(255 * progress)
            else:
                back_alpha = round(255 * (1 - progress))
            cards.append((face, interpolate(self.source, self.target, progress), back_alpha))
        if self.kind == 'recycle':
            count = self.before.discard_count
            for i in range(min(8, count)):
                n = 1 + round(i * max(0, count - 1) / max(1, min(8, count) - 1))
                cards.append((None, interpolate(pile_position(False, n),
                                                pile_position(True, self.before.deck_count + n), progress), None))
        return self.before, cards, progress


class Timeline:
    def __init__(self, segments, final):
        self.segments, self.final = segments, final
        self.duration = sum(s.frames for s in segments) / FPS
        self._emitted = set()

    def sample(self, elapsed):
        offset = 0
        for i, segment in enumerate(self.segments):
            duration = segment.frames / FPS
            if elapsed < offset + duration:
                return (*segment.sample(elapsed - offset), segment)
            offset += duration
        # A draw can reach the end before the next update retires this timeline.
        # Its terminal sample must still contain the complete visible hands.
        return self.final, self.final.cards(), 1, None

    def sounds_due(self, elapsed):
        offset = 0
        result = []
        for i, segment in enumerate(self.segments):
            if offset > elapsed:
                break
            if i not in self._emitted:
                self._emitted.add(i)
                result.extend(segment.sounds)
            if offset + segment.frames / FPS <= elapsed and ('end', i) not in self._emitted:
                self._emitted.add(('end', i))
                result.extend(segment.end_sounds)
            offset += segment.frames / FPS
        return result


class Builder:
    def __init__(self, scene, voice_duration=lambda name: 0.8):
        self.scene, self.segments = scene, []
        self.voice_duration = voice_duration
        self.counter = 0

    def pause(self, frames, kind='pause', sounds=(), change=None):
        before = deepcopy(self.scene)
        if change:
            change(self.scene)
        self.segments.append(Segment(before, deepcopy(self.scene), frames, kind, sounds=sounds))

    def voice(self, name):
        # Speech is not sped up: retain real audio duration while motion uses FPS.
        self.pause(max(1, math.ceil((self.voice_duration(name) + 0.2) * FPS)), sounds=(name,))

    def transfer(self, source, target, tile=None, frames=6, kind='transfer', index=0, card=None, start_override=None):
        before = deepcopy(self.scene)
        if source == 'deck':
            self.counter += 1
            tile = Tile(f'draw:{self.counter}', card)
            start = pile_position(True, self.scene.deck_count)
            self.scene.deck_count -= 1
        else:
            tile = tile or self.scene.hands[source][index]
            start = before.poses()[tile.key][2]
            self.scene.hands[source] = [t for t in self.scene.hands[source] if t.key != tile.key]
        if card:
            tile.card = card
        if target == 'discard':
            self.scene.discard_count += 1
            end = pile_position(False, 1 if kind == 'mercy' else self.scene.discard_count)
            if kind not in ('mercy', 'discard_all'):
                self.scene.top = tile.card
        else:
            self.scene.hands[target].append(tile)
            end = self.scene.poses()[tile.key][2]
        sound = 'sfx: draw slow' if source == 'deck' else 'sfx: draw fast'
        self.segments.append(Segment(before, deepcopy(self.scene), frames, kind, tile,
                                     start_override or start, end, source, target, (sound,)))

    def finish(self, view):
        final = Scene.from_view(view)
        if view['winner'] is not None:
            final.hands = [[] for _ in final.hands]
            final.top, final.deck_count, final.discard_count, final.wheel = None, 168, 0, False
        return Timeline(self.segments, final)


def opening(view, voice_duration=lambda name: 0.8):
    scene = Scene.from_view(view)
    scene.hands = [[] for _ in scene.hands]
    scene.top, scene.deck_count, scene.discard_count, scene.wheel = None, 168, 0, False
    b = Builder(scene, voice_duration)
    b.pause(12, sounds=('sfx: shuffle pack',))
    b.pause(25, kind='opening_fade')
    for index in range(7):
        for pid in range(len(scene.hands)):
            card = view['hand'][index] if pid == view['you'] else None
            b.transfer('deck', pid, frames=6, kind='deal', card=card)
    b.pause(18)
    b.transfer('deck', 'discard', frames=6, kind='first_discard', card=view['top'])
    b.pause(18)
    b.scene.wheel = True
    b.pause(20, kind='pulse')
    return b.finish(view)


def action_timeline(old, new, voice_duration=lambda name: 0.8, hovered_start=None):
    b = Builder(Scene.from_view(old), voice_duration)
    you = old['you']
    for event in new['events']:
        kind = event['type']
        if kind == 'play':
            pid, card = event['player'], event['card']
            index = next((i for i, t in enumerate(b.scene.hands[pid]) if t.card and t.card['id'] == card['id']),
                         min(event.get('hand_index', 0), len(b.scene.hands[pid]) - 1))
            b.transfer(pid, 'discard', frames=15, kind='play', index=index, card=card,
                       start_override=hovered_start if pid == you else None)
            if card['color'] != 'wild':
                b.scene.view['color'] = card['color']
            voice = {'reverse': 'reverse', 'skip': 'skip', 'skip all': 'skip everyone',
                     'wild reverse draw 4': 'reverse',
                     'wild colour roulette': 'wild colour roulette'}.get(card['value'])
            if card['value'] == 'discard all':
                voice = 'discard all ' + card['color'] + 's'
            if card['value'] in ('draw 2', 'draw 4'):
                voice = f'draw {new["pending"]}' if new['pending'] <= 40 else 'draw cards'
            if voice and b.scene.hands[pid]:
                b.segments[-1].sounds += ('voice: ' + voice,)
            if card['value'] == '7' and new['phase'] == 'choose_player' and pid == you:
                b.voice('voice: sevens swap')
        elif kind == 'draw':
            b.transfer('deck', event['player'], frames=15, kind='draw', card=event.get('card'))
            if event.get('revealed'):
                tile = b.scene.hands[event['player']][-1]
                tile.revealed = True
                b.segments[-1].moving.revealed = True
                b.segments[-1].after = deepcopy(b.scene)
        elif kind == 'discard_all':
            for number, card in enumerate(event['cards']):
                pid = event['player']
                positions = event.get('hand_indices', [])
                original = positions[number] - number if number < len(positions) else 0
                index = next((i for i, t in enumerate(b.scene.hands[pid]) if t.card and t.card['id'] == card['id']), original)
                b.transfer(pid, 'discard', frames=15, kind=kind, index=index, card=card)
        elif kind in ('swap', 'rotate'):
            if kind == 'swap':
                players = [event['target'], event['player']]
                targets = [event['player'], event['target']]
                if old['current'] != you:
                    b.voice('voice: sevens swap')
            else:
                active = [p['id'] for p in old['players'] if not p['eliminated']]
                start = active.index(old['current'])
                players = [active[(start + i * event['direction']) % len(active)] for i in range(len(active))]
                targets = players[1:] + players[:1]
                b.voice('voice: zeroes pass')
            originals = {pid: list(b.scene.hands[pid]) for pid in players}
            for pid, target in zip(players, targets):
                for i, tile in enumerate(originals[pid]):
                    card = new['hand'][i] if target == you and i < len(new['hand']) else tile.card
                    b.transfer(pid, target, tile=tile, card=card, frames=6)
        elif kind == 'mercy':
            b.voice('voice: mercy')
            pid = event['player']
            for tile in list(reversed(b.scene.hands[pid])):
                b.transfer(pid, 'discard', tile=tile, frames=6, kind=kind)
            b.scene.view['players'][pid]['eliminated'] = True
        elif kind == 'color':
            b.scene.view['color'] = event['color']
            b.voice('voice: ' + event['color'])
            if new['pending']:
                n = new['pending']
                b.voice(f'voice: draw {n}' if n <= 40 else 'voice: draw cards')
        elif kind == 'reverse':
            b.scene.view['direction'] = event['direction']
        elif kind == 'uno':
            if b.segments[-1].kind == 'play':
                sounds = b.segments[-1].sounds
                b.segments[-1].sounds = sounds[:1] + ('voice: uno',) + sounds[1:]
            else:
                b.segments[-1].end_sounds += ('voice: uno',)
        elif kind == 'turn':
            if old['phase'] == 'roulette' and old['current'] != you:
                b.pause(15)
                for tile in b.scene.hands[old['current']]:
                    if tile.revealed:
                        b.pause(3, sounds=('sfx: draw fast',))
                        tile.revealed = False
            b.scene.view['current'] = event['player']
            b.scene.view['legal'] = new['legal']
            b.scene.view['phase'] = new['phase']
            b.pause(20, kind='pulse')
        elif kind == 'shuffle':
            def recycle(scene):
                scene.deck_count = new['deck_count']
                scene.discard_count = 1
            b.pause(10, kind='recycle', sounds=('sfx: shuffle pack',), change=recycle)
        elif kind == 'win':
            b.voice('voice: hooray')
            winner = event['player']
            b.scene.wheel = False
            for delta in range(1, len(b.scene.hands)):
                pid = (winner + delta) % len(b.scene.hands)
                if new['players'][pid]['eliminated']:
                    b.voice('voice: mercy tally')
                    b.segments[-1].end_sounds += ('sfx: game start ding',)
                    b.scene.view['players'][winner]['score'] += 250
                    continue
                faces = event.get('scoring_hands', {}).get(str(pid), [])
                for index in range(len(b.scene.hands[pid]) - 1, -1, -1):
                    card = faces[index] if index < len(faces) else None
                    b.transfer(pid, 'discard', frames=10, kind='tally', index=index, card=card)
                    b.segments[-1].end_sounds += ('sfx: game start ding',)
                    if card:
                        points = int(card['value']) if card['value'].isdigit() else 50 if card['color'] == 'wild' else 20
                        b.scene.view['players'][winner]['score'] += points
            b.pause(30)
            def collect(scene):
                scene.deck_count, scene.discard_count, scene.top = 168, 0, None
            b.pause(10, kind='recycle', change=collect)
    if not b.segments:
        b.pause(1)
    return b.finish(new)
