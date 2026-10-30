# NoX Voice — multi-model engine contract

Decision baseline: **October 29, 2026**. Design revision: **4**.
Asset layout amendment: **October 30, 2026**.

Status: **agreed implementation target, partially implemented; see section 14**.
This document records the requested evolution. It does not authorize changing a
running installation, another repository, credentials, or publication settings.
Implementation in this repository requires its own instruction; publication of
this document alone does not implement the target or release an API.

## 1. Purpose and authority

This is the single reference for integrating PersonaPlex and LFM3 into the main
NoX Voice application. Implementation work must follow it and record an explicit
decision here before changing a behavior described as agreed.

- **Existing** means observable in this repository or reported by its user.
- **Agreed** means required by this contract, even when not implemented.
- **Conditional** means available only when a provider implements and validates
  the stated capability. It must not appear as a working setting before then.

[Architecture](architecture.md) describes the current production application.
[The experiment](../experiments/lfm3-audio/README.md) describes the current LFM3
demonstration. Their setup instructions do not implement the changes below.

## 2. Decisions

1. Keep the **existing main webapp** as the interface for both models, including
   its Settings workspace, authentication, themes, mobile support and assistant orb.
   The LFM3 experiment's separate page is not the final interface.
2. Preserve **PersonaPlex conversation as it works now**. Selecting it must not
   require an executor LLM, MCP connection, transcription stage or Suite service.
3. Add **LFM3-Audio-2B** through a separate provider. Its target production path
   is transcription → executor LLM → response text → speech synthesis. The
   executor also handles ordinary questions; a tool call is not mandatory.
4. Keep model-specific streaming and runtime behavior inside provider adapters.
   Selecting a model changes the provider and the available settings; users do
   not start a different application or manually rewrite engine commands.
5. Run MCP tasks outside the audio-turn lifecycle so the interface can accept
   another spoken request while earlier work is pending.
6. Discover applications, tools and schemas dynamically. Product names,
   pronunciation rules, IDs, menus and business actions are not code branches.
7. Use the same executor with configured direct MCP servers now and the future
   **NoX Suite Gateway** later. NoX Suite integration follows its contracts.
8. Preserve manual startup. Selecting or saving a model does not install an
   autostart service or start a stopped controller/engine.

PersonaPlex MCP/LLM delegation is **not part of this implementation target**.
Its conversational role remains useful and fully accessible. A later proposal
must establish reliable request extraction and delivery of external results
before advertising agent capabilities for that provider.

## 3. Existing behavior and target behavior

| Area | Existing | Agreed target |
| --- | --- | --- |
| Main webapp | PersonaPlex through the native controller | Both models through the same controller and interface |
| PersonaPlex | MLX, continuous 24 kHz audio, native full duplex; voice, persona and precision settings | Preserve those capabilities and defaults |
| LFM3 | Isolated local experiment using pinned llama.cpp/GGUF assets | Integrated provider in the main webapp |
| LFM3 execution | Model-selected direct calls or one explicitly selected delegated write | Generic executor LLM with discovered tools and dependent calls |
| Interruption | PersonaPlex native behavior; experiment blocks Space during replies | Preserve PersonaPlex behavior; immediate Space interruption throughout LFM3 replies |
| Pending work | Experiment blocks the next spoken request | Independent tasks with visible state and queued completion messages |
| Configuration | Flat PersonaPlex settings; experimental MCP/LLM settings in memory | Versioned settings with independent provider profiles |
| Completion | Experimental canned clips and model-generated direct replies | Brief, result-grounded speech; full output available separately |

The old experiment's fixed target tool, product-name aliases, fixed completion
phrases, and six-round/twelve-call ceilings are not target requirements.

## 4. Components and placement

| Component | Responsibility |
| --- | --- |
| Main frontend | Settings, capture/playback, explicit interruption, task activity and connection state |
| Web gateway (`gateway/`) | Existing browser authentication, session ownership and fixed transport to the controller |
| Native controller (`mac/controller/`) | Provider lifecycle, validated settings, conversation ownership and task APIs |
| Provider adapters (`mac/engine/`) | PersonaPlex MLX or LFM3 runtime, formats, voices and model-specific operations |
| Task executor on the native host | LLM requests, MCP discovery, argument validation, execution, receipts and context |
| External LLM service | The configured model endpoint, initially LM Studio; its process remains user-managed |
| External MCP servers / Suite Gateway | Advertised tools and authoritative product results |

The **web gateway** above is this application's existing Docker component. The
**Suite Gateway** is the future NoX Suite product. They are different services.
The Docker image does not load the voice models or the executor LLM.

