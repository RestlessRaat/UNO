"""Preload the local neural policy before opening the game window."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, default=ROOT / 'build/neural/local/actor.pt')
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    if not args.checkpoint.is_file():
        parser.error(f'Model missing: {args.checkpoint}; run train_neural.cmd first')
    # Keep the opt-in run's UI settings/replays out of the user's regular profile.
    os.environ['UNO_USER_DIR'] = str(ROOT / 'build/neural/game-user')
    os.environ['UNO_PLUGIN_DIR'] = str(ROOT / 'examples/plugins')
    os.environ['UNO_NEURAL_CHECKPOINT'] = str(args.checkpoint.resolve())
    import torch
    from uno.neural.policy import load_actor
    torch.set_num_threads(2)
    load_actor(os.environ['UNO_NEURAL_CHECKPOINT'])
    if args.smoke:
        smoke()
        return
    from uno.__main__ import entrypoint
    sys.argv = ['uno', '--skip-intro', '--ai-plugin', 'local.neural']
    entrypoint()


def smoke():
    os.environ['SDL_VIDEODRIVER'] = 'dummy'
    os.environ['SDL_AUDIODRIVER'] = 'dummy'
    import pygame
    from uno.app import App
    app = App(silent=True, skip_intro=True)
    try:
        for seat in app.local_ai_seats.values():
            seat['plugin_id'], seat['settings'] = 'local.neural', {}
        app._start_local()
        app.game.current = 1
        app.view = app.game.view_for(0)
        app.finish_animations()
        # Exercise the actual async runner and rendered UI, not just a direct
        # network call that could hide fallback-to-Normal in the application.
        for _ in range(180):
            pygame.event.pump()
            app.next_ai = 0
            app.update()
            app.draw()
            app.clock.tick(60)
            if app.game.revision > 0:
                break
        if app.game.revision == 0 or app.ai_failure_log:
            raise RuntimeError(f'Neural UI smoke failed: {app.ai_failure_log}')
        target = ROOT / 'build/neural/ui-smoke.png'
        pygame.image.save(app.canvas, target)
        print(json.dumps({'plugin': 'local.neural', 'revision': app.game.revision,
                          'fallbacks': app.ai_failure_log, 'screenshot': str(target)}))
    finally:
        app._disconnect()
        app.ai_runner.close()
        pygame.quit()


if __name__ == '__main__':
    main()
