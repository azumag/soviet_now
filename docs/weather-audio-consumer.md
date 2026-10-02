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
python3 lib/weather_audio_consumer.py quiescence --queue-dir "$COMMENT_QUEUE_DIR" \
  "weather_corner:<execution UUID>:<item index>"
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

Enqueue and `_play_comment_queue` claim checks verify a ready canonical
GameSwitch identity and an unexpired forecast lease. Each check while holding
the receipt ledger uses the bounded GameSwitch lock burst (0.5 seconds) and
fails closed on contention; it cannot hold the ledger indefinitely behind a
game switch. `say_enqueue.sh` routes a weather item's owned player command
through the weather fence helper, which holds the existing GameSwitch shared
lock while it checks the complete identity and spawns that player. During
playback the helper rechecks the full identity using bounded nonblocking lock
attempts before taking the receipt ledger lock. If runtime identity is lost or
the control plane remains unreadable/busy, it stops only its owned process
group first, then records the interruption. A ledger contender therefore
cannot delay stopping audio while an exclusive GameSwitch lock is held.
Forecast/runtime expiry gates starting; it does not terminate a player that
already started.

Before playback, `say_enqueue.sh` persists the expected number of owned player
chunks. Each helper records only that one player process exited with status 0;
this leaves the item receipt `queued` and the chunk awaiting acknowledgement.
After the caller's strict duration check succeeds, `say_enqueue.sh` acknowledges
that chunk. Only the finalizer can write `played`, and only when the acknowledged
chunk count equals the persisted plan with no active or pending player. An exit-0
player that ends too early is interrupted before acknowledgement. A missing,
failed, or uncertain chunk produces `interrupted`; runtime loss uses
`runtime_fence_lost`. The finalizer never promotes a queued or incomplete item
to `played`.
Validation or queue failures produce `rejected`. If an audio worker disappears
with an item claimed as `.playing`, recovery records `interrupted` and removes
it from the FIFO instead of risking replay. A retry after a terminal receipt
returns that receipt and never republishes the item.

A terminal receipt and owned-player stop completion are separate states.
`interrupt` may first persist an `interrupted` receipt while the playback
wrapper is still stopping its owned player group. The wrapper sets the durable
`player_stop_confirmed` ledger field only after it has stopped and waited for
that child. `quiescence ITEM_KEY` returns both the receipt and this stop
acknowledgement; it remains false until the acknowledgement is durable. A lost
interrupt response can be recovered by querying the same item key. Legacy
schema-2 interrupted receipts have no stop acknowledgement and remain
unconfirmed. Queue-file disappearance alone is not proof that a player exited.

Receipt JSON follows the docich weather-audio receipt schema, including the
whole-request digest, item key, exact runtime fence, forecast identity,
recorded time, and a limited reason code. Receipt storage is local metadata;
the digest binds a payload but is not a signature or proof of its source.

## Offline verification

`tests/test_weather_audio_consumer.py` uses temporary queue and GameSwitch
fixtures plus a dummy subprocess that only writes a sentinel file. It exercises
stable-key retries and conflicts, enqueue/play-start/runtime-loss fences,
bounded lock contention and owned-group termination, durable completion
receipts, interrupted recovery, and the actual prerendered shell path for
two-chunk success, second-chunk failure, and zero-exit early truncation without
calling TTS or emitting audio. A three-party regression holds the exclusive
GameSwitch lock while an enqueue or check contender owns the receipt ledger,
then verifies that a long-running owned player stops within the termination
bound before the exclusive lock is released.
A gated long-lived dummy player also proves that an `interrupted` receipt can
precede process termination, and that the separate quiescence acknowledgement
appears only after the consumer has stopped and waited for its owned player.
