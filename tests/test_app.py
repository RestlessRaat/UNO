import json
import os
import queue
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from concurrent.futures import Future
from unittest.mock import Mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("UNO_USER_DIR", str(Path(__file__).resolve().parents[1] / "build/test-user"))

import pygame
import pytest

from uno.app import App
from uno.ai import choose_action
from uno.engine import BY_ID
from uno.engine import GameConfig, new_game
from uno.layout import hand_layout


@pytest.fixture
def app():
    result = App(silent=True, skip_intro=True)
    yield result
    result._disconnect()
    pygame.quit()


def test_all_prepared_assets_decode(app):
    images = set()
    sounds = set()
    for target in app.assets.manifest["targets"].values():
        images.update(c["path"] for c in target["costumes"].values())
        sounds.update(target["sounds"].values())
    images.update(app.assets.manifest["cards"].values())
    assert len(images) == 348 and len(sounds) == 66
    for p in images:
        surface = pygame.image.load(app.assets.root / p)
        assert surface.get_width() and surface.get_height()
    for p in sounds:
        assert pygame.mixer.Sound(app.assets.root / p).get_length() > 0


def test_menu_click_reaches_offline_game(app):
    app.draw()
    app.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(720, 255), button=1))
    assert app.screen == "local_setup"
    app.draw()
    app.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(690, 633), button=1))
    assert app.screen == "game" and len(app.game.hands) == 4
    assert app.game.config.mercy_limit == 36


def test_hard_selection_is_saved_used_and_kept_for_rematch(app):
    app.screen = "local_setup"
    app.draw()
    app.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(380, 562), button=1))
    assert app.ai_difficulty == "hard"
    settings = json.loads((app.directory / "settings.json").read_text(encoding="utf8"))
    assert settings["ai_difficulty"] == "hard"
    app._start_local()
    app.finish_animations()
    app.game.current = 1
    app.next_ai = 0
    with patch("uno.app.choose_action", wraps=choose_action) as choose:
        app.update()
    assert choose.call_count == 1
    assert choose.call_args.args[2] == "hard"
    app.game._finish(0)
    app._rematch()
    assert app.ai_difficulty == "hard"
    # Read the saved setting through the same path a new process uses.
    restored = App(silent=True, skip_intro=True)
    assert restored.ai_difficulty == "hard"
    app._set_ai_difficulty("normal")


@pytest.mark.parametrize("saved", [None, {"ai_difficulty": "obsolete"}, {"ai_difficulty": ["hard"]}])
def test_missing_or_invalid_difficulty_defaults_to_normal(app, saved):
    settings_path = app.directory / "settings.json"
    settings_path.write_text(json.dumps(saved or {}), encoding="utf8")
    try:
        result = App(silent=True, skip_intro=True)
        assert result.ai_difficulty == "normal"
    finally:
        app.save_settings()


def test_host_setup_passes_selected_difficulty(app):
    app.ai_difficulty = "hard"
    with patch("uno.app.RoomServer") as server, patch("uno.app.NetworkClient"):
        app._host()
    assert server.call_args.kwargs["ai_difficulty"] == "hard"


def test_difficulty_shows_local_elo_and_host_supplied_room_elo(app):
    app.ai_elo = {"normal": 1000.0, "hard": 1180.4}
    assert app.difficulty_label("hard") == "Hard 1180"
    app.room = {"ai_elo": {"normal": 1000.0, "hard": 1170.0}}
    assert app.difficulty_label("hard", room=True) == "Hard 1170"
    app.room = {}
    assert app.difficulty_label("hard", room=True) == "Hard"


def test_guest_cannot_click_difficulty_controls(app):
    app.room = {"is_host": False, "ai_difficulty": "hard", "ai_elo": {"normal": 1000, "hard": 1180}}
    app.network_connected = True
    app.buttons = []
    app.draw_ai_difficulty(262, room=True)
    assert app.buttons == []


def test_devil_button_selects_third_difficulty(app):
    app.screen = 'local_setup'
    app.draw()
    app.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(580, 562), button=1))
    assert app.ai_difficulty == 'devil'
    app._set_ai_difficulty('normal')


def test_devil_thinks_without_blocking_ui_and_applies_only_finished_result(app):
    app._start_local()
    app.ai_difficulty = 'devil'
    app.finish_animations()
    app.game.current = 1
    app.next_ai = 0
    future = Future()
    app.ai_executor = Mock()
    app.ai_executor.submit.return_value = future
    action = {'player_id': 1, **app.game.legal_actions(1)[0]}
    app.update()
    app.draw()
    assert app.game.revision == 0 and not future.done()
    app.ai_executor.submit.assert_called_once()
    future.set_result(action)
    app.update()
    assert app.game.revision == 1 and app.ai_pending is None


def test_new_round_discards_old_devil_search(app):
    app._start_local()
    future = Future()
    app.ai_pending = (app.game, app.game.revision, future)
    app._rematch()
    assert app.ai_pending is None and future.cancelled()


def test_room_difficulty_selection_waits_for_host_snapshot(app):
    app.ai_difficulty = "normal"
    app.network_connected = True
    app.client = SimpleNamespace(send=lambda *args, **kwargs: None, close=lambda: None)
    app.room = {"ai_difficulty": "normal"}
    with patch.object(app.client, "send") as send:
        app._set_ai_difficulty("hard", room=True)
    send.assert_called_once_with("set_ai_difficulty", ai_difficulty="hard")
    assert app.ai_difficulty == "normal"
    assert app.room["ai_difficulty"] == "normal"


