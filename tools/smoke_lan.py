"""Exercise a full LAN round with two real client processes and two server AIs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import random
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from uno.ai import choose_action
from uno.network import NetworkClient, RoomServer


def worker(address, role, token, result):
    client = NetworkClient(address, role, token).start()
    rng = random.Random(310 if role == "Host" else 420)
    last_key = None
    ready = False
    sent_lobby = None
    snapshots = 0
    until = time.monotonic() + 90
    try:
        while time.monotonic() < until:
            try:
                message = client.incoming.get(timeout=1)
            except queue.Empty:
                continue
            if message["type"] == "error" and message.get("fatal"):
                raise RuntimeError(message["message"])
            if message["type"] != "snapshot":
                continue
            room = message["room"]
            game = room["game"]
            snapshots += 1
            if not game:
                if role == "Guest" and not ready:
                    client.send("ready")
                    ready = True
                if role == "Host" and room["revision"] != sent_lobby:
                    # Wait for the human guest before filling remaining slots.
                    if any(s["id"] != 0 and not s["bot"] and s["ready"] for s in room["seats"]):
                        if len(room["seats"]) < 4:
                            client.send("add_ai")
                        else:
                            client.send("start")
                        sent_lobby = room["revision"]
                continue
            assert "seed" not in game and "deck" not in game
            assert len(game["hand"]) == game["players"][game["you"]]["count"]
            for e in game["events"]:
                if e["type"] == "draw" and e["player"] != game["you"] and not e["revealed"]:
                    assert "card" not in e
            if game["winner"] is not None:
                Path(result).write_text(json.dumps({"winner": game["winner"], "revision": game["revision"],
                                                    "scores": [p["score"] for p in game["players"]],
                                                    "snapshots": snapshots}), encoding="utf8")
                time.sleep(0.5)
                return
            key = (room["round_id"], game["revision"])
            if key != last_key and game["legal"]:
                client.send("action", round_id=room["round_id"], revision=game["revision"],
                            action=choose_action(game, rng))
                last_key = key
        raise TimeoutError("LAN round exceeded 90 seconds")
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=("Host", "Guest"))
    parser.add_argument("--address")
    parser.add_argument("--token", default="")
    parser.add_argument("--result")
    args = parser.parse_args()
    if args.worker:
        worker(args.address, args.worker, args.token, args.result)
        return
    out = ROOT / "build/lan-smoke"
    out.mkdir(parents=True, exist_ok=True)
    server = RoomServer("Host", host="127.0.0.1", port=0, ai_delay=0.008,
                        log_dir=out / "replays").start()
    processes = []
    try:
        for role, token in (("Host", server.host_token), ("Guest", "")):
            processes.append(subprocess.Popen([sys.executable, str(Path(__file__)), "--worker", role,
                                                "--address", f"127.0.0.1:{server.port}", "--token", token,
                                                "--result", str(out / f"{role}.json")]))
        for p in processes:
            if p.wait(timeout=100) != 0:
                raise RuntimeError("A LAN client process failed")
        host, guest = [json.loads((out / f"{role}.json").read_text()) for role in ("Host", "Guest")]
        assert host["winner"] == guest["winner"]
        assert host["revision"] == guest["revision"]
        assert host["scores"] == guest["scores"]
        print(json.dumps({"result": "passed", "host": host, "guest": guest}))
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=5)
        server.stop()


if __name__ == "__main__":
    main()
