import "./style.css";
import { ConversationAudio } from "./audio";
import { LFM3Audio } from "./lfm3-audio";
import { VoiceOrb } from "./orb";
import { ModelManagement, setupSettingsTabs } from "./model-manager";
import {
  canUseCurrentAudio,
  legacyProvider,
  settingOptions,
  updatedProfile,
  type ProviderCatalogue,
  type ProviderDescription,
  type VoiceConfiguration,
  type VoiceSettings,
} from "./providers";

type Session = {
  email: string;
  csrfToken: string;
  expiresAt: number;
  controllerUrl: string;
};
type EngineStatus = {
  state:
    "stopped" | "starting" | "ready" | "stopping" | "error" | "unreachable";
  phase: string;
  error: string | null;
  conversationActive: boolean;
  settingsChanging?: boolean;
  memoryBytes: number;
  controllerReachable: boolean;
  selectedProvider?: string;
  activeProvider?: string | null;
  modelFilesMissing?: string[];
  preparation?: {
    provider: string | null;
    state: string;
    phase: string;
    error: string | null;
  };
};
type Theme = "system" | "light" | "dark";

function element<T extends HTMLElement = HTMLElement>(id: string): T {
  const result = document.getElementById(id);
  if (!result) throw new Error(`Missing element: ${id}`);
  return result as T;
}
function visible(id: string, value: boolean): void {
  element(id).hidden = !value;
}
function text(id: string, value: string): void {
  element(id).textContent = value;
}
function disabled(id: string, value: boolean): void {
  element<HTMLButtonElement>(id).disabled = value;
}

let session: Session | null = null;
let status: EngineStatus | null = null;
let pendingAction = false;
let saving = false;
let settingsLoaded = false;
let settingsLoading = false;
let configuration: VoiceConfiguration | null = null;
let providers: ProviderDescription[] = [];
let polling = false;
let actionError = "";
let lastFocus: HTMLElement | null = null;
const drawer = element<HTMLDialogElement>("settings");

function notify(message: string): void {
  text("notice", message);
  visible("notice", Boolean(message));
}

const plexAudio = new ConversationAudio(
  (message) => {
    notify(message);
    render();
    void poll();
  },
  (stats) => {
    text("latency", stats.avg_latency_ms.toFixed(0));
    text("rtf", `${stats.rtf.toFixed(2)}×`);
  },
  notify,
);
const lfmAudio = new LFM3Audio(
  (message) => {
    notify(message);
    render();
    void poll();
  },
  () => render(),
  (value) => text("transcript", value || "Your transcript will appear here."),
  notify,
);
let audio: ConversationAudio | LFM3Audio = plexAudio;

function savedProvider(): ProviderDescription | undefined {
  const id =
    status?.activeProvider ||
    status?.selectedProvider ||
    configuration?.selectedProvider ||
    providers[0]?.id;
  return providers.find((provider) => provider.id === id);
}

