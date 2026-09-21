# UNO Show 'Em No Mercy — Python 36-Card Edition

A Windows desktop remake built with the 168 cards, costumes, fonts, music, and voice clips from the SB3 project included here. Supports offline AI matches for 2–4 players, plus LAN matches with a mix of human players and AI.

## Run the Game

Extract `dist/UNO_No_Mercy_Windows.zip` and launch `UNO_No_Mercy.exe` from the extracted folder. Keep the entire folder, including the `_internal` subfolder. The game does not require Python, Scratch, or a browser to be installed.

To run from source (Python 3.14):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m uno
```

The generated `assets` directory is ready to use. `sb3_assets` and the original SB3 file are the source files used to import the assets.

## Controls

- **Play with AI**: Enter player names, choose 2, 3, or 4 players and AI / Elo, then start. **Normal** keeps the original strength; **Hard** uses a strategic AI; **Devil** uses simulation search. Normal is the default, and the selection is saved automatically. The number on each button is the relative Elo calibrated in two-player matches.
- Animations play at **1.5× speed**, including dealing, drawing, hand swaps, scoring, hover effects, and color selection. Voice and music stay at normal speed. In LAN games, the host schedules AI actions using the same accelerated animation timings.
- Hover over a card to enlarge it. Click a highlighted legal card to play it. Click the draw pile on the right or the Draw button to draw.
- Wild cards let you choose a color; a 7 lets you choose a player to swap hands with. The next player receiving a Color Roulette penalty chooses the color.
- Use `←` / `→` to select a card and Enter to play it; `D` / Space to draw, `M` to mute, `F11` for fullscreen, and `Esc` to go back or open the quit confirmation.
- Messages uses the original 20 preset phrases. Click the question mark to open the original rules pages.
- UNO is called automatically. No Mercy mode has no regular “end turn” button and does not use the classic UNO +4 challenge.
- Reach a cumulative score of 500 with two players, 750 with three, or 1000 with four to win the match. **New match** resets the score.

## Local Network Play

1. The host selects **Create LAN room**, using the default port `8765` or choosing another port.
2. The room displays the host's local IP address and port. Other players enter that address in **Join LAN room**, for example `192.168.1.10:8765`.
3. The host can add AI players, remove seats, and choose the AI / Elo setting in the lobby. Human guests click Ready; the host clicks Start. All AI players in the room, including AI that takes over after a disconnect, use the same difficulty. It is fixed for the match and carries over to the next one. Guests see the Elo value provided by the host.
4. If Windows Firewall prompts you, allow the game on **Private networks**. Both computers must be on a network that lets them reach each other. If the host has multiple network adapters, use the local IPv4 address that guests can reach.

The game does not need internet access. The first version uses direct IP connections and does not provide automatic discovery, public matchmaking, or NAT traversal. The host controls card order and rules, and sends each guest only that guest's hand and public information.

If a non-host disconnects, AI takes over and the client attempts to reconnect automatically. After closing and reopening the game, the player can reclaim the same seat with the same address and saved token. A reconnecting player regains control on their next turn; AI finishes any action already in progress. If the host exits, the room ends. During a match, only reconnects are accepted; new players cannot join. Do not expose the LAN service port to the public internet.

## Rules Reference

This version follows the No Mercy branch as implemented in the SB3. **The only intentional gameplay change is elimination when a hand reaches 36 cards.** Some details differ from Mattel's official physical-game rules; see [`docs/RULES.md`](docs/RULES.md). The original four colors, stacking restrictions, 7 hand swap, 0 rotation, special cards, and scoring are retained. No other UNO modes have been added.

## AI Difficulties

- **Normal**: Uses the original behavior: choose a legal card, hand-swap target, and color at random.
- **Hard**: Prioritizes playing out, evaluates same-color discards and follow-up plays, and uses 7 hand swaps, 0 rotation, skips, and penalties based on each player's public hand size. It selects colors based on its remaining cards and handles Color Roulette and two-player reverse +4 separately.
- **Devil**: Searches for finishing combinations and keeps a persistent belief model from public play, concealed draws, chosen colours, and exact hands it previously saw through 7/0 transfers. It samples hidden-card worlds from that evidence, evaluates the usefulness of whole hands when choosing 7/0 targets, models strategic replies, and can deliberately draw up to three cards when a continuation is worth pursuing. It computes in the background in offline and LAN games so the interface stays responsive.

Normal, Hard, and Devil only read their own hand and public information. They do not inspect opponents' hidden cards or the deck order. Hidden cards in simulations are randomized assumptions; the rules are otherwise the same.

- **God**: An omniscient difficulty that can read every hand, the discard pile, and the complete deck order. It uses iterative-deepening adversarial search on the actual game state, fully resolves penalties and Color Roulette, and plans color locks, penalty stacking, and eliminations. Color Roulette selects the nearest matching color in the real deck order, including after recycling and shuffling. Deck order is cached between shuffles; simulation branches share the order and advance their indices separately. Forced draws are executed directly. A warning appears before selection, with a dark red Yes button to confirm and No to cancel. God still follows the play rules; omniscience does not guarantee a win. Elo is not calculated or displayed.

## Relative AI Elo

Normal AI is the **1000**-point baseline. Hard is **1174** (a historical reference calibrated under the old 35-card rule). The strengthened Devil is **1265** under the current 36-card rule. God does not display Elo.

The latest Devil calibration used this game's native rule engine and measured single-round outcomes in two-player matches:

- Seeds 52001–52200 were held out from the implementation check. Each seed was played twice with Devil and Normal swapping seats, for **400 rounds** total.
- Devil won **329** and Normal won **71**, an **82.25%** Devil win rate. Devil won 169/200 as the first seat and 160/200 as the second seat.
- Fitting the stated Elo curve gives Devil **1265.42** relative to Normal at 1000. This two-player estimate does not claim a human rating or guaranteed strength in three- and four-player games.

Devil received only its own hand and public information, including publicly revealed Color Roulette cards. It did not receive hidden hands or deck order. Its persistent card beliefs are deductions from public actions; exact remembered cards come only from hands the same seat previously possessed and transferred through 7 or 0. The displayed two-player rating is also used for three- and four-player games.

The rating difference is calculated as `400 × log10((wins + 0.5) / (losses + 0.5))`. The program recalculates scores from the recorded wins in `assets/ai_elo.json` instead of trusting a manually entered value.

To reproduce the latest calibration:

```powershell
.\.venv\Scripts\python.exe tools/calibrate_elo.py --challenger devil --start-seed 52001 --seeds 200 --workers 16 --output assets/ai_elo.json
.\.venv\Scripts\python.exe tools/build_release.py
```

## Saves and Diagnostics

Settings, reconnect tokens, recent offline replays, host replays, and error logs are saved under `%LOCALAPPDATA%\UnoNoMercy`. A token is only used to reclaim a seat in the current room. Replays contain the full deck order and are stored only on the offline player's or host's computer; they are not sent to guests. Set `UNO_USER_DIR` to use another directory.

```powershell
.\.venv\Scripts\python.exe -m uno --replay path\to\replay.json
```

## Rebuilding Assets

Developers need Node.js and Chromium only to rebuild assets. The packaged game does not need them.

```powershell
.\.venv\Scripts\python.exe tools/prepare_assets.py
npm ci --prefix tools/renderer --no-audit --no-fund
node tools/renderer/render.mjs
.\.venv\Scripts\python.exe tools/finalize_assets.py
```

The renderer uses an installed copy of Chrome or Edge by default; set `UNO_CHROMIUM_PATH` to choose another browser. SVGs are rendered with Scratch's bundled fonts; empty SVGs produce transparent placeholders. Page 1 of the original rules contains a bitmap with “25”; this version re-lays it out in the same handwritten font for the 36-card rules. The remaining pages reuse the original. Source files are not modified; the asset index records original filenames, sprite/costume mappings, rotation centers, and bitmap scales.

## Tests and Packaging

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe tools/benchmark_ai.py --seeds 100 --output build/ai-benchmark.json
.\.venv\Scripts\python.exe tools/smoke_lan.py
.\.venv\Scripts\python.exe -m uno --smoke game --screenshot build/screenshots/game.png
.\.venv\Scripts\python.exe tools/build_release.py
.\.venv\Scripts\python.exe tools/smoke_release.py
```

