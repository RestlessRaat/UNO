from __future__ import annotations

from collections import deque
from functools import lru_cache
import json
import os
from pathlib import Path
import sys

import pygame


def asset_root():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "assets"


def user_dir():
    path = Path(os.environ.get("UNO_USER_DIR") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "UnoNoMercy")
    path.mkdir(parents=True, exist_ok=True)
    return path


class Assets:
    def __init__(self):
        self.root = asset_root()
        try:
            self.manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf8"))
        except OSError as exc:
            raise RuntimeError("Assets are missing. Run tools/prepare_assets.py and tools/renderer/render.mjs.") from exc

    @lru_cache(maxsize=400)
    def image(self, path):
        return pygame.image.load(self.root / path).convert_alpha()

    def costume(self, target, name):
        return self.image(self.manifest["targets"][target]["costumes"][name]["path"])

    def card(self, card=None):
        key = f'{card["color"]}:{card["value"]}' if card else "back"
        return self.image(self.manifest["cards"][key])

    @lru_cache(maxsize=512)
    def font(self, size, handwriting=True, unicode=False):
        if unicode:
            return pygame.font.SysFont("microsoftyahei,segoeui,arial", round(size * 2))
        name = "handlee-regular.ttf" if handwriting else "NotoSans-Medium.ttf"
        return pygame.font.Font(self.root / "fonts" / name, round(size * 2))

    @lru_cache(maxsize=600)
    def scaled(self, path, width, height):
        return pygame.transform.smoothscale(self.image(path), (max(1, round(width * 2)), max(1, round(height * 2))))


class Audio:
    def __init__(self, assets, muted=False):
        self.assets = assets
        self.muted = muted
        self.available = False
        self.queue = deque()
        self.cache = {}
        self.current_music = ""
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.set_num_channels(8)
            self.voice = pygame.mixer.Channel(0)
            self.effects = pygame.mixer.Channel(1)
            self.available = True
        except pygame.error:
            pass
        self.set_muted(muted)

    def set_muted(self, value):
        self.muted = value
        if self.available:
            self.voice.set_volume(0 if value else 0.8)
            self.effects.set_volume(0 if value else 0.45)
            pygame.mixer.music.set_volume(0 if value else 0.18)

    def sound(self, name):
        if not self.available:
            return None
        if name not in self.cache:
            path = self.assets.manifest["targets"]["uno"]["sounds"].get(name)
            if path:
                try:
                    self.cache[name] = pygame.mixer.Sound(self.assets.root / path)
                except pygame.error:
                    self.cache[name] = None
        return self.cache.get(name)

    def play(self, name):
        sound = self.sound(name)
        if sound:
            if name.startswith("voice:"):
                self.queue.append(sound)
            else:
                self.effects.play(sound)

    def music(self, name="music: song 1", once=False):
        if not self.available or self.current_music == name:
            return
        path = self.assets.manifest["targets"]["uno"]["sounds"].get(name)
        if path:
            try:
                pygame.mixer.music.load(self.assets.root / path)
                pygame.mixer.music.play(0 if once else -1, fade_ms=500)
                self.current_music = name
                self.set_muted(self.muted)
            except pygame.error:
                pass

    def update(self):
        if self.available and self.queue and not self.voice.get_busy():
            self.voice.play(self.queue.popleft())

    def events(self, events, view):
        for event in events:
            kind = event["type"]
            if kind == "draw":
                self.play("sfx: draw slow")
            elif kind == "play":
                self.play("sfx: bloop")
                value = event["card"]["value"]
                voice = {"reverse": "reverse", "skip": "skip", "skip all": "skip everyone",
                         "7": "sevens swap", "0": "zeroes pass", "wild colour roulette": "wild colour roulette"}.get(value)
                if value == "discard all":
                    voice = "discard all " + event["card"]["color"] + "s"
                if voice:
                    self.play("voice: " + voice)
                if view["pending"]:
                    n = view["pending"]
                    self.play(f"voice: draw {n}" if n <= 40 and n % 2 == 0 else "voice: draw cards")
            elif kind == "color":
                self.play("voice: " + event["color"])
            elif kind == "uno":
                self.play("voice: uno")
            elif kind == "mercy":
                self.play("voice: mercy")
            elif kind == "shuffle":
                self.play("sfx: shuffle pack")
            elif kind == "win":
                self.play("voice: hooray")
                self.music("music: winner", once=True)

