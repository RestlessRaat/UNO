"""Authoritative LAN room and threaded client adapters.

Only the server event loop mutates a room. Pygame never touches sockets or the
server's Game object. Snapshots are projected separately for every seat.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import queue
import random
import secrets
import socket
import threading
import time

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from .ai import DIFFICULTIES, choose_action, decision_view
from .animation import action_timeline, opening
from .engine import GameConfig, RuleError, new_game
from .elo import load_ai_elo

PROTOCOL = 1
DEFAULT_PORT = 8765
PRESET_MESSAGES = ("Hello", "Scratch on", "Well played", "It's your turn!", "Not again",
                   "Revenge!", "Ha ha ha!", "Keep drawing!", "Rain the pain!", "Ouch",
                   "So many cards", "You still there?", "I'm rockin' this", "What??",
                   "Lucky ducky!", "No mercy!", "You got skillz", "Loving this!",
                   "Uno all the day", "Oh.. oh... OHH!")


def local_addresses():
    try:
        addresses = {a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
        return sorted(addresses - {"127.0.0.1"}) or ["127.0.0.1"]
    except OSError:
        return ["127.0.0.1"]


@dataclass
class Seat:
    id: int
    name: str
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    bot: bool = False
    ready: bool = False
    ws: object = None
    last_seen: float = 0
    last_seq: int = 0
    pending_return: bool = False
    return_after: int = -1


class RoomServer:
    def __init__(self, name="Host", host="0.0.0.0", port=DEFAULT_PORT,
                 ai_delay=0.75, timeout=15.0, log_dir: Path | None = None,
                 presentation_pacing=False, voice_lengths=None, ai_difficulty="normal"):
        if ai_difficulty not in DIFFICULTIES:
            raise ValueError("Unsupported AI difficulty.")
        self.host, self.port = host, port
        self.ai_delay, self.timeout = ai_delay, timeout
        self.ai_difficulty = ai_difficulty
        self.presentation_pacing = presentation_pacing
        self.voice_lengths = voice_lengths or {}
        self.ai_elo = load_ai_elo()
        self.log_dir = log_dir
        self.seats = [Seat(0, name[:16] or "Host")]
        self.host_token = self.seats[0].token
        self.game = None
        self.game_seats: list[int] = []
        self.round_id = 0
        self.revision = 0
        self.chat: list[dict] = []
        self.rng = random.Random()
        self.next_ai = 0.0
        self.closed = False
        self.error = ""
        self.started = threading.Event()
        self.stopped = threading.Event()
        self._stop_requested = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._thread_main, name="UnoRoom", daemon=True)
        self.thread.start()
        if not self.started.wait(5):
            raise OSError("Room server didn't start.")
        if self.error:
            raise OSError(self.error)
        return self

    def stop(self):
        self._stop_requested.set()
        if self.thread and threading.current_thread() is not self.thread:
            self.thread.join(4)

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.started.set()
            self.stopped.set()

    async def _run(self):
        try:
            async with serve(self._handler, self.host, self.port, max_size=16384,
                             ping_interval=5, ping_timeout=15, close_timeout=1) as server:
                self.port = server.sockets[0].getsockname()[1]
                self.started.set()
                while not self._stop_requested.is_set() and not self.closed:
                    await self._tick()
                    await asyncio.sleep(0.05)
                await self._end_room()
        except Exception as exc:
            self.error = str(exc)
            self.started.set()
            raise

    async def _send(self, ws, data):
        try:
            await asyncio.wait_for(ws.send(json.dumps(data)), timeout=2)
        except (ConnectionClosed, TimeoutError):
            pass

    async def _handler(self, ws):
        seat = None
        try:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 5))
            if not isinstance(hello, dict) or hello.get("type") != "hello" or hello.get("protocol") != PROTOCOL:
                await self._send(ws, {"type": "error", "message": "Protocol version mismatch.", "fatal": True})
                return
            token = hello.get("token")
            seat = next((s for s in self.seats if token and secrets.compare_digest(str(token), s.token)), None)
            if seat and seat.ws is not None:
                await self._send(ws, {"type": "error", "message": "This player is already connected.", "fatal": True})
                return
            if not seat:
                if self.game is not None or len(self.seats) >= 4:
                    await self._send(ws, {"type": "error", "message": "Room is full or the game has started.", "fatal": True})
                    return
                raw_name = hello.get("name", "Player")
                name = str(raw_name).strip()[:16]
                name = ''.join(c for c in name if c.isprintable()) or "Player"
                used = {s.id for s in self.seats}
                seat = Seat(next(i for i in range(4) if i not in used), name)
                self.seats.append(seat)
                self.seats.sort(key=lambda s: s.id)
            if self.game and self.game.winner is None:
                seat.pending_return = True
                seat.return_after = self.game.turn_serial
            seat.ws = ws
            seat.last_seen = time.monotonic()
            self.revision += 1
            await self._send(ws, {"type": "welcome", "token": seat.token,
                                  "seat_id": seat.id, "next_seq": seat.last_seq + 1, "protocol": PROTOCOL})
            await self._broadcast()
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    if not isinstance(msg, dict):
                        raise RuleError("Invalid message.")
                    seat.last_seen = time.monotonic()
                    if msg.get("type") == "heartbeat":
                        continue
                    await self._command(seat, msg)
                except (RuleError, ValueError, TypeError, KeyError) as exc:
                    await self._send(ws, {"type": "error", "message": str(exc), "fatal": False})
                    await self._snapshot(seat)
        except (ConnectionClosed, TimeoutError, ValueError, TypeError):
            pass
        finally:
            # A rejected duplicate connection must not disconnect the genuine one.
            if seat is not None and seat.ws is ws:
                seat.ws = None
                if seat.id == 0:
                    self.closed = True
                else:
                    seat.ready = False
                    seat.pending_return = False
                    self.revision += 1
                    await self._broadcast()

    async def _command(self, seat, msg):
        sequence = msg.get("seq")
        if type(sequence) is not int or sequence <= seat.last_seq:
            raise RuleError("Duplicate or expired operation.")
        seat.last_seq = sequence
        kind = msg.get("type")
        if kind == "action":
            if self.game is None or msg.get("round_id") != self.round_id:
                raise RuleError("This round is no longer active.")
            if msg.get("revision") != self.game.revision:
                raise RuleError("The table changed. Please try again.")
            if seat.pending_return:
                raise RuleError("AI is finishing this turn. Control returns on your next turn.")
            action = msg.get("action")
            if not isinstance(action, dict):
                raise RuleError("Invalid action.")
            before = self.game.view_for(0)
            self.game.apply_action({**action, "player_id": self.game_seats.index(seat.id)})
            self.next_ai = time.monotonic() + self._presentation_delay(before)
            self._save_replay()
        elif kind == "chat":
            index = msg.get("index")
            if type(index) is not int or not 0 <= index < len(PRESET_MESSAGES):
                raise RuleError("Invalid message selection.")
            self.chat.append({"id": self.revision + 1, "name": seat.name, "text": PRESET_MESSAGES[index]})
            self.chat = self.chat[-6:]
        elif kind == "ready":
            if self.game and self.game.winner is None:
                raise RuleError("A round is in progress.")
            seat.ready = not seat.ready
        elif kind in ("add_ai", "remove_seat", "set_ai_difficulty", "start", "rematch"):
            if seat.id != 0:
                raise RuleError("Only the host can do that.")
            if self.game and self.game.winner is None:
                raise RuleError("A round is in progress.")
            if kind == "set_ai_difficulty":
                difficulty = msg.get("ai_difficulty")
                if difficulty not in DIFFICULTIES:
                    raise RuleError("Unsupported AI difficulty.")
                self.ai_difficulty = difficulty
            elif kind == "add_ai":
                if len(self.seats) >= 4:
                    raise RuleError("Room is full.")
                new_id = next(i for i in range(4) if not any(s.id == i for s in self.seats))
                self.seats.append(Seat(new_id, ("Ada", "Turing", "Grace")[new_id - 1], bot=True, ready=True))
                self.seats.sort(key=lambda s: s.id)
            elif kind == "remove_seat":
                target = next((s for s in self.seats if s.id == msg.get("seat_id") and s.id != 0), None)
                if not target:
                    raise RuleError("Unknown seat.")
                if target.ws:
                    await self._send(target.ws, {"type": "room_closed", "message": "The host removed your seat."})
                    await target.ws.close()
                self.seats.remove(target)
            else:
                if not 2 <= len(self.seats) <= 4:
                    raise RuleError("Add at least one player or AI.")
                if any(not s.bot and (s.ws is None or not s.ready) for s in self.seats if s.id != 0):
                    raise RuleError("All human guests must be connected and ready.")
                old_scores = {}
                if self.game and self.game.match_winner is None:
                    old_scores = dict(zip(self.game_seats, self.game.scores))
                self.game_seats = [s.id for s in self.seats]
                scores = tuple(old_scores.get(i, 0) for i in self.game_seats)
                self.game = new_game(GameConfig(tuple(s.name for s in self.seats), scores=scores), secrets.randbelow(65536))
                self.round_id += 1
                for s in self.seats:
                    s.pending_return = False
                # Allow the opening deal animation to complete before AI starts.
                delay = opening(self.game.view_for(0)).duration if self.presentation_pacing else 2.5
                self.next_ai = time.monotonic() + max(self.ai_delay, delay)
                self._save_replay()
        else:
            raise RuleError("Unknown room operation.")
        self.revision += 1
        await self._broadcast()

    async def _tick(self):
        now = time.monotonic()
        for seat in list(self.seats):
            if seat.ws and now - seat.last_seen > self.timeout:
                await seat.ws.close(code=4000, reason="Heartbeat timeout")
        if not self.game or self.game.winner is not None:
            return
        current_id = self.game_seats[self.game.current]
        seat = next(s for s in self.seats if s.id == current_id)
        if seat.pending_return and self.game.turn_serial > seat.return_after:
            seat.pending_return = False
            self.revision += 1
            await self._broadcast()
        if (seat.bot or seat.ws is None or seat.pending_return) and now >= self.next_ai:
            game, revision = self.game, self.game.revision
            view = decision_view(game, self.ai_difficulty)
            if self.ai_difficulty in ("devil", "god") and len(view["legal"]) > 1:
                action = await asyncio.to_thread(choose_action, view, self.rng, self.ai_difficulty)
                if self.game is not game or game.revision != revision or self.closed:
                    return
            else:
                action = choose_action(view, self.rng, self.ai_difficulty)
            if action:
                before = self.game.view_for(0)
                self.game.apply_action(action)
                self.revision += 1
                self.next_ai = time.monotonic() + self._presentation_delay(before)
                self._save_replay()
                await self._broadcast()

    def _presentation_delay(self, before):
        if not self.presentation_pacing:
            return self.ai_delay
        timeline = action_timeline(before, self.game.view_for(0), lambda name: self.voice_lengths.get(name, 0.8))
        return timeline.duration + self.ai_delay

    def _save_replay(self):
        if self.log_dir and self.game:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            # Host-local diagnostics: never sent to clients.
            (self.log_dir / f"lan-round-{self.round_id}.json").write_text(
                json.dumps(self.game.replay(), indent=2), encoding="utf8")

    def _room_view(self, seat):
        view = self.game.view_for(self.game_seats.index(seat.id)) if self.game and seat.id in self.game_seats else None
        return {"revision": self.revision, "round_id": self.round_id, "you": seat.id,
                "is_host": seat.id == 0, "port": self.port, "chat": list(self.chat),
                "ai_difficulty": self.ai_difficulty,
                "ai_elo": dict(self.ai_elo),
                "seats": [{"id": s.id, "name": s.name, "bot": s.bot,
                           "ready": s.ready, "connected": s.ws is not None,
                           "pending_return": s.pending_return} for s in self.seats],
                "game": view, "pending_return": seat.pending_return}

    async def _snapshot(self, seat):
        if seat.ws:
            await self._send(seat.ws, {"type": "snapshot", "room": self._room_view(seat)})

    async def _broadcast(self):
        await asyncio.gather(*(self._snapshot(s) for s in list(self.seats) if s.ws))

    async def _end_room(self):
        self.closed = True
        await asyncio.gather(*(self._send(s.ws, {"type": "room_closed", "message": "The host closed the room."})
                               for s in self.seats if s.ws))


class NetworkClient:
    def __init__(self, address, name, token=""):
        address = address.strip()
        self.uri = address if address.startswith("ws://") else "ws://" + address
        if not self.uri.rsplit('/', 1)[-1].count(':'):
            self.uri += f":{DEFAULT_PORT}"
        self.name, self.token = name, token
        self.incoming = queue.Queue()
        self.outgoing = queue.Queue()
        self.stop_requested = threading.Event()
        self.seq = 1
        self.thread = threading.Thread(target=self._thread_main, name="UnoClient", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def send(self, kind, **payload):
        self.outgoing.put({"type": kind, **payload})

    def close(self):
        self.stop_requested.set()
        if self.thread.is_alive():
            self.thread.join(3)

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self.incoming.put({"type": "error", "message": str(exc), "fatal": True})

    async def _run(self):
        ever_connected = False
        while not self.stop_requested.is_set():
            try:
                async with connect(self.uri, open_timeout=3, close_timeout=1, proxy=None,
                                   max_size=262144, ping_interval=5, ping_timeout=15) as ws:
                    await ws.send(json.dumps({"type": "hello", "protocol": PROTOCOL,
                                              "name": self.name, "token": self.token}))
                    first = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    self.incoming.put(first)
                    if first.get("type") != "welcome":
                        return
                    ever_connected = True
                    self.token = first["token"]
                    self.seq = first["next_seq"]
                    # Don't replay clicks collected while disconnected.
                    while not self.outgoing.empty():
                        self.outgoing.get_nowait()
                    self.incoming.put({"type": "connected"})
                    receiver = asyncio.create_task(self._receive(ws))
                    sender = asyncio.create_task(self._send_loop(ws))
                    tasks = {receiver, sender}
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    for task in done:
                        task.result()
                    if self.stop_requested.is_set():
                        return
            except (OSError, ConnectionClosed, TimeoutError) as exc:
                if not ever_connected:
                    self.incoming.put({"type": "error", "message": f"Cannot connect: {exc}", "fatal": True})
                    return
                self.incoming.put({"type": "reconnecting", "message": "Connection lost. Reconnecting..."})
            # Yield in short intervals so window close stays responsive.
            for _ in range(20):
                if self.stop_requested.is_set():
                    return
                await asyncio.sleep(0.1)

    async def _receive(self, ws):
        async for raw in ws:
            msg = json.loads(raw)
            self.incoming.put(msg)
            if msg.get("type") == "room_closed" or msg.get("fatal"):
                self.stop_requested.set()
                return

    async def _send_loop(self, ws):
        last_heartbeat = 0
        while not self.stop_requested.is_set():
            if time.monotonic() - last_heartbeat >= 5:
                await ws.send(json.dumps({"type": "heartbeat"}))
                last_heartbeat = time.monotonic()
            try:
                message = self.outgoing.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.02)
                continue
            message["seq"] = self.seq
            self.seq += 1
            await ws.send(json.dumps(message))
