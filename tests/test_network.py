import json
import asyncio
import time

import pytest
from websockets.sync.client import connect

from uno.network import PROTOCOL, RoomServer, Seat
from uno.engine import BY_ID, GameConfig, RuleError, new_game


class Peer:
    def __init__(self, server, name="Guest", token="", protocol=PROTOCOL):
        self.ws = connect(f"ws://127.0.0.1:{server.port}", proxy=None, close_timeout=0.5)
        self.ws.send(json.dumps({"type": "hello", "protocol": protocol, "name": name, "token": token}))
        self.first = json.loads(self.ws.recv(timeout=3))
        self.seq = self.first.get("next_seq", 1)
        self.token = self.first.get("token", "")
        self.room = None
        if self.first["type"] == "welcome":
            self.snapshot()

    def send(self, kind, **values):
        msg = {"type": kind, "seq": self.seq, **values}
        self.seq += 1
        self.ws.send(json.dumps(msg))
        return msg

    def receive(self, predicate, timeout=5):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            msg = json.loads(self.ws.recv(timeout=max(0.01, until - time.monotonic())))
            if msg["type"] == "snapshot":
                self.room = msg["room"]
            if predicate(msg):
                return msg
        raise AssertionError("Expected server message did not arrive")

    def snapshot(self, predicate=lambda room: True):
        return self.receive(lambda msg: msg["type"] == "snapshot" and predicate(msg["room"]))["room"]

    def action(self, action, **override):
        return self.send("action", round_id=self.room["round_id"], revision=self.room["game"]["revision"],
                         action=action, **override)

    def close(self):
        self.ws.close()


def test_protocol_v2_and_per_seat_ai_configuration():
    assert PROTOCOL == 2

    async def check():
        room = RoomServer()
        host = room.seats[0]
        await room._command(host, {"type": "add_ai", "seq": 1,
                                   "plugin_id": "builtin.hard", "settings": {}})
        bot = room.seats[1]
        assert bot.bot and bot.ai_plugin_id == "builtin.hard"
        await room._command(host, {"type": "set_seat_ai", "seq": 2, "seat_id": bot.id,
                                   "plugin_id": "builtin.devil", "settings": {}})
        public = room._room_view(host)
        assert public["seats"][1]["ai"]["id"] == "builtin.devil"
        assert public["seats"][1]["ai"]["settings"] == {}
        room.stop()
    asyncio.run(check())


def test_protocol_v2_disconnected_takeover_is_always_normal(monkeypatch):
    async def check():
        room = RoomServer(ai_delay=0, ai_difficulty="god", takeover_plugin_id="builtin.normal")
        room.seats = [Seat(0, "Host"), Seat(1, "Guest")]
        room.game_seats = [0, 1]
        game = room.game = new_game(GameConfig(("Host", "Guest")), 123)
        room.seats[game.current].ws = None
        calls = []
        monkeypatch.setattr("uno.network.choose_action",
                            lambda view, rng, difficulty: calls.append(difficulty)
                            or {"player_id": view["you"], **view["legal"][0]})

        async def broadcast():
            pass

        monkeypatch.setattr(room, "_broadcast", broadcast)
        await room._tick()
        assert calls == ["normal"]
        room.stop()
    asyncio.run(check())


@pytest.fixture
def server():
    room = RoomServer("Host", host="127.0.0.1", port=0, ai_delay=0.01).start()
    yield room
    room.stop()


def joined_game(server):
    host = Peer(server, token=server.host_token)
    guest = Peer(server)
    guest.send("ready")
    host.snapshot(lambda r: len(r["seats"]) == 2 and r["seats"][1]["ready"])
    host.send("start")
    host.snapshot(lambda r: r["game"] is not None)
    guest.snapshot(lambda r: r["game"] is not None)
    return host, guest


def test_room_rejects_bad_version(server):
    peer = Peer(server, protocol=99)
    assert peer.first["type"] == "error" and peer.first["fatal"]
    peer.close()


def test_room_defaults_to_normal_ai():
    room = RoomServer()
    assert room.ai_difficulty == "normal"
    assert room._room_view(room.seats[0])["ai_difficulty"] == "normal"


def test_host_elo_is_shared_with_guests():
    room = RoomServer()
    room.ai_elo = {"normal": 1000.0, "hard": 1180.0}
    guest = Seat(1, "Guest")
    assert room._room_view(guest)["ai_elo"] == room.ai_elo


def test_lan_pacing_uses_accelerated_animation_duration(monkeypatch):
    room = RoomServer(presentation_pacing=True, ai_delay=0.75)
    room.game = new_game(GameConfig(), 123)
    old = room.game.view_for(0)
    room.game.apply_action({"type": "draw", "player_id": 0})
    accelerated = room._presentation_delay(old)
    monkeypatch.setattr("uno.animation.FPS", 30)
    original = room._presentation_delay(old)
    assert (accelerated - room.ai_delay) * 1.5 == pytest.approx(original - room.ai_delay)