The executor belongs alongside the native controller, so configured localhost
LLM/MCP URLs refer to that host. The browser never directly connects to those
services. No new public model-runtime port is needed: providers remain behind
the existing controller transport, with private loopback runtimes.

Implementation may split provider and executor processes where lifecycle or
runtime isolation requires it. The controller owns those children and their
handles; it does not terminate unrelated processes by name or port.

## 5. Provider capabilities

The controller publishes a provider/model catalogue from installed adapters and
verified asset manifests. This catalogue is distinct from MCP tool discovery.

Each entry includes a stable provider/model ID, display name, runtime version,
asset readiness, input/output formats, interaction modes, supported operations,
settings definitions and reload requirements. Examples of provider IDs are
`personaplex` and `lfm3-audio`; they are technical adapters, not product routes.

| Capability | PersonaPlex target | LFM3 target |
| --- | --- | --- |
| Native continuous conversation | Yes, existing behavior | Not claimed |
| Push-to-talk command entry | Not required for existing native mode | Yes, Space and equivalent pointer/touch control |
| User speech transcription for the executor | Not provided by its assistant-text output | Yes, complete-turn transcription |
| Read supplied text aloud | Not advertised without a validated adapter | Yes, streaming TTS |
| Generic MCP execution | Not advertised | Through the executor LLM |
| Input while an executor task is pending | Not applicable | Yes |
| Voice selection | Existing 18 prepared prompts | Runtime-supported female/male presets initially |
| Persona/instructions | Existing persona prompt | Separate executor instructions, transcription hints and speech controls |
| Precision | Existing full/4/5/6/8-bit options | Only verified prepared artifacts; current experiment uses Q8_0 + BF16 projector |

The frontend renders controls from capabilities. The server validates them
independently. Unsupported settings must return an explicit validation error,
never be silently accepted, repurposed or advertised as effective.

`7B` and `2B` are parameter counts, not selectable quantization bit depths.
Selecting a precision does not create an artifact that was never prepared.
Language availability follows the actual model/runtime; this contract promises
neither PT-PT synthesis nor language learning through a persona instruction.

## 6. Model selection and lifecycle

There is one active voice provider and one live conversation at a time, as in the
current application. Several executor tasks within that conversation do not
create several voice conversations. Simultaneous independent conversations and
simultaneously loaded voice providers are outside this change.

- Settings identifies the selected and loaded models separately. Selection and
  saving never download or load a model. **Download** prepares verified files;
  **Load** explicitly loads the selected model, ejecting an idle outgoing model;
  **Eject** releases model memory while retaining files and the controller.
- Applying a different selection while idle ejects the outgoing worker and saves
  the selection. Loading the incoming model is a separate explicit action.
- Switching requires the conversation to end and its tasks to reach a terminal
  state. Show the reason while blocked and offer the existing end/cancel actions;
  do not silently abandon work or change the selected model in the background.
- Missing assets produce a preparation-required state. Selecting a model never
  silently downloads gigabytes, substitutes another model or changes precision.
- A failed switch leaves an explicit error and both profiles intact. Returning
  to the previous model is an explicit user selection; no hidden fallback.
- Runtime crashes and controller restarts do not auto-start inference, restore a
  live microphone, resume tasks or replay tools.

Conversation state, model caches and task reference namespaces do not migrate
between providers. Saved settings and endpoint profiles survive a provider
switch. Switching cannot silently transfer stale object IDs into a new session.

## 7. Audio, turns and interruption

### PersonaPlex

Preserve continuous microphone streaming, native interruption by speaking,
24 kHz conversion, silence cadence and the current voice-conditioned state.
It must remain usable without any new LFM3/executor setup or service dependency.
Do not force push-to-talk, end-of-turn detection or LFM3 sampling controls onto
this provider.

### LFM3

Space down starts capture; Space up ends the utterance and requests transcription.
An on-screen hold control provides the same behavior on touch devices. While
speaking a response, Space down first stops scheduled playback immediately,
cancels that speech generation best-effort, then captures the new utterance.
This applies to ordinary conversation, progress notices and MCP confirmations.

During a connected LFM3 audio session, Space owns push-to-talk even when an
ordinary button such as Read aloud has focus. Suppress that button's native
Space activation on press/repeat/release. Text entry and open Settings/Downloads
dialogs retain normal keyboard behaviour. Space never unmutes the microphone
implicitly; muted capture stays muted.

The model transcribes the complete utterance faithfully. It does not choose MCP
tools, simplify compound instructions, or reinterpret quoted titles beforehand.
The executor receives the original transcript, not a voice-model task summary.
Use the complete-turn multilingual ASR mode for the selected input language;
the documented English-only LiveASR mode is not a Portuguese transcription path.
An empty or failed transcription creates no execution task. A user must be able
to inspect the transcript associated with a task without enabling disk recording.

