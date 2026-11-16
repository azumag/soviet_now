# A/B migration gate (soviet_now#865)

This document governs what to do when a strategy.py change must be merged or
reflected into the running agent, in the presence of a live A/B experiment.

## 1. Why this exists

Confirm-stage A/B experiments (108 matches at #865, also 10-31 and 11-04) ended
"無判定" (no judgment) because the strategy PR was directly merged (root swapped)
without going through the A/B gate. The gate (`ab_gate.sh`) only guards
improvement candidates; it does not cover direct merges of `strategy.py`.

The runtime safety nets (`ab_interleave.sh::_ab_active`, `ab_integrity.sh`) do
detect root-hash drift, but they are game-loop triggered — they do not fire
during a merge window. The missing piece is a fail-closed pre-merge / pre-
reflection check.

## 2. Mechanical check: `strategy/ab_gate_on_merge.sh`

A self-contained bash gate. Source it (or run it) with the A/B environment
already configured (`AB_STATE_FILE`, `TMP_STATE_DIR`):

    strategy/ab_gate_on_merge.sh check     # exit 0 = clear / 1 = active A/B / 2 = unknown

The check looks at:

   - `tmp/state/ab_state.json` (a_hash / b_hash set, no `ab_abort` marker)
   - `SOREN_AB_ALT_STRATEGY` non-empty (live arm selected)
   - `.env` `REGRESSION_DISABLED=1`
   - `tmp/state/improve_daemon.paused`

Any one is sufficient to report "active". `improve.lock` is intentionally not
a signal: its real path is `tmp/improve.lock`, not `tmp/state/`, and the
repository convention treats it as a game-history data file that is normally
present. `ab_interleave.sh` requires "pause present AND improve.lock absent"
for an A/B, so a present lock is evidence *against* an A/B, not for one.
`REGRESSION_DISABLED=1` is not A/B-exclusive (it has been used to freeze other
improvements); over-detection is accepted because the gate is fail-closed.

### Where it must run

- **Before PR merge** of `strategy.py` (enforced by CI workflow
  `.github/workflows/ab-migration-gate.yml`, and wired into
  `merge-opportunities.yml`).
- **Before live reflection** of `strategy.py` into the running agent.
  Implemented (soviet_now#925): `core/strategy_runtime.sh`'s
  `_strategy_runtime_ab_merge_guard` runs the check inside
  `strategy_runtime_projection_apply_gate` — the shared choke point every
  structured live-bytes writer passes through (improve adopt / revert,
  `eloop.sh` recovery rollback + repair, `strategy/ab_gate.sh` B adopt) — and
  in front of the two direct-writer paths that bypass that gate
  (`strategy/regression.sh` auto-rollback and `core/version.sh`
  `recover_strategy_backup`). `strategy/ab_gate.sh::_ab_finish` sets
  `AB_GATE_ON_MERGE_INTERNAL_ADOPT=1` for the experiment's own B adopt so the
  conclusion path does not deadlock on the gate.
- **docich deploy/reconcile plane** (carve-out, tracked as a docich
  follow-up): `ops/vm_actions/gateway.py`'s `deploy_git` (`_apply_projection`)
  and `reconcile_presynced` (`reconcile_presynced_submodule.py`,
  `LIVE_PROJECTION=/home/ubuntu/soren`) write reviewed bytes to the live root
  outside this repo's runtime. The same check belongs in that plane before
  the `games/soviet_now` projection is applied.

Fail-closed: exit 2 (unknown) and exit 1 (active) both abort the merge/reflect.
An `ab_state.json` that exists but cannot be read returns 2 rather than
falling through to the remaining signals; a non-empty but unparseable state
file also returns 2. A missing gate script (`strategy/ab_gate_on_merge.sh`)
under the resolved runtime root is treated as unknown — the guard refuses
(rc=3) rather than letting an unverifiable apply through.

Two operational consequences of covering the direct-writer paths:

- **Startup `.bak` restore is gated.** If `strategy.py` is absent and an A/B
  is active (or the state is unreadable) when `soren_loop.sh` runs
  `recover_strategy_backup`, the restore is refused and the loop exits with
  "strategy.py が見つかりません". The operational remedy is to retire the
  experiment first — write `tmp/state/ab_abort` — then re-run the restore.
- **`eloop.sh`'s fallback `cp` is unreachable in production.** `eloop_lib.sh`
  lines 28-29 unconditionally source `core/strategy_runtime.sh` and
  `core/strategy_runtime_strict.sh`, so `_strategy_runtime_ab_merge_guard` is
  always defined in the production loop; the `command -v` fallback branch in
  `eloop.sh` only exists for non-production sourcing.

On refusal the runtime leaves evidence: a log line
(`ab_gate_on_merge refused live apply`), the durable markers
`tmp/state/projection_guard_last_event.json` (event `refused`, reason
`ab_merge_gate_active` / `ab_merge_gate_unknown`) and
`tmp/state/strategy_apply_last_failure.json` (failure_code
`ab_merge_gate_refused`), plus an `_ab_status_publish_async` event when that
publisher is loaded.

### Note on CI

`tmp/state/` is gitignored, so CI cannot see the runtime A/B state. This CI
workflow validates the gate's syntax and unit tests (including the runtime
boundary regression tests in `tests/test_ab_merge_gate_runtime.sh`); the
runtime enforcement point is the orchestrator reflection boundary.

## 3. Decision: handling a root change while an A/B is active

Three options, with recommendation (**A**):

**A — Wait (fail-closed, recommended).** Block any merge/reflect of
`strategy.py` while an A/B is active. The A/B finishes on its own (finish A or
B), then the merge/reflect proceeds. Aligns with the existing gate semantics
and protects the 108-game confirmation stage. Cost: deploy available later.

**B — Recreate candidates on the new root and continue.** When root is changed,
re-snapshot the in-flight B arm as the new candidate, rebuild `ab_state.json`
(`a_hash` = new root, `b_hash` = candidate), and keep the A/B running. Preserves
the experiment's investment but invalidates the confirmation sample (the sample
was collected against the old root) and can silently corrupt the confirmation
contract (`confirmation_required` / `games_recorded`).

