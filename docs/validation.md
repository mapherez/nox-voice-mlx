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

**Where:** `frontend/`, with Node 24 and npm installed.

```sh
npm ci
npm run format:check
npm test
npm run build
npm audit --audit-level=high
```

The suites cover authentication policy, session expiry/revocation, CSRF, Host and
Origin validation, concurrent local/domain access, callback origin binding,
mutual TLS transport, process lifecycle, model-asset validation and continuous
audio resampling. Provider and model mocks do not replace real deployment tests.
Dependency audits report known advisories at execution time, not a guarantee
that all code is free of vulnerabilities.

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
