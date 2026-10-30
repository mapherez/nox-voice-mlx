import { pcmToBase64, base64ToPCM, microphoneError } from "./audio.js";
import { sessionSettings, ToolRunner, delegatedProfile, delegatedRequest, toolContinuation, prepareDelegationNotice,
  delegationMessages, completedDelegation, silentDelegationCompletion } from "./tools.js";

const el = (id) => document.getElementById(id);
const statuses = {
  disconnected: "Ready to connect", connecting: "Allow microphone access. Connecting…",
  ready: "Ready · hold Space to speak", recording: "Listening · release to send",
  stopping: "Sending your audio…", responding: "Thinking…", playing: "Speaking…", error: "Disconnected",
  executing: "Running an MCP tool…",
};
let state = "disconnected", generation = 0, turn = 0, sentSamples = 0;
let stream, context, capture, input, silent, analyser, socket;
let inputRate = 16000, outputRate = 24000, nextPlayback = 0;
let responseDone = false, responseText, responseArticle, turnTimer, watchdog;
let holdOwner = null;
let mcpProfile = { connected: false, tools: [] }, mcpBusy = true, toolRunner;
let delegationSettings = { configured: false };
let delegationSpeech, noticePlayback, noticePreparation, finishingDelegation = false;
const sources = new Set();
const waveform = new Float32Array(256);

function setState(value) {
  state = value;
  document.body.dataset.state = value;
  el("status").textContent = statuses[value];
  el("connect").hidden = !["disconnected", "error", "connecting"].includes(value);
  el("connect").disabled = value === "connecting";
  el("disconnect").hidden = ["disconnected", "error"].includes(value);
  el("talk").disabled = !["ready", "recording"].includes(value);
  el("talk").setAttribute("aria-pressed", String(value === "recording"));
  el("hint").textContent = value === "recording" ? "Release Space or the button to send. Maximum 60 seconds per turn."
    : ["responding", "stopping", "playing", "executing"].includes(value) ? "One turn at a time. Wait for the reply before speaking again."
    : value === "ready" ? "Hold Space, or hold the button with your mouse or touch."
    : "Microphone permission is requested when you connect.";
  refreshMCPControls();
}

