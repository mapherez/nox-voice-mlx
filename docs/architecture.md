# Architecture and security boundaries

For installation, start with [Installation](getting-started.md). This page
describes the implementation and its trust boundaries.

The [multi-model engine contract](voice-engine-contract.md) records the agreed
integration of PersonaPlex and LFM3 in this same webapp. Stages 1–2 provide the
catalogue, versioned settings and LFM3 audio checks. PersonaPlex keeps its native
conversation. LFM3 can transcribe complete utterances and synthesize supplied
text; its executor, task store and conversation flow are still pending.

The production path is frontend → authenticated gateway → mutually authenticated
Mac controller → authenticated loopback worker. Google OAuth and Hugging Face
downloads are separate from inference. The original NVIDIA reference server and
React client are not installed, served or included in the webapp container.

## Browser boundary

The gateway validates Google OpenID Connect through Authlib (state, nonce,
signature, issuer and audience), then requires a verified configured email and a
subject identifier. Google tokens are not persisted. The browser receives an
opaque, HttpOnly/Secure/SameSite cookie; only its digest is stored in SQLite.
For HTTP loopback deployment only, cookies use distinct names without the
`__Host-` prefix or Secure flag; HttpOnly, SameSite, owner authentication, CSRF
and Origin validation remain enabled. HTTPS deployments retain Secure cookies.
HTTP webapp origins are accepted only for exact `localhost`, `127.0.0.1` or
`::1` hosts, and Compose binds the published port to `127.0.0.1` by default.
The controller URL always requires HTTPS and mutual TLS.
CSRF tokens remain in page memory, never URLs or localStorage. The only
localStorage value is the light/dark/system theme preference.

Local origins are generated from `NOX_VOICE_WEB_PORT` and remain enabled when
`NOX_VOICE_PUBLIC_URL` adds an HTTPS domain. Optional additional HTTPS LAN origins
are listed in `NOX_VOICE_ALLOWED_ORIGINS`. The request Host must match an explicit
configured authority, including its port. Forwarded headers do not select an
origin or redirect destination; a configured HTTPS authority remains HTTPS even
when a reverse proxy connects to the gateway over HTTP.

OAuth state, callbacks, cookie security and CSP follow the selected origin.
Unsafe API methods require that same Origin and the session's CSRF token.
WebSockets require a valid session bound to that exact origin. Sessions are
checked during streams and revoked on logout. Requests and audio frames are bounded. All static
scripts, styles and fonts are self-hosted; CSP blocks third-party script/API
connections. Access logging is disabled and application telemetry is disabled.

Each configured browser origin has its own authenticated session. Signing out
locally does not sign out the HTTPS domain.

The PersonaPlex audio worklet resamples microphone PCM continuously to 24 kHz mono, with
1920-sample float32 frames. Audio playback alone connects to the sphere's analyser;
the microphone has no analyser/visualization. Input uses a silent processing path
to avoid local echo. Browser queues are bounded and interruption cleanup stops
tracks, worklets, scheduled output and sockets, including permission grants that
arrive after cancellation.

The LFM3 client uses the additive `/voice/v1/ws` JSON protocol through the same
authenticated gateway and mTLS controller. Holding Space or the touch button
captures 16 kHz PCM16; release flushes the worklet's final partial packet before
committing the utterance. Transcription results are shown literally. A separate
text field requests audio-only 24 kHz TTS. These controls are explicitly labelled
as audio checks: no executor, MCP call or automatic assistant answer is involved.
Pressing Space during speech stops scheduled browser output immediately, cancels
the native operation and starts a new capture. Session, turn and playback IDs
reject stale results. Only synthesized output feeds the orb's analyser.

Capture permission is requested once per connection and the stream is reused
between turns. The browser controls whether permission persists across visits;
the application cannot force permanent permission. Permission/device failures
have actionable messages. Disconnect, backgrounding and logout release capture.

## Native boundary

The controller is exposed only at its configured private IP and uses server TLS
with a required client certificate. The gateway verifies the CA and host/IP and
presents its own certificate. Its URL is deployment-only, not a browser-controlled
SSRF destination. The CA signing key is retained on the Mac.

The controller owns one provider worker, using a fixed interpreter/module argument list,
without a shell. The worker binds to loopback and requires a fresh, per-process
random bearer credential. Inherited token/secret variables are removed. Start is
idempotent; Stop terminates the child, then forcibly kills it after a bounded wait
if necessary. Crashes do not automatically reload the model. Startup reports only
approved phase messages; raw child output and tracebacks are not forwarded.

For LFM3, that worker owns a separate pinned llama.cpp process on a dynamically
allocated loopback port, with its own random bearer key and an isolated cache
environment. The worker's parent watcher and signal handling close its native
child on Stop or controller loss. Only one ASR/TTS operation owns the model slot
at a time; each operation gets a short native session, while weights stay loaded.
No new public ports, deployment URLs or certificate inputs are introduced.

