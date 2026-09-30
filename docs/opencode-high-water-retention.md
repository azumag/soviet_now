# OpenCode high-water retention

## Scope and completion

Only the dedicated, single-threaded retention process changes. The existing
one-day cutoff, 1 GiB filesystem reserve, producer flock gate, SQLite EXCLUSIVE
lock, synchronous=FULL and single-transaction child/session rollback remain.
Production execution, cleanup policy, deployment, provider settings and game
inputs are outside this PR.

When the complete-database WAL preflight cannot fit, attempt at most eight old
sessions in **one** transaction. The session limit is a candidate bound only.
After an initial successful TRUNCATE checkpoint, RLIMIT_FSIZE gives this process
a kernel-enforced file-size ceiling of at most 128 MiB during DELETE/COMMIT.
The soft limit can only decrease; the hard limit is never changed. SIGXFSZ is
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

Preflight requires the WAL ceiling plus **4 MiB** for WAL-index/file allocation
overhead above the unchanged 1 GiB reserve. With SQLite's minimum 512-byte page
size, a 128 MiB WAL contains fewer than 251,000 frames; WAL-index growth at eight
bytes per frame is less than 2 MiB. The 4 MiB allowance includes block rounding.
The existing free-space/deadline progress checks remain; unrelated filesystem
writers can still consume disk space, as with the existing contract.

## Observable outcomes and limitations

- Success commits the chosen set atomically, checkpoints, records
  `deleted_sessions`, `selected_sessions`, `remaining_sessions` and freelist.
  Return code 75 / `deferred`, stage `compact_deferred`, reason
  `bounded_prune_committed` explicitly preserves the outstanding compaction.
- No VACUUM or compact copy is attempted in this fallback. Existing freelist
  does not force a compact-first attempt when even its output image cannot fit.
  Later writers can reuse freed pages, reducing growth; the DB file remains the
  same inode and size. This is **not** proof of increased physical free space.
- An I/O/size-limit failure rolls the whole selected set back, reports
  `bounded_prune_io_error`, `bounded_prune_blocked=true` and
  `recovery_action=inspect_io_or_add_capacity`. An arbitrary I/O error is not
  labelled proof that the ceiling was reached.
- If the oldest eight sessions exceed the ceiling, the existing timer may
  retry that same set. This PR explicitly reports intervention needed and does
  not silently promise eventual recovery. It does not add repeated scans,
  multiple transactions per invocation, unbounded adaptive retries or a larger
  limit. Capacity expansion or a separately reviewed smaller-batch strategy is
  required for persistent failures. No automatic bypass is provided.
- Post-COMMIT checkpoint/compaction failure must retain the committed deletion
  count; it is never reported as rollback of already committed rows.

## Regression acceptance

Ubuntu 24.04 History retention CI runs the complete retention and producer gate
tests. Synthetic SQLite fixtures cover recent sessions, every child table,
multiple indices, overflow blobs with secure_delete ON, event payloads,
trigger amplification, cache spills, EFBIG at COMMIT, SIGKILL during an
uncommitted WAL transaction and reopen/integrity/foreign-key verification.
They assert all selected children survive on failure, inode/recent payload
preservation, producer resumption, ungated-writer exclusion, limit/signal/
automatic-checkpoint restoration, low-space/install failure and result JSON.
No production data or live mutation is used by these tests.

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