On this computer, `tools/renderer/baseline.mjs` reads the original SB3 in Scratch VM and saves the original opening, lobby, table, and states to `build/baseline`; it does not upload the project. `tools/benchmark_ai.py --challenger devil --opponent hard --players 2` compares Devil with Hard. By default, one Hard AI plays against Normal AI opponents across every seat in 2-, 3-, and 4-player games: 100 seeds per seat, 900 games total, with actual win rates and decision times reported.

The project has passed rule, animation, UI, and network tests; 75 fixed-seed AI games; a full local game using two client processes; and standalone EXE launch and LAN-service smoke checks. Python dependencies are pinned in `requirements.lock.txt`, and asset-build dependencies are pinned in `tools/renderer/package-lock.json`.

Animations preserve the original frame-by-frame motion and play the original 30-frame timing at 45 logical frames per second, or 1.5× speed. Source-script references, frame comparisons, and GIF-preview instructions are in [`docs/ANIMATIONS.md`](docs/ANIMATIONS.md). Visual and test records are in [`docs/VERIFICATION.md`](docs/VERIFICATION.md). Testing on two physical computers and on a Windows installation without Python is still needed.

## Project Structure

- `uno/engine.py`: deck, pure rules state machine, visible state, and replays.
- `uno/ai.py`: difficulty entry points and Normal / Hard AI.
- `uno/ai_api.py`, `uno/ai_plugins.py`: stable third-party AI contract, plugin discovery, configuration validation, and built-in AI registry.
- `uno/application.py`: game sessions, seat controllers, unified AI execution, and failure fallback.
- `uno/devil.py`: Devil's play-out search and public-information simulations.
- `uno/elo.py`, `tools/calibrate_elo.py`: cumulative-match Elo fitting, two-player seat-swapped calibration, and result loading.
- `uno/network.py`: rooms, host authority, private snapshots, disconnects, and reconnects.
- `uno/app.py`, `uno/layout.py`, `uno/resources.py`: Pygame interface, layout, animation, music, and voice.
- `tools/`: read-only SB3 inspection, asset building, original-project comparisons, LAN smoke checks, and release packaging.
- `tests/`: rules, full matches, assets, UI interactions, networking, and large-hand selection.

## External AI Plugins

Copy a plugin folder to `%LOCALAPPDATA%\UnoNoMercy\plugins` and restart the game for it to be discovered. External AIs can read only their own hand and public game information. Each bot seat can use a different plugin and its own settings.

Plugins run as trusted Python code inside the game process, so install them only from sources you trust. See [`docs/AI_PLUGINS.md`](docs/AI_PLUGINS.md) and [`examples/plugins/random-plus`](examples/plugins/random-plus) for the development contract, manifest, timeout fallback, and example. This version uses LAN protocol v2; older clients receive a clear version-mismatch message and cannot join a v2 room.