function readTheme(): Theme {
  try {
    const theme = localStorage.getItem("nox-voice-theme");
    return theme === "light" || theme === "dark" ? theme : "system";
  } catch {
    return "system";
  }
}
function applyTheme(value: Theme): void {
  if (value === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = value;
  element<HTMLSelectElement>("theme").value = value;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute(
      "content",
      getComputedStyle(document.documentElement)
        .getPropertyValue("--bg")
        .trim(),
    );
}
applyTheme(readTheme());
const orb = new VoiceOrb(element<HTMLCanvasElement>("orb"), () =>
  audio.assistantEnergy(),
);
element<HTMLSelectElement>("theme").addEventListener("change", (event) => {
  const value = (event.target as HTMLSelectElement).value as Theme;
  try {
    localStorage.setItem("nox-voice-theme", value);
  } catch {
    /* Theme is optional local state. */
  }
  applyTheme(value);
  orb.themeChanged();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  applyTheme(readTheme());
  orb.themeChanged();
});

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function api<T>(
  path: string,
  method = "GET",
  payload?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      ...(payload !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" ? { "X-CSRF-Token": session?.csrfToken || "" } : {}),
    },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const data = await response.json();
  if (response.status === 401) {
    audio.stop();
    session = null;
    settingsLoaded = false;
    configuration = null;
    providers = [];
    management.clear();
    if (drawer.open) drawer.close();
    render();
    throw new Error("Sign in to continue.");
  }
  if (!response.ok)
    throw new ApiError(
      typeof data.error === "string"
        ? data.error
        : "The request could not be completed.",
      response.status,
    );
  return data as T;
}

const selectSettingsTab = setupSettingsTabs();
const management = new ModelManagement(
  api,
  () => element<HTMLSelectElement>("model-provider").value,
  () => Boolean(session && status?.controllerReachable),
  () => render(),
  selectSettingsTab,
);

function render(): void {
  const current = savedProvider();
  if (!audio.busy)
    audio = current?.capabilities.audioChecks ? lfmAudio : plexAudio;
  const checkingAudio = audio === lfmAudio;
  visible("signed-in-actions", Boolean(session));
  visible("header-note", !session);
  visible("login-panel", !session);
  visible("conversation-actions", Boolean(session));
  visible(
    "conversation-metrics",
    Boolean(session && audio.active && !checkingAudio),
  );
  visible("audio-checks", Boolean(session && audio.active && checkingAudio));
  text("active-model-name", current?.name || "NoX Voice");
  text(
    "start-conversation-label",
    checkingAudio ? "Check audio" : "Start conversation",
  );
  visible("start-conversation", !audio.active);
  visible("mute", audio.active);
  visible("end-conversation", audio.active);
  text("mute", audio.muted ? "Unmute" : "Mute");
  element("mute").setAttribute("aria-pressed", String(audio.muted));
  if (!session) {
    text("conversation-title", "A space to talk.");
    text("conversation-subtitle", "Sign in to start a conversation.");
    visible("engine-hint", false);
    return;
  }
  text("account-email", session.email);
  text("avatar", session.email[0].toUpperCase());
  element<HTMLInputElement>("controller-url").value = session.controllerUrl;
  const state = status?.state || "unreachable";
  const phase = status?.phase || "Checking controller connection";
  const busyElsewhere = Boolean(status?.conversationActive && !audio.busy);
  const selected = providers.find(
    (provider) =>
      provider.id === element<HTMLSelectElement>("model-provider").value,
  );
  const compatibleAudio = settingsLoaded && canUseCurrentAudio(current);
  const preparing = status?.preparation?.state === "preparing";
  const draftModel = Boolean(
    configuration && selected?.id !== configuration.selectedProvider,
  );
  const missing =
    status?.selectedProvider === selected?.id
      ? Boolean(status?.modelFilesMissing?.length)
      : selected?.availability === "preparation_required";
  const loading =
    state === "starting" ||
    state === "stopping" ||
    Boolean(status?.settingsChanging) ||
    preparing;
  const labels: Record<string, string> = {
    stopped: "No model loaded",
    starting: "Loading model",
    ready: "Model loaded",
    stopping: "Ejecting model",
    error: "Engine error",
    unreachable: "Controller unavailable",
  };
  text("header-state", labels[state]);
  element("engine-badge").dataset.state = state;
  text("drawer-state", labels[state]);
  text(
    "loaded-model-name",
    status?.activeProvider
      ? providers.find((item) => item.id === status?.activeProvider)?.name ||
          "Voice model"
      : "No model loaded",
  );
  text(
    "model-memory-label",
    status?.activeProvider && state === "ready"
      ? `Loaded: ${providers.find((item) => item.id === status?.activeProvider)?.name || "Voice model"}`
      : state === "starting"
        ? "Loading model…"
        : state === "stopping"
          ? "Ejecting model…"
          : state === "unreachable"
            ? "Model status unavailable"
            : "No model loaded",
  );
  text("voice-model-label", selected?.name || "Select a model in Models");
  text(
    "engine-phase",
    preparing ? status?.preparation?.phase || "Preparing files" : phase,
  );
  const memory = status?.memoryBytes || 0;
  text("engine-memory", memory ? `${(memory / 1024 ** 3).toFixed(1)} GB` : "—");
  visible("engine-progress", loading);
  const engineError =
    actionError || status?.error || status?.preparation?.error || "";
  text("engine-error", engineError);
  visible("engine-error", Boolean(engineError));
  const localModel = management.selectedModel();
  const downloading = management.jobs.some(
    (job) =>
      job.provider === selected?.id &&
      [
        "queued",
        "resolving",
        "downloading",
        "verifying",
        "extracting",
        "cancelling",
      ].includes(job.state),
  );
  disabled(
    "start-engine",
    pendingAction ||
      loading ||
      downloading ||
      !settingsLoaded ||
      !status?.controllerReachable ||
      Boolean(status?.conversationActive) ||
      audio.busy ||
      (localModel ? localModel.localState === "not_downloaded" : missing) ||
      (state === "ready" && !draftModel),
  );
  disabled(
    "stop-engine",
    !status?.controllerReachable ||
      state === "stopped" ||
      state === "stopping" ||
      pendingAction,
  );
  disabled(
    "restart-engine",
    pendingAction ||
      loading ||
      downloading ||
      draftModel ||
      state !== "ready" ||
      Boolean(status?.conversationActive) ||
      audio.busy,
  );
  disabled(
    "start-conversation",
    state !== "ready" ||
      loading ||
      audio.busy ||
      busyElsewhere ||
      !compatibleAudio,
  );
  disabled(
    "save-settings",
    !settingsLoaded ||
      !status?.controllerReachable ||
      Boolean(status?.conversationActive) ||
      audio.busy ||
      loading ||
      saving ||
      !canUseCurrentAudio(selected),
  );
  for (const id of [
    "voice",
    "persona",
    "quantization",
    "model-provider",
    "input-language",
    "output-language",
    "speech-voice",
    "vocabulary",
  ])
    disabled(
      id,
      !settingsLoaded ||
        Boolean(status?.conversationActive) ||
        audio.busy ||
        loading ||
        saving,
    );
  management.render();
  disabled("hold-to-talk", !lfmAudio.active || lfmAudio.muted);
  element("hold-to-talk").setAttribute(
    "aria-pressed",
    String(lfmAudio.recording),
  );
  text(
    "audio-check-state",
    {
      disconnected: "Disconnected",
      connecting: "Connecting…",
      ready: "Ready to listen",
      recording: "Listening — release to transcribe",
      transcribing: "Transcribing…",
      speaking: "Reading aloud — hold Space to interrupt",
    }[lfmAudio.activity],
  );
  disabled(
    "read-aloud",
    !lfmAudio.active ||
      lfmAudio.recording ||
      !element<HTMLTextAreaElement>("speech-text").value.trim(),
  );
  disabled("stop-speech", lfmAudio.activity !== "speaking");
  visible("engine-hint", !audio.busy && state !== "ready");
  text(
    "engine-hint",
    state === "unreachable"
      ? "Open Settings to check your controller connection ↗"
      : "Open Settings to load a model ↗",
  );
  if (audio.active) {
    text("conversation-title", "You're connected.");
    text(
      "conversation-subtitle",
      checkingAudio
        ? "Hold Space or the button below to speak. Release to transcribe."
        : "Speak naturally. You can interrupt at any time.",
    );
  } else if (audio.busy) {
    text("conversation-title", "Getting connected.");
    text("conversation-subtitle", "Preparing your audio connection…");
  } else if (busyElsewhere) {
    text("conversation-title", "A conversation is active.");
    text(
      "conversation-subtitle",
      "End the conversation on your other device to continue here.",
    );
  } else if (state === "ready" && !loading) {
    text("conversation-title", "Ready when you are.");
    text(
      "conversation-subtitle",
      checkingAudio
        ? "Check transcription and speech with LFM3. Conversation support comes next."
        : "Start a conversation. Make it your own.",
    );
  } else if (loading) {
    text(
      "conversation-title",
      state !== "stopping" ? "Getting ready." : "Winding down.",
    );
    text("conversation-subtitle", phase);
  } else {
    text("conversation-title", "A space to talk.");
    text(
      "conversation-subtitle",
      state === "unreachable"
        ? "Your native controller is currently unavailable."
        : "Load a model in Settings to begin.",
    );
  }
}

async function poll(): Promise<void> {
  if (!session || polling) return;
  polling = true;
  try {
    status = await api<EngineStatus>("/api/engine/status");
    await management.refresh();
  } catch {
    /* Existing status stays visible during short network interruptions. */
  } finally {
    polling = false;
    render();
  }
}

async function loadSettings(): Promise<void> {
  if (settingsLoaded || settingsLoading || !session) return;
  settingsLoading = true;
  const owner = session;
  try {
    let settings: Record<string, unknown>;
    try {
      const [catalogue, saved] = await Promise.all([
        api<ProviderCatalogue>("/api/voice/v1/providers"),
        api<VoiceConfiguration>("/api/voice/v1/settings"),
      ]);
      if (catalogue.version !== 1 || saved.schemaVersion !== 1)
        throw new Error(
          "This controller uses an unsupported settings version.",
        );
      if (session !== owner) return;
      providers = catalogue.providers;
      configuration = saved;
      settings = saved.profiles[saved.selectedProvider];
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 404) throw error;
      configuration = null;
      providers = [legacyProvider()];
      settings = await api<VoiceSettings>("/api/settings");
    }
    if (session !== owner) return;
    const selector = element<HTMLSelectElement>("model-provider");
    selector.replaceChildren();
    for (const provider of providers) {
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = `${provider.name} · ${provider.runtime}${provider.implemented ? "" : " · coming later"}`;
      option.disabled = !canUseCurrentAudio(provider);
      selector.append(option);
    }
    selector.value = configuration?.selectedProvider || providers[0].id;
    renderProviderSettings();
    fillProviderSettings(settings);
    settingsLoaded = true;
    await management.refresh();
    visible("reload-settings", false);
    render();
  } catch (error) {
    if (session === owner) text("settings-feedback", (error as Error).message);
  } finally {
    settingsLoading = false;
  }
}