Use short, isolated transcription/synthesis sessions while keeping weights
loaded. Release the LFM3 runtime slot before waiting for the external LLM or MCP.
One runtime slot is sufficient for sequential audio operations: interrupt TTS to
accept a new utterance and queue non-urgent speech while capturing. Do not retain
a conversation WebSocket as an exclusive lock throughout a long task.

This is interruptible streamed audio with explicit turn boundaries, not a claim
that LFM3 becomes a natively full-duplex model. Speech controls and capability
labels must reflect that distinction.

Every session, turn and playback operation has its own identity/generation.
Ignore late text/audio from interrupted playback and ended sessions. Task IDs
are separate: stopping speech does not cancel its associated task.

Keep the orb driven only by assistant playback. Microphone permission must be
explained before capture; denial/missing-device errors must identify the remedy.
Reuse an acquired stream within a connected conversation. Permission persistence
belongs to the browser; the app cannot force a permanent grant. This change does
not weaken secure-origin requirements or add recordings.

## 8. Executor and task contract

The first executor adapter uses the configured local LM Studio service through
its tool-capable `/v1/chat/completions` API. It receives discovered schemas and
retains execution control in NoX Voice. The experimental one-tool native
`/api/v1/chat` argument generator is not the target generic agent.

The selected voice model and executor model are distinct settings. No particular
executor model ID, installation path or product catalogue is built into code.
The application does not start, download or stop the external LLM service.

A task contains `taskId`, conversation/session owner, source turn ID, original
transcript, settings revision, endpoint/catalogue identity, state, execution steps,
actual receipts, references, final outcome and presentation state. Preserve the
request's wording and record each step separately from the model's narration.

Task states are:

| State | Meaning |
| --- | --- |
| `queued` | Accepted, awaiting capacity or the configured acknowledgement |
| `planning` | Executor LLM is interpreting the request or next step |
| `executing` | A specific MCP call has been dispatched |
| `awaiting_input` | More information or a contract-required confirmation is needed |
| `cancel_requested` | Stop requested; admitted work may still be finishing |
| `succeeded` | Required work has confirmed successful results |
| `partially_succeeded` | Some work succeeded and the requested remainder did not |
| `failed` | A known failure prevents completion |
| `cancelled` | Further work stopped and no dispatched effect remains unresolved |
| `outcome_unknown` | A dispatched operation's outcome could not be established |

The final five outcome states are terminal. A presentation failure does not turn
a successful product action into a failed action or justify replaying it.

For each execution step:

1. Supply the relevant authorized tool definitions and actual prior results to
   the LLM, including required arguments and available NoX descriptors.
2. Validate the selected tool, arguments and schema in application code.
3. Resolve bindings to earlier structured results in code; preserve actual IDs,
   revisions and values. Do not synthesize an object ID from a spoken label.
4. Execute the chosen call through the configured MCP connection, recording its
   invocation identity and actual outcome.
5. Return the result/error to the LLM for the next decision. Dependent calls wait
   for the result they need; no blind batch of guessed follow-up calls.
6. Finish only when the objective is met, a real error prevents progress, the
   user must clarify/confirm, or cancellation applies.

Invocation identities belong to execution steps. Re-delivering the same identity
and payload returns its recorded outcome; reusing it with different arguments
fails. A new user-requested action gets a new identity. This deduplicates delivery,
not legitimate repeated requests, and cannot establish a remote outcome that was
lost after dispatch.

This supports compound and conditional requests across arbitrary products.
Published workflows, options, previews and confirmations are interpreted
generically where supported. Unknown required descriptor features block that
advanced path explicitly. An LLM does not grant authorization or satisfy a
required confirmation merely by generating a boolean.

There is **no built-in ceiling of six rounds, twelve calls, or another arbitrary
number of reasoning/tool steps per request**. Model context windows, actual
server budgets, payload bounds and finite execution capacity still apply and
must be visible/configurable where controllable. They must not masquerade as a
business rule limiting the user's objective. On context pressure, retain the
objective and structured references while compacting presentation/history; never
silently drop identifiers or report incomplete work as complete.

### Context and stale results

Keep actual results in a structured store scoped to the conversation and trusted
connection/catalogue identity. Cross-turn references such as “those results” bind
to the relevant prior set, including its filters and origin; a task can perform
the search and the subsequent actions within one instruction as well.

