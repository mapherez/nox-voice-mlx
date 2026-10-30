import { test } from "node:test";
import assert from "node:assert/strict";
import { sessionSettings, ToolRunner, delegatedProfile, delegatedRequest, toolResultForModel, toolContinuation,
  prepareDelegationNotice, DELEGATION_NOTICE } from "../app/tools.js";
import { applicationName, delegationMessages, completedDelegation, silentDelegationCompletion } from "../app/tools.js";

const profile = { connected: true, id: "connection", tools: [{ name: "notes.create", function: {
  type: "function", name: "notes_create", description: "Create a note", parameters: { type: "object" },
} }] };
const call = { type: "function_call", call_id: "call1", name: "notes_create", arguments: '{"title":"Test"}' };
const success = { name: "notes.create", isError: false, uncertain: false,
  result: { content: [{ type: "text", text: '{"id":"note-1"}' }] } };

test("tools enable automatic modality so the model can choose a function call before speech", () => {
  const settings = sessionSettings(profile);
  assert.equal(settings.tool_choice, "auto");
  assert.equal(settings.output_modalities, undefined);
  assert.deepEqual(settings.tools, profile.tools.map((tool) => tool.function));
  assert.match(settings.instructions, /List of tools:/);
  assert.equal(sessionSettings({ connected: false }).tool_choice, "none");
  assert.deepEqual(sessionSettings(null).output_modalities, ["text", "audio"]);
});

test("tool output preserves call IDs and actual results; duplicate IDs do not execute twice", async () => {
  const requests = [], reports = [];
  const runner = new ToolRunner(profile, async (body) => { requests.push(body); return success; },
    (record, entry) => { reports.push(record); return entry || {}; }, () => true, "voice-session");
  const result = await runner.run([call]);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].invocationId, "voice-session:call1");
  assert.equal(result[0].call_id, "call1");
  assert.equal(JSON.parse(result[0].output).isError, false);
  assert.match(result[0].output, /note-1/);
  assert.equal(reports.at(-1).status, "completed");
  assert.deepEqual(await runner.run([call]), result);
  assert.equal(requests.length, 1);
  await assert.rejects(runner.run([{ ...call, arguments: '{}' }]), /reused/);
});

test("bad JSON and unavailable tools return failures without external execution", async () => {
  let requests = 0;
  const runner = new ToolRunner(profile, async () => { requests++; return success; }, () => ({}), () => true);
  for (const bad of [{ ...call, arguments: '[]' }, { ...call, name: "unknown", call_id: "call2" }]) {
    const output = await runner.run([bad]);
    assert.equal(JSON.parse(output[0].output).isError, true);
  }
  assert.equal(requests, 0);
});

test("disconnect during a call stops the remaining calls and never resumes the old voice session", async () => {
  let active = true, requests = 0;
  const runner = new ToolRunner(profile, async () => { requests++; active = false; return success; }, () => ({}), () => active);
  assert.equal(await runner.run([call, { ...call, call_id: "call2" }]), null);
  assert.equal(requests, 1);
});

test("repeated tool rounds stop execution and force a final answer", async () => {
  let requests = 0;
  const runner = new ToolRunner(profile, async () => { requests++; return success; }, () => ({}), () => true);
  for (let i = 0; i < 7; i++) await runner.run([{ ...call, call_id: String(i) }]);
  assert.equal(requests, 6);
  assert.equal(runner.forceFinal, true);
  runner.beginTurn();
  assert.equal(runner.forceFinal, false);
});

test("unknown outcomes are reported to the model and binary tool content is omitted", async () => {
  const reports = [];
  const runner = new ToolRunner(profile, async () => ({ ...success, isError: true, uncertain: true,
    result: { content: [{ type: "image", data: "private-binary-data" }, { type: "text", text: "No confirmed result" }] } }),
    (record) => { reports.push(record); return {}; }, () => true);
  const output = await runner.run([call]);
  assert.equal(JSON.parse(output[0].output).outcomeUnknown, true);
  assert.doesNotMatch(output[0].output, /private-binary-data/);
  assert.equal(reports.at(-1).status, "unknown");
  assert.equal(runner.forceFinal, true);
});

test("an unknown outcome stops remaining queued actions and automatic model retries", async () => {
  let requests = 0;
  const runner = new ToolRunner(profile, async () => {
    requests++;
    return { ...success, isError: true, uncertain: true };
  }, () => ({}), () => true);
  const result = await runner.run([call, { ...call, call_id: "second" }]);
  assert.equal(requests, 1);
  assert.equal(result.length, 2);
  assert.equal(JSON.parse(result[1].output).isError, true);
  assert.equal(runner.forceFinal, true);
});

