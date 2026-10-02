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
`say_enqueue.sh` routes a weather item’s owned player command through the
weather fence helper, which holds the existing GameSwitch shared lock while it
checks the complete identity and spawns that player. Expiry gates starting;
it does not terminate a player that already started.

The durable receipt stays `queued` until `say_enqueue.sh` returns after its
owned player subprocesses finish. Only a zero exit for the complete weather
item produces `played`; a player failure or uncertain completion produces
`interrupted`. Validation or queue failures produce `rejected`. If an audio
worker disappears with an item claimed as `.playing`, recovery records
`interrupted` and removes it from the FIFO instead of risking replay. A retry
after a terminal receipt returns that receipt and never republishes the item.

Receipt JSON follows the docich weather-audio receipt schema, including the
whole-request digest, item key, exact runtime fence, forecast identity,
recorded time, and a limited reason code. Receipt storage is local metadata;
the digest binds a payload but is not a signature or proof of its source.

## Offline verification

`tests/test_weather_audio_consumer.py` uses temporary queue and GameSwitch
fixtures plus a dummy subprocess that only writes a sentinel file. It exercises
stable-key retries and conflicts, enqueue and play-start fences, durable
completion receipts, and interrupted recovery without calling TTS or emitting
audio.
