// Optional browser QA. Pass a locally installed playwright/index.mjs as the first argument.
// Uses an isolated profile and synthetic microphone; never an existing browser profile.
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { chromium } = await import(pathToFileURL(resolve(process.argv[2])).href);
const temporary = join(root, ".tmp");
await mkdir(temporary, { recursive: true });
const profile = await mkdtemp(join(temporary, "browser-"));
const browser = await chromium.launchPersistentContext(profile, {
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
  viewport: { width: 1280, height: 900 },
  env: { PATH: "/usr/bin:/bin", TMPDIR: `${temporary}/` },
  args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
    "--disable-breakpad", "--disable-crash-reporter", `--disk-cache-dir=${profile}/cache`],
});
const url = process.env.LFM3_TEST_URL || "http://localhost:8765";
const events = [];
const errors = [];
try {
  await browser.grantPermissions(["microphone"], { origin: url });
  const page = await browser.newPage();
  await page.addInitScript(() => {
    const start = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (when = 0, ...args) {
      (window.playbackStarts ||= []).push(this.buffer.duration);
      window.playbackEndsAt = performance.now() + (Math.max(0, when - this.context.currentTime) + this.buffer.duration) * 1000;
      return start.call(this, when, ...args);
    };
  });
  let delegatedGenerations = 0;
  await page.route("**/api/delegation/generate", async (route) => {
    const timing = await page.evaluate(() => ({ now: performance.now(), end: window.playbackEndsAt,
      state: document.body.dataset.state }));
    assert.ok(timing.now >= timing.end - 10, "LLM request must wait for scheduled audio playback to finish");
    assert.equal(timing.state, "executing");
    delegatedGenerations++;
    await route.continue();
  });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.routeWebSocket("**/realtime", (ws) => {
    let tools = [], sentCall = false, preparingNotice = false, spokenPhrase = "";
    ws.send(JSON.stringify({ type: "session.created", session: { audio: {
      input: { format: { rate: 16000 } }, output: { format: { rate: 24000 } },
    } } }));
    ws.onMessage((message) => {
      const event = JSON.parse(message);
      events.push(event);
      if (event.type === "session.update") {
        tools = event.session.tools || [];
        preparingNotice = event.session.instructions.startsWith("Perform TTS");
        ws.send(JSON.stringify({ type: "session.updated" }));
      }
      if (event.type === "conversation.item.create" && event.item.type === "message")
        spokenPhrase = event.item.content[0].text;
      if (event.type === "response.create") {
        ws.send(JSON.stringify({ type: "response.created" }));
        if (tools.length && !sentCall) {
          sentCall = true;
          const delegated = tools.length === 1 && tools[0].name === "delegate_local_llm";
          ws.send(JSON.stringify({ type: "response.done", response: { status: "completed", output: [
            { type: "function_call", call_id: "browser-call", name: delegated ? "delegate_local_llm" : "list_notes",
              arguments: delegated ? '{"task":"Create a formatted note using the local LLM"}' : "{}" },
          ] } }));
          return;
        }
        ws.send(JSON.stringify({ type: "response.output_text.delta", delta: preparingNotice ? spokenPhrase
          : event.response?.output_modalities?.[0] === "text" ? "UNWANTED_RECIPE_READBACK: Ingredients and instructions…"
          : "A clear sky is blue. <test>" }));
        ws.send(JSON.stringify({ type: "response.output_audio.delta", delta: Buffer.alloc(preparingNotice ? 28800 : 4800).toString("base64") }));
        ws.send(JSON.stringify({ type: "response.done", response: { status: "completed" } }));
      }
    });
  });
  await page.goto(url);
  await page.getByRole("button", { name: "Connect microphone" }).click();
  await page.waitForFunction(() => document.body.dataset.state === "ready");
  assert.equal(events.filter((event) => event.type === "input_audio_buffer.append").length, 0);
  await page.keyboard.down("Space");
  await page.waitForFunction(() => document.body.dataset.state === "recording");
  await page.waitForTimeout(450);
  const during = events.filter((event) => event.type === "input_audio_buffer.append").length;
  assert.ok(during >= 2, "holding Space streams microphone packets");
  assert.equal(events.filter((event) => event.type === "response.create").length, 0);
  await page.keyboard.up("Space");
  await page.waitForFunction(() => document.body.dataset.state === "ready");
  assert.equal(events.filter((event) => event.type === "response.create").length, 1);
  assert.match(await page.locator(".reply p").innerText(), /<test>/);
  assert.equal(await page.locator(".reply test").count(), 0, "model text never becomes HTML");
  const after = events.length;
  await page.waitForTimeout(250);
  assert.equal(events.length, after, "idle microphone is not forwarded");
  assert.ok(!events.some((event) => event.tools?.length), "no MCP tools are exposed");
  await page.screenshot({ path: join(temporary, "desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(400);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: join(temporary, "mobile.png"), fullPage: true });
  // Pointer capture is released on mouse-up, even when the cursor leaves the button.
  const button = await page.getByRole("button", { name: "Hold to speak" }).boundingBox();
  await page.mouse.move(button.x + 15, button.y + 15);
  await page.mouse.down();
  await page.waitForTimeout(250);
  await page.mouse.move(5, 5);
  await page.mouse.up();
  await page.waitForFunction(() => document.body.dataset.state === "ready");
  assert.equal(events.filter((event) => event.type === "response.create").length, 2);
  await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  assert.equal(await page.locator("body").getAttribute("data-state"), "disconnected");
  await page.getByRole("button", { name: "Connect microphone" }).click();
  await page.waitForFunction(() => document.body.dataset.state === "ready");
  await page.keyboard.down("Space");
  await page.evaluate(() => window.dispatchEvent(new Event("blur")));
  await page.waitForFunction(() => document.body.dataset.state === "disconnected");
  await page.keyboard.up("Space");
  assert.equal(events.filter((event) => event.type === "response.create").length, 2, "lost-focus recording is discarded");
  const denied = await browser.newPage();
  await denied.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => { throw new DOMException("denied", "NotAllowedError"); };
  });
  await denied.goto(url);
  await denied.getByRole("button", { name: "Connect microphone" }).click();
  await denied.waitForFunction(() => document.body.dataset.state === "error");
  assert.match(await denied.locator("#error").innerText(), /permissions/);
  await denied.close();
  if (process.env.LFM3_TEST_MCP_URL) {
    await page.getByRole("button", { name: /^MCP/ }).click();
    await page.getByLabel("MCP server URL").fill(process.env.LFM3_TEST_MCP_URL);
    await page.getByRole("button", { name: "Connect MCP", exact: true }).click();
    await page.waitForFunction(() => document.getElementById("mcp-count").textContent === "2 tools");
    assert.equal(await page.locator(".mcp-tool").count(), 2);
    await page.getByRole("button", { name: "Close MCP settings" }).click();
    await page.getByRole("button", { name: "Connect microphone" }).click();
    await page.waitForFunction(() => document.body.dataset.state === "ready");
    const withTools = events.filter((event) => event.type === "session.update").at(-1).session;
    assert.equal(withTools.tool_choice, "auto");
    assert.equal(withTools.output_modalities, undefined);
    await page.getByRole("button", { name: /^MCP/ }).click();
    assert.equal(await page.getByLabel("MCP server URL").isDisabled(), true);
    await page.getByRole("button", { name: "Close MCP settings" }).click();
    await page.getByRole("button", { name: "Hold to speak" }).focus();
    await page.keyboard.down("Space");
    await page.waitForTimeout(250);
    await page.keyboard.up("Space");
    await page.waitForFunction(() => document.body.dataset.state === "ready");
    assert.equal(await page.locator(".tool-entry[data-status=completed]").count(), 1);
    const toolOutput = events.find((event) => event.type === "conversation.item.create" && event.item.type === "function_call_output");
    assert.equal(toolOutput.item.call_id, "browser-call");
    assert.equal(JSON.parse(toolOutput.item.output).isError, false);
    await page.screenshot({ path: join(temporary, "mcp-activity.png"), fullPage: true });
    await page.getByRole("button", { name: "Disconnect", exact: true }).click();
    await page.getByRole("button", { name: /^MCP/ }).click();
    if (process.env.LFM3_TEST_LLM_URL) {
      await page.getByText("Explicit local LLM delegation", { exact: true }).click();
      await page.getByLabel("LM Studio chat API URL").fill(process.env.LFM3_TEST_LLM_URL);
      await page.getByLabel("Model ID", { exact: true }).fill("fixture-model");
      await page.getByLabel("Target MCP tool").selectOption("create_note");
      await page.getByRole("button", { name: "Save delegation", exact: true }).click();
      await page.waitForFunction(() => document.getElementById("request-path").value === "delegated");
      await page.screenshot({ path: join(temporary, "delegation-settings.png"), fullPage: true });
      await page.getByRole("button", { name: "Close MCP settings" }).click();
      await page.getByRole("button", { name: "Connect microphone" }).click();
      await page.waitForFunction(() => document.body.dataset.state === "ready");
      const session = events.filter((event) => event.type === "session.update").at(-1).session;
      assert.deepEqual(session.tools.map((tool) => tool.name), ["delegate_local_llm"]);
      const playbackCount = await page.evaluate(() => window.playbackStarts.length);
      await page.keyboard.down("Space");
      await page.waitForTimeout(250);
      await page.keyboard.up("Space");
      await page.waitForFunction(() => document.body.dataset.state === "ready");
      assert.equal(await page.locator(".tool-entry[data-status=completed]").count(), 2);
      await page.locator(".tool-entry").last().locator("summary").click();
      const receipt = await page.locator(".tool-entry").last().locator("pre").innerText();
      assert.match(receipt, /executedArguments/);
      assert.match(receipt, /Unicode stays intact/);
      assert.equal(delegatedGenerations, 1);
      assert.match(await page.locator(".reply").last().innerText(), /pass that to the local model/);
      assert.match(await page.locator(".reply").last().innerText(), /Done\. The result is available in/);
      assert.ok(!(await page.locator(".reply").last().innerText()).includes("UNWANTED_RECIPE_READBACK"));
      assert.equal(await page.evaluate(() => window.playbackStarts.length), playbackCount + 2,
        "only announcement and short completion audio should play; model readback must be suppressed");
      assert.equal(events.filter((event) => event.type === "response.create").at(-1).response.tool_choice, "none");
      assert.deepEqual(events.filter((event) => event.type === "response.create").at(-1).response.output_modalities, ["text"]);
      await page.getByRole("button", { name: "Disconnect", exact: true }).click();
      await page.getByRole("button", { name: "Connect microphone" }).click();
      await page.waitForFunction(() => document.body.dataset.state === "ready");
      await page.keyboard.down("Space");
      await page.waitForTimeout(250);
      await page.keyboard.up("Space");
      await page.waitForFunction(() => document.body.dataset.state === "playing");
      await page.getByRole("button", { name: "Disconnect", exact: true }).click();
      await page.waitForTimeout(800);
      assert.equal(delegatedGenerations, 1, "disconnect during notice must not contact LLM");
      assert.equal(await page.locator("body").getAttribute("data-state"), "disconnected");
      await page.getByLabel("Request path").selectOption("direct");
      await page.getByRole("button", { name: /^MCP/ }).click();
      console.log("Delegation UI: prepared notice, waited for playback before LLM, saved exact content, confirmed result; disconnect during notice prevented generation.");
    }
    await page.screenshot({ path: join(temporary, "mcp-settings.png"), fullPage: true });
    await page.getByRole("button", { name: "Disconnect MCP", exact: true }).click();
    await page.waitForFunction(() => document.getElementById("mcp-count").textContent === "Off");
    await page.getByRole("button", { name: "Close MCP settings" }).click();
    console.log("MCP UI: entered URL, discovered tools, called real fixture, returned result to voice, rendered activity, disconnected.");
  }
  if (process.env.LFM3_LIVE_BROWSER === "1") {
    const live = await browser.newPage();
    live.on("pageerror", (error) => errors.push(error.message));
    await live.goto(url);
    await live.getByRole("button", { name: "Connect microphone" }).click();
    await live.waitForFunction(() => document.body.dataset.state === "ready", null, { timeout: 30000 });
    await live.keyboard.down("Space");
    await live.waitForTimeout(1500);
    await live.keyboard.up("Space");
    await live.waitForFunction(() => document.body.dataset.state === "ready", null, { timeout: 90000 });
    assert.ok((await live.locator(".reply p").innerText()).trim());
    await live.getByRole("button", { name: "Disconnect", exact: true }).click();
    await live.close();
    console.log("Live browser: real local model accepted synthetic microphone audio and returned a reply.");
  }
  assert.deepEqual(errors, []);
  console.log("Browser checks passed: Space, pointer release, partial flush, idle silence, reply playback, safe text, disconnect, focus loss, microphone denial, mobile layout.");
} finally {
  await browser.close();
  await rm(profile, { recursive: true, force: true });
}
