# Muse free retry diagnosis (2026-09-08 JST)

The Soren log reported 300–360 second timeouts. A bounded CLI probe with
`--print-logs` instead exposed `AI_APICallError: Rate limit exceeded` within the
CLI. OpenCode waits internally for Retry-After and does not emit that error to
normal stderr, so the shell previously classified it as a transient timeout and
repeated the long wait.

Direct requests from the same VM to the documented Zen Responses endpoint for
both `muse-spark-1.3-contributor-free` and `muse-spark-1.2-contributor-free`
returned HTTP 429, `FreeUsageLimitError`, and Retry-After. The public credential
used by anonymous OpenCode and the existing account credential both returned the
same daily limit. No authentication or provider configuration was changed.
A public request at 2026-09-07 19:45:28 UTC returned Retry-After 15273 seconds,
consistent with UTC midnight (09:00 JST), not 24 hours from the failed request.

The upstream [IP limiter](https://github.com/anomalyco/opencode/blob/dev/packages/console/app/src/routes/zen/util/ipRateLimiter.ts)
uses UTC date buckets and [the handler](https://github.com/anomalyco/opencode/blob/dev/packages/console/app/src/routes/zen/util/handler.ts)
selects this before authentication for anonymous-enabled models. Authentication
alone does not prove a separate free quota. The exact limit and this VM's usage
counter are not exposed by these observations; neither successful recovery nor
why each day's available requests are exhausted has been established.

## Change

- Keep the same CLI, model IDs, authentication and prompts. For Muse 1.2/1.3
  free only, observe CLI error logs and return the existing rate-limit status 79
  immediately; terminate only the CLI's owned process group.
- Suppress debug logs instead of putting prompt/credential metadata into Soren
  logs. Preserve model stdout, ordinary CLI errors, and genuine timeouts.
- Cap free-model rate-limit backoff at UTC midnight. Preserve shorter explicit
  limits, paid Go limits and other providers. This avoids missing the next daily
  allowance because a late-day failure parked the model for another 24 hours.
- Use the guard in the recovery probe, require the requested answer, and notify
  only the failed-to-successful transition (previously `ok <timestamp>` was
  compared to the literal `ok`).

## Validation and limits

Regression tests cover hidden retries, success, ordinary errors, true timeout,
answer text containing rate-limit words, real shell dispatch, daily expiry,
shorter configured limits, paid limits, and repeated recovery probes. VM staging
with the real CLI returned 79 on the actual rate limit rather than timing out.

The upstream is still rejecting free requests at validation time. This change
repairs detection/wait/retry behavior; it does not reset or bypass provider
quotas. A successful free-model answer after the upstream reset remains to be
observed. No paid fallback is added, and game/stream restart is not required.
