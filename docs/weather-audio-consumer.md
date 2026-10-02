# Weather-corner shared audio consumer

This adds a weather-specific adapter to the existing Soren comment queue. It
does not connect or activate the docich weather producer. A caller must pass a
request that already contains one literal line from the approved narration.
The consumer does not fetch JMA data or generate, predict, extend, or rewrite
forecast text.

## Entry points

```bash
enqueue_weather_audio_request "$request_json"
get_weather_audio_receipt "weather_corner:<execution UUID>:<item index>"
```

The request shape is the `SharedWeatherAudioPort` value contract in docich:
schema version 1, fixed source `weather_corner`, a canonical execution UUID,
item index 0–12, literal text up to 1000 characters, the complete
`weather-view` runtime fence, and JMA forecast metadata. The `item_key` must be
`weather_corner:<execution UUID>:<two-digit index>`. Request identity is the
SHA-256 of canonical JSON for the complete normalized request. A retry with
the same key and digest returns its existing receipt. A changed payload for an
existing key is refused.

## Queue and playback boundary

Items are text files in `COMMENT_QUEUE_DIR` and are consumed by the existing
`audio_worker` → `_play_comment_queue` path. Item keys bypass content-hash
dedup, so identical wording at different ordinals remains distinct. Per-key
receipts and the enqueue lock are metadata under the existing queue directory;
they do not form another queue or playback lane.

Enqueue verifies a ready canonical GameSwitch identity and an unexpired
forecast lease. `_play_comment_queue` checks again when claiming the item.
`say_enqueue.sh` routes a weather item's owned player command through the
weather fence helper, which holds the existing GameSwitch shared lock while it
checks the complete identity and spawns that player. During playback the
helper rechecks the full identity using bounded nonblocking lock attempts. A
runtime change, unreadable control plane, or sustained exclusive hold causes
only this helper-owned process group to stop within its termination bound.
Forecast/runtime expiry gates starting; it does not terminate a player that
already started.

The owned-player helper records `played` only after the actual player exits
successfully while the exact runtime identity still matches. The later
`say_enqueue.sh`/queue finalizer can clean up or record uncertainty, but cannot
promote a queued or playing item to `played`. Player failure or uncertain
completion produces `interrupted`; runtime loss uses `runtime_fence_lost`.
Validation or queue failures produce `rejected`. If an audio worker disappears
with an item claimed as `.playing`, recovery records `interrupted` and removes
it from the FIFO instead of risking replay. A retry after a terminal receipt
returns that receipt and never republishes the item.

Receipt JSON follows the docich weather-audio receipt schema, including the
whole-request digest, item key, exact runtime fence, forecast identity,
recorded time, and a limited reason code. Receipt storage is local metadata;
the digest binds a payload but is not a signature or proof of its source.

## Offline verification

`tests/test_weather_audio_consumer.py` uses temporary queue and GameSwitch
fixtures plus a dummy subprocess that only writes a sentinel file. It exercises
stable-key retries and conflicts, enqueue/play-start/runtime-loss fences,
bounded lock contention and owned-group termination, durable completion
receipts, and interrupted recovery without calling TTS or emitting audio.
