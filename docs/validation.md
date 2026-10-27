# Validation and runtime acceptance

## Checks completed during implementation

The local implementation checks passed with Python 3.12 and Node 24:

| Check | Result |
| --- | --- |
| Python suite | 38 tests passed |
| Browser audio resampling suite | 4 tests passed, including 24/44.1/48 kHz input and mute |
| Native MLX smoke test | Small model warmup at full/4/5/6/8-bit precision, cache restore and unsafe pickle rejection passed |
| Frontend production build | TypeScript and Vite passed |
| Dependency audits | No known vulnerabilities reported by npm audit or pip-audit for the installed gateway/controller and native engine environments |
| Formatting and static checks | Ruff, Prettier and Git whitespace checks passed |
| Lockfiles | Root, gateway and Mac lockfiles verified offline |
| Real transport integration | PCM roundtrip through gateway, mutual TLS controller and authenticated echo worker passed |
| Visual review | Compiled frontend reviewed in Chrome at desktop and emulated 375 px iPhone width, including light/dark themes and the Settings drawer |

The echo worker and browser preview use synthetic data. They do not establish
real model performance or browser microphone compatibility. The production build
currently reports a non-failing chunk-size warning for the Three.js bundle
(approximately 542 kB minified, 137 kB compressed).

Docker was not available in the development environment, so the image has not
been built locally or published. Actual Google login, the full PersonaPlex model,
Cloudflare routing and physical iPhone audio remain deployment acceptance work.

## Deployment acceptance

Automated checks cover authentication policy, CSRF/origin, session revocation,
asset traversal/tampering, bounded requests, process lifecycle, mutual TLS and
streaming PCM resampling. They do not claim that a 7B model has been loaded merely
because the mocked lifecycle tests pass.

Before considering a deployment ready, validate with the actual model, Google
OAuth project, Docker host and Cloudflare route:

- Sign in only with the configured verified account; reject another account.
- Verify valid client certificates connect and absent/wrong/expired certificates
  fail. Keep verification enabled on both sides.
- Start twice and confirm only one model process exists. Cancel while loading.
- Confirm startup phases, all 18 voices and 4/5/6/8-bit/full precision settings.
- Stop during a conversation and verify the process exits and its memory is
  released. Settings must still load and Restart must work.
- Open two devices and confirm the second cannot change/reset an active session.
- Test the microphone, mute, interruption and playback on Chrome on the MacBook
  and Safari/Chrome on the iPhone. Permission requires a user gesture and HTTPS.
- Confirm the sphere reacts to assistant playback only, including when the
  microphone is muted. It must not animate in response to microphone input alone.
- Test light/dark/system themes, drawer focus/keyboard, 320 px and iPhone widths,
  reduced motion, and the 2D fallback if WebGL cannot initialize.
- Run a long call through Cloudflare, observing latency/real-time factor and
  ensuring browser queues do not grow indefinitely. Disconnect Wi-Fi, background
  the page, sign out and lock the phone; verify capture ends and requires a new
  explicit start.
- Listen to English and Portuguese samples. Document Portuguese limitations
  rather than promising multilingual quality from an English model.
- Confirm no audio or transcripts appear in persistent files, logs or external
  network requests. Persona settings are intentionally persisted on the Mac.

The visual frontend is a browser workload. Model throughput remains a native Mac
workload; performance must be measured on the target hardware and network.
