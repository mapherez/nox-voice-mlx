# Stage 3 — Generic executor and in-memory task store

Status: implemented; automated acceptance passed; actual LLM/MCP/model deployment
acceptance pending before stage 4. Stage 2.2 has user-confirmed deployment
acceptance. Original stages 4–6 retain their scope and order.

## Delivery

- Native-host, privately saved executor settings; LM Studio-compatible chat
  completions and explicit connection tests. Voice and executor models remain
  independent. No external process startup or model download.
- Multiple named Streamable HTTP MCP connections, JSON/SSE responses,
  initialization, pagination, supported JSON Schema validation and private bearer
  credentials. Discovery/test never calls a business tool. Suite descriptors
  and unsupported required protocol features block explicitly pending stage 6.
- Conversation-owned tasks and actual step receipts in bounded memory. Original
  transcripts, settings/catalogue identities, deterministic structured-result
  bindings and invocation deduplication remain separate from model narration.
  Dependent calls execute sequentially. No fixed reasoning-round/tool-call cap.
- LFM3 transcript admission and concise ordinary answers through the same
  controller/audio path. PersonaPlex remains independent of executor setup.
- Minimal task activity and usable Executor & MCP Settings. End/disconnect/Stop
  prevents further dispatch, with uncertain remote effects never retried.

## Verification

Exercise ordinary answers without tools, compound search/filter/action chains,
schema rejection before dispatch, invocation deduplication, ambiguous and stale
references, catalogue changes, partial/errors/unknown outcomes, cancellation,
bounded capacity/context and authenticated gateway ownership with disposable
LLM/MCP fixtures. Run existing Python/frontend/browser/build checks. Actual
LM Studio, MCP products and model audio require separate real deployment tests;
fixture results do not establish real product compatibility or latency.

Full background notification queues, progress acknowledgements and their
dispatch policies belong to stage 4. Full provider customization belongs to
stage 5; actual NoX Suite acceptance belongs to stage 6.

Observed checks and limits are in [Validation](validation.md#stage-3-executor-validation).
The user-facing setup and complete application boundary are in [Executor](executor.md).
