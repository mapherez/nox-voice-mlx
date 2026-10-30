# LFM3 answers and MCP actions

The executor runs in the native Mac controller. PersonaPlex keeps its own
conversation and requires neither this configuration nor an external LLM.
LFM3 still supports transcription and supplied-text speech with execution disabled.

## Setup

1. Run a tool-capable model in your LM Studio service. NoX Voice does not download,
   load, stop or start that external service/model.
2. Open **Settings → Executor & MCP**. Set the LLM base URL, including `/v1`,
   and the exact model ID. Addresses refer to the controller Mac: for a service
   on that Mac, use `http://127.0.0.1:1234/v1`.
3. Save, then **Test LLM**. Testing lists models; it does not run inference or a
   tool and cannot certify that the selected model will follow tool instructions.
   Available model IDs become input suggestions.
4. Optionally add named **Streamable HTTP** MCP endpoints. Save each profile,
   then explicitly **Connect**. Test discovers the server identity and paginated
   tools without calling business tools; **Refresh tools** replaces the catalogue.
5. If authentication is required, select the credential checkbox, save, and put
   the key in **Credentials** under the LLM or connection name. Tests use saved
   settings. Anonymous and explicit bearer authentication are supported; OAuth
   challenges and required unsupported protocol features fail explicitly.
6. Enable **LFM3 answers and actions**, save, load LFM3 and connect. Hold Space
   or the touch control to speak. Release submits the complete transcript. An
   empty/failed transcript creates no task. All configured MCP profiles must be
   connected before execution; an unavailable profile is not silently omitted.

End the conversation before saving executor settings or changing connections.
Settings save does not connect MCP, load voice weights or start a conversation.
Changed settings disconnect catalogues, so connect them explicitly again. After
a controller restart, connect MCP again; no task or tool call is restored/replayed.

The executor profile is saved privately as `executor-settings.json` in the
controller's configured `data_dir`; secrets are separate entries in
`credentials.json`. No credential value is returned by settings/status APIs or
put in browser storage. Configured credentials echoed by an endpoint are redacted
before receipts reach the model/browser. The current conversation's content,
arguments, receipts and references remain in bounded memory, without recording.

## Tasks and speech

**Activity** shows each request, state, answer/error and tool-call count.
**View full result** retrieves its original transcript, steps and actual receipts.
**Cancel task** prevents unsent steps and requests cooperative cancellation of
an admitted call. An admitted effect may have happened: losing its response or
cancelling it is never represented as rollback. Unknown outcomes are not retried.

The LLM receives the original request and discovered schemas. NoX validates and
executes one tool decision at a time, returning the actual receipt before asking
for the next decision. Object IDs/revisions can be bound directly from previous
structured results using explicit JSON Pointers. Connection identities scope
those references; a reconnect cannot make an old reference executable. Errors
invalidate prior references from that connection until fresh results are obtained.
Unstructured content is preserved, without claiming deterministic ID extraction.

Ordinary answers need no tool. A concise current-turn response is read once if
the audio session is idle. A late response never interrupts new capture/playback;
it stays in Activity with **Read response**. Interruption stops speech without
cancelling the task. Progress acknowledgements, queued completion notifications
and their custom policies belong to stage 4. Failure to speak cannot replay a tool.

Generation settings and resource budgets are in the advanced disclosure. Defaults:
2,048 maximum generated tokens, a 120-second LLM/call timeout, a 600-second task
deadline, four pending tasks, 32 activity entries, 128 KiB LLM request context
and 8 MiB activity memory. Each external response/catalogue has a 2 MiB payload
bound. There is no fixed tool-call or reasoning-round ceiling. When a budget is
reached, work stops explicitly; start a new conversation when activity is full.
Context pressure compacts tool presentation while retaining the objective and
structured references, readable through the local reference lookup. It never
claims unfinished work succeeded.

## Application boundary

All routes use the existing authenticated gateway and mTLS controller. Settings
are validated by the models in `mac/controller/executor/config.py`. PUT carries
the full settings document with `schemaVersion: 1` and the expected `revision`;
conflicts return 409. No secrets belong in URLs or settings values.

| Route | Behavior |
| --- | --- |
| `GET /api/voice/v1/executor/settings` | Saved executor profile and effective budgets |
| `PUT /api/voice/v1/executor/settings` | Explicit revisioned save; no network/tool execution |
| `POST /api/voice/v1/executor/test` | List models using saved LLM endpoint/auth |
| `GET /api/voice/v1/executor/connections` | Connected identities and discovered tool availability |
| `POST /api/voice/v1/executor/connections/{id}/{connect\|disconnect\|refresh\|test}` | Explicit connection/catalogue operation, never a business tool |
| `GET /api/voice/v1/sessions/{sessionId}/tasks` | Refreshable in-memory activity for its authenticated owner |
| `GET /api/voice/v1/sessions/{sessionId}/tasks/{taskId}` | Full task and receipt data for that owner |
| `POST /api/voice/v1/sessions/{sessionId}/tasks/{taskId}/cancel` | Cooperative cancellation, with actual admitted outcomes retained |

Session/task IDs are 32 hexadecimal characters. The gateway derives an opaque
owner from the authenticated browser session; it ignores browser-supplied owner
headers. Another browser session, including another login by the same account,
cannot inspect/cancel that conversation's tasks. Session expiry, logout,
disconnect and Stop close admission before waiting for cancellation. The most
recent ended conversation can be inspected by its owner until a new one starts.

The version-1 audio handshake and audio controls remain unchanged. The controller
adds `capabilities.executor` to `session.ready` and one ordered sequence across
worker audio and task events. A `task.updated` event contains `sessionId`,
`sequence`, and a `task` summary: task/session/turn IDs, state, settings and
catalogue identities, presentation state, transcript/output excerpts, speech,
error and `stepCount`. Full receipts use HTTP instead of oversized audio frames.
`task.list` and `task.cancel` controls carry `sessionId`; cancel also carries
`taskId`. Listing emits individual task updates. Unsupported fields/controls
are rejected. Resynchronization never resumes execution or repeats speech.

MCP tools are given collision-free local aliases. The LLM's call payload is
`{arguments: {...}, bindings?: [{target, reference, pointer}]}`. Targets and source
pointers follow JSON Pointer; source reference IDs are controller-issued tokens.
Bindings are resolved before JSON Schema 2020-12 validation. External schema
references are rejected and never fetched. Invocation identity combines task and
model call IDs: redelivery with the same payload returns its receipt; changed
arguments fail. Unknown results remain unknown, including after cancellation.

Direct MCP uses negotiated 2025-11-25, 2025-06-18 or 2025-03-26, JSON/SSE
responses, pagination and protocol/session headers. It advertises no sampling,
elicitation or asynchronous task features. Tools requiring metadata/descriptor
interpretation or mandatory asynchronous execution are marked unavailable.
NoX Suite discovery and descriptor/confirmation negotiation require stage 6;
selecting a Suite-role profile currently fails clearly before execution.

Protocol references: [LM Studio tool API](https://lmstudio.ai/docs/developer/openai-compat/tools),
[MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports),
[MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools),
[offline JSON Schema references](https://python-jsonschema.readthedocs.io/en/stable/referencing/).