function renderProviderSettings(): void {
  const selected = providers.find(
    (provider) =>
      provider.id === element<HTMLSelectElement>("model-provider").value,
  );
  if (!selected) return;
  text("engine-model-name", selected.name);
  text("model-language-help", selected.languageNotice);
  visible(
    "precision-settings",
    Boolean(selected.settingsSchema.properties.bits),
  );
  visible(
    "persona-settings",
    Boolean(selected.settingsSchema.properties.prompt),
  );
  visible("transcription-settings", Boolean(selected.capabilities.audioChecks));
  const unavailable = providers.filter((provider) => !provider.implemented);
  text(
    "model-provider-help",
    configuration
      ? unavailable.map((provider) => provider.reason).join(" ")
      : selected.reason,
  );
  for (const [id, field] of [
    ["voice", "voice"],
    ["quantization", "bits"],
    ["input-language", "inputLanguage"],
    ["output-language", "outputLanguage"],
    ["speech-voice", "voice"],
  ]) {
    const selector = element<HTMLSelectElement>(id);
    selector.replaceChildren();
    for (const choice of settingOptions(selected, field)) {
      const option = document.createElement("option");
      option.value = choice.value;
      option.textContent = choice.label;
      selector.append(option);
    }
  }
}

function fillProviderSettings(value: Record<string, unknown>): void {
  for (const [id, field] of [
    ["voice", "voice"],
    ["persona", "prompt"],
    ["quantization", "bits"],
    ["input-language", "inputLanguage"],
    ["output-language", "outputLanguage"],
    ["speech-voice", "voice"],
    ["vocabulary", "vocabulary"],
  ]) {
    element<HTMLInputElement>(id).value = String(value[field] ?? "");
  }
}