@pytest.mark.parametrize("difficulty", ["normal", "hard", "devil", "god"])
def test_room_accepts_ai_difficulty(difficulty):
    room = RoomServer(ai_difficulty=difficulty)
    assert room._room_view(room.seats[0])["ai_difficulty"] == difficulty


@pytest.mark.parametrize("difficulty", [None, "easy", "HARD", 1, [], {}])
def test_room_rejects_invalid_ai_difficulty(difficulty):
    with pytest.raises(ValueError, match="difficulty"):
        RoomServer(ai_difficulty=difficulty)


def test_host_sets_room_ai_difficulty_and_guests_receive_it(server):
    host = Peer(server, token=server.host_token)
    guest = Peer(server)
    try:
        assert guest.room["ai_difficulty"] == "normal"
        host.send("set_ai_difficulty", ai_difficulty="hard")
        assert host.snapshot(lambda r: r["ai_difficulty"] == "hard")["game"] is None
        guest.snapshot(lambda r: r["ai_difficulty"] == "hard")
        guest.send("set_ai_difficulty", ai_difficulty="normal")
        assert "host" in guest.receive(lambda m: m["type"] == "error")["message"]
        assert server.ai_difficulty == "hard"
        for invalid in (None, "easy", [], {}):
            host.send("set_ai_difficulty", ai_difficulty=invalid)
            assert "difficulty" in host.receive(lambda m: m["type"] == "error")["message"]
            assert server.ai_difficulty == "hard"
        guest.send("ready")
        host.snapshot(lambda r: r["seats"][1]["ready"])
        host.send("start")
        host.snapshot(lambda r: r["game"] is not None)
        host.send("set_ai_difficulty", ai_difficulty="normal")
        assert "in progress" in host.receive(lambda m: m["type"] == "error")["message"]
        assert server.ai_difficulty == "hard"
    finally:
        guest.close()
        host.close()


def test_room_retains_ai_difficulty_across_rematch():
    async def check():
        room = RoomServer(ai_difficulty="hard")
        host = room.seats[0]
        room.seats.append(Seat(1, "Bot", bot=True, ready=True))
        await room._command(host, {"type": "start", "seq": 1})
        assert room.ai_difficulty == "hard"
        room.game.winner = 0
        await room._command(host, {"type": "rematch", "seq": 2})
        assert room.round_id == 2 and room.ai_difficulty == "hard"
        with pytest.raises(RuleError, match="in progress"):
            await room._command(host, {"type": "set_ai_difficulty", "seq": 3,
                                       "ai_difficulty": "normal"})
        room.game.winner = 0
        await room._command(host, {"type": "set_ai_difficulty", "seq": 4,
                                   "ai_difficulty": "normal"})
        await room._command(host, {"type": "rematch", "seq": 5})
        assert room.round_id == 3 and room.ai_difficulty == "normal"
    asyncio.run(check())


@pytest.mark.parametrize("difficulty", ["normal", "hard", "devil", "god"])
@pytest.mark.parametrize("control", ["bot", "disconnected", "pending_return"])
def test_ai_tick_uses_room_difficulty(monkeypatch, difficulty, control):
    async def check():
        room = RoomServer(ai_delay=0, ai_difficulty=difficulty)
        room.seats = [Seat(0, "Host"), Seat(1, "Guest")]
        room.game_seats = [0, 1]
        game = room.game = new_game(GameConfig(("Host", "Guest")), 123)
        seat = room.seats[game.current]
        seat.bot = control == "bot"
        seat.ws = None if control == "disconnected" else object()
        seat.last_seen = time.monotonic()
        seat.pending_return = control == "pending_return"
        seat.return_after = game.turn_serial
        calls = []

        def choose(view, rng, selected_difficulty):
            calls.append((view["you"], selected_difficulty))
            return {"player_id": view["you"], **view["legal"][0]}

        async def broadcast():
            pass

        monkeypatch.setattr("uno.network.choose_action", choose)
        monkeypatch.setattr(room, "_broadcast", broadcast)
        before = game.revision
        await room._tick()
        assert calls == [(seat.id, difficulty)]
        assert game.revision == before + 1
        game.assert_invariants()
    asyncio.run(check())


@pytest.mark.parametrize('change', ['new_round', 'revision', 'closed'])
def test_devil_discards_stale_background_result(monkeypatch, change):
    async def check():
        room = RoomServer(ai_delay=0, ai_difficulty='devil')
        room.seats = [Seat(0, 'Host', bot=True), Seat(1, 'Bot', bot=True)]
        room.game_seats = [0, 1]
        room.game = new_game(GameConfig(('A', 'B')), 124)

        async def changed_while_thinking(function, view, rng, difficulty):
            if change == 'new_round':
                room.game = new_game(GameConfig(('A', 'B')), 125)
            elif change == 'revision':
                room.game.apply_action({'player_id': 0, 'type': 'draw'})
            else:
                room.closed = True
            return {'player_id': view['you'], **view['legal'][0]}

        monkeypatch.setattr('uno.network.asyncio.to_thread', changed_while_thinking)
        await room._tick()
        assert room.game.revision == (1 if change == 'revision' else 0)
    asyncio.run(check())