@pytest.mark.parametrize("is_host", [False, True])
def test_room_snapshot_saves_only_hosts_difficulty(app, is_host):
    app.ai_difficulty = "normal"
    incoming = queue.Queue()
    incoming.put({"type": "snapshot", "room": {
        "is_host": is_host, "ai_difficulty": "hard", "game": None, "chat": []}})
    app.client = SimpleNamespace(incoming=incoming, close=lambda: None)
    app.update()
    assert app.room["ai_difficulty"] == "hard"
    assert app.ai_difficulty == ("hard" if is_host else "normal")
    app._set_ai_difficulty("normal")


def test_draw_click_and_modal_selection(app):
    app._start_local()
    app.finish_animations()
    app.draw()
    before = len(app.game.hands[0])
    app.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(558, 346), button=1))
    assert len(app.game.hands[0]) == before + 1
    assert app.view["revision"] == 1


def test_keyboard_select_survives_stationary_mouse(app):
    app._start_local()
    app.finish_animations()
    app.handle(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT, mod=0))
    app.mouse = (0, 0)
    app.draw()
    assert app.hovered_card == 1
    app.handle(pygame.event.Event(pygame.MOUSEMOTION, pos=(0, 0)))
    app.draw()
    assert app.hovered_card is None


def test_original_colour_quadrants_require_press_and_release(app):
    app._start_local()
    app.game.phase = 'choose_color'
    app.view = app.game.view_for(0)
    app.finish_animations()
    app.draw()
    app._choice_started -= 1
    app.draw()
    assert app._color_at((300, 85)) == 'blue'
    assert app._color_at((170, 85)) == 'red'
    assert app._color_at((170, 200)) == 'yellow'
    assert app._color_at((300, 200)) == 'green'
    assert app._color_at((240, 144)) is None
    app.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(600, 170), button=1))
    assert app.game.phase == 'choose_color'
    app.handle(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(600, 170), button=1))
    assert app.game.color == 'blue' and app.game.revision == 1


def test_35_cards_and_all_screens_render(app):
    app._start_local()
    while len(app.game.hands[0]) < 35:
        app.game.hands[0].append(app.game.deck.pop())
    app.view = app.game.view_for(0)
    app.finish_animations()
    out = Path(__file__).resolve().parents[1] / "build/screenshots"
    out.mkdir(parents=True, exist_ok=True)
    for n, phase in enumerate(("turn", "choose_color", "choose_player", "roulette")):
        app.game.phase = phase
        app.view = app.game.view_for(0)
        app.finish_animations()
        app.draw()
        pygame.image.save(app.canvas, out / f"ui-{phase}.png")
    for screen in ("menu", "local_setup", "host_setup", "join_setup", "intro", "help"):
        app.screen = screen
        app.draw()
    for page in range(1, 9):
        app.help_page = page
        app.draw()
    app.screen = "game"
    app.game.phase = "turn"
    app.game._finish(0)
    app.view = app.game.view_for(0)
    app.finish_animations()
    app.draw()
    pygame.image.save(app.canvas, out / "ui-result.png")


@pytest.mark.parametrize('cross_before_draw', [False, True])
def test_draw_boundary_keeps_all_cards_in_one_consistent_frame(app, monkeypatch, cross_before_draw):
    now = [100.0]
    monkeypatch.setattr('uno.app.time.monotonic', lambda: now[0])
    game = new_game(GameConfig(), 123)
    app._new_round(game.view_for(0))
    app.finish_animations()
    game.apply_action({'type': 'draw', 'player_id': 0})
    app._accept_view(game.view_for(0))
    app.update()
    timeline = app.current_move
    end = app.move_start + timeline.duration
    now[0] = end - 0.001
    app.update()
    if cross_before_draw:
        now[0] = end + 0.001

    draw_wheel = app._draw_wheel
    def cross_boundary(scene):
        now[0] = end + 0.001
        draw_wheel(scene)
    monkeypatch.setattr(app, '_draw_wheel', cross_boundary)
    draw_hands = app._draw_scene_cards
    rendered = []
    def record_hands(*args):
        with patch.object(app, 'draw_card', wraps=app.draw_card) as cards:
            result = draw_hands(*args)
            rendered.append(cards.call_count)
        return result
    monkeypatch.setattr(app, '_draw_scene_cards', record_hands)
    with patch.object(timeline, 'sample', wraps=timeline.sample) as sample:
        app.draw()
        assert sample.call_count == 1
    assert rendered == [29]
    app.update()
    app.draw()
    assert app.current_move is None
    assert rendered == [29, 29]


def test_god_warning_confirmation_and_cancel(app):
    app.screen = "local_setup"
    app.ai_difficulty = "normal"
    app._set_ai_difficulty("god")
    assert app.ai_difficulty == "normal" and app.confirm_god is False
    app.draw()
    assert len(app.buttons) == 2
    app.buttons[1][1]()
    assert app.confirm_god is None and app.ai_difficulty == "normal"
    app._set_ai_difficulty("god")
    app.draw()
    app.buttons[0][1]()
    assert app.confirm_god is None and app.ai_difficulty == "god"
    assert app.difficulty_label("god") == "God"


def test_god_room_warning_waits_before_sending(app):
    app._room_command = Mock()
    app._set_ai_difficulty("god", room=True)
    app._room_command.assert_not_called()
    app._set_ai_difficulty("god", room=True, confirmed=True)
    app._room_command.assert_called_once_with("set_ai_difficulty", ai_difficulty="god")