async function api(path, body) {
  const response = await fetch(path, body === undefined ? { cache: "no-store" } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The MCP request failed.");
  return result;
}

function refreshMCPControls() {
  const voiceActive = !["disconnected", "error"].includes(state);
  for (const id of ["mcp-url", "mcp-connect", "mcp-disconnect"])
    el(id).disabled = mcpBusy || voiceActive;
  for (const id of ["llm-url", "llm-model", "llm-target", "llm-save"])
    el(id).disabled = mcpBusy || voiceActive || !mcpProfile.connected;
  el("request-path").disabled = mcpBusy || voiceActive;
  const canDelegate = delegationSettings.configured && mcpProfile.connected && delegationSettings.connectionId === mcpProfile.id;
  el("request-path").querySelector('[value="delegated"]').disabled = !canDelegate;
  if (!canDelegate) el("request-path").value = "direct";
  el("connect").disabled = mcpBusy || state === "connecting";
  el("mcp-hint").textContent = voiceActive
    ? "Disconnect the microphone before changing MCP tools. An action already sent may still finish after disconnecting."
    : "Connect MCP before connecting the microphone. Configuration stays in memory until the experiment stops.";
}

function renderMCP() {
  el("mcp-count").textContent = mcpProfile.connected ? `${mcpProfile.tools.length} tools` : "Off";
  el("mcp-disconnect").hidden = !mcpProfile.connected;
  el("mcp-connect").textContent = mcpBusy ? "Connecting…" : "Connect MCP";
  el("mcp-status").textContent = mcpProfile.connected
    ? `${mcpProfile.name} · ${mcpProfile.tools.length} tools discovered` : "No MCP server connected.";
  if (mcpProfile.connected) el("mcp-url").value = mcpProfile.url;
  el("mcp-tools").replaceChildren();
  for (const tool of mcpProfile.tools) {
    const details = document.createElement("details"); details.className = "mcp-tool";
    const summary = document.createElement("summary"); summary.textContent = tool.name;
    const text = document.createElement("p"); text.textContent = tool.description || "No description provided.";
    details.append(summary, text); el("mcp-tools").append(details);
  }
  const target = el("llm-target").value || delegationSettings.target;
  el("llm-target").replaceChildren();
  const placeholder = document.createElement("option"); placeholder.value = "";
  placeholder.textContent = mcpProfile.connected ? "Select the creation tool" : "Connect MCP first";
  el("llm-target").append(placeholder);
  for (const tool of mcpProfile.tools) {
    const option = document.createElement("option"); option.value = tool.function.name; option.textContent = tool.name;
    el("llm-target").append(option);
  }
  if ([...el("llm-target").options].some((option) => option.value === target)) el("llm-target").value = target;
  else if (mcpProfile.tools.some((tool) => tool.name === "create_note"))
    el("llm-target").value = mcpProfile.tools.find((tool) => tool.name === "create_note").function.name;
  renderDelegation();
  refreshMCPControls();
}

function renderDelegation() {
  el("llm-status").textContent = delegationSettings.configured
    ? `${delegationSettings.model} → ${delegationSettings.targetName}. No automatic routing.` : "Not configured.";
  if (delegationSettings.configured) {
    el("llm-url").value = delegationSettings.url;
    el("llm-model").value = delegationSettings.model;
    el("llm-target").value = delegationSettings.target;
  }
}

function reportTool(record, entry) {
  if (!entry) {
    document.querySelector(".tool-history").hidden = false;
    entry = document.createElement("details"); entry.className = "tool-entry";
    const summary = document.createElement("summary");
    const name = document.createElement("span"); name.textContent = record.name;
    const status = document.createElement("span"); status.className = "tool-state";
    summary.append(name, status);
    const body = document.createElement("pre");
    entry.append(summary, body); el("tool-history").append(entry);
    entry.arguments = record.arguments;
    while (el("tool-history").children.length > 30) el("tool-history").firstElementChild.remove();
  }
  entry.dataset.status = record.status;
  entry.querySelector(".tool-state").textContent = record.status === "unknown" ? "OUTCOME UNKNOWN · NO RETRY"
    : record.phase ? record.phase.toUpperCase() : record.status.toUpperCase();
  const text = JSON.stringify({ arguments: entry.arguments, ...(record.delegated ? { model: record.model, executedArguments: record.arguments } : {}),
    ...(record.result ? { result: record.result } : {}) }, null, 2);
  entry.querySelector("pre").textContent = text.length > 16000 ? text.slice(0, 16000) + "\n[Display truncated]" : text;
  if (record.status === "running") {
    armWatchdog(record.phase === "generating" ? 180000 : 90000);
    if (record.phase) el("status").textContent = record.phase === "announcing" ? "Announcing delegation…"
      : record.phase === "generating" ? "Generating with local LLM…" : "Saving through MCP…";
  }
  return entry;
}

function send(event) {
  if (socket?.readyState !== WebSocket.OPEN) throw new Error("The model connection closed. Connect again.");
  socket.send(JSON.stringify(event));
}

function disconnect() {
  generation++;
  noticePreparation?.abort(); noticePreparation = null;
  noticePlayback?.reject(new Error("Conversation ended during the notice. No LLM request was sent."));
  noticePlayback = null;
  delegationSpeech = null;
  finishingDelegation = false;
  holdOwner = null;
  clearTimeout(turnTimer);
  clearTimeout(watchdog);
  if (socket) { socket.onmessage = null; socket.onclose = null; socket.close(); }
  socket = null;
  capture?.disconnect(); capture?.port.close(); capture = null;
  input?.disconnect(); input = null;
  silent?.disconnect(); silent = null;
  stream?.getTracks().forEach((track) => track.stop()); stream = null;
  for (const source of sources) { source.onended = null; source.stop(); source.disconnect(); }
  sources.clear();
  analyser?.disconnect(); analyser = null;
  if (context) { context.onstatechange = null; void context.close().catch(() => {}); }
  context = null;
  nextPlayback = 0;
  responseDone = false;
  responseText = null;
  toolRunner = null;
  setState("disconnected");
}

function fail(message) {
  disconnect();
  el("error").textContent = message;
  el("error").hidden = false;
  setState("error");
}

function armWatchdog(timeout = 90000) {
  clearTimeout(watchdog);
  watchdog = setTimeout(() => fail("The model stopped responding. Disconnect and start a new conversation."), timeout);
}

function finishReply() {
  if (noticePlayback && sources.size === 0) {
    const pending = noticePlayback;
    noticePlayback = null;
    pending.resolve();
    return;
  }
  if (responseDone && sources.size === 0 && ["responding", "playing"].includes(state)) {
    clearTimeout(watchdog);
    if (!responseText?.textContent)
      responseText.textContent = "The model returned no text. Its audio, if any, was played above.";
    setState("ready");
  }
}

async function playDelegationSpeech(kind, current) {
  const notice = delegationSpeech?.[kind];
  if (current !== generation || !notice || !context || context.state !== "running")
    throw new Error("The spoken message is unavailable. Reconnect voice and check Tool activity for any completed action.");
  responseText.textContent += `${notice.text}\n\n`;
  await new Promise((resolve, reject) => {
    noticePlayback = { resolve, reject };
    try {
      for (const chunk of notice.chunks) play(chunk);
      if (!sources.size) throw new Error("The spoken notice contained no playable audio.");
    } catch (error) { noticePlayback = null; reject(error); }
  });
  if (current !== generation) throw new Error("Conversation ended during playback.");
}

async function announceDelegation(current) {
  await playDelegationSpeech("announcement", current);
  setState("executing");
}

function play(encoded) {
  const samples = base64ToPCM(encoded);
  if (!samples.length) return;
  if (nextPlayback - context.currentTime > 60)
    throw new Error("Audio playback fell behind. Connect again to start a fresh conversation.");
  const buffer = context.createBuffer(1, samples.length, outputRate);
  buffer.copyToChannel(samples, 0);
  const source = context.createBufferSource();
  source.buffer = buffer;
  source.connect(analyser);
  sources.add(source);
  source.onended = () => { sources.delete(source); source.disconnect(); finishReply(); };
  nextPlayback = Math.max(context.currentTime + 0.05, nextPlayback);
  source.start(nextPlayback);
  nextPlayback += buffer.duration;
  setState("playing");
}

async function onEvent(event, current) {
  if (event.type === "error") throw new Error(event.error?.message || "The model returned an error.");
  if (["response.created", "response.output_audio.delta", "response.output_text.delta"].includes(event.type)) armWatchdog();
  if (event.type === "response.output_audio.delta") { if (!finishingDelegation) play(event.delta); }
  else if (event.type === "response.output_text.delta") {
    if (typeof event.delta !== "string") throw new Error("Invalid text response.");
    if (!finishingDelegation) responseText.textContent += event.delta;
    el("replies").scrollTop = el("replies").scrollHeight;
  } else if (event.type === "response.done") {
    const response = event.response || {};
    if (response.status === "failed") throw new Error(response.status_details?.error?.message || "The model could not finish its reply.");
    const calls = Array.isArray(response.output) ? response.output.filter((item) => item.type === "function_call") : [];
    if (calls.length) {
      const runner = toolRunner;
      if (!runner || runner.forceFinal || calls.length > 32) throw new Error("The model could not finish its tool request. Start a new conversation.");
      setState("executing"); armWatchdog();
      const outputs = await runner.run(calls);
      if (current !== generation || !outputs) return;
      for (const output of outputs) send({ type: "conversation.item.create", item: output });
      if (completedDelegation(runner.profile, outputs)) {
        finishingDelegation = true;
        send(silentDelegationCompletion());
        await playDelegationSpeech("completion", current);
        if (current === generation) finishReply();
        return;
      }
      send(toolContinuation(runner.forceFinal));
      setState("responding"); armWatchdog();
      return;
    }
    responseDone = true;
    clearTimeout(watchdog);
    finishReply();
  }
}

async function connect() {
  if (mcpBusy) return;
  disconnect();
  const current = generation;
  let profile;
  el("error").hidden = true;
  setState("connecting");
  try {
    profile = JSON.parse(JSON.stringify(el("request-path").value === "delegated"
      ? delegatedProfile(mcpProfile, delegationSettings) : mcpProfile));
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia)
      throw new Error("Open this experiment at http://localhost to enable microphone access.");
    context = new AudioContext();
    const audioContext = context;
    const resumed = audioContext.resume();
    const acquired = await navigator.mediaDevices.getUserMedia({ audio: {
      channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
    }, video: false });
    if (current !== generation) { acquired.getTracks().forEach((track) => track.stop()); return; }
    stream = acquired;
    await resumed;
    if (current !== generation) return;
    for (const track of stream.getAudioTracks())
      track.onended = () => { if (current === generation) fail("The microphone was disconnected. Select an available input and connect again."); };
    await audioContext.audioWorklet.addModule("/capture.js");
    if (current !== generation) return;
    capture = new AudioWorkletNode(audioContext, "push-to-talk");
    silent = audioContext.createGain(); silent.gain.value = 0;
    input = audioContext.createMediaStreamSource(stream);
    input.connect(capture).connect(silent).connect(audioContext.destination);
    analyser = audioContext.createAnalyser(); analyser.fftSize = 512; analyser.connect(audioContext.destination);
    capture.port.onmessage = ({ data }) => {
      if (current !== generation || data.turn !== turn) return;
      try {
        if (data.type === "audio" && ["recording", "stopping"].includes(state)) {
          if (socket.bufferedAmount > 256000) throw new Error("The audio connection cannot keep up. Connect again.");
          sentSamples += data.samples.length;
          send({ type: "input_audio_buffer.append", audio: pcmToBase64(data.samples) });
        } else if (data.type === "stopped" && state === "stopping") {
          if (!sentSamples) { setState("ready"); return; }
          newReply();
          send({ type: "response.create" });
          setState("responding"); armWatchdog();
        }
      } catch (error) { fail(error.message); }
    };
    if (profile.delegation) {
      const speech = {};
      for (const [kind, phrase] of Object.entries(delegationMessages(profile))) {
        el("status").textContent = kind === "announcement" ? "Preparing spoken delegation notice…" : "Preparing short completion message…";
        socket = new WebSocket(new URL("/realtime", location.href).href.replace(/^http/, "ws"));
        noticePreparation = new AbortController();
        speech[kind] = await prepareDelegationNotice(socket, noticePreparation.signal, phrase);
        if (current !== generation) return;
        noticePreparation = null;
      }
      delegationSpeech = speech;
    }
    toolRunner = new ToolRunner(profile, (body, phase) => body.kind === "delegation"
      ? delegatedRequest(body, api, () => current === generation, phase, () => announceDelegation(current)) : api("/api/mcp/call", body), reportTool,
      () => current === generation);
    socket = new WebSocket(new URL("/realtime", location.href).href.replace(/^http/, "ws"));
    const connection = socket;
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("The model connection timed out. Check the experiment terminal.")), 30000);
      const rejectConnection = (message) => { clearTimeout(timeout); reject(new Error(message)); };
      connection.onerror = () => rejectConnection("Could not connect to the local model.");
      connection.onclose = () => rejectConnection("The model connection closed. Connect again.");
      connection.onmessage = ({ data }) => {
        if (current !== generation) return;
        try {
          const event = JSON.parse(data);
          if (event.type === "error") throw new Error(event.error?.message || "The model is unavailable.");
          if (event.type === "session.created") {
            inputRate = event.session.audio.input.format.rate;
            outputRate = event.session.audio.output.format.rate;
            if (inputRate !== 16000 || outputRate !== 24000) throw new Error("The runtime returned an unexpected audio format.");
            send({ type: "session.update", session: sessionSettings(profile) });
          } else if (event.type === "session.updated") {
            clearTimeout(timeout);
            connection.onmessage = ({ data }) => {
              if (current !== generation) return;
              try {
                onEvent(JSON.parse(data), current).catch((error) => { if (current === generation) fail(error.message); });
              } catch (error) { fail(error.message); }
            };
            connection.onclose = () => { if (current === generation) fail("The model connection closed. Connect again to start a new conversation."); };
            connection.onerror = () => { if (current === generation) fail("The model connection failed. Connect again."); };
            resolve();
          }
        } catch (error) { clearTimeout(timeout); reject(error); }
      };
    });
    if (current !== generation) return;
    audioContext.onstatechange = () => {
      if (current === generation && audioContext.state === "suspended")
        fail("Browser audio was suspended. Return to this page and connect again.");
    };
    setState("ready"); el("talk").focus();
  } catch (error) { if (current === generation) fail(microphoneError(error)); }
}