const delegation = { configured: true, id: "configuration", connectionId: "connection", target: "notes_create", targetName: "notes.create" };

test("explicit delegation exposes only the delegate, with the selected configuration", () => {
  const delegated = delegatedProfile(profile, delegation);
  const settings = sessionSettings(delegated);
  assert.deepEqual(settings.tools.map((tool) => tool.name), ["delegate_local_llm"]);
  assert.match(settings.instructions, /explicit delegation/);
  assert.equal(delegated.delegation.id, "configuration");
  assert.throws(() => delegatedProfile(profile, { ...delegation, connectionId: "old" }), /Configure/);
});

test("NoteX vocabulary is scoped to the connected app and does not add tools or rewrite user arguments", async () => {
  const notex = { ...profile, name: "notex-mcp" };
  assert.equal(applicationName(notex), "NoteX");
  const settings = sessionSettings(delegatedProfile(notex, delegation));
  assert.match(settings.instructions, /NoteX is pronounced 'no tex'/);
  assert.match(settings.instructions, /'notecs'/);
  assert.match(settings.instructions, /preserve dictated note titles and content exactly/);
  assert.match(settings.instructions, /If a different application is explicitly requested/);
  assert.deepEqual(settings.tools.map((tool) => tool.name), ["delegate_local_llm"]);
  assert.ok(!sessionSettings({ ...profile, name: "Different app" }).instructions.includes("notecs"));
  const requests = [];
  const runner = new ToolRunner(notex, async (body) => { requests.push(body); return success; }, () => ({}), () => true);
  await runner.run([{ ...call, arguments: '{"title":"Notepad notes and notecs"}' }]);
  assert.equal(requests[0].arguments.title, "Notepad notes and notecs");
});

test("successful delegation uses a fixed completion clip and closes the model turn silently", () => {
  const delegated = delegatedProfile({ ...profile, name: "NoteX" }, delegation);
  assert.equal(delegationMessages(delegated).completion, "Done. The result is available in NoteX.");
  assert.equal(completedDelegation(delegated, [{ output: toolResultForModel({ ...success, delegated: true }) }]), true);
  assert.equal(completedDelegation(profile, [{ output: toolResultForModel(success) }]), false);
  for (const record of [{ ...success, delegated: true, isError: true },
    { ...success, delegated: true, isError: true, uncertain: true }, { ...success, delegated: true, uncertain: true }])
    assert.equal(completedDelegation(delegated, [{ output: toolResultForModel(record) }]), false);
  assert.equal(completedDelegation(delegated, [{ output: "invalid JSON" }]), false);
  assert.deepEqual(silentDelegationCompletion().response,
    { tool_choice: "none", output_modalities: ["text"], max_output_tokens: 32 });
});

test("delegation executes by reference, keeps generated content out of voice arguments, and finishes tool use", async () => {
  const paths = [], phases = [];
  const delegated = delegatedProfile(profile, delegation);
  const api = async (path, body) => {
    paths.push([path, body]);
    return path.endsWith("generate") ? { draftId: "server-draft" } : { ...success, final: true, delegated: true };
  };
  const runner = new ToolRunner(delegated, (body, report) => delegatedRequest(body, api, () => true, report),
    (record) => { phases.push(record.phase); return {}; }, () => true, "voice");
  const output = await runner.run([{ ...call, name: "delegate_local_llm", arguments: '{"task":"Write a formatted note"}' }]);
  assert.equal(paths[0][0], "/api/delegation/generate");
  assert.equal(paths[0][1].task, "Write a formatted note");
  assert.deepEqual(paths[1], ["/api/delegation/execute", { draftId: "server-draft" }]);
  assert.equal(JSON.parse(output[0].output).isError, false);
  assert.equal(runner.forceFinal, true);
  assert.ok(phases.includes("generating") && phases.includes("saving"));
});

test("disconnecting during delegated generation discards the draft without executing MCP", async () => {
  const paths = [];
  let current = true;
  const api = async (path) => { paths.push(path); current = false; return { draftId: "discard-me" }; };
  await assert.rejects(delegatedRequest({ arguments: { task: "Write a note" } }, api, () => current, () => {}),
    (error) => error.outcomeUnknown === false && /No MCP action/.test(error.message));
  assert.deepEqual(paths, ["/api/delegation/generate", "/api/delegation/discard"]);
});

