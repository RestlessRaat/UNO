# Source rules and port decisions

Reference: `Uno Show 'Em No Mercy (Multiplayer).sb3`, sprite `uno`, current `_Uno.Game type = 7`.
`tools/inspect_sb3.py` produces readable listings of the procedures named below.

| Behaviour | Python implementation / source evidence |
|---|---|
| Physical deck | `_initial deck`: 168 entries, order and multiplicities checked against Python `CARDS` |
| Random generator | `rand seed = xorshift prng`: 16 bits, shifts 7 left / 9 right / 8 left; seed zero retained |
| Shuffle | `deck.shuffle`: start IDs 168 down to 1; repeatedly select and remove a random remaining item |
| Initial deal | `intro.play`: seven rounds of one card per seat, from the end of the draw pile |
| First discard | `get usable initial discard`: scan downward to a number card, leave skipped action cards in the draw pile |
| Normal legal play | Match colour or value, or play a wild. Equal-valued draw cards also match across ordinary/wild +4 types |
| Stacking | `update legal moves for current player`: draw value at least as large as the last draw card, AND matching current colour / equal draw value / a wild |
| Drawing | May voluntarily draw with playable cards. No ordinary end-turn button; may play any legal card after drawing |
| Accepting a penalty | First draw commits to the entire remaining penalty; newly drawn action cards cannot interrupt it |
| Roulette | `play turn`: next player chooses any of the four colours, draws publicly until that colour, keeps the matching card, then loses the turn; wilds don't satisfy a colour |
| Reverse / skip | In two-player games both give another turn to the same player; skip all always does so |
| Reverse +4 | `prepare for next turn`: reverses direction; in a two-player game the penalty returns to its player, who may stack |
| 7 and 0 | Swap with a selected active opponent; or rotate all active hands along the direction of play |
| Discard all | Discard all remaining cards of that colour underneath the action card; action card remains the top discard |
| Last card | `play game`: empty hand wins before next-turn effects; a last 7 does not swap, and a last wild does not require colour selection or charge the next player |
| Mercy | Intentional change: `len(hand) >= 36` after each draw. Original uses `> 25` in several player/AI paths |
| Eliminated hand | `apply mercy rule`, `transition to bottom of discard pile`: place underneath the discard pile; clear pending draw penalty; skip eliminated players |
| Recycling | `rebuild deck from discard pile if deck is empty`: keep the top discard; randomly remove remaining discards into the draw pile |
| Scoring | `score card...`, `tally points`: face value / 20 / 50; +250 per eliminated opponent |
| Match target | No Mercy branch: `250 + (players - 1) * 250`; 500 / 750 / 1000 |
| UNO / challenge | Automatic UNO voice. No manually enabled UNO penalty/challenge interaction in this No Mercy branch |
| Normal AI | Random legal card, otherwise draw; randomly choose a colour present in its own hand, or any colour for wild-only hands; random swap target |
| Hard AI (optional) | Strategic legal play based on its own hand and public counts, including hand exchanges, action-card threats and bounded follow-up planning; no hidden hands or deck order |

| Devil AI (optional) | Bounded finishing-sequence search and sampled hidden-world rollouts using only public information; simulations reuse the rule engine |

## Representation

`GameConfig` holds names, the fixed mercy limit and previous scores. `Game` holds physical card IDs in deck/hands/discard, current player, direction, pending draw total, last draw value, selection phase, eliminated seats and scores.

`legal_actions(player_id)` returns allowed semantic operations. `apply_action()` rejects invalid actions before mutation and emits events. `view_for()` hides other hands and non-public draws. Its `known_discards` records publicly revealed cards still in the discard pile, resets when shuffled, and excludes concealed cards discarded through mercy. `replay_game()` rebuilds the state from config, seed and commands.

UI animation and network timing never determine the legality of a move. Normal, Hard and Devil use the same player-visible view. God alone receives a private host-side clone with every hand, the discard pile and the ordered draw pile; this clone is never included in network snapshots. AI choices are recorded as actions, so replay verification does not depend on the AI policy or selected difficulty; shuffle randomness remains the source algorithm. Difficulty is an application/room setting, defaults to Normal, and applies to all bots and disconnected-seat substitutes in that room. The host selects it before play; rematches keep it.

## Deliberate implementation repairs

- A discard-all that empties the hand settles the round correctly; don't reproduce stale Scratch list/index bugs.
- AI with only wild cards can choose a colour; don't reproduce a possible endless colour-selection loop.
- Network logic replaces Scratch cloud variables with an authoritative room and private snapshots.
- Non-host disconnection uses AI and reconnection, as specified, instead of original cloud disconnection handling.
- Old variant branches, unused challenge buttons and disabled promotional intro sequences are not gameplay features.
- Instruction page 1 is re-typeset because its original SVG contains a raster image with the old threshold.