def test_real_socket_privacy_and_duplicate_stale_actions(server):
    host, guest = joined_game(server)
    assert host.room["game"]["hand"] != guest.room["game"]["hand"]
    assert len(guest.room["game"]["hand"]) == 7
    op = host.action({"type": "draw"})
    host.snapshot(lambda r: r["game"]["revision"] == 1)
    other = guest.snapshot(lambda r: r["game"]["revision"] == 1)
    assert 'card' not in other["game"]["events"][0]
    host.ws.send(json.dumps(op))
    assert "Duplicate" in host.receive(lambda m: m["type"] == "error")["message"]
    host.send("action", round_id=host.room["round_id"], revision=0, action={"type": "draw"})
    assert "changed" in host.receive(lambda m: m["type"] == "error")["message"]
    assert server.game.revision == 1 and len(server.game.hands[0]) == 8
    guest.send("add_ai")
    assert "host" in guest.receive(lambda m: m["type"] == "error")["message"]
    guest.close()
    host.close()


def test_reconnect_keeps_same_seat_and_waits_for_next_turn(server):
    host, guest = joined_game(server)
    token = guest.token
    guest.close()
    host.snapshot(lambda r: not r["seats"][1]["connected"])
    guest = Peer(server, token=token)
    assert guest.room["pending_return"]
    assert len(guest.room["seats"]) == 2
    # Advance the host through legal play/choices; don't mutate the server game.
    for _ in range(80):
        view = host.room["game"]
        if view["current"] != 0 or view["winner"] is not None:
            break
        legal = view["legal"]
        action = next((a for a in legal if a["type"] == "play"), legal[0])
        rev = view["revision"]
        host.action(action)
        host.snapshot(lambda r: r["game"]["revision"] > rev)
    room = guest.snapshot(lambda r: not r["pending_return"] or r["game"]["winner"] is not None)
    assert not room["pending_return"] or room["game"]["winner"] is not None
    guest.close()
    host.close()


def test_new_player_cannot_join_started_round(server):
    host, guest = joined_game(server)
    stranger = Peer(server, "Late")
    assert stranger.first["type"] == "error"
    assert len(server.seats) == 2
    stranger.close()
    guest.close()
    host.close()


def test_duplicate_connection_does_not_evict_existing_player(server):
    host = Peer(server, token=server.host_token)
    duplicate = Peer(server, token=server.host_token)
    assert duplicate.first["type"] == "error"
    host.send("add_ai")
    room = host.snapshot(lambda r: len(r["seats"]) == 2)
    assert room["seats"][0]["connected"]
    duplicate.close()
    host.close()


def test_host_exit_notifies_guest(server):
    host, guest = joined_game(server)
    host.close()
    msg = guest.receive(lambda m: m["type"] == "room_closed")
    assert "host" in msg["message"].lower()
    guest.close()


def test_port_conflict_reports_error(server):
    second = RoomServer(host="127.0.0.1", port=server.port)
    with pytest.raises(OSError):
        second.start()
    second.stop()


@pytest.mark.parametrize("choice", ["wild draw 6", "7", "wild colour roulette"])
@pytest.mark.parametrize("difficulty", ["normal", "hard", "devil", "god"])
def test_ai_finishes_disconnected_choice(choice, difficulty):
    # Isolate the same server tick used after a real socket disconnect. Keep
    # all state and AI actions on one event loop, including reconnection.
    async def check():
        room = RoomServer(ai_delay=0, ai_difficulty=difficulty)
        room.seats = [Seat(0, "Host"), Seat(1, "Guest")]
        room.game_seats = [0, 1]
        game = room.game = new_game(GameConfig(("Host", "Guest")), 123)
        card = next(c.id for c in BY_ID.values() if c.value == choice and (c.wild or c.color == game.color))
        for pile in [game.deck, game.discard, *game.hands]:
            if card in pile:
                pile.remove(card)
        game.current = 0 if choice == "wild colour roulette" else 1
        game.hands[game.current].append(card)
        game.apply_action({"player_id": game.current, "type": "play", "card_id": card})
        assert game.current == 1 and game.phase in ("choose_color", "choose_player")
        before = game.revision
        await room._tick()
        assert game.revision == before + 1
        assert game.phase not in ("choose_color", "choose_player")
        assert game.log[-1]["type"] == ("choose_player" if choice == "7" else "choose_color")
        game.assert_invariants()
    asyncio.run(check())


def test_application_heartbeat_disconnects_silent_guest():
    room = RoomServer("Host", host="127.0.0.1", port=0, timeout=0.4).start()
    host = Peer(room, token=room.host_token)
    guest = Peer(room)
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and room.seats[1].ws is not None:
            host.ws.send(json.dumps({"type": "heartbeat"}))
            time.sleep(0.04)
        assert room.seats[1].ws is None
        assert room.seats[0].ws is not None and not room.closed
    finally:
        guest.close()
        host.close()
        room.stop()