element("model-provider").addEventListener("change", () => {
  renderProviderSettings();
  const value =
    configuration?.profiles[element<HTMLSelectElement>("model-provider").value];
  if (value) {
    fillProviderSettings(value);
  }
  render();
});

element("reload-settings").addEventListener("click", () => {
  settingsLoaded = false;
  void loadSettings().then(() => {
    if (settingsLoaded) text("settings-feedback", "Saved settings reloaded.");
  });
});

async function diagnostics(): Promise<void> {
  disabled("test-connection", true);
  text("connection-feedback", "Testing secure connection…");
  try {
    const result = await api<{
      controllerUrl: string;
      autostartEnabled: boolean;
      missingFiles: string[];
    }>("/api/diagnostics");
    element<HTMLInputElement>("controller-url").value = result.controllerUrl;
    text(
      "autostart-state",
      result.autostartEnabled ? "Automatic after login" : "Manual",
    );
    text(
      "connection-feedback",
      result.missingFiles.length
        ? `Connected securely. ${result.missingFiles.length} selected-model files need preparation. Use Settings or the model preparation command in the setup guide.`
        : "Connected. The controller's identity and your client certificate are verified.",
    );
    await loadSettings();
  } catch (error) {
    text("connection-feedback", (error as Error).message);
  } finally {
    disabled("test-connection", false);
  }
}