function newReply() {
  finishingDelegation = false;
  responseDone = false;
  el("replies").querySelector(".empty")?.remove();
  responseArticle = document.createElement("article"); responseArticle.className = "reply";
  const label = document.createElement("span"); label.className = "reply-label"; label.textContent = "ASSISTANT";
  responseText = document.createElement("p");
  responseArticle.append(label, responseText); el("replies").append(responseArticle);
  while (el("replies").children.length > 20) el("replies").firstElementChild.remove();
}

function beginHold(owner) {
  if (state !== "ready") return;
  holdOwner = owner;
  turn++; sentSamples = 0;
  toolRunner?.beginTurn();
  capture.port.postMessage({ type: "start", turn, rate: inputRate });
  setState("recording");
  turnTimer = setTimeout(endHold, 60000);
}

function endHold() {
  holdOwner = null;
  if (state !== "recording") return;
  clearTimeout(turnTimer);
  setState("stopping");
  capture.port.postMessage({ type: "stop", turn });
}

el("connect").addEventListener("click", connect);
el("mcp-toggle").addEventListener("click", () => el("mcp-panel").showModal());
el("mcp-close").addEventListener("click", () => el("mcp-panel").close());
el("mcp-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (mcpBusy || !["disconnected", "error"].includes(state)) return;
  const url = el("mcp-url").value.trim();
  mcpBusy = true; el("mcp-error").hidden = true; renderMCP();
  try { mcpProfile = await api("/api/mcp/connect", { url }); delegationSettings = { configured: false }; }
  catch (error) { el("mcp-error").textContent = error.message; el("mcp-error").hidden = false; }
  finally { mcpBusy = false; renderMCP(); }
});
el("mcp-disconnect").addEventListener("click", async () => {
  if (mcpBusy || !["disconnected", "error"].includes(state)) return;
  mcpBusy = true; el("mcp-error").hidden = true; refreshMCPControls();
  try { mcpProfile = await api("/api/mcp/disconnect", {}); delegationSettings = { configured: false }; }
  catch (error) { el("mcp-error").textContent = error.message; el("mcp-error").hidden = false; }
  finally { mcpBusy = false; renderMCP(); }
});
el("llm-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (mcpBusy || !["disconnected", "error"].includes(state) || !mcpProfile.connected) return;
  const body = { url: el("llm-url").value.trim(), model: el("llm-model").value.trim(),
    connectionId: mcpProfile.id, target: el("llm-target").value };
  mcpBusy = true; el("llm-error").hidden = true; refreshMCPControls();
  try {
    delegationSettings = await api("/api/delegation/configure", body);
    el("request-path").value = "delegated";
  } catch (error) { el("llm-error").textContent = error.message; el("llm-error").hidden = false; }
  finally { mcpBusy = false; renderDelegation(); refreshMCPControls(); }
});
el("disconnect").addEventListener("click", disconnect);
el("talk").addEventListener("pointerdown", (event) => {
  if (event.button !== 0 || state !== "ready") return;
  event.preventDefault(); el("talk").setPointerCapture(event.pointerId); beginHold("pointer");
});
for (const name of ["pointerup", "pointercancel", "lostpointercapture"])
  el("talk").addEventListener(name, () => { if (holdOwner === "pointer") endHold(); });