The one-conversation guard is acquired before any model cache or codec state is
reset. Codec state is reset at each session boundary; the voice-only cache is
restored under exclusive ownership. Voice/persona/precision settings cannot
mutate an active conversation. No upload, arbitrary path selection, shell, power
management or generic model-loading API exists.

## Model assets

Request/response fields and lifecycle semantics are documented in the
[model management API](model-management-api.md).

The configured `weights_dir` is the shared asset root. PersonaPlex uses
`personaplex/`; LFM3 uses `lfm3-audio/`. Preparation, verification, diagnostics
and worker bootstrap all use those provider paths. Selection/download/save do
not load inference. Load starts the selected provider; Eject releases the owned
worker and model memory. Active conversations prevent provider changes.

The controller owns bounded persistent download-job metadata and separate
preparation subprocesses. They receive credentials via stdin and emit bounded
structured progress. Their environment contains no inherited API keys. Closing
the browser or ejecting a model does not cancel another model's download;
controller shutdown terminates its workers and marks jobs interrupted. Explicit
retry resumes checksummed immutable artifacts using validated HTTP ranges.
Cross-host HTTPS redirects strip authorization headers. Files are verified before
installation. No inference or downloads resume automatically after restart.

Credentials use an extensible service registry and a separate private mode-600
file on the controller Mac. Status endpoints never return secrets; validation
errors never echo secret request fields. Hugging Face testing distinguishes token
identity, gated model access and network failure. The browser never stores a
saved token; gateway/worker logs do not contain tokens. The global download UI
and Credentials API retain the existing browser authentication/CSRF/mTLS boundary.

The folder action accepts a provider ID only. It opens Finder on the controller
Mac at the known provider directory; no arbitrary path or web filesystem browser
is exposed. A graphical login by the controller user is required.

PersonaPlex preparation is restricted to the official NVIDIA repository and an immutable
revision. Download hashes are checked against official file metadata. Voice
archives accept only expected filenames and reject paths, links and resource
abuse. Runtime verifies the stored SHA-256 manifest offline. Voice prompts are
loaded with `weights_only=True` on a patched PyTorch version and checked for
finite, bounded tensor dimensions. There is no fallback to unrestricted pickle.

This reduces the model-loading attack surface; model files and executable
dependencies still need trusted provenance and ongoing security updates.

LFM3 preparation is a separate explicit CLI/UI action, never a side effect of
reading settings, selecting a model or starting inference. Pinned public GGUF
weights and the pinned macOS ARM64 llama.cpp archive have expected sizes and
SHA-256 hashes. Archive validation rejects traversal, special entries, external
links and writes through links. An installation inventory and model hashes are
verified offline on each start; an installation lock prevents preparation while
those assets are running. Everything belongs under
`weights_dir/lfm3-audio/`, including `.cache/` and `.tmp/`. The importer
reads only known model files from an explicitly supplied experiment directory,
verifies and copies them, and downloads a fresh pinned runtime. The original
experiment, its runtime and its caches are not modified or migrated implicitly.

The native uv project pins `moshi-mlx` inference code to 0.3.0. Upstream pins
older MLX, Hub, SentencePiece and safetensors versions and also declares a
standalone aiohttp/sounddevice/sphn client stack. Native uv overrides keep the
parser/runtime dependencies current and omit that unused web/audio client stack;
the application imports only the model, transformer and sampling modules. Use
the supplied uv lockfile rather than installing the engine with raw pip. Native
compatibility checks cover imports/configuration and a small model forward pass;
the real 7B checkpoint still requires the runtime acceptance procedure.

## Persistence and transport

The Docker bootstrap reads only the five configured webapp secret inputs into a
private tmpfs directory, without modifying their host mounts. It then removes
supplementary groups, switches to UID/GID `10001`, validates the TLS files and
executes the gateway. The HTTP server has no effective Linux capabilities and
cannot regain privileges. Invalid local TLS inputs stop startup before the
healthcheck can succeed; TLS peer identity is still checked on connection.

Only sessions are stored in the Docker data volume. Native settings, credentials,
download metadata and certificates are private local files. Technical events
are held in a bounded in-memory buffer with fixed messages. No conversation
audio/transcript is recorded. Container platform logs are rotated.

Native settings use schema version 1, a revision and independent provider
profiles. Versioned writes reject stale revisions and each worker receives only
its own private settings. PersonaPlex audio and its `/ws` protocol remain
independent of LFM3, executor and MCP configuration.

Cloudflare Tunnel encrypts its public route and connector link but terminates
browser TLS. The remote route therefore trusts Cloudflare with the traffic.
The separate gateway-to-Mac hop remains certificate-verified. Public deployment
uses Google login even when the request originates inside the home network.

Cloudflare is optional. Localhost runs without a reverse proxy or tunnel. A
trusted HTTPS hostname can also resolve directly to a LAN reverse proxy without
any public route. The configured `NOX_VOICE_PUBLIC_URL` adds an optional browser
origin and does not replace localhost or require public exposure. Only enabling
the optional tunnel profile starts its connector. Google authentication still requires Internet
access to Google; inference and local audio transport do not use Cloudflare in
a localhost or LAN-only deployment.
