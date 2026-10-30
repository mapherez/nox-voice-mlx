# Validation

Installation steps are in [Installation](getting-started.md). This page describes
development checks and the acceptance work for a deployment. Test results from
one machine do not establish model throughput or browser compatibility on another.

## Automated checks

**Where:** repository root, with uv installed.

```sh
uv sync --locked
uv run --locked ruff check .
uv run --locked pytest -q
uv run --locked pip-audit --skip-editable --progress-spinner off
```

This environment installs the lightweight controller/gateway and test tools. It
does not install the full inference stack or download the model. Some integration
tests open temporary loopback listeners and use synthetic audio/certificates.

**Where:** `frontend/`, with the Node version from `.node-version` and the npm
version declared in `package.json`.

```sh
npm ci
npm run check
npm test
npm run build
npm audit --audit-level=high
```

The project-local Vite+ toolchain runs formatting, lint, React/hooks/accessibility
rules and full TypeScript checks through `npm run check`, followed by HTML
validation of the document shell. The build includes these checks before
bundling. React's TSX is checked by the compiler/linter and rendered-browser
tests; validating the small `index.html` alone does not validate the interface.
Use `npm run format` to apply formatting or `npm run format:check` to check it
alone. A global Vite+ installation is optional. `npm run dev` starts Vite+;
authenticated API traffic is still owned by the gateway, not a standalone static
dev server.

The suites cover authentication policy, session expiry/revocation, CSRF, Host and
Origin validation, concurrent local/domain access, callback origin binding,
mutual TLS transport, process lifecycle, model-asset validation and continuous
audio resampling. Provider and model mocks do not replace real deployment tests.
Dependency audits report known advisories at execution time, not a guarantee
that all code is free of vulnerabilities.

