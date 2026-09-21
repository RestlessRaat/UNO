from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import queue
import random
import time

import pygame

from .ai import DIFFICULTIES, choose_action, decision_view
from .ai_plugins import (AiRegistry, DEFAULT_PLUGIN_ID, LEGACY_TO_PLUGIN,
                         PLUGIN_TO_LEGACY, normalize_plugin_id)
from .animation import Scene, action_timeline, opening, hover_layout, pile_position, FPS, ANIMATION_SPEED
from .application import (AiController, AiRunner, GameSession, HumanController,
                          plugin_directory, settings_fingerprint)
from .engine import GameConfig, RuleError, new_game
from .elo import load_ai_elo, load_plugin_elo
from .layout import hand_layout, hit_card, mouse_to_logical, seat_position, viewport
from .network import DEFAULT_PORT, NetworkClient, PRESET_MESSAGES, RoomServer, local_addresses
from .presentation import AnimationTurnPacing
from .resources import Assets, Audio, user_dir
from .settings import dump_ai_seats, load_ai_seats

WHITE = (245, 246, 255)
GOLD = (253, 208, 77)
INK = (10, 18, 39)
COLORS = {"red": (231, 55, 63), "yellow": (248, 202, 53), "green": (39, 168, 94), "blue": (45, 122, 231)}
DECK = (279, 173)
DISCARD = (208, 180)


