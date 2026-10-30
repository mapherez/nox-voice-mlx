// Settings QA against a disposable preview; never an existing browser profile.
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const { chromium } = await import(pathToFileURL(resolve(process.argv[2])).href);
const url = process.argv[3];
const frontendUrl = process.argv[4] || url;
const temporary = resolve(".tmp");
await mkdir(temporary, { recursive: true });
const profile = await mkdtemp(join(temporary, "provider-browser-"));
const browser = await chromium.launchPersistentContext(profile, {
  executablePath:
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
  reducedMotion: "reduce",
  hasTouch: true,
  viewport: { width: 1280, height: 900 },
  env: { PATH: "/usr/bin:/bin", TMPDIR: `${temporary}/` },
  args: [
    "--disable-breakpad",
    "--disable-crash-reporter",
    "--use-fake-device-for-media-stream",
    "--use-fake-ui-for-media-stream",
    `--disk-cache-dir=${profile}/cache`,
  ],
});
const errors = [];
try {
  const page = await browser.newPage();
  if (frontendUrl !== url) {
    // Keep the dev server's real TSX/HMR/StrictMode path, using only fixture APIs.
    await page.route("**/api/**", async route => {
      const request = new URL(route.request().url());
      const response = await route.fetch({ url: url + request.pathname + request.search });
      await route.fulfill({ response });
    });
  }
  const audioMessages = [];
  let sequence = 0;
  await page.routeWebSocket("**/voice/v1/ws", (wire) => {
    const emit = (value) => wire.send(JSON.stringify({ sessionId: "browser-fixture", sequence: ++sequence, ...value }));
    wire.onMessage((raw) => {
      const value = JSON.parse(String(raw));
      audioMessages.push(value);
      if (value.type === "session.start") emit({ type: "session.ready", version: 1, audio: { inputRate: 16000, outputRate: 24000, format: "pcm16le" } });
      else if (value.type === "turn.start") emit({ type: "turn.started", turnId: value.turnId });
      else if (value.type === "turn.commit") emit({ type: "transcript.completed", turnId: value.turnId, text: "Find all matching resources and delete the results." });
      else if (value.type === "speech.request") {
        emit({ type: "speech.started", playbackId: value.playbackId });
        emit({ type: "speech.audio", playbackId: value.playbackId, audio: Buffer.alloc(3840).toString("base64") });
      } else if (value.type === "speech.interrupt") {
        emit({ type: "speech.interrupted", playbackId: value.playbackId });
        // A delayed frame from the old playback must never resume that speech.
        emit({ type: "speech.audio", playbackId: value.playbackId, audio: Buffer.alloc(3840).toString("base64") });
      }
    });
  });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(frontendUrl);
  await page.locator("#open-settings").click();
  await page.waitForFunction(() => !document.getElementById("save-settings").disabled);
  assert.equal(await page.locator('[role="tab"]').count(), 4);
  assert.equal(await page.locator("#model-provider option").count(), 2);
  await page.screenshot({ path: join(temporary, "settings-models-desktop.png"), fullPage: true });
  // Native folder action calls the controller; there is no web file explorer.
  await page.locator("#open-model-folder").click();
  await page.waitForFunction(() => document.getElementById("model-feedback").textContent.includes("Finder"));
  await page.locator("#tab-credentials").click();
  await page.locator("#credential-huggingface").fill("hf_disposableBrowserFixture123");
  await page.locator("#credential-save-huggingface").click();
  await page.waitForFunction(() => document.getElementById("credential-state-huggingface").textContent === "Token saved");
  assert.equal(await page.locator("#credential-huggingface").inputValue(), "");
  assert.equal((await page.locator("#panel-credentials").innerText()).includes("disposableBrowserFixture"), false);
  await page.locator("#credential-test-huggingface").click();
  await page.waitForFunction(() => document.getElementById("credential-feedback-huggingface").textContent.includes("confirmed"));
  await page.screenshot({ path: join(temporary, "settings-credentials-desktop.png"), fullPage: true });
  await page.locator("#credential-remove-huggingface").click();
  await page.waitForFunction(() => document.getElementById("credential-state-huggingface").textContent === "Not configured");
  await page.locator("#tab-voice").click();
  assert.equal(await page.locator("#voice option").count(), 18);
  assert.equal(await page.locator("#quantization option").count(), 5);
  await page.locator("#persona").fill("A concise persona entered through the main webapp.");
  // Controller polling cannot replace a focused draft or move its caret.
  const caret = await page.locator("#persona").evaluate((input) => input.selectionStart);
  await page.waitForResponse(response => response.url().endsWith("/api/voice/v1/models") && response.request().method() === "GET");
  assert.equal(await page.locator("#persona").inputValue(), "A concise persona entered through the main webapp.");
  assert.equal(await page.locator("#persona").evaluate((input) => input.selectionStart), caret);
  assert.equal(await page.evaluate(() => document.activeElement.id), "persona");
  await page.locator("#save-settings").click();
  await page.waitForFunction(() => document.getElementById("settings-feedback").textContent.includes("saved"));
  const value = await (await page.request.get(`${url}/api/voice/v1/settings`)).json();
  assert.equal(value.profiles.personaplex.prompt, "A concise persona entered through the main webapp.");
  value.profiles["lfm3-audio"].voice = "male";
  await page.request.put(`${url}/api/voice/v1/settings`, { data: value });
  await page.locator("#persona").fill("Keep this edit while showing a revision conflict.");
  await page.locator("#save-settings").click();
  await page.locator("#reload-settings").waitFor({ state: "visible" });
  assert.equal(await page.locator("#persona").inputValue(), "Keep this edit while showing a revision conflict.");
  await page.locator("#reload-settings").click();
  await page.waitForFunction(() => document.getElementById("persona").value === "A concise persona entered through the main webapp.");
  await page.screenshot({ path: join(temporary, "settings-voice-desktop.png"), fullPage: true });
  await page.locator("#tab-models").click();
  await page.locator("#model-provider").selectOption("lfm3-audio");
  assert.equal(await page.locator("#start-engine").isDisabled(), true);
  // Browsing/downloading a model does not change the saved selection or load inference.
  assert.equal((await (await page.request.get(`${url}/api/voice/v1/settings`)).json()).selectedProvider, "personaplex");
  await page.locator("#prepare-model").click();
  await page.locator("#downloads").waitFor({ state: "visible" });
  assert.equal(await page.locator("#open-downloads").getAttribute("data-state"), "active");
  assert.equal(await page.locator("#clear-downloads").isDisabled(), true);
  await page.screenshot({ path: join(temporary, "downloads-active-desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "Cancel download" }).first().click();
  await page.getByRole("button", { name: "Retry / resume" }).first().waitFor();
  await page.getByRole("button", { name: "Retry / resume" }).first().click();
  await page.locator("#clear-downloads").click();
  await page.waitForFunction(() => document.querySelectorAll(".download-job").length === 1);
  const remaining = await (await page.request.get(`${url}/api/voice/v1/downloads`)).json();
  assert.equal(remaining.jobs.length, 1);
  assert.equal(remaining.jobs[0].state, "downloading");
  assert.equal(await page.locator("#open-downloads").getAttribute("data-state"), "active");
  await page.locator("#close-downloads").click();
  await page.waitForFunction(() => document.getElementById("model-local-state").textContent === "Verified on disk");
  await page.locator("#manage-downloads").click();
  // A proxy/static server returning HTML must leave history intact and show
  // the actual HTTP failure instead of leaking a JSON parser exception.
  const clearPath = "**/api/voice/v1/downloads";
  const htmlFailure = async (route) => {
    if (route.request().method() === "DELETE")
      await route.fulfill({ status: 405, contentType: "text/html", body: "<!DOCTYPE html><h1>Method not allowed</h1>" });
    else await route.fallback();
  };
  await page.route(clearPath, htmlFailure);
  await page.locator("#clear-downloads").click();
  await page.waitForFunction(() => document.getElementById("downloads-feedback").textContent.includes("HTTP 405"));
  assert.equal(await page.locator(".download-job").count(), 1);
  assert.doesNotMatch(await page.locator("#downloads-feedback").innerText(), /Unexpected token|DOCTYPE/);
  await page.unroute(clearPath, htmlFailure);
  await page.locator("#clear-downloads").click();
  await page.waitForFunction(() => document.querySelectorAll(".download-job").length === 0);
  assert.deepEqual((await (await page.request.get(`${url}/api/voice/v1/downloads`)).json()).jobs, []);
  assert.equal((await (await page.request.get(`${url}/api/voice/v1/models`)).json()).models.find((model) => model.id === "lfm3-audio").localState, "verified");
  assert.equal(await page.locator("#open-downloads").getAttribute("data-state"), "idle");
  await page.locator("#close-downloads").click();
  assert.equal((await (await page.request.get(`${url}/api/engine/status`)).json()).state, "stopped");
  await page.locator("#tab-voice").click();
  assert.equal(await page.locator("#precision-settings").isVisible(), false);
  assert.equal(await page.locator("#transcription-settings").isVisible(), true);
  await page.locator("#input-language").selectOption("Portuguese");
  await page.locator("#vocabulary").fill("Names supplied by the user");
  await page.locator("#save-settings").click();
  await page.waitForFunction(() => document.getElementById("settings-feedback").textContent.includes("saved"));
  assert.equal((await (await page.request.get(`${url}/api/engine/status`)).json()).state, "stopped");
  await page.setViewportSize({ width: 390, height: 844 });
  for (const tab of ["models", "voice", "credentials", "general"]) {
    await page.locator(`#tab-${tab}`).click();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.ok(await page.locator("#settings").evaluate((el) => el.scrollWidth <= el.clientWidth));
    await page.screenshot({ path: join(temporary, `settings-${tab}-mobile.png`), fullPage: true });
  }
  await page.locator("#tab-models").click();
  await page.locator("#start-engine").click();
  await page.waitForFunction(() => document.getElementById("header-state").textContent === "Model loaded");
  assert.equal(await page.locator("#loaded-model-name").textContent(), "LFM3-Audio-2B");
  await page.locator("#close-settings").click();
  assert.equal(await page.locator("#start-conversation-label").innerText(), "Check audio");
  await page.locator("#start-conversation").click();
  await page.locator("#audio-checks").waitFor({ state: "visible" });
  // Compact phones/tablets get touch wording, without a keyboard assumption.
  assert.equal(await page.locator("#hold-to-talk").textContent(), "Press here to speak");
  assert.doesNotMatch(await page.locator("#conversation-subtitle").innerText(), /Space/);
  const cdp = await browser.newCDPSession(page);
  await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: false });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.waitForFunction(() => document.getElementById("hold-to-talk").textContent === "Hold Space or press here to speak");
  await page.locator("#hold-to-talk").focus();
  await page.keyboard.down("Space");
  await page.waitForFunction(() => document.getElementById("hold-to-talk").getAttribute("aria-pressed") === "true");
  await page.waitForTimeout(350);
  await page.keyboard.up("Space");
  await page.waitForFunction(() => document.getElementById("transcript").textContent.startsWith("Find all"));
  assert.ok(audioMessages.some((message) => message.type === "input.audio"));
  const lastAudio = audioMessages.findLastIndex((message) => message.type === "input.audio");
  const firstCommit = audioMessages.findIndex((message) => message.type === "turn.commit");
  assert.ok(lastAudio < firstCommit);
  await page.locator("#speech-text").fill("An audio check without executing any tools.");
  await page.locator("#read-aloud").click();
  await page.waitForFunction(() => !document.getElementById("stop-speech").disabled);
  // Keep focus where a real click leaves it: Space must interrupt/capture,
  // never reactivate Read aloud on keydown, repeat or keyup.
  assert.equal(await page.evaluate(() => document.activeElement.id), "read-aloud");
  const speechRequests = audioMessages.filter((message) => message.type === "speech.request").length;
  const turnsBeforeInterrupt = audioMessages.filter((message) => message.type === "turn.start").length;
  await page.keyboard.down("Space");
  await page.waitForFunction(() => document.getElementById("hold-to-talk").getAttribute("aria-pressed") === "true");
  await page.keyboard.down("Space"); // Repeat while held must not create a second turn.
  await page.waitForTimeout(150);
  await page.keyboard.up("Space");
  await page.waitForFunction(() => document.getElementById("audio-check-state").textContent === "Ready to listen");
  assert.ok(audioMessages.some((message) => message.type === "speech.interrupt"));
  assert.equal(audioMessages.filter((message) => message.type === "speech.request").length, speechRequests);
  assert.equal(audioMessages.filter((message) => message.type === "turn.start").length, turnsBeforeInterrupt + 1);
  // Text fields retain normal typing: Space must not start another audio turn.
  const turns = audioMessages.filter((message) => message.type === "turn.start").length;
  await page.locator("#speech-text").focus();
  const textBeforeSpace = await page.locator("#speech-text").inputValue();
  await page.keyboard.press("Space");
  assert.equal(await page.locator("#speech-text").inputValue(), textBeforeSpace + " ");
  assert.equal(audioMessages.filter((message) => message.type === "turn.start").length, turns);
  // Modal controls retain normal Space activation while audio is connected.
  await page.locator("#open-downloads").click();
  await page.locator("#close-downloads").focus();
  await page.keyboard.press("Space");
  await page.locator("#downloads").waitFor({ state: "hidden" });
  assert.equal(audioMessages.filter((message) => message.type === "turn.start").length, turns);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.getElementById("hold-to-talk").textContent === "Press here to speak");
  const rect = await page.locator("#hold-to-talk").boundingBox();
  await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  const touchPoints = [{ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 }];
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints });
  await page.waitForTimeout(200);
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await page.waitForFunction(() => document.getElementById("audio-check-state").textContent === "Ready to listen");
  assert.equal(audioMessages.filter((message) => message.type === "turn.start").length, turns + 1);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.locator("#read-aloud").click();
  await page.waitForFunction(() => document.getElementById("audio-check-state").textContent.includes("Reading aloud"));
  assert.doesNotMatch(await page.locator("#audio-check-state").innerText(), /Space/);
  await page.locator("#stop-speech").click();
  await page.screenshot({ path: join(temporary, "providers-lfm3-audio-mobile.png"), fullPage: true });
  // Landscape tablets can be wider than the compact breakpoint and still use touch.
  await page.setViewportSize({ width: 1180, height: 820 });
  await page.waitForFunction(() => matchMedia("(pointer: coarse)").matches);
  assert.equal(await page.locator("#hold-to-talk").textContent(), "Press here to speak");
  assert.doesNotMatch(await page.locator("#conversation-subtitle").innerText(), /Space/);
  assert.equal(audioMessages.filter(message => message.type === "session.start").length, 1);
  await page.locator("#end-conversation").click();
  await page.locator("#open-settings").click();
  await page.locator("#tab-models").click();
  await page.locator("#stop-engine").click();
  await page.locator("#model-provider").selectOption("personaplex");
  await page.locator("#tab-voice").click();
  await page.locator("#save-settings").click();
  await page.waitForFunction(() => document.getElementById("settings-feedback").textContent.includes("saved"));

  // Roving tab focus and native dialog dismissal remain keyboard accessible.
  await page.locator("#tab-voice").focus();
  await page.keyboard.press("ArrowRight");
  assert.equal(await page.locator("#tab-credentials").getAttribute("aria-selected"), "true");
  assert.equal(await page.evaluate(() => document.activeElement.id), "tab-credentials");
  await page.keyboard.press("End");
  assert.equal(await page.locator("#tab-general").getAttribute("aria-selected"), "true");
  await page.keyboard.press("Escape");
  await page.locator("#settings").waitFor({ state: "hidden" });
  assert.equal(await page.evaluate(() => document.activeElement.id), "open-settings");
  await page.locator("#open-settings").click();
  await page.locator("#tab-general").click();
  await page.locator("#theme").selectOption("dark");
  await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
  await page.screenshot({ path: join(temporary, "settings-general-dark-tablet.png"), fullPage: true });
  assert.equal(await page.evaluate(() => localStorage.getItem("nox-voice-theme")), "dark");
  assert.equal(await page.evaluate(() => Object.values(localStorage).some(value => value.includes("disposableBrowserFixture"))), false);
  await page.reload();
  await page.locator("#open-settings").click();
  await page.locator("#tab-general").click();
  assert.equal(await page.locator("#theme").inputValue(), "dark");
  await page.locator("#theme").selectOption("system");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForFunction(() => !document.documentElement.dataset.theme && getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() === "#0b1018" && document.querySelector('meta[name="theme-color"]').content === "#0b1018");
  assert.equal(await page.locator('meta[name="theme-color"]').getAttribute("content"), "#0b1018");
  await page.locator("#theme").selectOption("light");

  assert.deepEqual(errors, []);
  console.log("Settings QA passed: tabs, credentials, Finder action, downloads/cancel/resume, Load/Eject, profile conflict, mobile layout and LFM3 Space/touch interruption.");
} finally {
  await browser.close();
  await rm(profile, { recursive: true, force: true });
}