When several sets could be intended, ask instead of guessing. A changed server
identity, removed object or explicit stale-reference error invalidates affected
references. Refresh through the advertised tools when appropriate. Reconnecting
or changing a connection must not silently make old references executable.
Unstructured tool output is preserved as such; do not claim deterministic
reference extraction where the server provides only ambiguous prose.

The model's statement that it deleted or created something is never an execution
receipt. Errors, partial results and unknown outcomes remain distinct.

### Background work and cancellation

Tasks do not hold the microphone, playback queue or LFM3 slot while waiting on
the executor/model server. New utterances can be transcribed and accepted during
earlier work. Actual parallel inference depends on the external service; queued
work remains visible rather than freezing the interface or promising immediate
execution. Concurrent tasks have separate IDs/results and must not overwrite
each other's LLM messages or references.

Starting a new utterance interrupts speech only. Cancelling a task stops its
unsent steps and requests cooperative cancellation of active work. An admitted
write may already have happened; a lost result is not proof of rollback. Never
automatically retry an unknown write or replay a task after reconnect/restart.

Ending the conversation, losing its authenticated connection or stopping the
engine prevents further dispatch for that session, cancels queued work and
requests cancellation of in-flight work. Already admitted effects may finish.
Retain any received terminal outcome in the current in-memory activity store;
do not inject it into a different session. No local task is restored or resumed
after restart. External work already admitted can still finish; startup must not
claim that it rolled back.

## 9. Response and notification policy

Default speech is concise throughout the application, not just after MCP calls.
Normal questions receive a short useful answer; completed actions receive a
short confirmation; failures receive a short explanation of the actual failure.
Long content, IDs, raw JSON and full tool results are not read unless requested.
Generated content goes to its requested destination without being narrated.

The executor composes final speech from actual receipts. The LFM3 synthesis
stage reads only that presentation text, not full tool results or its own
alternative interpretation of the outcome. Product/tool display names are
runtime data. No product-specific completion strings or pronunciation branches.

An optional progress acknowledgement identifies that work has been accepted or
is pending, without claiming a tool has already succeeded. Completion/failure
messages wait for a speaking/listening pause; they never interrupt a new user
utterance or silently erase a newer reply. Activity remains visible while speech
is queued, muted or interrupted.

Provide these LFM3 notification settings:

- Progress voice on/off; wording `automatic` or `custom`.
- Custom progress text belongs to the saved user profile, not application code.
- Dispatch order `after_notice` (default, preserving the tested order) or
  `overlap_notice` (explicitly begin work while the notice plays).
- Completion voice on/off; automatic concise wording or user-supplied template.
  The selected outcome controls which template can be used; failure/unknown
  outcomes never use a success template.

Automatic progress wording uses only authoritative admission/task-state facts.
It can be composed in a short presentation-only LFM3 text operation before TTS;
that operation has no tools and cannot modify or replace the original request.
Completion wording comes from the executor result. Both must pass the concise
speech acceptance checks. Templates use a documented, data-only placeholder
set such as application label, task label and outcome; they cannot execute code,
select tools, inject credentials, or invent success. Full task output is never
an implicit template placeholder.

An acknowledgement interrupted before dispatch cannot hold a task forever: end
that notice and release its accepted task according to the chosen order unless
the task/session was also cancelled. A task cancellation before dispatch sends
no MCP call. Speech interruption and task cancellation remain different actions.

PersonaPlex uses its persona to guide response style and retains native speech;
do not promise deterministic sentence counts or verbatim template reading from
that provider. Task notification settings are shown only for capable providers.

## 10. Settings workspace and customization

Use one responsive Settings workspace in the main application: a spacious
dialog on desktop and full-screen view on mobile. Organize available controls
into Models, Voice, Credentials and General, with tabs and disclosure controls
only where useful. Executor & MCP and Notifications follow their capabilities
when implemented. Do not expose nonfunctional settings.

| Group | Agreed controls and behavior |
| --- | --- |
| Model & engine | Provider/model selector; Download/Load/Eject; verified local status; supported precision/artifact choices; resource/status display; native folder action |
| Credentials | Extensible named service definitions, initially Hugging Face; explicit save/test/replace/remove, private controller storage and status-only reads |
| Shared conversation | Concise-by-default instructions; preferred input/output languages where supported; provider-appropriate interruption controls |
| PersonaPlex | Existing persona editor, 18 prepared voices and precision choices; preserve current defaults independently of LFM3 |
| PersonaPlex advanced | Text temperature/top-k and audio temperature/top-k, exposing the sampling controls already used by its worker; default to existing 0.7/25 and 0.8/250 |
| LFM3 transcription | Input language or supported auto-detection; user vocabulary/context hints and catalogue-derived app/tool labels as data; literal transcription remains the task |
| LFM3 speech | Supported output language and voice preset; runtime-supported synthesis instructions; guidance does not guarantee an accent |
| LFM3 advanced | Only adapter-mapped, validated sampling controls exposed by the pinned runtime; no dummy audio sliders or claims that every runtime has the same parameters |
| Executor | LLM base URL, model ID, connection test, general assistant instructions and effective supported generation settings |
| MCP | Multiple named Streamable HTTP connections, discovered identity/catalogue, connect/disconnect/refresh/test; direct or Suite connection role; credential reference when required |
| Notifications | The progress/completion choices in section 9; transcript/activity visibility |
| Existing controls | Theme, account/logout, deployment-controlled connection diagnostics and manual lifecycle behavior remain available |

