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
