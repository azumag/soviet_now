# OpenCode high-water retention

## Scope and completion

Only the dedicated, single-threaded retention process changes. The existing
one-day cutoff, 1 GiB filesystem reserve, producer flock gate, SQLite EXCLUSIVE
lock, synchronous=FULL and single-transaction child/session rollback remain.
Production execution, cleanup policy, deployment, provider settings and game
inputs are outside this PR.

When the complete-database WAL preflight cannot fit, drain old sessions in
guarded batches of at most eight sessions in **one** transaction each. The
session limit is a candidate bound only. After an initial successful
TRUNCATE checkpoint, RLIMIT_FSIZE gives this process a kernel-enforced
file-size ceiling of at most `PRUNE_WAL_LIMIT` (1 GiB) during every
DELETE/COMMIT, re-armed per batch against the space the previous batch's
checkpoint returned. The ceiling is always `min(1 GiB, free − reserve − 4 MiB
− cap/64)`: the floor, not the constant, is what keeps one batch from filling
the filesystem, and `cap/64` reserves the WAL-index (worst case about eight
bytes per 512-byte page) that the kernel ceiling cannot see because it lives
in the separate `-shm` file. The 1 GiB constant has to cover realistic
sessions: a single session's child rows can reach several hundred MB, and the
former 128 MiB value turned one oversized session into a total stall. The
soft limit can only decrease; the hard limit is never changed. SIGXFSZ is
temporarily ignored so the write returns EFBIG instead of killing the process.
The ceiling includes WAL frames from blobs, indices, triggers and cache spills.
Existing nonzero WAL must be checkpointed first; a busy checkpoint defers.

The automatic checkpoint is disabled inside the guard so it cannot write into
the larger live database under the small ceiling. Restore both resource limits,
the exact signal disposition and the previous automatic-checkpoint setting
before rollback, explicit checkpoint, connection close or result-file writes.
The process-wide ceiling is inappropriate for an embedded/multithreaded host;
that path refuses to mutate. The cooperative gate is not assumed to cover every
producer: SQLite's EXCLUSIVE connection blocks noncooperative SQLite writers.

Preflight requires the WAL ceiling plus **4 MiB** for filesystem block
rounding above the unchanged 1 GiB reserve; the WAL-index allowance is carved
out of the ceiling itself (`cap/64`, at most 16 MiB at the 1 GiB ceiling, so
WAL plus index stay inside the free-space floor). The existing
free-space/deadline progress checks remain; unrelated filesystem writers can
still consume disk space, as with the existing contract.

## Observable outcomes and limitations

- Success commits the chosen set atomically, checkpoints, records
  `deleted_sessions`, `selected_sessions`, `remaining_sessions` and freelist.
  Return code 75 / `deferred`, stage `compact_deferred`, reason
  `bounded_prune_committed` explicitly preserves the outstanding compaction.
- Under a full-image WAL shortfall the invocation now drains the expired
  backlog in guarded batches: at most `CATCHUP_MAX_BATCHES` (64) transactions
  of `PRUNE_BATCH_SESSIONS` (8) sessions each, every batch under a freshly
  re-armed ceiling and followed by a TRUNCATE checkpoint, with the deadline
  and free-space checks still bounding one invocation. `prune_batches` records
  committed batches; each batch is committed or rolled back on its own and a
  later failure never relabels earlier committed rows.
- After the eligible set is drained (catch-up complete, freelist nonzero), one
  live-image compaction is attempted. `VACUUM INTO` excludes freelist pages, so
  the copy is budgeted from live bytes rather than `page_count`; the measured
  writeback WAL is budgeted separately and a tmpfs copy is used only under the
  same opt-in RAM rules as before. The attempt is fail-closed: if it cannot
  fit, the committed-prune outcome is kept, reason stays
  `bounded_prune_committed` and `compact_defer_reason` records only the fixed
  enum (`insufficient_space`, `deadline`, `checkpoint_busy`, ...). A
  successful compaction shrinks the live inode and is reported as
  `completed`; physical free-space change is then measurable via
  `after_bytes` instead of assumed.
- An I/O/size-limit failure rolls back only the batch in flight, reports
  `bounded_prune_io_error`, `bounded_prune_blocked=true`,
  `recovery_action=inspect_io_or_add_capacity` and the fixed
  `sqlite_error_code`/`sqlite_extended_error_code`; batches committed earlier
  in the invocation remain committed. An arbitrary I/O error is not labelled
  proof that the ceiling was reached.
- A batch that hits the ceiling is retried one session per transaction: only
  the session that fails again is excluded for this run (`skipped_sessions`,
  counted in `skipped_batches`), its neighbours are pruned normally, and a
  TRUNCATE checkpoint clears the rolled-back frames before the next guard.
  Retries are bounded by `CATCHUP_MAX_RETRIES` (64) per invocation, so an
  oversized session no longer stalls every expired session behind it. If
  nothing at all could be pruned the run reports `bounded_prune_io_error`;
  with a completed sweep except for known skipped blockers, compaction is
  still attempted. No automatic bypass, unbounded adaptive retries or
  unreviewed larger limit are added.
- Throughput is at most 512 sessions per invocation at an hourly cadence;
  anything left over is reported in `remaining_sessions` and continues on the
  next run. A committed batch is not a claim that storage exhaustion has been
  resolved, and the growth cause of the database itself is outside this
  contract.