Changing a PersonaPlex persona/voice remains supported; conversational state is
rebuilt at a clean session boundary. Prompting cannot train a language or add
native tool calling. Voice uploads/cloning and arbitrary model executable paths
are not implied by customization.

The LFM3 managed path separates executor persona from transcription and TTS
instructions. User vocabulary is context, not global string replacement: a
dictated title is not rewritten because it resembles an application name.
The base ASR/TTS and execution protocol requirements remain in force under
customization. No custom prompt can redirect a call outside configured tools.

Each settings definition declares type, default, supported range/choices, whether
it is secret, capability requirements and when it applies: live presentation,
next task, next conversation or worker reload. The UI displays that scope before
Apply. Runtime-dependent numeric ranges must be mapped and validated before a
control ships. Unsafe/unsupported values return errors rather than being ignored.

Save independent provider profiles. Editing inactive LFM3 settings does not alter
PersonaPlex's voice or prompt. Tasks snapshot their settings/catalogue revision;
editing defaults never changes an already accepted task's destination or model.
The external LLM service is not a voice-provider artifact or a quantization choice.

Persist settings and endpoint profiles through controller-owned private storage.
Secrets are separate private references, never returned by settings GET, placed
in browser storage/prompts, baked into images, or copied to logs. Secret changes
are explicit writes; a masked placeholder cannot overwrite a stored secret.
Existing deployment-mounted certificates and web secrets retain their ownership
and mounting policy; UI customization does not relocate or regenerate them.

## 11. Application API and compatibility

Use an additive, versioned application boundary under `/api/voice/v1` and
`/voice/v1/ws`, relayed by the existing web gateway. These names are this
application's API, not extensions to the MCP protocol or released SDK versions.

The first stage implements `GET /api/voice/v1/providers`,
`GET|PUT /api/voice/v1/settings`, `GET /api/voice/v1/engine/status` and
`POST /api/voice/v1/engine/{start|stop|restart}`. Provider metadata includes
`version: 1`, `selectedProvider` and `providers`; each entry carries identity,
runtime, implementation/selection availability, capabilities, audio format,
`settingsSchema`, apply scope and explanatory text. `files_present` describes
presence only; actual asset verification still occurs at engine startup.

The settings document has `schemaVersion: 1`, an integer `revision`,
`selectedProvider`, and `profiles` keyed by `personaplex` and `lfm3-audio`.
PUT requires the complete document with the expected revision and both profiles;
409 rejects a stale revision or a busy engine, 422 rejects invalid settings, and
503 rejects selecting an unimplemented provider without stopping the active one.
Actual changes increment the revision; saving unchanged values does not.
LFM3 provides versioned audio-check sessions today. Executor task endpoints and
activity events are implemented in stages 3–4.

The namespace provides:

- Provider/model catalogue and capability/settings definitions.
- Read/update settings with schema version and revision conflict detection.
- Engine lifecycle and active/desired model status, including preparation state.
- Session creation/end with provider, settings revision and negotiated formats.
- Task list/detail/cancel and refreshable in-memory activity for the session.
- Configured LLM/MCP connection testing/discovery without hidden tool execution.

The session stream carries explicitly typed control messages and negotiated audio
frames. Control events include `session.ready`, `turn.started`,
`transcript.completed`, `task.updated`, `speech.started`, `speech.ended`,
`speech.interrupted` and `error`. They carry the relevant session, turn, task or
playback identity and an ordering sequence. Audio frames are bound to a playback
or capture identity so an old generation cannot play after interruption.

Document and validate complete request/event schemas before deploying this API;
reject incompatible versions explicitly. Clients can reload an activity snapshot
after missed events; that resynchronizes presentation and never resumes a task.

