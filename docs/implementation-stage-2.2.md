# Stage 2.2 — React, TypeScript and Vite+ implementation plan

**Status:** complete. Automated acceptance passed and the user confirmed real
deployment parity on October 30, 2026, including Clear downloads after updating
the running native controller.

The user approved this plan and selected React, TypeScript and Vite+.
This document expands [stage 2.2 of the engine
contract](voice-engine-contract.md#stage-22--react-typescript-and-vite). Stages
3–6 retain their scope and order; stage 3 follows completion and acceptance of 2.2.

## Outcome and scope

Migrate the existing frontend to modular React components and the Vite+
toolchain, preserving its current functionality, appearance, settings and
deployment behavior. This is an internal frontend refactor, with corresponding
build, test and documentation updates.

Keep the current screens, four Settings tabs, controls, SVG icons, responsive
layouts, themes and interaction wording. Preserve both providers and their
distinct audio capabilities. LFM3 remains audio checks at this point; the
executor and automatic answers belong to stage 3.

The gateway/controller API, authentication policy, audio protocols, model files,
settings schema and private credential storage remain the application boundary.
No new backend service, public route, port, recording, persistent conversation
history or product feature is part of this work. The other NoX repositories,
the isolated experiment and the vendor subtree are outside this migration.

## Migration baseline and known issue

The baseline used TypeScript, Vite 8 and Vitest 5, with static markup, global
state and imperative DOM updates concentrated in `frontend/index.html`,
`src/main.ts` and `src/model-manager.ts`. The implemented frontend replaces those
renderers with the React structure below. Audio, worklets, PCM utilities,
provider helpers and the Three.js orb retain their separate implementations.

The deployed Clear downloads failure was traced to the running controller copy
missing the DELETE route and history-clearing implementation. The gateway hid
the resulting 405 behind a generic 502. Updating the native controller resolved
the failure; the user confirmed successful real operation. React migration was
not the fix for that deployment mismatch.

## Target structure and ownership

Use one React root. `index.html` becomes the document shell; `main.tsx` mounts
the application. `App.tsx` composes features rather than containing their
implementation. The following layout describes responsibilities; filenames can
follow the final boundaries discovered during implementation:

```text
frontend/src/
  main.tsx
  app/                  App, providers and application lifecycle
  features/
    session/            Session/status types; lifecycle coordinated by app/
    conversation/       Conversation UI, audio checks and input controls
    settings/           Settings dialog, tabs and provider-specific forms
    models/             Catalogue, local/loaded state and Load/Eject actions
    downloads/          Download trigger, manager and job presentation
    credentials/        Service credential forms and actions
  components/ui/        Shared buttons, icons, fields and dialog primitives
  services/             Typed HTTP client and controller-facing operations
  audio.ts              Existing PersonaPlex browser engine
  lfm3-audio.ts          Existing LFM3 browser engine
  ...                   Existing worklets, DSP utilities, orb and styles
```

React owns component markup and UI state. Service modules do not search for DOM
IDs or render controls. Components receive typed data/actions. Native browser
resources use explicit refs, subscriptions and cleanup. Preserve the existing
CSS and visual design while assigning styles to understandable component areas.

Use React hooks/reducers and narrowly scoped contexts for shared state. Keep
server snapshots separate from unsaved form drafts and transient operation
state. Backend responses remain authoritative for model loading and downloads.
Avoid introducing a router, state-management framework or replacement component
library for the existing single-page interface.

## Implementation sequence

Each item is part of stage 2.2, not a replacement for an original engine stage.

1. [x] **Record the baseline.** Run the existing tests/build and capture representative
   desktop/mobile screenshots and interaction checks. Record existing failures
   separately. Map controls, keyboard handlers, polling, session cleanup and
   async race guards before moving them.
2. [x] **Adopt Vite+ and add React support.** Select compatible stable releases and
   pin dependencies/toolchain versions in the project lockfile. Keep npm and its
   lockfile. Configure TSX, React support, Vite+ checks/tests/build and explicit
   type checking. Validate this tooling change against the existing app before
   replacing its interface.
3. [x] **Separate state and services.** Extract typed requests, session/controller
   state, operation state and settings drafts from DOM manipulation. Preserve
   CSRF, credentials handling, request invalidation and revision conflicts.
   Adapt the audio/orb lifecycle interfaces only where component ownership needs
   explicit subscription or disposal.
4. [x] **Migrate the interface by feature.** Move the shell/theme/session UI,
   Settings and its four tabs, model management, Downloads and Credentials,
   then Conversation/audio checks into focused React components. Run the
   relevant regression checks after each area. Keep existing behavior and CSS
   through the transition. The final interface has one React owner per region;
   remove the superseded static markup, manual renderers and event wiring.
5. [x] **Complete build and delivery integration.** Update the frontend Docker build
   stage, CI, development commands and third-party notices. Keep the final
   static output at `frontend/dist` for the Python gateway. Validate clean
   installs and the supported Linux image platforms.
6. [x] **Verify parity and hand over for deployment validation.** Run the acceptance
   checks below, compare the baseline, update architecture/validation/setup
   documentation, and report results and limitations. Mark 2.2 complete only
   after its acceptance is satisfied; then resume stage 3.

Automated checks, handover and user-reported real deployment acceptance are
complete. Observed test/build
results and limits are recorded in [validation](validation.md#stage-22-automated-migration-validation).
The project pins React/React DOM 19.3.1, Vite+ 1.4.0, Node 24.22.1 and npm
11.19.1. Its bundled Vite/Vitest versions remain 8.3.5/5.0.5.

## Vite+ decisions

- Use the project-local `vite-plus` package, with its compatible bundled tool
  versions. A global developer installation is optional. Keep all setup scoped
  to `frontend/` and the relevant repository build/CI files.
- Consolidate development, checking, testing and bundling around `vp dev`,
  `vp check`, `vp test` and `vp build`, exposed through documented project
  scripts. Enable both lint type awareness and full type checking explicitly.
- Move formatting to Vite+'s formatter after checking its support/configuration
  against the current readable HTML/TS/TSX formatting. Retire redundant tools
  only when equivalent checks run. Retain HTML validation for the document
  shell; add applicable React/hooks/accessibility checks and browser verification
  for the rendered interface. Static shell validation alone cannot validate JSX.
- Preserve test assertions and test isolation while aligning runner imports and
  dependency versions. Existing audio/worklet tests remain in the suite.
- If using `vp migrate`, review its generated diff and disable automatic Git
  hooks, agent files and editor setup. No global Node/package-manager changes
  are required for this repository migration.
- Use a pinned, supported glibc frontend builder, targeting the existing
  `linux/amd64` and `linux/arm64` images. The builder uses glibc and its native
  `BUILDPLATFORM`, so frontend checks do not require target-platform emulation.
  The deployed Python runtime still receives only the static frontend output.
- Update the CI's frontend install/cache/check/test/build steps and preserve its
  Python/security/container checks. Keep dependency installation reproducible.
  Compose service topology, secrets, volumes and runtime ports stay the same.

## Lifecycle requirements

Audio engines must survive ordinary React renders, Settings tab changes and
dialog opening/closing. Microphone access and AudioContext startup remain tied
to explicit user gestures. React mounting, effect replay, a state update or
opening Settings must never start inference, start a conversation, download a
model or repeat a write.

Clean up listeners, subscriptions, intervals, requests, worklets, sockets,
animation frames, observers and WebGL resources at their owning lifecycle
boundary. Exercise React development effect setup/cleanup behavior explicitly.
Retain the current stop/release behavior for end, logout, session expiry,
page backgrounding and page exit, including a late microphone permission grant.

Keep high-frequency audio analysis and orb rendering outside application-wide
React state updates. Only assistant playback drives the orb. Late audio,
transcript, polling and credential responses cannot update a replaced session.

Closing/unmounting a download view never cancels controller-owned downloads.
Settings polling never overwrites a user's unsaved draft or resets focus/caret.
Modal focus restoration and keyboard handling remain deliberate and testable.

## Acceptance checklist

| Area | Required evidence |
| --- | --- |
| Toolchain | Clean locked install; Vite+ format/lint/type checks, tests and production build pass; emitted worklet/font/license assets resolve |
| Authentication | Existing Google entry/callback, session expiry, logout, local/HTTPS origins, CSRF and controller-offline behavior are preserved |
| Conversation | PersonaPlex start/end/mute and full-duplex behavior; LFM3 capture, literal transcript, supplied-text speech and interruption remain available |
| Input and cleanup | Space while Read aloud has focus, key repeat/release, text-field typing, modal keyboard controls, touch/pointer release/cancel, late permission grants and resource release retain their behavior |
| Lifecycle replay | Development effect replay, repeated renders and opening/closing views do not duplicate sockets, microphone streams, listeners, timers or unsafe requests |
| Settings | Four tabs, model-specific controls, inactive profiles, draft preservation, save/conflict/reload, connection diagnostics, theme and account controls behave as before |
| Models and downloads | Download/local/loaded states stay distinct; Load/Eject, Finder, progress, cancel/retry/resume, terminal-only Clear and global indicator behave as before; files and active work survive Clear |
| Credentials | Save/test/replace/remove and feedback remain; no saved secret is returned or persisted in browser storage/logs |
| Visual and accessibility | Compare representative desktop, phone and tablet layouts in light/dark themes; retain labels, SVGs, focus, tab/dialog navigation and touch wording |
| Delivery | Build/run the actual container, check gateway-served assets and existing startup/security checks; verify both image targets without claiming an unrun platform passed |
| Real installation | Repeat the user's established Mac/browser checks, including actual audio, Finder, model memory and another-device exclusivity; identify the Clear response if it still fails |

Preserve and adapt the existing unit and browser regression suites. Add targeted
tests for new component ownership, async draft/session races and lifecycle
cleanup; do not substitute snapshots or reduced assertions for behavior checks.
Use disposable fixtures for automated checks. Real model/device checks require
the actual installation and are reported separately from synthetic results.

## Completion and review

Review the finished frontend for feature boundaries, remaining imperative DOM
ownership, unnecessary dependencies and accidental UI/behavior changes. Update
the main contract's status with observed acceptance evidence. An unresolved
deployment result stays explicitly unresolved rather than being hidden by the
refactor or described as verified parity.

This stage should leave a clear structure and reproducible toolchain that can
inform later product migrations. Creating a shared cross-product package,
monorepo or design system is a separate scope decision.

## References

- [React integration into an existing project](https://react.dev/learn/add-react-to-an-existing-project)
- [React effects and cleanup](https://react.dev/learn/synchronizing-with-effects)
- [Vite+ migration](https://viteplus.dev/guide/migrate)
- [Vite+ checks](https://viteplus.dev/guide/check)
- [Vite+ CI](https://viteplus.dev/guide/ci)
- [Vite+ Docker builds](https://viteplus.dev/guide/docker)
- [Vite+ platform support](https://viteplus.dev/guide/)