window.addEventListener("keydown", (event) => {
  if (event.code !== "Space" || event.repeat || event.ctrlKey || event.metaKey || event.altKey
      || event.target.closest("input, textarea, select, [contenteditable], a")
      || (event.target.closest("button") && event.target !== el("talk"))) return;
  event.preventDefault(); beginHold("keyboard");
});
window.addEventListener("keyup", (event) => {
  if (event.code === "Space" && holdOwner === "keyboard") { event.preventDefault(); endHold(); }
});
// Losing focus cancels the session rather than leaving a microphone turn open.
window.addEventListener("blur", () => { if (state === "recording") disconnect(); });
document.addEventListener("visibilitychange", () => { if (document.hidden) disconnect(); });
window.addEventListener("pagehide", disconnect);

function animate() {
  let energy = 0;
  if (analyser && state === "playing") {
    analyser.getFloatTimeDomainData(waveform);
    for (const value of waveform) energy += value * value;
    energy = Math.min(1, Math.sqrt(energy / waveform.length) * 6);
  }
  document.querySelector(".orb").style.setProperty("--energy", String(energy));
  requestAnimationFrame(animate);
}
setState("disconnected"); renderMCP(); animate();
Promise.all([api("/api/mcp"), api("/api/delegation")]).then(([profile, delegation]) => {
  mcpProfile = profile; delegationSettings = delegation; renderMCP();
})
  .catch((error) => { el("mcp-error").textContent = error.message; el("mcp-error").hidden = false; })
  .finally(() => { mcpBusy = false; renderMCP(); });