Keep existing controller routes and the PersonaPlex `/ws` audio protocol compatible
with PersonaPlex clients, including the existing Discord integration. A PersonaPlex
stream must not receive LFM3 PCM or different formats under the same protocol.
When the selected provider cannot serve that PersonaPlex path, report it explicitly
before accepting audio, without switching providers behind the user's back.
Updating Discord for LFM3 is separate work and must not break text commands.

Settings use independent versioned provider profiles. PersonaPlex-specific API
writes apply only to that profile and cannot replace LFM3/executor settings.
The product requires no installation migration, downgrade workflow or support
for historical application versions.

## 12. NoX Suite integration

Follow the current NoX MCP documents: `NOX_SUITE_IMPLEMENTATION_PLAN.md`,
`GATEWAY_CONTRACT.md` and `DISCOVERY.md`. This file does not amend those contracts
or claim that the Gateway/CLI/Portal already exist.

NoX Voice is a client of the Suite Gateway using the account's configured API
key and resulting policy. The Backend remains the authority. The executor never
forwards that key as model input or creates its own registry/role system.

Use conventional `tools/list`/`tools/call`, and `nox/discovery`/descriptors when
negotiated. Preserve origins, aliases, fingerprints, structured results, errors
and required confirmation semantics. Products remain the execution authority;
the agent is not a bypass for permissions or revision checks.

Direct external MCP connections use their own explicitly configured endpoints
and authentication. They do not become Suite products merely by being connected.
Credential capabilities are validated in code; unsupported authentication fails
clearly rather than silently downgrading to anonymous access.

LLM reasoning/generation time is outside a product tool call's Gateway budget.
Long product work uses declared asynchronous operations where available or an
explicit suitable call budget. Session lifetime, task lifetime and individual
RPC deadlines remain distinct. No fixed per-product execution code is introduced.

## 13. Assets, privacy and deployment

Keep the current browser → web gateway → mTLS controller boundary and private
worker authentication. Local, LAN and optional remote access retain their
existing policies. Model switching requires no changed Compose URL, new public
runtime port, certificate copy, external route or extra Docker service.

Preserve the existing PersonaPlex assets. New provider assets use a known
provider-owned subtree beneath the configured engine asset root, with pinned
versions, hashes, safe extraction, isolated cache/temp directories and an explicit
inventory/cleanup procedure. Paths resolve from the configured installation;
documentation uses placeholders rather than personal paths, users or addresses.
Never scan other installations or silently move/delete existing assets.

**Asset layout:** `weights_dir` is the shared root with direct `personaplex/`
and `lfm3-audio/` children. `--weights-dir` always selects this root, including
preparation/checking for either model. Download/verification manifests belong to
their provider directory. The UI uses PersonaPlex revision
`fdaf4090a61cb315c138a1faee287ffd6c716309` and its published SHA-256/size manifest;
LFM3 retains its pinned GGUF revision and llama.cpp b11960. No implicit upgrades.

Preparation is explicit and separate from inference. Switching never installs
global packages or scatters model downloads into unrelated caches. Existing
experiment files can be reused only after verification and an explicit import
step; the application does not search the computer for models.

Audio, transcripts, LLM history, task arguments/results and references stay in
bounded memory by default. Diagnostics redact credentials and content. No new
database of conversations or task history is implied. Persistent recording,
history, export/import and background work after restart require a separate
decision. Restarting never replays a write. External LLM/MCP services retain their
own storage policies; request non-persistent/stateless operation where supported.

## 14. Implementation sequence and acceptance

### Stage 2.1 — model management and Settings (approved October 30, 2026)

Implement this stage before stage 3. It extends stages 1–2 and advances part of
stage 5 without removing stages 3–6 or changing the executor/Suite architecture.

1. Record the contract and API decisions.
2. Implement controller-owned model downloads, credentials, local asset metadata
   and opening the provider folder in Finder.
3. Implement the responsive Settings workspace and global Download Manager.
4. Validate lifecycle, downloads, credentials, audio preservation and browser UX.
5. Update product documentation to describe the current product, without old
   installation/downgrade instructions or migration requirements.

The model catalogue supplies source, runtime/revision, expected download size,
credential requirements and asset status. Presence, integrity verification and
loaded state remain distinct. Downloading a model never starts inference.
Both model providers have the same explicit download experience. PersonaPlex
requires a Hugging Face read credential plus accepted access terms; the pinned
LFM3 GGUF files and GitHub runtime support anonymous downloads.

A global SVG download button opens a Download Manager with bounded recent jobs,
per-file and total byte progress, speed, verification/extraction stages, errors,
cancel and retry/resume. Active, idle and attention states are distinguishable
without colour alone. Closing Settings/browser does not cancel a controller-owned
download. Controller shutdown stops owned jobs, retains safe partial downloads
and never automatically resumes them or loads inference after restart. Resume
is explicit, bound to immutable artifact identity and verified before install.

