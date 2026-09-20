"""Render deterministic animated previews of the actual desktop presentation."""
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
os.environ['UNO_USER_DIR'] = str(ROOT / 'build/animation-user')
import pygame
from PIL import Image, ImageDraw
from uno.app import App
from uno.animation import Scene, opening, action_timeline
from uno.engine import GameConfig, new_game


def main():
    out = ROOT / 'build/animation-preview'
    out.mkdir(parents=True, exist_ok=True)
    real_clock = time.monotonic
    clock = [100.0]
    time.monotonic = lambda: clock[0]
    app = App(silent=True, skip_intro=True)
    app.screen = 'game'
    app.animation_clock = clock[0]
    thumbnails = []

    def render(name, game, timeline):
        app.game, app.view = game, game.view_for(0)
        app.finish_animations()
        app.current_move, app.move_start = timeline, clock[0]
        app.brightness.clear()
        app._wheel_state = None
        start = clock[0]
        frames = []
        for i in range(int(timeline.duration * 15) + 2):
            elapsed = min(timeline.duration, i / 15)
            clock[0] = start + elapsed
            app.visual_scene = timeline.sample(elapsed)[0]
            if elapsed >= timeline.duration:
                app.current_move = None
            app.draw()
            image = Image.frombytes('RGB', (960, 720), pygame.image.tobytes(app.canvas, 'RGB')).resize((480, 360))
            frames.append(image)
        frames[0].save(out / f'{name}.gif', save_all=True, append_images=frames[1:], duration=67, loop=0)
        middle = frames[len(frames) // 2].copy()
        ImageDraw.Draw(middle).text((8, 34), name, fill='white', stroke_width=2, stroke_fill='black')
        thumbnails.append(middle)
        clock[0] += 2

    try:
        game = new_game(GameConfig(), 123)
        render('opening', game, opening(game.view_for(0), app._voice_duration))
        old = game.view_for(0)
        game.apply_action({'player_id': 0, 'type': 'draw'})
        render('draw', game, action_timeline(old, game.view_for(0), app._voice_duration))
        game = new_game(GameConfig(), 123)
        game.phase = 'choose_player'
        old = game.view_for(0)
        game.apply_action({'player_id': 0, 'type': 'choose_player', 'target': 1})
        render('swap', game, action_timeline(old, game.view_for(0), app._voice_duration))
        game = new_game(GameConfig(), 123)
        while len(game.hands[0]) < 34:
            game.hands[0].append(game.deck.pop())
        old = game.view_for(0)
        game.apply_action({'player_id': 0, 'type': 'draw'})
        render('mercy', game, action_timeline(old, game.view_for(0), app._voice_duration))
        game = new_game(GameConfig(), 123)
        game.deck.extend(game.hands[0])
        game.hands[0] = []
        old = game.view_for(0)
        game._finish(0)
        game.revision += 1
        game.last_events = game.events
        render('scoring', game, action_timeline(old, game.view_for(0), app._voice_duration))
        app.game = new_game(GameConfig(), 123)
        app.game.phase = 'choose_color'
        app.view = app.game.view_for(0)
        app.finish_animations()
        app.draw()
        clock[0] += 0.7
        app.draw()
        pygame.image.save(app.canvas, out / 'colour-selector.png')
        sheet = Image.new('RGB', (1440, 720))
        for i, image in enumerate(thumbnails):
            sheet.paste(image, ((i % 3) * 480, (i // 3) * 360))
        sheet.save(out / 'contact-sheet.png')
        print(f'Animation previews: {out}')
    finally:
        time.monotonic = real_clock
        app._disconnect()
        pygame.quit()


if __name__ == '__main__':
    main()