test("delegation waits for the spoken notice to finish before contacting the LLM", async () => {
  const requests = [], phases = [];
  let finishNotice;
  const notice = new Promise((resolve) => { finishNotice = resolve; });
  const pending = delegatedRequest({ arguments: { task: "Create a note" } }, async (path) => {
    requests.push(path); return { draftId: "draft" };
  }, () => true, (event) => phases.push(event.phase), () => notice);
  await Promise.resolve();
  assert.deepEqual(phases, ["announcing"]);
  assert.deepEqual(requests, []);
  finishNotice();
  await pending;
  assert.deepEqual(phases, ["announcing", "generating", "saving"]);
  assert.deepEqual(requests, ["/api/delegation/generate", "/api/delegation/execute"]);
});

test("disconnect or failed notice prevents both LLM generation and MCP execution", async () => {
  for (const broken of [false, true]) {
    let active = true, requests = 0;
    await assert.rejects(delegatedRequest({ arguments: { task: "Create a note" } }, async () => { requests++; },
      () => active, () => {}, async () => {
        if (broken) throw new Error("Playback failed");
        active = false;
      }), (error) => error.outcomeUnknown === false);
    assert.equal(requests, 0);
  }
});

test("notice is generated by the voice runtime without tools, then kept in memory", async () => {
  const sent = [];
  const connection = { send: (data) => sent.push(JSON.parse(data)), close() { this.closed = true; } };
  const pending = prepareDelegationNotice(connection);
  const emit = (event) => connection.onmessage({ data: JSON.stringify(event) });
  emit({ type: "session.created", session: { audio: { output: { format: { rate: 24000 } } } } });
  assert.equal(sent[0].session.tool_choice, "none");
  assert.deepEqual(sent[0].session.output_modalities, ["audio"], "TTS must read the phrase rather than answer it as chat");
  emit({ type: "session.updated" });
  assert.equal(sent[1].item.content[0].text, DELEGATION_NOTICE);
  emit({ type: "response.output_audio.delta", delta: "AAAA" });
  emit({ type: "response.done", response: { status: "completed" } });
  assert.deepEqual(await pending, { text: DELEGATION_NOTICE, chunks: ["AAAA"] });
  assert.equal(connection.closed, true);
  assert.equal(connection.onmessage, null);
});

test("completion preparation reads only the short completion phrase and preserves its display text", async () => {
  const sent = [], phrase = "Done. The result is available in NoteX.";
  const connection = { send: (data) => sent.push(JSON.parse(data)), close() {} };
  const pending = prepareDelegationNotice(connection, undefined, phrase);
  const emit = (event) => connection.onmessage({ data: JSON.stringify(event) });
  emit({ type: "session.updated" });
  assert.equal(sent[0].item.content[0].text, phrase);
  emit({ type: "response.output_text.delta", delta: "Unwanted generated recipe" });
  emit({ type: "response.output_audio.delta", delta: "AAAA" });
  emit({ type: "response.done", response: { status: "completed" } });
  assert.equal((await pending).text, phrase);
});

test("failed notice preparation closes its session and fails visibly instead of delegating silently", async () => {
  const connection = { send() {}, close() { this.closed = true; } };
  const pending = prepareDelegationNotice(connection);
  connection.onmessage({ data: JSON.stringify({ type: "response.done", response: { status: "completed" } }) });
  await assert.rejects(pending, /did not generate/);
  assert.equal(connection.closed, true);
});

test("disconnect cancels notice preparation immediately and closes its voice session", async () => {
  const controller = new AbortController();
  const connection = { send() {}, close() { this.closed = true; } };
  const pending = prepareDelegationNotice(connection, controller.signal);
  controller.abort();
  await assert.rejects(pending, /ended while preparing/);
  assert.equal(connection.closed, true);
  assert.equal(connection.onmessage, null);
});

test("delegated success gives voice a receipt rather than the generated note body", () => {
  const content = "# Long note\n\nContent that should be saved without being read back.";
  const output = toolResultForModel({ ...success, delegated: true, arguments: { title: "Guide", content },
    result: { content: [{ type: "text", text: JSON.stringify({ title: "Guide", content }) }] } });
  assert.equal(JSON.parse(output).title, "Guide");
  assert.equal(JSON.parse(output).completed, true);
  assert.ok(!output.includes(content));
  assert.ok(!output.includes("Content that should be saved"));
});

test("final confirmation forces speech only after tool selection and execution are complete", () => {
  assert.equal(toolContinuation(false).response, undefined);
  assert.deepEqual(toolContinuation(true).response, { tool_choice: "none", output_modalities: ["text", "audio"] });
});
