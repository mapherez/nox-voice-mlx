# Model management API

This is the native controller API, relayed at the same paths by the authenticated
web gateway. Browser mutations require the current Origin and CSRF token. The
controller uses mTLS; the browser never connects to Hugging Face or runtimes.
Provider IDs are `personaplex` and `lfm3-audio`. Payloads do not accept URLs,
filesystem paths or executable names. Credential services come from the native
registry; currently `huggingface` is supported.

## Models and memory

`GET /api/voice/v1/models` returns `{models: [...]}`. Each entry includes:

| Field | Meaning |
| --- | --- |
| `id`, `name` | Stable provider ID and display name |
| `source`, `runtime`, `revision` | Official model source, runtime and immutable model revision |
| `directory` | Configured provider folder on the controller Mac |
| `downloadBytes`, `sizeApproximate` | Required download size and whether it is an estimate |
| `credentialService` | Required service ID or `null` for public downloads |
| `localState` | `not_downloaded`, `present` or `verified` |
| `loaded` | Whether the model is currently ready in memory |
| `missingCount` | Missing required installation entries |

Presence does not prove integrity. Verification is cached against local file
metadata for completed downloads and repeated in full on Load. Models do not
load from GET, download or settings writes.

`POST /api/voice/v1/engine/load` loads the saved selected provider. To load another
provider, save the versioned settings selection first. Saving a changed provider
while idle ejects the outgoing worker and stays stopped. An active conversation
blocks switching/loading with 409. `POST /api/voice/v1/engine/eject` stops the owned
inference worker and frees its memory, keeping files and controller available.
Eject does not cancel separate downloads.

`POST /api/voice/v1/providers/{id}/open-folder` takes no path or body. It opens
Finder at that model's directory on the controller Mac. A matching graphical
login is required; 409 provides a remedy if Finder is unavailable. No file list
or filesystem browser is served.

## Downloads

- `GET /api/voice/v1/downloads` returns `{jobs: [...]}`, oldest first.
- `DELETE /api/voice/v1/downloads` clears terminal history entries and returns the
  remaining `{jobs: [...]}`. It persists the cleared list and keeps active jobs
  and workers still shutting down. It never deletes assets/partials or ejects a model.
- `POST /api/voice/v1/downloads` accepts exactly `{"provider":"personaplex"}` or
  `{"provider":"lfm3-audio"}` and returns the admitted job with HTTP 202.
- `POST /api/voice/v1/downloads/{id}/cancel` requests cancellation of its owned worker.
- `POST /api/voice/v1/downloads/{id}/retry` creates a new job for the same provider,
  reusing already verified files and safe partial downloads.

A job contains `id` (24 lowercase hex characters), `provider`, `name`, `createdAt`
(Unix seconds), `state`, `downloadedBytes`, `totalBytes`, `bytesPerSecond`, `files`,
`error` and `code`. Before source resolution `totalBytes` can be zero. Each file
has `name`, `totalBytes`, `downloadedBytes` and `state`. Clients must display an
unknown total as indeterminate rather than invent a percentage.

States are `queued`, `resolving`, `downloading`, `verifying`, `extracting`,
`cancelling`, `completed`, `cancelled`, `failed` and `interrupted`. Bytes reaching
100% do not mean preparation/verification is complete. Completed jobs mean files
are ready on disk; they never imply a loaded model.

One job per provider can be active; repeated admission returns that job. The
controller retains up to 20 recent jobs privately, evicting terminal jobs first.
A loaded model cannot be repaired/downloaded until Eject. The other provider can
download independently of a conversation. Closing a browser does not cancel jobs.
Controller shutdown terminates owned download workers and preserves partials;
startup marks unfinished jobs interrupted and never resumes them automatically.

Partial files have checksum/size identity sidecars. A mismatch restarts that
file; a valid HTTP range continues at the saved byte offset. A source returning
HTTP 200 instead of 206 restarts that file. A bad range or checksum fails visibly;
unverified bytes are never installed. The worker receives any token through stdin,
not command arguments or inherited environment. HTTPS cross-host redirects strip
authorization. Public LFM3/GitHub downloads never receive the Hugging Face token.

## Credentials

`GET /api/voice/v1/credentials` returns `{services: [...]}` with service metadata,
`configured`, `testSupported`, `test` and `testedAt`. No token, masked placeholder
or token suffix is returned.

`PUT /api/voice/v1/credentials/{service}` accepts exactly `{"secret":"TOKEN"}`.
It explicitly replaces that credential and clears the previous test result.
`DELETE` removes the local credential; it does not revoke it at the external
service. Both return the same status-only service description.

`POST /api/voice/v1/credentials/{service}/test` tests the saved credential without
starting any model download. Its `{state,message}` result distinguishes
`not_configured`, `invalid`, `access_required`, `network_error`, `valid` and
`changed` (a concurrent replacement invalidated the test). Hugging Face testing
checks identity and PersonaPlex file access separately. Unsupported service test
adapters return `unsupported`; their UI control is hidden.

Secrets live in `credentials.json` under private controller storage, mode 600,
with atomic replacement. Requests/errors/status/logs never expose secret fields.
The service registry supplies metadata, validation and test adapters; additional
services do not require a separate Credentials page or a different secret store.

## Errors

Responses use `{error: "user-facing message"}`. 404 means an unknown identity or
action, 409 means a busy model/job or unavailable Finder session, 422 means invalid
input or a missing required credential, and 503 means unavailable controller or
provider. Worker failures appear in the job, with codes such as `MODEL_ACCESS`,
`RATE_LIMIT`, `NETWORK`, `DISK_SPACE`, `INTEGRITY`, `RANGE` or `PREPARATION`.
Retries are explicit. Validation errors never echo credential request bodies.
