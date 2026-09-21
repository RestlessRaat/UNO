import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="UNO No Mercy - 36-card edition")
    parser.add_argument("--skip-intro", action="store_true")
    parser.add_argument("--silent", action="store_true")
    parser.add_argument("--smoke", choices=("menu", "game", "ai-setup", "plugin-config", "help", "room", "intro"))
    parser.add_argument("--frames", type=int, default=None)
    parser.add_argument("--screenshot")
    parser.add_argument("--replay", help="Verify a recorded JSON game without opening a window")
    parser.add_argument("--ai-plugin", help="Use an installed AI plugin for local bot seats")
    args = parser.parse_args()
    if args.replay:
        from .engine import replay_game
        game = replay_game(json.loads(Path(args.replay).read_text(encoding="utf8")))
        print(json.dumps({"revision": game.revision, "winner": game.winner, "scores": game.scores}))
        return
    if args.smoke:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    from .app import App
    app = App(silent=args.silent, skip_intro=args.skip_intro or bool(args.smoke))
    if args.ai_plugin:
        if not app.ai_registry.has(args.ai_plugin):
            parser.error(f"Unknown AI plugin: {args.ai_plugin}")
        for seat in app.local_ai_seats.values():
            seat["plugin_id"], seat["settings"] = args.ai_plugin, {}
    if args.smoke == "game":
        app._start_local()
        app.finish_animations()
        if args.ai_plugin:
            app.game.current = 1
            app.next_ai = 0
    elif args.smoke == "help":
        app.open_help()
    elif args.smoke == "ai-setup":
        app.screen = "ai_setup"
    elif args.smoke == "plugin-config":
        app._open_plugin_config(1)
    elif args.smoke == "intro":
        app.screen = "intro"
    elif args.smoke == "room":
        from .network import NetworkClient, RoomServer
        app.server = RoomServer("Packaged host", host="127.0.0.1", port=0).start()
        app.client = NetworkClient(f"127.0.0.1:{app.server.port}", "Packaged host", app.server.host_token).start()
        app.screen = "room"
    default_frames = 90 if args.smoke == "room" or args.smoke == "game" and args.ai_plugin else 3 if args.smoke else None
    app.run(max_frames=args.frames or default_frames, screenshot=args.screenshot)


def entrypoint():
    try:
        main()
    except Exception:
        import traceback
        from .resources import user_dir
        error = traceback.format_exc()
        (user_dir() / "crash.log").write_text(error, encoding="utf8")
        if getattr(sys, "frozen", False):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "The game could not start. See crash.log in\n" + str(user_dir()), "UNO No Mercy", 0x10)
        else:
            raise


if __name__ == "__main__":
    entrypoint()