- Post-COMMIT checkpoint/compaction failure must retain the committed deletion
  count; it is never reported as rollback of already committed rows.

## Regression acceptance

The additive `preflight_phase` field reports fixed enums for input/budget,
connection, each initial PRAGMA, BEGIN EXCLUSIVE, its COMMIT, journal mode,
initial checkpoint, page/count reads and delete-budget calculation. `complete`
means preflight has finished; use the unchanged `stage` for later failures.
On SQLite BUSY/LOCKED, `sqlite_error_code` contains the basic code (5/6) and
`sqlite_extended_error_code` the numeric exception code supplied by Python.
No SQL, exception text, DB data or arguments are recorded. Existing status,
reason, stage and exit codes retain their meaning. Old allowlist collectors
ignore the new fields; direct status JSON has the detail. Reused metrics clear
prior busy codes so a later success cannot inherit a previous failure.

The catch-up and compaction follow-up adds only fixed integer/enum fields:
`prune_batches` (committed guarded batches this invocation),
`selected_sessions` accumulated across batches, `remaining_sessions` after
catch-up, `skipped_batches` (batch/single attempts that hit the ceiling and
rolled back) and `skipped_sessions` (sessions excluded for this run after
their single-session retry failed again), both reset per invocation, and
`compact_defer_reason` restricted to the existing fixed reason set when a
post-catch-up compaction is deferred. `sqlite_error_code` is also recorded at
the ceiling-failure conversion, not only on BUSY/LOCKED. `compact_storage`
and `compact_bytes` describe a successful compaction the same way as the
normal path. Collectors that allowlist fields must be extended separately;
missing keys degrade to `unknown`, never to raw text.

Ubuntu 24.04 History retention CI runs the complete retention and producer gate
tests. Synthetic SQLite fixtures cover recent sessions, every child table,
multiple indices, overflow blobs with secure_delete ON, event payloads,
trigger amplification, cache spills, EFBIG at COMMIT, SIGKILL during an
uncommitted WAL transaction and reopen/integrity/foreign-key verification.
They assert all selected children survive on failure, inode/recent payload
preservation, producer resumption, ungated-writer exclusion, limit/signal/
automatic-checkpoint restoration, low-space/install failure and result JSON.
No production data or live mutation is used by these tests.

VM retention Python read-only metadata: SQLite3.45.1, in-memory connection
secure_delete default=1, compile options SECURE_DELETE/THREADSAFE=1/
DEFAULT_WAL_AUTOCHECKPOINT=1000/DEFAULT_WAL_SYNCHRONOUS=2. This is not a query of
the live DB's connection setting or the OpenCode binary's SQLite version.
Local tests used SQLite3.53.4/default0; fixtures explicitly exercise overflow
erasure with secure_delete ON. The CI records its own platform/version/default.
Parent separately verified Linux/SQLite3.53.1 caps and rollback under both
secure_delete values. These tests are not production recovery proof.

Kernel semantics: [Linux getrlimit(2)](https://man7.org/linux/man-pages/man2/getrlimit.2.html).
SQLite references: [result codes](https://www.sqlite.org/rescode.html),
[automatic checkpoint](https://www.sqlite.org/pragma.html#pragma_wal_autocheckpoint).

## Emergency capacity alternative

Read-only VM status at 2026-09-30 21:02:57 UTC recorded page_size=4096,
page_count=2,230,873 and free=4,509,192,192 B. Existing DELETE preflight needs
10,265,004,120 B, leaving **5,755,811,928 B** of additional free-space need at
that snapshot. At 21:20:01 UTC, free=4,344,602,624 B and DB logical size was
9,511,055,360 B. Using that logical size only as an estimate gives an additional
**6,295,988,936 B** for DELETE preflight. Live page_count at 21:20 was not queried.

Compaction is a separate budget: output image plus measured writeback WAL plus
reserve. Assuming both images remain as large as the 21:20 DB gives a
conservative estimated free-space requirement of 20,151,712,456 B, or
**15,807,109,832 B additional** relative to that free-space sample. Compact output
size is unknown, so this is not a measured minimum or a guarantee under ongoing
growth. Cloud/volume changes are not made by this PR.

Read-only block-device comparison: sda=50,010,783,744 B, root sda1 partition=
48,935,976,448 B, EFI=103,809,536 B, boot=967,836,160 B. Device minus partition
totals leaves only 3,161,600 B, so there is no multi-GB already allocated but
unpartitioned area. statvfs root size=47,321,268,224 B; its difference from
partition size includes filesystem accounting/metadata and is not proof of
unexpanded free space. No growpart/resize2fs/cloud operation was performed.

## Cause attribution remains open

The fixed caller collector counts JSON characters only in message/part/event;
it is neither a physical-byte census nor a media-type/duplicate census.
session_message/session_input/session_context_epoch are in retention's deletion
list but not that size collector; their presence/size in production is unknown.
Do not infer their absence or run a full production dbstat scan for this PR.

Parent review of docich main12cb0e8 found the Python common OpenCode dispatcher
does not supply a fixed --title or Soren shared flock, unlike Soren ai_generate.
This is a concrete unattributed-producer candidate, not proof of the DB growth
cause. The Python image dispatch rejects OpenCode image requests and screen
reply strips images before its fallback, weakening direct-screen-image storage
through that route. Other routes and historical/base64 text remain unverified.
No provider/dispatcher change is included here.