**C — Discard (abort).** Manually abort the in-flight A/B (write
`tmp/state/ab_abort`), retire it as a stale experiment, and require a fresh
start after the merge/reflect. Fastest to move, but any investment in the
running A/B is lost and a fresh confirmation sample is needed.

**Recommendation: A (wait).** It is the only option that keeps the confirmation
sample's validity unquestioned and matches the existing fail-closed gate
convention. If the team prefers B/C for schedule reasons, the issue thread
should explicitly record the choice before proceeding.

## 4. Retroactive comparison for directly-merged changes

A procedure to attach a post-hoc A/B comparison to a strategy change that was
merged directly (no A/B gate):

1. **Freeze the baseline.** Tag the pre-merge `strategy.py` hash with the merge
   timestamp, e.g. `tmp/history/ab_${ts}_baseline_hash.txt`.
2. **Run the simulator.** Use `tools/ab_decide.py --simulate <games.jsonl>` against
   the new root to replay a fixed set of past games (or a new matched batch) and
   record the roll-in verdict for the merged strategy vs the pre-merge root.
3. **Archive the evidence.** Write `tmp/history/ab_${ts}_retro_comparison.json`
   containing: merged hash, baseline hash, simulation command, verdict, and
   `games_recorded`.
4. **Outcome routing:**
   - If the newly merged strategy loses the retroactive comparison, close it as
     a "direct merge" regression and schedule a gate fix.
   - If it wins, file a follow-up PR that puts the comparison behind the A/B
     gate for the next such change (optional, best-effort).
5. **Retention.** Keep these in `tmp/history/` only; they are evidence, not a
   new gate. Do not alter the live strategy.py to reflect the simulation.

## 5. References

- `strategy/ab_gate.sh` — existing improvement-candidate gate (A/B of new
  candidates, not direct merges).
- `strategy/ab_confirmation.sh` — confirmation-stage A/B contract.
- `strategy/ab_integrity.sh` — stale retirement (game-loop triggered).
- `tools/ab_decide.py --simulate` — post-hoc comparison replay.
