# Scratch animation fidelity

The supplied SB3 is the reference. `uno/animation.py` translates its display
updates into a presentation timeline. Original frame counts, geometry and
durations are preserved and played on a 45 Hz source clock (30 × 1.5). Pygame
draws at 60 Hz and continuously interpolates between source-frame landmarks,
so movement no longer repeats quantised 45 Hz poses. The same actions still
complete in two-thirds of their original time. Hover, wheel, selection fades
and the intro use the same 1.5x speed. Voice pauses use real audio duration,
and speech/music retain normal speed.

| Animation | Source procedure | Implemented behaviour |
|---|---|---|
| Opening deal | `intro.play` | 12-frame hold, 25-frame backdrop fade, round-robin deals at 6 frames per card, 18-frame pause, separate 6-frame first discard, 18-frame pause; four-player timeline including pulse: 5.933 s, previously 8.9 s |
| Play / draw | `transition animation...` / `transition cards over...` | 15 frames (0.333 s, previously 0.5 s), linear movement of every affected hand card, no easing; shortest rotation with Scratch's 180-degree tie convention |
| Hand spacing | `update cards for current index` | Original seat centres and directions, 75% maximum spacing, total span limited per seat |
| Hover | `jiggle card under mouse` / `check for touching card...` | X ±4, Y ±8, angle ±6 degrees; three source sine/cosine frequencies multiplied by 1.5; neighbours to the right open to a 75% gap; no enlargement |
| Turn pulse | `pulse player's cards` / `pulse size` | 20 frames, `1 + 0.15 * sin(frame * 9°)`; card centres stay fixed |
| Face / back | `Uno.Update sprites` clone dispatch | Back overlays fade in/out throughout the transfer; remote private hands remain concealed |
| Brightness | `highlight playable cards` | Scratch's additive brightness, target -48, transitions at 4 units per frame |
| Direction wheel | `update colour wheel for direction...` | Continuous rotation at the source rate of ±1 degree per frame, brightness -20; direction cross-fade over 25 source frames; old colour fades by 8 ghost units per frame |
| Colour selection | `process colour select clones` | Original circle and four quadrant costumes with original rotation centres; 20-frame fades, ±2% size and ±2° rotation; silhouette hit testing and press/release selection |
| 7 swap | `Swap cards between players` | Original target hand moves first, then the playing hand; one card at a time, 6 frames each; both hands reflow for each transfer |
| 0 rotation | `rotate hands in direction of play` | Start at the playing seat, follow active seats in the play direction; original hand contents move one card at a time, 6 frames each |
| Discard all | `discard all card played` | Each matching card travels separately for 15 frames underneath the action card |
| Mercy | `transition to bottom of discard pile` | Mercy voice, then 6-frame transfers in reverse hand order under the discard pile; inclusive 35-card elimination remains a rule-engine decision |
| Roulette | `transition to backs of non-local player` | Revealed draws stay visible; after the matching draw, a 15-frame pause and 3-frame per-card return to backs |
| Scoring | `tally points` | Hooray voice, opponents clockwise after the winner, reverse hand order, 10-frame card transfers, ding and incremental score per card; eliminated players add 250; 30-frame pause and 10-frame collection |

Sound triggers are attached to the timeline's segment boundaries. Rules are
resolved independently and snapshots continue to arrive during animation.
The desktop host paces AI commands to the animation duration so long swaps
and mercy sequences do not cause an ever-growing animation queue.

## Reproduce the comparison

```powershell
node tools/renderer/baseline.mjs --animations
.\.venv\Scripts\python.exe tools/audit_animation.py
.\.venv\Scripts\python.exe tools/render_animation_preview.py
```

The first command executes the original Scratch VM locally and records
positions, transition sources/targets, frame counters and screenshots in
`build/baseline`. The audit compares pygame-coordinate interpolation with
those actual Scratch frames and writes `animation-audit.json`. It also
archives readable procedure listings in `scratch-animation-procedures.txt`.
The preview command produces GIFs of the actual Python renderer in
`build/animation-preview`.

Matching movement formulae does not guarantee pixel-identical
output: WebGL/SVG and Pygame rasterisation differ, desktop/LAN controls have
their own layout, and private remote card identities cannot be used for
cosmetic animation. Audio device latency and loaded-machine scheduling also
affect perceived timing. The disabled promotional scene stays disabled.

Card scale, rotation and brightness results are cached by their actual pixel
transform. This removes repeated work for stationary hands while continuous
poses remain uncached until the same transform is reused. At the default
960×720 window size the already matching canvas is blitted directly rather
than being smooth-scaled to its existing size.
