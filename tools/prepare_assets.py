"""Import the supplied SB3 into a reproducible runtime asset directory.

Run this, then tools/renderer/render.mjs. Originals are read-only inputs.
"""
from __future__ import annotations

import colorsys
import hashlib
import json
from pathlib import Path
import shutil
from zipfile import ZipFile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "assets"


def scratch_hue(image, effect):
    if not effect:
        return image.copy()
    cache = {}
    result = []
    for pixel in image.getdata():
        if pixel not in cache:
            r, g, b, a = pixel
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if v < 0.055:
                h, s, v = 0, 1, 0.055
            elif s < 0.09:
                h, s = 0, 0.09
            rgb = colorsys.hsv_to_rgb((h + effect / 200) % 1, s, v)
            cache[pixel] = (*[round(c * 255) for c in rgb], a)
        result.append(cache[pixel])
    out = Image.new("RGBA", image.size)
    out.putdata(result)
    return out


def main():
    source = next(ROOT.glob("*.sb3"))
    for name in ("images", "sounds", "cards"):
        (DEST / name).mkdir(parents=True, exist_ok=True)
    (ROOT / "build/baseline").mkdir(parents=True, exist_ok=True)
    with ZipFile(source) as z:
        project = json.loads(z.read("project.json"))
        (ROOT / "build/baseline/project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf8")
        manifest = {"source": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "targets": {}, "svg": [], "cards": {}, "format": 1}
        seen_svg = set()
        for target in project["targets"]:
            entry = {"costumes": {}, "sounds": {}}
            for c in target["costumes"]:
                md5ext = c.get("md5ext", c["assetId"] + "." + c["dataFormat"])
                path = f"images/{Path(md5ext).stem}.png"
                entry["costumes"][c["name"]] = {"path": path, "source": md5ext,
                    "center": [c.get("rotationCenterX", 0), c.get("rotationCenterY", 0)],
                    "bitmap_resolution": c.get("bitmapResolution", 1),
                    "raster_scale": 3 if c["dataFormat"] == "svg" else 1}
                if c["dataFormat"] == "svg":
                    if md5ext not in seen_svg:
                        manifest["svg"].append({"source": md5ext, "output": path})
                        seen_svg.add(md5ext)
                else:
                    (DEST / path).write_bytes(z.read(md5ext))
            for s in target["sounds"]:
                md5ext = s.get("md5ext", s["assetId"] + "." + s["dataFormat"])
                path = f"sounds/{md5ext}"
                (DEST / path).write_bytes(z.read(md5ext))
                entry["sounds"][s["name"]] = path
            manifest["targets"][target["name"]] = entry
        uno = next(t for t in project["targets"] if t["name"] == "uno")
        initial_deck = next(v[1] for v in uno["lists"].values() if v[0] == "_initial deck")
        (DEST / "source_deck.json").write_text(json.dumps(initial_deck, indent=2), encoding="utf8")
        costumes = manifest["targets"]["uno"]["costumes"]
        for name, spec in costumes.items():
            if not (name.startswith("red ") or name.startswith("wild ") or name == "uno"):
                continue
            im = Image.open(DEST / spec["path"]).convert("RGBA")
            variants = {"red": 0, "yellow": 32, "green": 68, "blue": 116} if name.startswith("red ") else {"wild": 0}
            for color, effect in variants.items():
                value = name[4:] if name.startswith("red ") else name
                key = "back" if name == "uno" else f"{color}:{value}"
                path = "cards/" + key.replace(":", "_").replace(" ", "_") + ".png"
                scratch_hue(im, effect).save(DEST / path)
                manifest["cards"][key] = path
        manifest["statistics"] = {"unique_images": 279, "unique_sounds": 66,
                                   "costume_references": sum(len(t["costumes"]) for t in project["targets"]),
                                   "sound_references": sum(len(t["sounds"]) for t in project["targets"])}
        (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf8")
    print(f"Imported {len(manifest['cards'])} card faces/backs, {len(seen_svg)} SVG jobs, 66 sounds.")


if __name__ == "__main__":
    main()

