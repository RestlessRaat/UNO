# Local neural UNO implementation plan

Scope: implement and verify a local self-play learning pipeline and a playable
plugin. This run includes a short training experiment only, not a strength claim
or an unattended multi-hour training job.

1. Inspect installed PyTorch/CUDA and select a reproducible local environment.
2. Add a versioned observation/action encoding: unordered card types, relative
   seats, public events, known cards, and grouped equivalent legal actions.
3. Implement a small set-attention actor with an event-history GRU, a separate
   privileged critic, and hidden-card auxiliary prediction.
4. Implement batched headless self-play, PPO updates, per-seat returns, historical
   opponents, checkpoint/optimizer/RNG restoration, logs, and a time limit.
5. Add held-out evaluation with seat rotation, explicit truncation reporting,
   and no training from evaluation trajectories.
6. Deploy an opt-in local neural plugin, launch scripts, and documented commands.
7. Verify hidden-information isolation, action legality, permutation invariance,
   parameter updates, reload/resume, and full UI/plugin integration. Run a short
   CUDA training experiment and record measured performance and evaluation.

Design decisions:

- Reuse the current rules engine. Neural modules are optional; importing the
  rules engine or ordinary UI must not import PyTorch.
- Start with complete rounds and terminal win rewards. Full-match mode preserves
  scores and uses match-win rewards. No heuristic shaping or combined round-score
  reward changes the objective silently.
- All recurrent history is recomputed from a bounded public-event window. This
  makes PPO sequence gradients exact for the chosen observation and prevents
  cross-seat hidden-state leakage or stale recurrent states after weight updates.
- Actor inputs never contain full hands, deck order, random seed, or physical
  card identifiers as learned features. The critic and auxiliary targets are
  separate training-only paths.
- League opponents are frozen historical checkpoints. Their actions are not used
  as on-policy PPO samples. Exploiter training is a later extension, not represented
  as implemented by merely naming historical snapshots exploiters.
- Use full legal rules for 2/3/4 players. Fixed seed ranges are split into training
  and evaluation because the source shuffle has only 65,536 possible seeds.
- End-of-rollout and step-limit cutoffs bootstrap value; they are not losses.
  Eliminated players still receive their eventual round/match outcome.
- Deploy a source-run desktop launcher and CPU inference plugin. Prewarm PyTorch
  before opening the window, then reuse the existing background AI executor for
  inference. Existing packaged EXEs lack the new API and ML dependencies.

Status: implementation in progress. Final commands, measurements, and limitations
will be recorded in NEURAL_AI.md and the local run directory.
