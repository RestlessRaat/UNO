# Credits and source assets

This project is a Python recreation of the user-supplied `Uno Show 'Em No Mercy (Multiplayer).sb3`.
Its original artwork, card scans, sounds, music, voices and attribution remain associated with their original creators and rights holders. Original project scripts refer to RokCoder and Mrs RokCoder.
UNO and UNO Show 'Em No Mercy are Mattel marks. This port is not an official Mattel release.

The original SB3 and `sb3_assets` are preserved. `assets/manifest.json` records the source SHA-256 and asset-name mappings.

Runtime components: Python, pygame-ce/SDL, websockets, and a PyInstaller bootloader.
Build/reference tooling: Scratch SVG Renderer, Scratch VM, Scratch Render, Scratch Storage, Playwright and Chromium.
The Scratch runtime is used only to build assets and verify the reference; it is not the Python game's execution engine.

Fonts are from `scratch-render-fonts`; the included license notices are in `assets/licenses` (or `_internal/assets/licenses` in the portable app).
