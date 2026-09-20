"""Exercise the frozen executable outside the project and without Python PATH."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    executable = ROOT / "dist/UNO_No_Mercy/UNO_No_Mercy.exe"
    results = []
    with tempfile.TemporaryDirectory(prefix="uno-frozen-check-") as cwd:
        for screen in ("game", "help", "room"):
            environment = dict(os.environ)
            environment.pop("PYTHONPATH", None)
            environment.pop("PYTHONHOME", None)
            environment["PATH"] = os.pathsep.join((str(Path(os.environ["SYSTEMROOT"]) / "System32"), os.environ["SYSTEMROOT"]))
            directory = ROOT / "build/exe-user" / screen
            directory.mkdir(parents=True, exist_ok=True)
            environment["UNO_USER_DIR"] = str(directory)
            screenshot = ROOT / f"build/screenshots/packaged-{screen}.png"
            started = time.monotonic()
            subprocess.run([str(executable), "--smoke", screen, "--screenshot", str(screenshot)],
                           cwd=cwd, env=environment, timeout=30, check=True)
            assert screenshot.exists() and screenshot.stat().st_mtime >= time.time() - 30
            assert not (directory / "crash.log").exists()
            results.append({"screen": screen, "image": str(screenshot), "bytes": screenshot.stat().st_size,
                            "seconds": round(time.monotonic() - started, 2)})
    report = {"executable": str(executable), "checks": results}
    (ROOT / "build/frozen-verification.json").write_text(json.dumps(report, indent=2), encoding="utf8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