async function openSettings(): Promise<void> {
  if (!session) return;
  lastFocus = document.activeElement as HTMLElement;
  if (!drawer.open) drawer.showModal();
  void loadSettings();
  void diagnostics();
}
for (const id of ["open-settings", "engine-badge", "engine-hint"])
  element(id).addEventListener("click", () => {
    void openSettings();
  });
element("close-settings").addEventListener("click", () => drawer.close());
drawer.addEventListener("click", (event) => {
  if (event.target !== drawer) return;
  const rect = drawer.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right) drawer.close();
});
drawer.addEventListener("close", () => lastFocus?.focus());
element("test-connection").addEventListener("click", () => {
  void diagnostics();
});

for (const action of ["start", "stop", "restart"] as const)
  element(`${action}-engine`).addEventListener("click", async () => {
    if (action !== "start") audio.stop();
    actionError = "";
    pendingAction = true;
    render();
    try {
      if (
        action === "start" &&
        configuration &&
        element<HTMLSelectElement>("model-provider").value !==
          configuration.selectedProvider
      ) {
        if (!(await saveSettings())) return;
      }
      const operation = configuration
        ? ({ start: "load", stop: "eject", restart: "restart" } as const)[
            action
          ]
        : action;
      await api(
        `${configuration ? "/api/voice/v1" : "/api"}/engine/${operation}`,
        "POST",
      );
      await poll();
    } catch (error) {
      actionError = (error as Error).message;
    } finally {
      pendingAction = false;
      render();
    }
  });

async function saveSettings(): Promise<boolean> {
  saving = true;
  render();
  text("settings-feedback", "Applying changes…");
  const selected = providers.find(
    (provider) =>
      provider.id === element<HTMLSelectElement>("model-provider").value,
  );
  const value = selected?.capabilities.audioChecks
    ? {
        inputLanguage: element<HTMLSelectElement>("input-language").value,
        outputLanguage: element<HTMLSelectElement>("output-language").value,
        voice: element<HTMLSelectElement>("speech-voice").value,
        vocabulary: element<HTMLTextAreaElement>("vocabulary").value,
      }
    : {
        voice: element<HTMLSelectElement>("voice").value,
        prompt: element<HTMLTextAreaElement>("persona").value,
        bits: Number(element<HTMLSelectElement>("quantization").value),
      };
  try {
    if (configuration) {
      const update = updatedProfile(
        configuration,
        element<HTMLSelectElement>("model-provider").value,
        value,
      );
      configuration = await api<VoiceConfiguration>(
        "/api/voice/v1/settings",
        "PUT",
        update,
      );
    } else {
      await api("/api/settings", "PUT", value);
    }
    visible("reload-settings", false);
    text("settings-feedback", "Your settings have been saved.");
    return true;
  } catch (error) {
    text("settings-feedback", (error as Error).message);
    visible(
      "reload-settings",
      error instanceof ApiError && error.status === 409,
    );
    return false;
  } finally {
    saving = false;
    await poll();
    render();
  }
}
element("save-settings").addEventListener("click", () => void saveSettings());