**Clear downloads** clears terminal history entries only, persists that change,
and updates the global indicator. It never deletes model/partial files, ejects a
model, or cancels active jobs. Jobs whose workers are still shutting down remain
visible until they have released their resources.

Credentials use a service registry, initially `huggingface`. Status reads expose
configured/tested state only; explicit writes store secrets privately on the
native host, separately from settings. No secret is returned, logged, added to
worker arguments, browser storage or model prompts. Testing distinguishes invalid
credentials, missing model access and network failure. Public downloads do not
require configuring a token.

The folder SVG opens Finder on the controller Mac at the selected provider's
configured asset directory. It never opens a web file explorer or accepts an
arbitrary filesystem path. Remote browser use opens Finder on that Mac; absence
of a graphical login produces a clear error. Folder actions require an explicit
authenticated user request.

Additive APIs: `GET /api/voice/v1/models`, `POST /api/voice/v1/providers/{id}/open-folder`,
`GET|POST|DELETE /api/voice/v1/downloads`, `POST /api/voice/v1/downloads/{id}/{cancel|retry}`,
`GET /api/voice/v1/credentials`, `PUT|DELETE /api/voice/v1/credentials/{service}`,
`POST /api/voice/v1/credentials/{service}/test`, and explicit engine load/eject
actions. All are relayed through authenticated web gateway and mTLS. Validate
identities and payloads server-side; jobs and credentials never become arbitrary
URL fetchers, executables or filesystem browsers.

### Stage 2.2 — React, TypeScript and Vite+

Migrate the main frontend to React components and Vite+, preserving current
features, appearance, provider behavior, settings and API/audio/security
boundaries. Organize Conversation, Settings and its tabs, Models, Downloads and
Credentials by responsibility, with typed services and explicit resource
lifecycle management. Include development checks, tests, Docker build, CI and
documentation in the migration.

The [stage 2.2 implementation plan](implementation-stage-2.2.md) is approved and
the migration is implemented. Automated component, lifecycle, browser and
container checks pass; real deployment acceptance remains pending. This stage
follows 2.1 and precedes stage 3. Stages 3–6 retain their scope and order.
Completion requires functional and visual parity acceptance, with real
deployment findings reported separately in [validation](validation.md).

Current implementation status:

| Stage | Repository status |
| --- | --- |
| 1. Provider boundary and versioned settings | Implemented: provider-defined worker module/bootstrap/metadata, independent typed profiles, revision conflicts, authenticated API relay and catalogue-aware controls; existing PersonaPlex audio path retained |
| 2. LFM3 audio in the main webapp | Implemented: explicit pinned preparation/import, private owned runtime, complete-turn ASR, audio-only TTS, Space/touch interruption and independent audio-check sessions in the existing frontend |
| 2.1. Model management and Settings | Implemented: both-provider downloads, bounded Download Manager, explicit Load/Eject, private service credentials, native Finder action and responsive Settings tabs; see validation for test coverage |
| 2.2. React, TypeScript and Vite+ | Implemented: modular React UI, typed feature state/services, Vite+ checks/tests/build, CI and amd64/arm64 container validation; real deployment parity acceptance pending before stage 3 |
| 3. Generic executor and task store | Pending; the isolated demo's MCP/LLM paths remain independent |
| 4. Background tasks and presentation | Pending |
| 5. Full customization and compatibility | Pending beyond PersonaPlex controls, LFM3 input/output language, vocabulary and voice controls, and profile/API compatibility delivered in stages 1–2 |
| 6. Suite acceptance | Pending; Gateway integration follows the Suite contract and actual service availability |

Stages 1 and 2.1 are verified by controller lifecycle/stream/download/credential tests, gateway auth
and validation tests, frontend provider tests, and disposable browser QA of the
Settings workspace (save/conflict/reload, downloads, credentials and mobile layout).
Stage 2 also verifies pinned assets, the versioned gateway/mTLS/worker transport,
worklet flush ordering, stale-event rejection, operation cancellation and
microphone cleanup. An opt-in test with real LFM3 weights exercises ASR, TTS,
speech interruption, reuse of the single model slot and owned-process shutdown.
See [validation](validation.md) for observed results and their limits.

The main LFM3 interface is deliberately labelled **audio checks** at this stage:
speech is transcribed literally and a supplied text can be synthesized. No main
app executor or MCP task is dispatched. This is the audio layer for stage 3,
not a replacement architecture or a claim that its conversation flow is done.

These are implementation stages for the full contract, not different architectures
or per-product pilots. Keep the working PersonaPlex path throughout.

