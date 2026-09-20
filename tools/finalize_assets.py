"""Prepare verified live-table backdrop and direction-wheel colour variants."""
import json
from pathlib import Path
from PIL import Image
from prepare_assets import scratch_hue

root = Path(__file__).resolve().parents[1] / "assets"
manifest_path = root / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf8"))
(root / "wheels").mkdir(exist_ok=True)
manifest["wheels"] = {}
for direction in ("direction", "direction2"):
    spec = manifest["targets"]["uno"]["costumes"][direction]
    image = Image.open(root / spec["path"]).convert("RGBA")
    for color, effect in {"red": 0, "yellow": 32, "green": 68, "blue": 116}.items():
        path = f"wheels/{direction}_{color}.png"
        scratch_hue(image, effect).save(root / path)
        manifest["wheels"][f"{direction}:{color}"] = path
background = Image.open(root / manifest["targets"]["Stage"]["costumes"]["blank"]["path"]).convert("RGB")
# Original runtime verified: blank backdrop with Scratch brightness -20.
background = background.point(lambda value: max(0, value - 51))
background.save(root / "images/table.png")
manifest["table"] = "images/table.png"
manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf8")
print("Prepared original table and 8 direction-wheel variants.")