element("start-conversation").addEventListener("click", async () => {
  notify("");
  text("latency", "—");
  text("rtf", "—");
  const promise = audio.start();
  render();
  try {
    await promise;
  } catch (error) {
    notify(
      (error as Error).message ||
        "Could not start audio. Check microphone permission.",
    );
  } finally {
    render();
  }
});
element("end-conversation").addEventListener("click", () => {
  audio.stop();
  notify("");
  render();
  void poll();
});
element("mute").addEventListener("click", () => {
  audio.toggleMute();
  render();
});
element("speech-text").addEventListener("input", render);
element("read-aloud").addEventListener("click", () =>
  lfmAudio.speak(element<HTMLTextAreaElement>("speech-text").value),
);
element("stop-speech").addEventListener("click", () =>
  lfmAudio.interruptSpeech(),
);
const hold = element<HTMLButtonElement>("hold-to-talk");
let pointer: number | null = null;
let spaceHeld = false;
hold.addEventListener("pointerdown", (event) => {
  if (event.button !== 0 || pointer !== null) return;
  event.preventDefault();
  pointer = event.pointerId;
  hold.setPointerCapture(event.pointerId);
  lfmAudio.beginHold();
});
const releasePointer = (event: PointerEvent) => {
  if (pointer !== event.pointerId) return;
  pointer = null;
  if (!spaceHeld) lfmAudio.endHold();
};
for (const kind of [
  "pointerup",
  "pointercancel",
  "lostpointercapture",
] as const)
  hold.addEventListener(kind, releasePointer);
document.addEventListener("keydown", (event) => {
  if (
    event.code !== "Space" ||
    event.repeat ||
    drawer.open ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey ||
    !lfmAudio.active
  )
    return;
  const target = event.target as HTMLElement;
  if (
    target.closest(
      "input, textarea, select, [contenteditable], a, button:not(#hold-to-talk)",
    )
  )
    return;
  event.preventDefault();
  spaceHeld = true;
  lfmAudio.beginHold();
});
document.addEventListener("keyup", (event) => {
  if (event.code !== "Space" || !spaceHeld) return;
  event.preventDefault();
  spaceHeld = false;
  if (pointer === null) lfmAudio.endHold();
});
window.addEventListener("blur", () => {
  pointer = null;
  spaceHeld = false;
  if (lfmAudio.recording) lfmAudio.endHold();
});
element("logout").addEventListener("click", async () => {
  audio.stop();
  render();
  try {
    await api("/auth/logout", "POST");
    session = null;
    status = null;
    settingsLoaded = false;
    configuration = null;
    providers = [];
    management.clear();
    drawer.close();
    notify("");
    render();
  } catch (error) {
    text("settings-feedback", (error as Error).message);
  }
});
element("events-details").addEventListener("toggle", async () => {
  if (!element<HTMLDetailsElement>("events-details").open) return;
  try {
    const result = await api<{ events: { time: number; message: string }[] }>(
      "/api/events",
    );
    const list = element<HTMLOListElement>("events");
    list.replaceChildren();
    for (const event of result.events.slice(-20).reverse()) {
      const row = document.createElement("li");
      const time = document.createElement("time");
      const message = document.createElement("span");
      const date = new Date(event.time * 1000);
      time.dateTime = date.toISOString();
      time.textContent = date.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });
      message.textContent = event.message;
      row.append(time, message);
      list.append(row);
    }
    if (!result.events.length) {
      const row = document.createElement("li");
      row.textContent = "No events yet.";
      list.append(row);
    }
  } catch {
    element("events").textContent =
      "Events are unavailable while the Mac is disconnected.";
  }
});

setInterval(() => {
  if (!document.hidden) void poll();
}, 2000);
setInterval(() => {
  const speaking = audio.assistantEnergy() > 0.025;
  element("assistant-indicator").dataset.speaking = String(speaking);
  text(
    "assistant-label",
    audio.active
      ? speaking
        ? "Assistant speaking"
        : "Assistant is ready"
      : "Made for natural conversation",
  );
}, 150);
document.addEventListener("visibilitychange", () => {
  if (document.hidden && audio.busy) {
    audio.stop();
    notify("Conversation ended when the page moved to the background.");
    render();
  }
  if (!document.hidden) void poll();
});
window.addEventListener("pagehide", () => audio.stop());

async function initialize(): Promise<void> {
  try {
    session = await api<Session>("/api/session");
    await poll();
    await loadSettings();
  } catch {
    session = null;
  }
  const loginResult = new URLSearchParams(location.search).get("login");
  if (loginResult) {
    notify(
      loginResult === "denied"
        ? "This Google account is not authorized for this workspace."
        : "Sign-in could not be completed. Please try again.",
    );
    history.replaceState(null, "", "/");
  }
  render();
}
void initialize();