1. **Provider boundary and versioned settings:** implement capabilities, profile
   isolation and lifecycle; prove PersonaPlex behavior remains unchanged.
2. **LFM3 audio in the main webapp:** integrate verified assets, complete-turn
   transcription, TTS, Space/touch interruption and independent audio sessions.
   **Stage 2.1 — Model management and Settings:** complete the approved work
   package above before proceeding to stage 3.
   **Stage 2.2 — React, TypeScript and Vite+:** complete the frontend migration
   and parity acceptance in the linked implementation plan before stage 3.
3. **Generic executor and task store:** connect the existing LLM service, discover
   MCP schemas, validate calls, bind results and execute dependent/conditional work.
4. **Background tasks and presentation:** accept new turns during pending work,
   correlate outcomes, queue concise notifications and implement cancellation.
5. **Settings and compatibility:** expose validated provider controls, multiple
   connections and notification policy; preserve PersonaPlex client protocols.
6. **Suite acceptance:** validate against its contracts and then the real Gateway
   when implemented. Use direct MCP and generic disposable fixtures beforehand.

| Acceptance case | Required result |
| --- | --- |
| PersonaPlex without LLM/MCP | Existing full-duplex conversation, voice, persona and precision still work |
| Main webapp model change | Correct worker/settings/formats switch; no second frontend or URL/config rewrite |
| Manual lifecycle | Saving/selecting while stopped stays stopped; no autostart installed |
| Inactive provider edits | Other provider's saved profile and behavior remain intact |
| Space during any LFM3 speech | Audio stops immediately; new utterance is captured; late old audio is ignored |
| Touch/mobile and microphone errors | Equivalent control; clear permission/device guidance; assistant-only orb |
| Compound request | Complete transcript reaches executor; search/filter/action are separate correct steps |
| Conditional request | Inspect a resource, evaluate returned state, act only when the user's condition holds |
| Cross-turn references | Resolve the actual previous result set; no invented IDs or unrelated selection |
| Stale/reconnected source | Invalidate affected references and refresh/clarify without blindly reusing an old ID |
| Long task plus new utterance | Capture/transcription/UI remain available; jobs retain separate state and visible queuing |
| More than twelve valid calls | Continue toward the objective without the experiment's fixed step ceiling |
| Arbitrary MCP catalogue | Add/change a compliant server without app-name rules or product-specific executor code |
| Success/query/failure | Concise grounded speech; no unsolicited IDs, full generated content or invented explanation |
| Error/partial/unknown outcome | Accurate distinct status; no success clip or automatic replay of an uncertain write |
| Progress dispatch choices | Before-notice/overlap behavior matches settings; speech interruption cannot strand a job |
| End/logout/network loss/Stop | No further dispatch; cooperative cancellation; no false rollback or cross-session result injection |
| Assets/settings/privacy | Provider isolation, private persisted settings/credentials; no recording or hidden downloads |
| PersonaPlex/Discord | Existing format and exclusivity preserved; incapable provider fails clearly before streaming |
| NoX Suite | Dynamic authorized catalogue and real execution through Gateway; no business routing hardcodes |

Use several spoken paraphrases and actual target languages for compound requests.
Measure transcription fidelity, time to first audio, interrupt-to-silence,
capture readiness, task queueing and memory under concurrent model activity.
Publish observed results; acceptance cannot be based only on assistant narration,
one phrasing, mocked model output or an untested latency promise.

## 15. Upstream references and change discipline

- [PersonaPlex model](https://huggingface.co/nvidia/personaplex-7b-v1) and
  [official implementation](https://github.com/NVIDIA/personaplex).
- [LFM3-Audio model and ASR/TTS modes](https://huggingface.co/LiquidAI/LFM3-Audio-2B).
- [Pinned experiment runtime protocol](https://github.com/ggml-org/llama.cpp/blob/b11960/tools/server/README.md#realtime-conversation).
- [LM Studio tool API](https://lmstudio.ai/docs/developer/openai-compat/tools).
- [Current application architecture](architecture.md),
  [validation](validation.md) and [experiment](../experiments/lfm3-audio/README.md).

The current LFM3 artifact pin is an existing tested baseline, not permission to
upgrade a runtime implicitly. Record changes to capabilities, prompts, formats,
dependencies and compatibility before updating implementations. Numerical model
controls must reflect the selected runtime, not a generic assumption that every
provider supports every upstream parameter.

This change does not introduce a visual workflow editor, automatic model router,
new speech model, voice cloning, PersonaPlex agent bridge, persistent task history,
or automatic changes to another NoX repository. Those require explicit scope.