The [multi-model stages](voice-engine-contract.md#14-implementation-sequence-and-acceptance)
cover independent profiles, revision conflicts, PersonaPlex streaming, provider
asset roots, pinned downloads, resume/restart ranges and checksum failures,
credential redaction/private permissions/access tests, owned download cancellation
and restart recovery, native Finder actions, and explicit Load/Eject behaviour.
LFM3 checks cover the real mTLS transport with a fixture worker, ASR/TTS protocol,
cancellation, capture flush ordering and rejection of late audio/turn events.

For optional browser verification of the main Settings workspace, build the frontend
and provide the module path of a locally installed Playwright package:

```text
uv run --locked python tests/browser/providers_host.py PLAYWRIGHT_MODULE
```

Replace `PLAYWRIGHT_MODULE` with its `playwright/index.mjs` path. This harness uses
a disposable Chrome profile under `.tmp/`, its own synthetic preview server and
no real controller, microphone, model, credentials or Google account. It verifies
Settings tab navigation, save/conflict/reload, credential save/test/remove,
Download Manager activity/cancel/resume, native-folder API feedback, explicit
Load/Eject, mobile layout and LFM3 keyboard/touch capture/interruption with a
fake microphone. Screenshots cover desktop and mobile. It does not establish
Safari or physical mobile-device compatibility.

Add `--dev` to the same harness command to exercise the real Vite+ development
server, TSX transformation, React Fast Refresh setup and StrictMode lifecycle.
The harness owns that temporary server and forwards only its browser API
requests to the synthetic fixture. Production mode serves the compiled assets.

### Stage 2.2 automated migration validation

The React/TypeScript and Vite+ migration is implemented. Automated validation
on October 30, 2026 observed:

- A clean `npm ci`, Vite+ checks with warnings denied, HTML validation, production
  build and 70 frontend tests passed. Existing audio/protocol assertions remain;
  new tests cover React lifecycle, stale requests/session resets, settings
  drafts and download-history races. `npm audit --audit-level=high` reported no
  known vulnerabilities at that execution.
- The full Python suite passed 183 tests with the ARM64 container test enabled;
  Ruff passed. The AMD64 container test passed separately.
- Both `linux/arm64` and `linux/amd64` images built and served the React shell,
  JavaScript and CSS through the actual gateway. Private fixture secret mounts
  and the unprivileged runtime checks passed. No model was loaded by these tests.
- A combined amd64/arm64 build with the frontend cache disabled also passed
  after pinning the frontend stage to `BUILDPLATFORM`. Checks and bundling ran
  once on the native builder, while both runtime architectures retained their
  startup/security checks and identical frontend asset hashes. Publication in
  GitHub Actions still requires a successful CI run with this Dockerfile change.
- Chrome regression QA passed for the compiled frontend and the Vite+
  development server. Representative Settings/Downloads light-theme screenshots
  matched the recorded baseline pixel for pixel in the compared captures.
  This does not establish all browser/device/animation states.

The build still reports the large-chunk notice for the Three.js/React application
bundle (about 823 kB, 221 kB gzipped). Static checks have no lint/type warnings;
this bundle-size notice is a separate build diagnostic, not a performance
measurement.

The user confirmed real deployment parity after the migration, covering the
established Mac/audio/Finder/Load/Eject and mobile checks. Clear was traced to
the running controller copy lacking its DELETE route and clearing function.
An isolated reproduction returned controller 405 and gateway 502 with the exact
reported localhost error. Updating the native controller resolved it; the user
confirmed successful Clear operation. The intermediary replacing the public
502 body with HTML was not separately identified. Stage 2.2 is accepted.

### Stage 3 executor validation

Automated validation on October 30, 2026 observed:

- 201 Python tests passed; the opt-in container test was skipped in that suite
  and passed separately for both ARM64 and AMD64. Ruff passed and the locked
  Python dependency audit reported no known vulnerabilities.
- 74 frontend tests passed, with Vite+ format/lint/type checks, HTML validation
  and production bundling. The existing large-bundle notice remains.
- Generic executor fixtures cover ordinary answers without MCP, dependent
  search/action bindings, 15 tool rounds without a fixed ceiling, invalid
  schemas/tools/batches before dispatch, deduplication, stale/ambiguous
  references, unsupported required descriptors, partial outcomes, cancellation
  and deadlines with unknown admitted effects, and explicit resource limits.
- A real loopback HTTP/WebSocket/mTLS test links synthetic LFM3 audio through
  the native controller to disposable LLM/MCP endpoints. It verifies the original
  transcript, two dependent calls using actual IDs/revisions, saved bearer auth,
  secret redaction in model/browser data, owner-only task detail, ordered events
  and supplied-text TTS. No actual product effect or real model inference occurs.
- MCP tests exercise JSON and SSE responses, initialization, session/protocol
  headers and paginated catalogues. Discovery performs no business tool call.
- Compiled and Vite+ development Chrome QA passed for existing controls and the
  new executor profiles, discovery, foreground response, activity/result detail
  and mobile layout. New state tests cover late profile responses, draft
  preservation, duplicate speech prevention and no interruption by stale results.
- The current two-platform Docker build and both startup/security checks passed.
  No image was published and no running installation was changed by these tests.

Stage 3 still needs actual user acceptance with the selected LM Studio model,
direct MCP servers and voice models. Fixture LLM decisions do not establish a
real model's tool selection, compound-request fidelity, confirmation behavior,
language quality or latency. Test ordinary questions, a read-only search, then
an explicitly requested disposable compound action and its actual receipts.
Check cancellation/end/Stop and PersonaPlex independently. Notification queue
policies remain stage 4 and NoX Suite negotiation/acceptance remains stage 6.

### Reported deployment validation

On October 30, 2026 the user confirmed real installation checks: model downloads
and loading, microphone transcription, LFM3 reading supplied text, speech
interruption, Finder opening the model folder on the controller Mac, memory
allocation/release on Load/Eject and controller restart, independent saved
profiles, downloads continuing while their window is closed, and mobile audio
and interruption. Device/browser versions and numerical latency were not recorded.

The user also confirmed that another device sees the active-conversation notice
while a phone owns the conversation, validating the visible single-session guard.
Phone/tablet capture and interruption instructions use touch wording; desktop
instructions can also mention Space. Responsive QA covers both input layouts.

The report identified a keyboard-focus regression: Space on a focused Read aloud
button restarted speech. Browser regression QA now exercises that exact focus,
repeated keydown and keyup, preserving Space typing and modal keyboard controls.
The requested Clear downloads action is tested for retaining model files and
active work, persisting cleared history, and keeping its global indicator accurate.

PersonaPlex voice conversation and interruption by speaking were confirmed. The
user also observed that it could continue its previous answer after a spoken
"stop". A spoken phrase is model input, not a deterministic engine stop command;
this observation is retained without claiming its cause has been diagnosed.

### Opt-in live LFM3 check

From the repository root, explicitly supply the existing experiment directory:

```text
uv run --locked python tests/mac/lfm3_live.py --experiment EXPERIMENT_DIRECTORY
uv run --locked python tests/mac/lfm3_live.py --experiment EXPERIMENT_DIRECTORY --language Portuguese
```

This creates a disposable installation under `.tmp/`, verifies the two source
weights, hard-links them read-only by convention, and copies the experiment's
pinned runtime for the test. It never modifies source model files and removes
the fixture afterwards. Production imports copy verified weights and download
the pinned runtime instead. The test owns and stops its worker/native child;
it does not connect to an existing controller, microphone, MCP app or LLM service.

Observed on October 29, 2026: the real worker became ready in about 6.8 seconds,
the first TTS packet arrived in about 0.07 seconds, and the model transcribed the
complete synthetic instruction “Find all notes with the title Hello World and
delete the results” in two separate turns, including after interrupting synthesis.
Each ASR test took about 1.7 seconds including accelerated packet delivery. Both
owned processes exited on Stop and their temporary installation was removed.
The Portuguese variant also transcribed “Lista todos os recursos e apaga os
resultados” completely in two turns (about 1.5 seconds each), including after
interruption. These are smoke-test observations using synthesized speech, not
microphone quality, end-user latency or PT-PT synthesis acceptance measurements.
No tools were executed. Desktop Chrome browser QA also passed with a fake
microphone and synthetic controller responses; physical iPhone/Safari testing
remains part of deployment acceptance.

Run the checks above for the current results. Docker startup/UID/secret-permission
integration is optional locally and runs in CI when a test image is selected.
No model downloads or real Hugging Face credentials are needed for the automated
suite. Model metadata can be checked read-only against Hugging Face; full gated
PersonaPlex download acceptance requires an authorized user token. Native Finder
launch is mocked in tests to avoid opening windows on the developer's desktop.

CI also builds the Linux Docker image and starts it with mode `600` secret files
owned by a different host UID. It checks that the service becomes healthy, runs
as UID/GID `10001` with no effective capabilities or supplementary groups, and
leaves the host files unchanged. This check requires Docker and is skipped in
the default local suite unless `NOX_VOICE_CONTAINER_TEST_IMAGE` selects a built
test image.

## Native compatibility smoke test

**Where:** repository root on an Apple Silicon Mac.

```sh
uv run --project mac --locked --extra engine python tests/mac/native_smoke.py
```

This loads a small test model, exercises full/4/5/6/8-bit precision, cache
restoration and restricted voice loading. It rejects an unsafe pickle payload.
It does not load the full PersonaPlex checkpoint or codec, so it does not measure
real conversation quality or performance.

The inherited full-model comparison diagnostic is excluded from the default
suite. See [port history](original-port.md) for its context.

## Deployment acceptance

Use the actual model, identity provider configuration and target devices:

| Area             | Verify                                                                                                       |
| ---------------- | ------------------------------------------------------------------------------------------------------------ |
| Access           | Localhost remains usable with and without the optional external route                                        |
| Multiple origins | Local and configured HTTPS logins return to their starting address; both can remain signed in simultaneously |
| Authentication   | The configured verified account succeeds and a different account is rejected                                 |
| Transport        | A valid client bundle connects; missing, incorrect or expired certificates fail                              |
| Lifecycle        | Repeated Start creates one worker; Stop cancels loading and releases model memory                            |
| Isolation        | A second conversation cannot reset or modify the active conversation                                         |
| Settings         | Voice, persona and precision changes work while idle; active conversations block changes                     |
| Downloads        | Both models download with progress, cancel/resume and verification; errors identify credentials, terms, network or disk space |
| Model memory     | Download/save never load inference; Load/Eject manage only owned workers and preserve files |
| Credentials      | Save/test/replace/remove work; reads, validation errors and logs never expose the secret |
| Native folders   | Open folder launches Finder on the controller Mac; missing graphical login has actionable feedback |
| Audio            | Capture, mute, interruption and playback work on the intended desktop and mobile browsers                    |
| Visualization    | Only assistant playback drives the sphere; microphone input does not                                         |
| Accessibility    | Drawer keyboard/focus, themes, narrow layouts, reduced motion and WebGL fallback work                        |
| Recovery         | Disconnects, backgrounding, logout and device locking stop capture and require an explicit new start         |
| Network          | Long calls keep bounded queues and acceptable latency on each enabled route                                  |
| Privacy          | Persistent files and logs contain no conversation audio, transcripts or credentials                          |

Microphone access requires a secure browser context: localhost or trusted HTTPS.
Measure quality in the languages intended for deployment. The official model
supports English; other languages require evaluation rather than an assumed
quality guarantee.

If an external tunnel is enabled, repeat long-call and recovery checks through
that route. Local-only deployments do not require Cloudflare acceptance checks.