class App:
    def __init__(self, *, silent=False, skip_intro=False):
        pygame.init()
        pygame.display.set_caption("UNO - Show 'Em No Mercy | 36-card edition")
        self.window = pygame.display.set_mode((960, 720), pygame.RESIZABLE)
        self.canvas = pygame.Surface((960, 720)).convert()
        self.assets = Assets()
        self.ai_elo = load_ai_elo(self.assets.root / "ai_elo.json")
        self.plugin_elo = load_plugin_elo(self.assets.root / "ai_elo.json")
        self.directory = user_dir()
        try:
            self.settings = json.loads((self.directory / "settings.json").read_text(encoding="utf8"))
        except (OSError, ValueError):
            self.settings = {}
        self.ai_registry = AiRegistry(plugin_directory(self.directory))
        self.local_ai_seats = load_ai_seats(self.settings, self.ai_registry)
        self.ai_runner = AiRunner(self.ai_registry)
        self.ai_failure_log = []
        self.session = None
        self.audio = Audio(self.assets, silent or bool(self.settings.get("muted", False)))
        self.clock = pygame.time.Clock()
        self.running = True
        self.screen = "menu" if skip_intro else "intro"
        self.intro_started = time.monotonic()
        self.game = None
        self.view = None
        self.visual_scene = None
        self.animation_clock = time.monotonic()
        self.brightness = {}
        self._brightness_frame = -1
        self._wheel_state = None
        self._wheel_changed = 0
        self._choice_started = 0
        self._choice_phase = None
        self._hover_pose = None
        self.previous_view = None
        self.client = None
        self.server = None
        self.room = None
        self.round_key = None
        self.network_connected = False
        self.awaiting = False
        self.next_ai = 0
        self.rng = random.Random()
        self.ai_executor = None
        self.ai_pending = None
        self.player_count = 4
        self.ai_difficulty = self.settings.get("ai_difficulty", "normal")
        if self.ai_difficulty not in DIFFICULTIES:
            self.ai_difficulty = "normal"
        self.selected_ai_seat = 1
        self.config_target = None
        self.config_return = "local_setup"
        self.config_page = 0
        self.fields = {"name": self.settings.get("name", "Player"),
                       "address": self.settings.get("address", "127.0.0.1:8765"), "port": "8765"}
        self.focus = None
        self.buttons = []
        self.mouse = (0, 0)
        self.hovered_card = None
        self.selected_index = 0
        self.keyboard_selection = False
        self.moves = deque()
        self.current_move = None
        self.move_start = 0
        self.deal_started = 0
        self.deal_duration = 0
        self._last_dealt = -1
        self.toast = ""
        self.toast_until = 0
        self.message_menu = False
        self.local_messages = []
        self.help_page = 1
        self.help_return = "menu"
        self.confirm_god = None
        self.confirm_leave = False
        self.fullscreen = False
        self.old_window_size = (960, 720)
        self._last_chat = None
        self.audio.music()
        icon = pygame.transform.smoothscale(self.assets.card(), (32, 50))
        pygame.display.set_icon(icon)

    def notify(self, text, seconds=4):
        self.toast, self.toast_until = str(text), time.monotonic() + seconds

    def text(self, text, pos, size=13, color=WHITE, center=False, handwriting=True, max_width=None):
        text = str(text)
        font = self.assets.font(size, handwriting, any(ord(c) > 255 for c in text))
        surf = font.render(text, True, color)
        if max_width and surf.get_width() > max_width * 2:
            scale = max_width * 2 / surf.get_width()
            surf = pygame.transform.smoothscale(surf, (round(max_width * 2), max(1, round(surf.get_height() * scale))))
        rect = surf.get_rect()
        if center:
            rect.center = (round(pos[0] * 2), round(pos[1] * 2))
        else:
            rect.topleft = (round(pos[0] * 2), round(pos[1] * 2))
        self.canvas.blit(surf, rect)

    def panel(self, rect, color=(13, 26, 52, 235), border=None, radius=10):
        x, y, w, h = rect
        layer = pygame.Surface((round(w * 2), round(h * 2)), pygame.SRCALPHA)
        pygame.draw.rect(layer, color, layer.get_rect(), border_radius=round(radius * 2))
        if border:
            pygame.draw.rect(layer, border, layer.get_rect(), 2, border_radius=round(radius * 2))
        self.canvas.blit(layer, (round(x * 2), round(y * 2)))

    def button(self, text, rect, callback, *, enabled=True, selected=False, small=False, danger=False):
        r = pygame.Rect(rect)
        hover = r.collidepoint(self.mouse) and enabled
        color = (37, 81, 120, 245) if hover else (24, 50, 80, 242)
        if danger:
            color = (145, 22, 35, 255) if hover else (100, 12, 25, 255)
        if selected:
            color = (35, 100, 108, 250)
        if not enabled:
            color = (29, 36, 49, 220)
        self.panel(rect, color, GOLD if selected else (74, 133, 154) if enabled else (60, 70, 85), 5)
        self.text(text, r.center, 11 if small else 14, WHITE if enabled else (110, 122, 138), center=True, max_width=r.width - 8)
        if enabled:
            self.buttons.append((r, callback))

    def field(self, key, label, rect):
        r = pygame.Rect(rect)
        self.text(label, (r.x, r.y - 18), 12)
        self.panel(rect, (5, 13, 29, 245), GOLD if self.focus == key else (78, 115, 144), 4)
        text = self.fields[key] + ("|" if self.focus == key and int(time.monotonic() * 2) % 2 else "")
        self.text(text, (r.x + 8, r.y + 6), 13, max_width=r.width - 16)
        self.buttons.append((r, lambda: self.set_focus(key)))

    def set_focus(self, key):
        self.focus = key
        pygame.key.start_text_input()

    def image(self, target, name, rect, alpha=255):
        spec = self.assets.manifest["targets"][target]["costumes"][name]
        x, y, w, h = rect
        im = self.assets.scaled(spec["path"], round(w, 2), round(h, 2))
        if alpha != 255:
            im = im.copy()
            im.set_alpha(alpha)
        self.canvas.blit(im, (round(x * 2), round(y * 2)))

    def scratch_sprite(self, target, name, x, y, scale=1, angle=0, alpha=255, brightness=0):
        spec = self.assets.manifest["targets"][target]["costumes"][name]
        source = self.assets.image(spec["path"])
        if brightness:
            source = source.copy()
            amount = round(abs(brightness) * 255 / 100)
            source.fill((amount, amount, amount, 0), special_flags=pygame.BLEND_RGB_SUB if brightness < 0 else pygame.BLEND_RGB_ADD)
        raster = spec["raster_scale"]
        resolution = spec["bitmap_resolution"]
        factor = scale * 2 / (raster * resolution)
        im = pygame.transform.rotozoom(source, angle, factor)
        cx, cy = spec["center"]
        anchor_x = cx / resolution * scale * 2
        anchor_y = cy / resolution * scale * 2
        dx = source.get_width() * factor / 2 - anchor_x
        dy = source.get_height() * factor / 2 - anchor_y
        r = math.radians(angle)
        center = ((240 + x) * 2 + dx * math.cos(r) + dy * math.sin(r),
                  (180 - y) * 2 - dx * math.sin(r) + dy * math.cos(r))
        if alpha != 255:
            im.set_alpha(alpha)
        rect = im.get_rect(center=center)
        self.canvas.blit(im, rect)
        return im, rect

    def draw_card(self, card, x, y, angle=0, width=56.7, height=88.55, dim=False, alpha=255, brightness=0):
        if dim:
            brightness = -48
        im = self.assets.card_surface(card, width, height, angle, brightness)
        if alpha != 255:
            im = im.copy()
            im.set_alpha(alpha)
        self.canvas.blit(im, im.get_rect(center=(round(x * 2), round(y * 2))))

    def _start_local(self):
        self._disconnect()
        names = (self.fields["name"].strip() or "Player", "Ada", "Turing", "Grace")[:self.player_count]
        self.game = new_game(GameConfig(names), self.rng.randrange(65536))
        self.ai_failure_log = []
        controllers = {0: HumanController(), **{
            seat: AiController(self.local_ai_seats[seat]["plugin_id"],
                               dict(self.local_ai_seats[seat].get("settings", {})))
            for seat in range(1, self.player_count)}}
        self.session = GameSession(self.game, controllers)
        self.round_key = ("local", time.monotonic())
        self._new_round(self.game.view_for(0))
        self._save_local()

    def _new_round(self, view):
        self._reset_ai_search()
        self.screen = "game"
        self.view = view
        self.previous_view = None
        self.awaiting = False
        self.moves.clear()
        self.current_move = None
        self.deal_started = time.monotonic()
        self.deal_duration = 0
        self._last_dealt = -1
        self.next_ai = self.deal_started
        self.selected_index = 0
        self.audio.queue.clear()
        self.audio.music()
        timeline = opening(view, self._voice_duration)
        self.visual_scene = timeline.segments[0].before
        self.moves.append(timeline)
        self.animation_clock = time.monotonic()
        self.brightness.clear()
        self._brightness_frame = -1
        self._wheel_state = None
        self._choice_phase = None
        self._choice_fade_start = -100

    def _voice_duration(self, name):
        sound = self.audio.sound(name)
        return sound.get_length() if sound else 0.8

    def finish_animations(self):
        """Fast-forward presentation for reconnection and headless diagnostics."""
        self.moves.clear()
        self.current_move = None
        self.deal_duration = 0
        if self.view:
            self.visual_scene = Scene.from_view(self.view)

    def _save_local(self):
        if self.game:
            metadata = {seat: {"controller": "ai", "plugin_id": self.local_ai_seats[seat]["plugin_id"],
                               "version": self.ai_registry.spec(self.local_ai_seats[seat]["plugin_id"]).version,
                               "settings_sha256": settings_fingerprint(self.local_ai_seats[seat].get("settings", {}))}
                        for seat in range(1, self.player_count)}
            replay = (self.session.replay(metadata, self.ai_failure_log)
                      if self.session and self.session.game is self.game else self.game.replay())
            (self.directory / "last-local-replay.json").write_text(json.dumps(replay, indent=2), encoding="utf8")

    def _host(self):
        try:
            port = int(self.fields["port"])
            if not 1 <= port <= 65535:
                raise ValueError()
        except ValueError:
            self.notify("Port must be between 1 and 65535.")
            return
        self._disconnect()
        try:
            self.server = RoomServer(self.fields["name"] or "Host", port=port,
                                     ai_difficulty=self.ai_difficulty,
                                     registry=self.ai_registry, takeover_plugin_id=DEFAULT_PLUGIN_ID,
                                     pacing=AnimationTurnPacing(self._voice_duration),
                                     log_dir=self.directory / "replays", presentation_pacing=True,
                                     voice_lengths={name: self._voice_duration(name)
                                                    for name in self.assets.manifest['targets']['uno']['sounds']
                                                    if name.startswith('voice:')}).start()
            self.client = NetworkClient(f"127.0.0.1:{port}", self.fields["name"], self.server.host_token).start()
            self.screen = "room"
            self.notify("Opening room...")
        except OSError as exc:
            self.notify(f"Cannot host on port {port}: {exc}", 7)
            self.server = None

    def _join(self):
        self._disconnect()
        address = self.fields["address"].strip()
        tokens = self.settings.get("tokens", {})
        self.client = NetworkClient(address, self.fields["name"], tokens.get(address, "")).start()
        self.screen = "room"
        self.notify("Connecting...")

    def _disconnect(self):
        self._reset_ai_search()
        if self.ai_executor:
            self.ai_executor.shutdown(wait=False, cancel_futures=True)
            self.ai_executor = None
        # Stop host server first so it can deliver room_closed before client close.
        if self.server:
            self.server.stop()
            self.server = None
        if self.client:
            self.client.close()
            self.client = None
        self.room = None
        self.network_connected = False
        self.awaiting = False

    def leave(self):
        self._disconnect()
        self.game = self.view = self.session = None
        self.confirm_god = None
        self.confirm_leave = False
        self.message_menu = False
        self.screen = "menu"
        self.audio.queue.clear()
        self.audio.music()

    def _accept_view(self, view):
        if self.view and view["revision"] == self.view["revision"]:
            self.view = view
            return
        previous = self.view
        self.previous_view, self.view = previous, view
        self.awaiting = False
        if previous and view['revision'] == previous['revision'] + 1:
            self.moves.append(action_timeline(previous, view, self._voice_duration, self._hover_pose))
        else:
            self.finish_animations()
        self.selected_index = min(self.selected_index, max(0, len(view["hand"]) - 1))

    def _action(self, action):
        if not self.view or self.awaiting or self._busy() or self.confirm_leave:
            return
        try:
            if action['type'] == 'choose_color':
                self._choice_fade_start = time.monotonic()
            if self.client:
                if not self.network_connected or self.room.get("pending_return"):
                    return
                self.client.send("action", action=action, revision=self.view["revision"], round_id=self.room["round_id"])
                self.awaiting = True
            else:
                if self.session and self.session.game is self.game:
                    self.session.submit(0, action)
                else:
                    self.game.apply_action({**action, "player_id": 0})
                self._accept_view(self.game.view_for(0))
                self.next_ai = time.monotonic() + 0.6
                self._save_local()
        except RuleError as exc:
            self.notify(str(exc))

    def _room_command(self, kind, **data):
        if self.client and self.network_connected:
            self.client.send(kind, **data)

    def _rematch(self):
        if self.client:
            self._room_command("rematch")
        else:
            scores = tuple(self.game.scores) if self.game.match_winner is None else ()
            self.game = new_game(GameConfig(self.game.config.names, scores=scores), self.rng.randrange(65536))
            self.ai_failure_log = []
            controllers = {0: HumanController(), **{
                seat: AiController(self.local_ai_seats[seat]["plugin_id"],
                                   dict(self.local_ai_seats[seat].get("settings", {})))
                for seat in range(1, len(self.game.hands))}}
            self.session = GameSession(self.game, controllers)
            self._new_round(self.game.view_for(0))

    def _busy(self):
        return bool(self.current_move or self.moves or time.monotonic() < self.deal_started + self.deal_duration)

    def _reset_ai_search(self):
        self.ai_runner.reset()
        if self.ai_pending:
            self.ai_pending[2].cancel()
            self.ai_pending = None

    def _local_ai_action(self):
        seat = self.game.current
        selection = self.local_ai_seats.get(seat, {"plugin_id": DEFAULT_PLUGIN_ID, "settings": {}})
        plugin_id = selection["plugin_id"]
        legacy = PLUGIN_TO_LEGACY.get(plugin_id)
        uniform_legacy = legacy == self.ai_difficulty
        # One-release compatibility for callers that set the former public field
        # and inject its executor directly.
        if self.ai_executor is not None and self.ai_difficulty in ("devil", "god"):
            plugin_id, legacy, uniform_legacy = LEGACY_TO_PLUGIN[self.ai_difficulty], self.ai_difficulty, True
        if uniform_legacy and self.ai_difficulty == "god":
            legal = self.game.legal_actions(self.game.current)
            if len(legal) == 1:
                if self.ai_pending:
                    self.ai_pending[2].cancel()
                    self.ai_pending = None
                return {"player_id": self.game.current, **legal[0]}
        if uniform_legacy and self.ai_difficulty not in ("devil", "god"):
            if self.ai_pending:
                self.ai_pending[2].cancel()
                self.ai_pending = None
            return choose_action(self.game.view_for(self.game.current), self.rng, self.ai_difficulty)
        if uniform_legacy and self.ai_pending:
            game, revision, future = self.ai_pending
            if game is not self.game or revision != self.game.revision:
                self._reset_ai_search()
            elif not future.done():
                return None
            else:
                self.ai_pending = None
                return future.result()
        if uniform_legacy and self.ai_executor is None:
            self.ai_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="UnoDevil")
        if uniform_legacy:
            view = decision_view(self.game, self.ai_difficulty)
            rng = random.Random(self.rng.getrandbits(64))
            future = self.ai_executor.submit(choose_action, view, rng, self.ai_difficulty)
            self.ai_pending = (self.game, self.game.revision, future)
            return None
        action = self.ai_runner.tick(
            self.game, seat, AiController(plugin_id, dict(selection.get("settings", {}))),
            0 if self.ai_runner.pending else self.rng.getrandbits(64))
        for failure in self.ai_runner.pop_failures():
            self.ai_failure_log.append({"plugin_id": failure.plugin_id, "version": failure.version,
                                        "player_id": failure.player_id, "revision": failure.revision,
                                        "reason": failure.reason})
            self.notify(f"{self.ai_registry.spec(failure.plugin_id).name} failed; using Normal.", 7)
        return action

    def update(self):
        now = time.monotonic()
        self.audio.update()
        if self.client:
            while True:
                try:
                    msg = self.client.incoming.get_nowait()
                except queue.Empty:
                    break
                kind = msg["type"]
                if kind == "welcome":
                    if not self.server:
                        self.settings.setdefault("tokens", {})[self.fields["address"].strip()] = msg["token"]
                        self.save_settings()
                elif kind == "connected":
                    self.network_connected = True
                    self.awaiting = False
                elif kind == "snapshot":
                    self.room = msg["room"]
                    difficulty = self.room.get("ai_difficulty", "normal")
                    if self.room["is_host"] and difficulty in DIFFICULTIES and difficulty != self.ai_difficulty:
                        self.ai_difficulty = difficulty
                        self.save_settings()
                    view = self.room.get("game")
                    if view:
                        key = ("lan", self.room["round_id"])
                        if key != self.round_key:
                            self.round_key = key
                            self._new_round(view)
                            if view["revision"] > 0:
                                self.finish_animations()
                        else:
                            self._accept_view(view)
                    if self.room["chat"] and self.room["chat"][-1]["id"] != self._last_chat:
                        self._last_chat = self.room["chat"][-1]["id"]
                        last = self.room["chat"][-1]
                        self.notify(f'{last["name"]}: {last["text"]}', 5)
                elif kind == "reconnecting":
                    self.network_connected = False
                    self.awaiting = False
                    self.notify(msg["message"], 8)
                elif kind in ("error", "room_closed"):
                    self.awaiting = False
                    if msg.get("fatal") or kind == "room_closed":
                        message = msg.get("message", "Room closed.")
                        self.leave()
                        self.notify(message, 8)
                        break
                    self.notify(msg["message"], 5)
        if self.current_move and now - self.move_start >= self.current_move.duration:
            for name in self.current_move.sounds_due(self.current_move.duration):
                self.audio.play(name)
            self.visual_scene = self.current_move.final
            self.current_move = None
            if self.game and self.game.winner is None:
                forced = self.game.pending or self.game.phase == 'roulette'
                self.next_ai = now + self.rng.uniform(0.1, 0.3) if forced else now + self.rng.uniform(0.4, 1.0)
        if self.current_move is None and self.moves:
            self.current_move = self.moves.popleft()
            self.move_start = now
        if self.current_move:
            for name in self.current_move.sounds_due(now - self.move_start):
                self.audio.play(name)
            self.visual_scene = self.current_move.sample(now - self.move_start)[0]
        if self.game and self.screen == "game" and self.game.winner is None and not self.client:
            if self.game.current != 0 and now >= self.next_ai and not self._busy():
                action = self._local_ai_action()
                if action is not None:
                    self.game.apply_action(action)
                    self._accept_view(self.game.view_for(0))
                    self.next_ai = now + self.rng.uniform(0.4, 0.85)
                    self._save_local()
        if self.screen == "intro" and now - self.intro_started > 4.2 / ANIMATION_SPEED:
            self.screen = "menu"

    def draw_intro(self):
        elapsed = (time.monotonic() - self.intro_started) * ANIMATION_SPEED
        self.image("Stage", "no mercy", (0, 0, 480, 360))
        self.image("uno", "thumb", (0, 0, 480, 360), min(255, max(0, int((elapsed - 0.3) * 150))))
        self.panel((150, 328, 180, 22), (0, 0, 0, 170), radius=4)
        self.text("Click anywhere to skip", (240, 339), 11, center=True)

    def draw_menu(self):
        self.image("uno", "thumb", (0, 0, 480, 360))
        self.panel((266, 35, 194, 290), (7, 18, 40, 242), (57, 154, 176))
        self.text("SHOW 'EM NO MERCY", (363, 62), 17, GOLD, center=True, max_width=177)
        self.text("36-card edition", (363, 86), 13, center=True)
        self.button("Play with AI", (289, 112, 148, 36), lambda: self._set_screen("local_setup"))
        self.button("Create LAN room", (289, 160, 148, 36), lambda: self._set_screen("host_setup"))
        self.button("Join LAN room", (289, 208, 148, 36), lambda: self._set_screen("join_setup"))
        self.button("How to play", (289, 261, 148, 29), self.open_help, small=True)
        self.text("2-4 players  |  168 cards", (363, 308), 10, (167, 198, 214), center=True)

    def _set_screen(self, screen):
        self.screen = screen
        self.focus = None

    def _set_ai_difficulty(self, difficulty, *, room=False, confirmed=False):
        if difficulty not in DIFFICULTIES:
            raise ValueError("Unknown AI difficulty.")
        self.focus = None
        if difficulty == "god" and not confirmed:
            self.confirm_god = room
            return
        self.confirm_god = None
        if room:
            self._room_command("set_ai_difficulty", ai_difficulty=difficulty)
        else:
            self.ai_difficulty = difficulty
            plugin_id = LEGACY_TO_PLUGIN[difficulty]
            for seat in self.local_ai_seats.values():
                seat["plugin_id"], seat["settings"] = plugin_id, {}
            self.save_settings()

    def _set_seat_ai(self, seat_id, plugin_id, *, room=False, confirmed=False):
        plugin_id = normalize_plugin_id(plugin_id)
        if not self.ai_registry.has(plugin_id):
            self.notify("That AI plugin is not available.")
            return
        if plugin_id == LEGACY_TO_PLUGIN["god"] and not confirmed:
            self.confirm_god = ("seat", seat_id, plugin_id, room)
            return
        self.confirm_god = None
        if room:
            self._room_command("set_seat_ai", seat_id=seat_id, plugin_id=plugin_id, settings={})
        else:
            self.local_ai_seats[seat_id] = {"plugin_id": plugin_id, "settings": {},
                                            "requested_plugin_id": plugin_id, "config_reset": False}
            selected = {self.local_ai_seats[i]["plugin_id"] for i in range(1, self.player_count)}
            if len(selected) == 1 and plugin_id in PLUGIN_TO_LEGACY:
                self.ai_difficulty = PLUGIN_TO_LEGACY[plugin_id]
            self.save_settings()

    def _cycle_seat_ai(self, seat_id, direction=1, *, room=False):
        specs = self.ai_registry.specs
        if room:
            current = next(s for s in self.room["seats"] if s["id"] == seat_id)["ai"]["id"]
        else:
            current = self.local_ai_seats[seat_id]["plugin_id"]
        ids = [spec.id for spec in specs]
        index = ids.index(current) if current in ids else 0
        self._set_seat_ai(seat_id, ids[(index + direction) % len(ids)], room=room)

    def _open_plugin_config(self, seat_id, *, room=False):
        self.config_target = ("room" if room else "local", seat_id)
        self.config_return = "room" if room else "ai_setup"
        self.config_page = 0
        self.screen = "plugin_config"

    def _config_state(self):
        scope, seat_id = self.config_target
        if scope == "local":
            entry = self.local_ai_seats[seat_id]
            return entry["plugin_id"], dict(entry.get("settings", {}))
        seat = next(item for item in self.room["seats"] if item["id"] == seat_id)
        return seat["ai"]["id"], dict(seat["ai"].get("settings", {}))

    def _change_plugin_setting(self, key, direction=1):
        scope, seat_id = self.config_target
        plugin_id, values = self._config_state()
        schema = {item.key: item for item in self.ai_registry.spec(plugin_id).settings_schema}
        item = schema[key]
        current = values.get(key, item.default)
        if item.type == "boolean":
            value = not current
        elif item.type == "enum":
            value = item.options[(item.options.index(current) + direction) % len(item.options)]
        else:
            step = item.step or (1 if item.type == "integer" else 0.1)
            value = current + direction * step
            if item.minimum is not None:
                value = max(item.minimum, value)
            if item.maximum is not None:
                value = min(item.maximum, value)
            if item.type == "integer":
                value = int(value)
        values[key] = value
        values = dict(self.ai_registry.normalize_settings(plugin_id, values))
        if scope == "local":
            self.local_ai_seats[seat_id]["settings"] = values
            self.save_settings()
        else:
            next(item for item in self.room["seats"] if item["id"] == seat_id)["ai"]["settings"] = values
            self._room_command("set_seat_ai", seat_id=seat_id, plugin_id=plugin_id, settings=values)

    def draw_ai_difficulty(self, y, *, room=False):
        difficulty = self.room.get("ai_difficulty", "normal") if room else self.ai_difficulty
        for i, level in enumerate(DIFFICULTIES):
            self.button(self.difficulty_label(level, room=room), (43 + 99 * i, y, 95, 26),
                        lambda level=level: self._set_ai_difficulty(level, room=room),
                        selected=difficulty == level, small=True,
                        enabled=not room or self.room.get("is_host", False) and self.network_connected)

    def difficulty_label(self, difficulty, *, room=False):
        if difficulty == "god":
            return "God"
        ratings = self.room.get("ai_elo", {}) if room else self.ai_elo
        rating = ratings.get(difficulty) if isinstance(ratings, dict) else None
        if type(rating) in (int, float) and math.isfinite(rating):
            return f"{difficulty.title()} {rating:.0f}"
        return difficulty.title()

    def draw_setup(self):
        self.image("lobby", "lobby", (0, 0, 480, 360))
        self.panel((24, 68, 432, 285), (6, 18, 45, 245))
        self.field("name", "Your name", (58, 103, 362, 31))
        if self.screen == "local_setup":
            self.text("Choose the number of players", (240, 155), 16, GOLD, center=True)
            for i, n in enumerate((2, 3, 4)):
                x = 98 + i * 104
                self.image("lobby", f"{n}p", (x, 176, 48, 48))
                self.button(f"{n} players", (x - 10, 230, 76, 29), lambda n=n: setattr(self, "player_count", n), selected=self.player_count == n, small=True)
            self.draw_ai_difficulty(268)
            self.button("Configure AI", (164, 302, 96, 32), lambda: self._set_screen("ai_setup"), small=True)
            self.button("Start game", (270, 302, 150, 32), self._start_local)
        elif self.screen == "host_setup":
            self.field("port", "Room port", (58, 174, 150, 31))
            self.text("Friends join using your LAN IP and port.", (58, 221), 12)
            self.text("Allow the game on Windows private networks.", (58, 242), 11, (160, 192, 208))
            self.draw_ai_difficulty(268)
            self.button("Create room", (270, 302, 150, 32), self._host)
        else:
            self.field("address", "Host IP:port", (58, 174, 362, 31))
            self.text("Example: 192.168.1.10:8765", (58, 220), 12, (160, 192, 208))
            self.text("Reconnect with the same saved player token.", (58, 247), 11, (160, 192, 208))
            self.button("Join room", (270, 302, 150, 32), self._join)
        self.button("Back", (58, 302, 95, 32), lambda: self._set_screen("menu"))

    def draw_ai_setup(self):
        self.image("lobby", "lobby", (0, 0, 480, 360))
        self.panel((24, 52, 432, 301), (6, 18, 45, 248))
        self.text("AI BY SEAT", (240, 73), 18, GOLD, center=True)
        self.text("Click an AI name to cycle installed plugins.", (240, 94), 11, center=True)
        for row, seat_id in enumerate(range(1, self.player_count)):
            y = 116 + row * 61
            entry = self.local_ai_seats[seat_id]
            spec = self.ai_registry.spec(entry["plugin_id"])
            self.text(f"Seat {seat_id + 1}", (53, y + 10), 13, GOLD)
            self.button("<", (112, y, 30, 30),
                        lambda seat_id=seat_id: self._cycle_seat_ai(seat_id, -1), small=True)
            rating = self.plugin_elo.get(spec.id)
            label = f"{spec.name} {rating:.0f}" if type(rating) in (int, float) else spec.name
            self.button(label, (147, y, 192, 30),
                        lambda seat_id=seat_id: self._cycle_seat_ai(seat_id), small=True)
            self.button(">", (344, y, 30, 30),
                        lambda seat_id=seat_id: self._cycle_seat_ai(seat_id), small=True)
            self.button("Config", (381, y, 54, 30),
                        lambda seat_id=seat_id: self._open_plugin_config(seat_id),
                        enabled=bool(spec.settings_schema), small=True)
            self.text(f"{spec.id}  v{spec.version}", (147, y + 35), 9, (151, 184, 204), max_width=285)
        if self.ai_registry.diagnostics:
            self.text(f"{len(self.ai_registry.diagnostics)} plugin(s) could not be loaded.",
                      (240, 289), 10, (244, 137, 137), center=True)
        self.button("Back", (90, 310, 95, 32), lambda: self._set_screen("local_setup"))
        self.button("Start game", (270, 310, 150, 32), self._start_local)

    def draw_plugin_config(self):
        self.image("lobby", "lobby", (0, 0, 480, 360))
        self.panel((24, 48, 432, 305), (6, 18, 45, 248))
        plugin_id, values = self._config_state()
        spec = self.ai_registry.spec(plugin_id)
        self.text(spec.name, (240, 69), 18, GOLD, center=True)
        self.text(spec.description or spec.id, (240, 91), 10, center=True, max_width=390)
        if not spec.settings_schema:
            self.text("This plugin has no configurable settings.", (240, 180), 13, center=True)
        page_size = 5
        pages = max(1, math.ceil(len(spec.settings_schema) / page_size))
        self.config_page = min(self.config_page, pages - 1)
        visible = spec.settings_schema[self.config_page * page_size:(self.config_page + 1) * page_size]
        for row, item in enumerate(visible):
            y = 115 + row * 34
            value = values.get(item.key, item.default)
            self.text(item.name, (53, y + 8), 11, max_width=170)
            self.button("-", (239, y, 28, 26),
                        lambda key=item.key: self._change_plugin_setting(key, -1), small=True)
            self.text(str(value), (318, y + 13), 11, GOLD, center=True, max_width=88)
            self.button("+", (374, y, 28, 26),
                        lambda key=item.key: self._change_plugin_setting(key, 1), small=True)
        if pages > 1:
            self.button("<", (183, 284, 32, 24),
                        lambda: setattr(self, "config_page", max(0, self.config_page - 1)),
                        enabled=self.config_page > 0, small=True)
            self.text(f"{self.config_page + 1}/{pages}", (240, 296), 10, center=True)
            self.button(">", (265, 284, 32, 24),
                        lambda: setattr(self, "config_page", min(pages - 1, self.config_page + 1)),
                        enabled=self.config_page + 1 < pages, small=True)
        self.button("Back", (90, 310, 95, 32), lambda: self._set_screen(self.config_return))

    def draw_room(self):
        self.image("lobby", "lobby", (0, 0, 480, 360))
        self.panel((22, 66, 436, 288), (5, 15, 38, 248))
        if not self.room:
            self.text("Connecting to room...", (240, 165), 18, center=True)
            self.button("Cancel", (175, 285, 130, 32), self.leave)
            return
        host = self.room["is_host"]
        addr = f'{local_addresses()[0]}:{self.room["port"]}' if host else self.fields["address"]
        self.text("LAN ROOM", (43, 78), 17, GOLD)
        self.text(addr, (43, 105), 12)
        if host:
            self.button("Copy IP", (366, 81, 70, 25), lambda: self.copy_address(addr), small=True)
        for index, seat in enumerate(self.room["seats"]):
            y = 131 + index * 32
            self.panel((40, y, 396, 30), (19, 41, 65, 245), radius=4)
            suffix = " (you)" if seat["id"] == self.room["you"] else ""
            self.text(seat["name"] + suffix, (50, y + 5), 13, GOLD if seat["id"] == 0 else WHITE, max_width=225)
            status = (seat.get("ai", {}).get("name", "AI") if seat["bot"] else
                      "Host" if seat["id"] == 0 else "Ready" if seat["ready"] else
                      "Waiting" if seat["connected"] else "Offline")
            self.text(status, (320, y + 6), 11, (108, 220, 184))
            if host and seat["bot"]:
                self.button(status, (274, y + 3, 121, 24),
                            lambda seat=seat: self._cycle_seat_ai(seat["id"], room=True), small=True)
                try:
                    configurable = bool(self.ai_registry.spec(seat["ai"]["id"]).settings_schema)
                except ValueError:
                    configurable = False
                if configurable:
                    self.button("cfg", (240, y + 3, 31, 24),
                                lambda seat=seat: self._open_plugin_config(seat["id"], room=True), small=True)
            if host and seat["id"] != 0:
                self.button("x", (403, y + 3, 25, 24), lambda seat=seat: self._room_command("remove_seat", seat_id=seat["id"]), small=True)
        self.draw_ai_difficulty(262, room=True)
        if host:
            self.button("Add AI", (43, 292, 94, 31), lambda: self._room_command("add_ai"), enabled=len(self.room["seats"]) < 4)
            self.button("Start", (333, 292, 103, 31), lambda: self._room_command("start"), enabled=len(self.room["seats"]) >= 2)
        else:
            yours = next(s for s in self.room["seats"] if s["id"] == self.room["you"])
            self.button("Unready" if yours["ready"] else "Ready", (333, 292, 103, 31), lambda: self._room_command("ready"))
        self.button("Leave", (170, 292, 94, 31), self.leave)
        self.text("36 cards = mercy  |  Guests ready up before starting", (240, 340), 10, center=True)

    def copy_address(self, address):
        try:
            pygame.scrap.put_text(address)
            self.notify("Room address copied.")
        except pygame.error:
            self.notify(address)

    def _draw_players(self, view):
        you, count = view["you"], len(view["players"])
        for p in view["players"]:
            pid = p["id"]
            offset = (pid - you) % count
            if offset == 0:
                pos = (199, 349)
            elif count == 2 or count == 4 and offset == 2:
                pos = (240, 14)
            elif offset == 1:
                pos = (72, 218)
            else:
                pos = (407, 218)
            self.panel((pos[0] - 66, pos[1] - 10, 132, 22), (4, 13, 25, 210), GOLD if view["current"] == pid and view["winner"] is None else None, 4)
            label = f'{p["name"]}  |  {"OUT" if p["eliminated"] else p["count"]}  |  {p["score"]}'
            self.text(label, pos, 10, GOLD if view["current"] == pid else WHITE, center=True, max_width=126)

    def draw_game(self):
        if not self.view:
            return
        if not self._busy() and (not self.visual_scene or self.visual_scene.view['revision'] != self.view['revision']):
            self.visual_scene = Scene.from_view(self.view)
        # Freeze one sample for the entire rendered frame. Sampling again after
        # drawing the table can cross a segment/end boundary and mix two scenes.
        animation_frame = self.current_move.sample(time.monotonic() - self.move_start) if self.current_move else None
        scene = animation_frame[0] if animation_frame else self.visual_scene or Scene.from_view(self.view)
        v = scene.visible_view()
        self.canvas.blit(self.assets.scaled(self.assets.manifest["table"], 480, 360), (0, 0))
        self._draw_wheel(scene)
        layers = min(8, scene.deck_count)
        for i in range(layers):
            pos = pile_position(True, 1 + round(i * max(0, scene.deck_count - 1) / max(1, layers - 1)))
            if i == layers - 1 and not self._busy() and scene.view['current'] == scene.view['you']:
                x, y, _, w, h = pos
                if x - w / 2 < self.mouse[0] < x + w / 2 and y - h / 2 < self.mouse[1] < y + h / 2:
                    pos = hover_layout([pos], 0, time.monotonic() - self.animation_clock)[0]
            self.draw_card(None, *pos)
        if scene.top:
            for i in range(min(4, scene.discard_count)):
                self.draw_card(scene.top, *pile_position(False, max(1, scene.discard_count - (3 - i) * 14)))
        segment = self._draw_scene_cards(scene, animation_frame)
        if segment and segment.kind in ('discard_all', 'mercy') and scene.top:
            self.draw_card(scene.top, *pile_position(False, scene.discard_count))
        self._draw_players(v)
        if segment and segment.kind == 'opening_fade':
            progress = animation_frame[2]
            self.image('transition', 'no mercy', (0, 0, 480, 360), round(255 * (1 - progress)))
        elif segment and segment.kind == 'pause' and not scene.top and scene.deck_count == 168:
            self.image('transition', 'no mercy', (0, 0, 480, 360))
        self.button("Menu", (7, 6, 47, 23), lambda: setattr(self, "confirm_leave", True), small=True)
        self.button("?", (444, 6, 28, 23), self.open_help, small=True)
        self.button("Messages", (393, 329, 79, 23), lambda: setattr(self, "message_menu", not self.message_menu), small=True)
        draw_allowed = {"type": "draw"} in self.view["legal"]
        if draw_allowed and not self._busy() and not self.awaiting:
            self.buttons.append((pygame.Rect(DECK[0] - 32, DECK[1] - 45, 64, 94), lambda: self._action({"type": "draw"})))
            label = f'Draw +{v["pending"]}' if v["pending"] else "Draw"
            self.button(label, (249, 230, 62, 22), lambda: self._action({"type": "draw"}), small=True)
        if v["pending"]:
            self.panel((172, 230, 73, 22), (138, 24, 34, 245), radius=5)
            self.text(f'+{v["pending"]} stacked', (208, 241), 11, center=True)
        if v["phase"] == "roulette":
            self.text("Draw until " + v["color"], (240, 115), 13, GOLD, center=True)
        elif v["winner"] is None:
            text = "Your turn" if v["current"] == v["you"] else v["players"][v["current"]]["name"] + "'s turn"
            if self.room and self.room["pending_return"]:
                text = "AI control until your next turn"
            if self.client and not self.network_connected:
                text = "Reconnecting..."
            self.text(text, (240, 115), 12, WHITE, center=True)
        if self.view["phase"] in ("choose_color", "choose_player") and self.view["current"] == self.view["you"] and not self._busy():
            self.draw_choice()
        elif time.monotonic() < getattr(self, '_choice_fade_start', -100) + 20 / FPS:
            self._draw_color_selector(fading=True)
        else:
            self._choice_phase = None
        if self.message_menu:
            self.draw_messages()
        if self.view["winner"] is not None and not self._busy():
            self.draw_result()

    def _draw_wheel(self, scene):
        if not scene.wheel:
            return
        now = time.monotonic()
        state = (scene.view['direction'], scene.view['color'])
        if state != self._wheel_state:
            self._old_wheel, self._wheel_state = self._wheel_state, state
            self._wheel_changed = now
        age = (now - self._wheel_changed) * FPS
        source_phase = (now - self.animation_clock) * FPS
        def layer(state, alpha):
            direction, color = state
            name = 'direction' if direction == 1 else 'direction2'
            path = self.assets.manifest['wheels'][f'{name}:{color}']
            source = self.assets.shaded_scaled(path, 240, 240, -20)
            im = pygame.transform.rotate(source, -direction * (source_phase % 360))
            im.set_alpha(round(max(0, min(255, alpha))))
            self.canvas.blit(im, im.get_rect(center=(480, 360)))
        old = getattr(self, '_old_wheel', None)
        if old and age < 25:
            layer(old, 255 * max(0, 1 - age / (12.5 if old[0] == state[0] else 25)))
        layer(state, 255 if old and old[0] == state[0] else 255 * min(1, age / 25))

    def _draw_scene_cards(self, scene, animation_frame=None):
        segment = None
        if animation_frame is not None:
            _, cards, _, segment = animation_frame
        else:
            cards = scene.cards()
        local = scene.hands[scene.view['you']]
        legal = {a.get('card_id') for a in self.view['legal'] if a['type'] == 'play'}
        scene_legal = {a.get('card_id') for a in scene.view['legal'] if a['type'] == 'play'}
        source_phase = (time.monotonic() - self.animation_clock) * FPS
        steps = max(0, source_phase - self._brightness_frame)
        self._brightness_frame = source_phase
        local_ids = {t.card['id'] for t in local if t.card}
        if segment and segment.moving and segment.moving.card and segment.target_owner == scene.view['you']:
            local_ids.add(segment.moving.card['id'])
        active = not self._busy() and self.view['current'] == self.view['you'] and self.view['phase'] == 'turn'
        layout = hand_layout(scene.view['you'], scene.view['you'], len(scene.hands), len(local))
        if active and not self.message_menu and not self.confirm_leave:
            opened = hover_layout(layout, getattr(self, '_hover_index', None), 0)
            self.hovered_card = self.selected_index if self.keyboard_selection else hit_card(self.mouse, opened)
            self._hover_index = self.hovered_card
            layout = hover_layout(layout, self.hovered_card, time.monotonic() - self.animation_clock)
            self._interactive_layout = layout
        else:
            self._hover_index = self.hovered_card = None
            self._interactive_layout = layout
        replacements = {t.card['id']: pos for t, pos in zip(local, layout) if t.card}
        self._hover_pose = layout[self.hovered_card] if self.hovered_card is not None and self.hovered_card < len(layout) else None
        for card, pos, back_alpha in cards:
            brightness = 0
            if card and card['id'] in local_ids:
                cid = card['id']
                target = 0 if scene.wheel and scene.view['current'] == scene.view['you'] and cid in scene_legal else -48
                if segment and segment.kind in ('deal', 'first_discard', 'opening_fade'):
                    target = -48
                value = self.brightness.get(cid, target if segment and segment.kind == 'deal' else 0)
                delta = target - value
                brightness = value + max(-steps * 4, min(steps * 4, delta))
                self.brightness[cid] = brightness
                if not self.current_move:
                    pos = replacements.get(cid, pos)
            self.draw_card(card, *pos, brightness=brightness)
            if card and back_alpha:
                self.draw_card(None, *pos, alpha=back_alpha)
        return segment

    def draw_choice(self):
        v = self.view
        if v['phase'] == 'choose_color':
            self._draw_color_selector()
            return
        self.panel((105, 88, 270, 174), (7, 15, 34, 250), GOLD)
        # Swallow all underlying card/deck clicks while choosing.
        self.buttons = []
        self.text("Swap hands with...", (240, 112), 19, GOLD, center=True)
        for i, action in enumerate(v["legal"]):
            target = action["target"]
            player = v["players"][target]
            self.button(f'{player["name"]} - {player["count"]} cards', (130, 142 + i * 36, 220, 30), lambda action=action: self._action(action), small=True)

    def _draw_color_selector(self, fading=False):
        now = time.monotonic()
        if self._choice_phase != 'choose_color' and not fading:
            self._choice_phase, self._choice_started = 'choose_color', now
            self._selector_brightness = {c: -40 for c in ('blue', 'green', 'red', 'yellow')}
            self._selector_frame = 0
        source_phase = (now - self._choice_started) * FPS
        scale = 1 + 0.02 * math.sin(math.radians(source_phase * 5))
        angle = -2 * math.sin(math.radians(source_phase * 6))
        alpha = round(255 * min(1, source_phase / 20))
        if fading:
            alpha = round(255 * max(0, 1 - (now - self._choice_fade_start) * FPS / 20))
        if not fading:
            self.buttons = []
        self.choice_regions = []
        self.scratch_sprite('uno', 'select', 0, 36, scale, angle, alpha)
        for color in ('blue', 'green', 'red', 'yellow'):
            name = 'select.' + color
            target = 0 if getattr(self, '_selector_hover', None) == color else -40
            previous = getattr(self, '_selector_brightness', {}).get(color, -40)
            steps = max(0, source_phase - getattr(self, '_selector_frame', 0))
            brightness = previous + max(-steps * 4, min(steps * 4, target - previous))
            self._selector_brightness[color] = brightness
            image, rect = self.scratch_sprite('uno', name, 0, 36, scale, angle, alpha, brightness)
            self.choice_regions.append((pygame.mask.from_surface(image, 1), rect, color))
        self._selector_hover = self._color_at(self.mouse)
        self._selector_frame = source_phase

    def _color_at(self, point):
        x, y = round(point[0] * 2), round(point[1] * 2)
        for mask, rect, color in getattr(self, 'choice_regions', []):
            if rect.collidepoint(x, y) and mask.get_at((x - rect.x, y - rect.y)):
                return color
        return None

    def draw_messages(self):
        self.panel((112, 36, 356, 288), (6, 18, 37, 252), (91, 170, 186))
        self.buttons = []
        self.text("Send message", (285, 52), 17, GOLD, center=True)
        for i, message in enumerate(PRESET_MESSAGES):
            self.button(message, (122 + i % 2 * 169, 72 + i // 2 * 23, 160, 21), lambda i=i: self.send_message(i), small=True)
        self.button("Close", (383, 300, 70, 20), lambda: setattr(self, "message_menu", False), small=True)

    def send_message(self, index):
        if self.client:
            self._room_command("chat", index=index)
        else:
            self.notify(f'{self.fields["name"]}: {PRESET_MESSAGES[index]}', 4)
            self.audio.play("sfx: bloop")
        self.message_menu = False

    def draw_result(self):
        self.panel((97, 89, 286, 193), (5, 17, 38, 248), GOLD)
        self.buttons = []
        winner = self.view["players"][self.view["winner"]]
        title = winner["name"] + (" wins the match!" if self.view["match_winner"] is not None else " wins!")
        self.text(title, (240, 117), 27, GOLD, center=True, max_width=260)
        self.text(f'+{self.view["round_points"]} points this round', (240, 149), 14, center=True)
        for i, player in enumerate(self.view["players"]):
            self.text(f'{player["name"]}: {player["score"]}', (135 + (i % 2) * 120, 176 + (i // 2) * 23), 12)
        if not self.client or self.room["is_host"]:
            self.button("New match" if self.view["match_winner"] is not None else "Next round", (121, 236, 126, 29), self._rematch)
        else:
            self.text("Waiting for host", (185, 251), 12, center=True)
        self.button("Leave", (265, 236, 95, 29), self.leave)

    def open_help(self):
        self.help_return = self.screen
        self.help_page = 1
        self.screen = "help"

    def draw_help(self):
        self.canvas.fill((8, 16, 35))
        spec = self.assets.manifest["targets"]["instructions"]["costumes"][f"page {self.help_page}"]
        im = self.assets.image(spec["path"])
        scale = min(912 / im.get_width(), 550 / im.get_height())
        im = pygame.transform.smoothscale(im, (round(im.get_width() * scale), round(im.get_height() * scale)))
        self.canvas.blit(im, im.get_rect(center=(480, 341)))
        if self.help_page == 1:
            # This source SVG embeds a raster page, so changing SVG text cannot
            # update its printed rule. Re-typeset that page in the bundled font.
            self.panel((35, 42, 410, 268), (0, 0, 0, 255), WHITE, 10)
            self.text("What is UNO Show 'Em No Mercy?", (240, 60), 18, GOLD, center=True)
            lines = ["Match a colour, number or symbol to play a card.",
                     "Draw cards until you can play. The game adds stacking,",
                     "hand swapping and six especially tough action cards.", "",
                     "Win a round by emptying your hand or by being the",
                     "last player remaining. Reach 36 cards and you are out!",
                     "Eliminated hands return underneath the discard pile.", "",
                     "Round winners score the cards their opponents hold:",
                     "numbers = face value; actions = 20; wild cards = 50.",
                     "Each eliminated opponent is worth 250 points.", "",
                     "Match targets: 2 players = 500, 3 = 750, 4 = 1000.",
                     "UNO is called automatically, just like the original."]
            for i, line in enumerate(lines):
                self.text(line, (49, 82 + i * 15), 11, WHITE, max_width=382)
        self.panel((12, 7, 456, 32), (102, 29, 34, 255), GOLD, 5)
        self.text("36 cards = eliminated. All other rules follow this Scratch project.", (240, 23), 11, center=True, max_width=440)
        self.button("<", (20, 322, 40, 28), lambda: setattr(self, "help_page", max(1, self.help_page - 1)), enabled=self.help_page > 1)
        self.text(f'{self.help_page} / 8', (88, 336), 12, center=True)
        self.button(">", (116, 322, 40, 28), lambda: setattr(self, "help_page", min(8, self.help_page + 1)), enabled=self.help_page < 8)
        self.button("Back", (365, 322, 95, 28), lambda: self._set_screen(self.help_return))

    def draw(self):
        self.canvas.fill(INK)
        self.buttons = []
        self.hovered_card = None
        if self.screen == "intro":
            self.draw_intro()
        elif self.screen == "menu":
            self.draw_menu()
        elif self.screen == "ai_setup":
            self.draw_ai_setup()
        elif self.screen == "plugin_config":
            self.draw_plugin_config()
        elif self.screen.endswith("_setup"):
            self.draw_setup()
        elif self.screen == "room":
            self.draw_room()
        elif self.screen == "game":
            self.draw_game()
        elif self.screen == "help":
            self.draw_help()
        if self.confirm_god is not None:
            self.buttons = []
            self.panel((52, 112, 376, 138), (10, 19, 36, 255), GOLD)
            self.text("God is omniscient,", (240, 139), 20, GOLD, center=True)
            self.text("are you sure to defy God's will?", (240, 174), 17, center=True)
            if isinstance(self.confirm_god, tuple):
                _, seat_id, plugin_id, room = self.confirm_god
                yes = lambda: self._set_seat_ai(seat_id, plugin_id, room=room, confirmed=True)
            else:
                yes = lambda: self._set_ai_difficulty("god", room=self.confirm_god, confirmed=True)
            self.button("Yes", (112, 209, 100, 28), yes, danger=True)
            self.button("No", (268, 209, 100, 28), lambda: setattr(self, "confirm_god", None))
        if self.confirm_leave:
            self.buttons = []
            self.panel((90, 118, 300, 115), (10, 19, 36, 252), GOLD)
            self.text("Leave this game?", (240, 143), 22, GOLD, center=True)
            self.text("The host leaving closes the LAN room.", (240, 170), 11, center=True)
            self.button("Stay", (120, 193, 100, 28), lambda: setattr(self, "confirm_leave", False))
            self.button("Leave", (260, 193, 100, 28), self.leave)
        if time.monotonic() < self.toast_until:
            self.panel((32, 39, 416, 30), (8, 17, 34, 244), (116, 185, 195), 5)
            self.text(self.toast, (240, 54), 12, center=True, max_width=400)
        if self.confirm_god is None and self.screen not in ("game", "help", "intro"):
            self.button("Sound off" if self.audio.muted else "Sound on", (5, 330, 74, 23), self.toggle_sound, small=True)
        ox, oy, scale = viewport(self.window.get_size())
        size = (round(480 * scale), round(360 * scale))
        self.window.fill((0, 0, 0))
        frame = self.canvas if size == self.canvas.get_size() else pygame.transform.smoothscale(self.canvas, size)
        self.window.blit(frame, (round(ox), round(oy)))
        pygame.display.flip()

    def toggle_sound(self):
        self.audio.set_muted(not self.audio.muted)
        self.save_settings()

    def save_settings(self):
        self.settings.update(name=self.fields["name"], address=self.fields["address"], muted=self.audio.muted,
                             ai_difficulty=self.ai_difficulty,
                             settings_version=2, local_ai_seats=dump_ai_seats(self.local_ai_seats))
        (self.directory / "settings.json").write_text(json.dumps(self.settings, indent=2), encoding="utf8")

    def handle(self, event):
        if event.type == pygame.QUIT:
            self.running = False
        elif event.type == pygame.VIDEORESIZE and not self.fullscreen:
            self.window = pygame.display.set_mode((max(480, event.w), max(360, event.h)), pygame.RESIZABLE)
        elif event.type == pygame.MOUSEMOTION:
            self.keyboard_selection = False
            self.mouse = mouse_to_logical(event.pos, self.window.get_size())
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.keyboard_selection = False
            self.mouse = mouse_to_logical(event.pos, self.window.get_size())
            if self.screen == 'game' and self.view and self.view['phase'] == 'choose_color' and not self._busy():
                self._pressed_color = self._color_at(self.mouse)
                return
            if self.screen == "intro":
                self.screen = "menu"
                return
            for rect, callback in reversed(self.buttons):
                if rect.collidepoint(self.mouse):
                    callback()
                    return
            self.focus = None
            if self.screen == "game" and self.view and not self.message_menu and not self.confirm_leave and self.view["phase"] == "turn":
                layout = getattr(self, '_interactive_layout', hand_layout(self.view["you"], self.view["you"], len(self.view["players"]), len(self.view["hand"])))
                index = hit_card(self.mouse, layout)
                if index is not None:
                    self.selected_index = index
                    self._action({"type": "play", "card_id": self.view["hand"][index]["id"]})
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.mouse = mouse_to_logical(event.pos, self.window.get_size())
            if self.screen == 'game' and self.view and self.view['phase'] == 'choose_color' and not self._busy():
                color = self._color_at(self.mouse)
                if color and color == getattr(self, '_pressed_color', None):
                    self.audio.play('sfx: game start beep')
                    self._action({'type': 'choose_color', 'color': color})
                self._pressed_color = None
        elif event.type == pygame.TEXTINPUT and self.focus:
            value = ''.join(c for c in event.text if c.isprintable())
            if self.focus == "port":
                value = ''.join(c for c in value if c.isdigit())
            limit = {"name": 16, "address": 100, "port": 5}[self.focus]
            self.fields[self.focus] = (self.fields[self.focus] + value)[:limit]
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_F11:
                self.fullscreen = not self.fullscreen
                if self.fullscreen:
                    self.old_window_size = self.window.get_size()
                self.window = pygame.display.set_mode((0, 0) if self.fullscreen else self.old_window_size,
                                                       pygame.FULLSCREEN if self.fullscreen else pygame.RESIZABLE)
            elif event.key == pygame.K_ESCAPE:
                if self.confirm_god is not None:
                    self.confirm_god = None
                elif self.screen == "intro":
                    self.screen = "menu"
                elif self.screen == "help":
                    self.screen = self.help_return
                elif self.screen == "plugin_config":
                    self.screen = self.config_return
                elif self.screen == "ai_setup":
                    self.screen = "local_setup"
                elif self.message_menu:
                    self.message_menu = False
                elif self.screen == "game":
                    self.confirm_leave = not self.confirm_leave
                elif self.screen != "menu":
                    self.leave()
            elif self.focus:
                if event.key == pygame.K_BACKSPACE:
                    self.fields[self.focus] = self.fields[self.focus][:-1]
                elif event.key == pygame.K_a and event.mod & pygame.KMOD_CTRL:
                    self.fields[self.focus] = ""
                elif event.key in (pygame.K_RETURN, pygame.K_TAB):
                    self.focus = None
            elif event.key == pygame.K_m:
                self.toggle_sound()
            elif self.screen == "game" and self.view and not self.confirm_leave and not self.message_menu:
                hand = self.view["hand"]
                if event.key in (pygame.K_LEFT, pygame.K_RIGHT) and hand:
                    self.keyboard_selection = True
                    self.selected_index = (self.selected_index + (1 if event.key == pygame.K_RIGHT else -1)) % len(hand)
                    x, y, *_ = hand_layout(self.view["you"], self.view["you"], len(self.view["players"]), len(hand))[self.selected_index]
                    self.mouse = (x - 18, y)
                elif event.key == pygame.K_RETURN and hand:
                    self._action({"type": "play", "card_id": hand[self.selected_index]["id"]})
                elif event.key in (pygame.K_d, pygame.K_SPACE):
                    self._action({"type": "draw"})

    def run(self, *, max_frames=None, screenshot=None):
        frames = 0
        try:
            while self.running and (max_frames is None or frames < max_frames):
                self.mouse = mouse_to_logical(pygame.mouse.get_pos(), self.window.get_size())
                for event in pygame.event.get():
                    self.handle(event)
                self.update()
                self.draw()
                self.clock.tick(60)
                frames += 1
            if screenshot:
                target = Path(screenshot)
                target.parent.mkdir(parents=True, exist_ok=True)
                pygame.image.save(self.canvas, target)
        finally:
            self.save_settings()
            self._disconnect()
            self.ai_runner.close()
            pygame.quit()
