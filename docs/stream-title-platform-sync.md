# Broadcast title synchronization

Game switches own the viewer-facing title body. The daily updater only refreshes
its day prefix; it must not replace that body with the first operational brief.

After a successful Twitch title update, or an unchanged desired title, the
updater passes only the composed title over stdin to `lib/stream_title_sync.py`.
The existing title lock stays held across the fan-out, preventing a slower
previous switch from overwriting a newer switch. Dry-run, show, category-only,
and failed Twitch updates do not mutate the other services.

- YouTube reuses the existing OAuth client/refresh configuration. An explicit
  existing `YOUTUBE_BROADCAST_STREAM_ID` is required. Only one currently live,
  owner-listed broadcast bound to that stream is eligible. Existing description,
  category, tags and language fields are preserved; no stream creation, binding,
  privacy change, encoder restart or credential generation occurs.
- Kick uses an existing `KICK_ACCESS_TOKEN` and `KICK_BROADCASTER_USER_ID` only.
  An authenticated-principal read must match that ID and be live. The PATCH
  contains only `stream_title`, followed by readback. No category is guessed.
- Missing setup produces `not_configured` / `stream_not_configured`; failures
  produce a fixed enum and never echo response bodies or tokens. This change
  does not grant OAuth scopes. Obtaining new credentials needs separate approval.
- Titles are normalized and bounded to 100 characters for cross-platform use.
  Platform failure is non-fatal to a successful Twitch update and game switching.

Official contracts checked 2026-10-01:
- https://developers.google.com/youtube/v3/docs/videos/update (50 quota units per
  update; snippet replacement requires preserving mutable fields)
- https://docs.kick.com/apis/channels (channel:read / channel:write)

Related older proposal: #451. Its global viewer-title generation is not included;
this slice preserves the currently displayed game title on daily refresh and
adds title-only fan-out. Raw private handoff text is never newly transmitted.

Live credentials, successful live delivery and production deployment are not
established by stub tests. No live operation was performed during development.


## Owner-only runtime journal

The helper appends fixed metadata to
`tmp/state/stream_title_sync/events.jsonl` inside a dedicated owner-only
directory. The journal has a 32 KiB cap. Records contain a UTC timestamp, the
Soren commit SHA, a fixed event name, fixed YouTube/Kick outcome enums, and an
allowlisted skip reason. They never contain the title, command arguments,
environment values, credentials, request/response bodies, or exception text.
Each record also contains `youtube_stream_id_present` and
`kick_broadcaster_id_present`: booleans for nonempty
`YOUTUBE_BROADCAST_STREAM_ID` and `KICK_BROADCASTER_USER_ID` in that helper
process's effective environment. Empty or missing IDs produce false;
whitespace is nonempty. No ID value or credential presence is recorded.
The 512-byte row limit and 32 KiB journal cap remain enforced.
The writer is best-effort and does not change the Twitch update result.

The read-only docich production diagnostics collector may project the newest
record when it is at most 15 minutes old and its Soren SHA matches the deployed
gitlink. `updated` means the API read-back matched the requested title; it does
not confirm what a public viewer sees. The journal itself and integrated
stream logs are not included in the diagnostic output.

The collector accepts both earlier journal schemas, with these two fields
unknown (`null`) when absent. Presence is projected only from a fresh, valid
record with matching reviewed source fingerprints; stale, malformed, future,
or unavailable/mismatched source records keep both fields unknown. It never
uses its own process environment to fill them in. Presence does not establish
validity of an ID, authorization, or successful title delivery.
