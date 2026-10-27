import "./style.css";
import { ConversationAudio } from "./audio";
import { VoiceOrb } from "./orb";

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
};
type VoiceSettings = { voice: string; prompt: string; bits: number };
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
let polling = false;
let actionError = "";
let lastFocus: HTMLElement | null = null;
const drawer = element<HTMLDialogElement>("settings");

function notify(message: string): void {
  text("notice", message);
  visible("notice", Boolean(message));
}

const audio = new ConversationAudio(
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

for (const [prefix, count, label] of [
  ["NATF", 4, "Natural female"],
  ["NATM", 4, "Natural male"],
  ["VARF", 5, "Variety female"],
  ["VARM", 5, "Variety male"],
] as const) {
  const group = document.createElement("optgroup");
  group.label = label;
  for (let i = 0; i < count; i++) {
    const option = document.createElement("option");
    option.value = `${prefix}${i}`;
    option.textContent = `${label} ${i + 1}${prefix === "NATF" && i === 0 ? " · Kendra" : ""}`;
    group.append(option);
  }
  element<HTMLSelectElement>("voice").append(group);
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
    if (drawer.open) drawer.close();
    render();
    throw new Error("Sign in to continue.");
  }
  if (!response.ok)
    throw new Error(
      typeof data.error === "string"
        ? data.error
        : "The request could not be completed.",
    );
  return data as T;
}

function render(): void {
  visible("signed-in-actions", Boolean(session));
  visible("header-note", !session);
  visible("login-panel", !session);
  visible("conversation-actions", Boolean(session));
  visible("conversation-metrics", Boolean(session && audio.active));
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
  const loading =
    state === "starting" ||
    state === "stopping" ||
    Boolean(status?.settingsChanging);
  const labels: Record<string, string> = {
    stopped: "Engine stopped",
    starting: "Starting engine",
    ready: "Engine ready",
    stopping: "Stopping engine",
    error: "Engine error",
    unreachable: "Mac unavailable",
  };
  text("header-state", labels[state]);
  element("engine-badge").dataset.state = state;
  text("drawer-state", labels[state]);
  text("engine-phase", phase);
  const memory = status?.memoryBytes || 0;
  text("engine-memory", memory ? `${(memory / 1024 ** 3).toFixed(1)} GB` : "—");
  visible("engine-progress", loading);
  text("engine-error", actionError || status?.error || "");
  visible("engine-error", Boolean(actionError || status?.error));
  disabled(
    "start-engine",
    pendingAction ||
      !status?.controllerReachable ||
      !["stopped", "error"].includes(state),
  );
  disabled(
    "stop-engine",
    !status?.controllerReachable || state === "stopped" || state === "stopping",
  );
  disabled(
    "restart-engine",
    pendingAction || !status?.controllerReachable || loading,
  );
  disabled(
    "start-conversation",
    state !== "ready" || loading || audio.busy || busyElsewhere,
  );
  disabled(
    "save-settings",
    !settingsLoaded ||
      !status?.controllerReachable ||
      Boolean(status?.conversationActive) ||
      audio.busy ||
      loading ||
      saving,
  );
  for (const id of ["voice", "persona", "quantization"])
    disabled(
      id,
      Boolean(status?.conversationActive) || audio.busy || loading || saving,
    );
  visible("engine-hint", !audio.busy && state !== "ready");
  text(
    "engine-hint",
    state === "unreachable"
      ? "Open Settings to check your Mac connection ↗"
      : "Open Settings to start the engine ↗",
  );
  if (audio.active) {
    text("conversation-title", "You're connected.");
    text(
      "conversation-subtitle",
      "Speak naturally. You can interrupt at any time.",
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
    text("conversation-subtitle", "Start a conversation. Make it your own.");
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
        ? "Your Mac controller is currently unavailable."
        : "Start your engine in Settings to begin.",
    );
  }
}

async function poll(): Promise<void> {
  if (!session || polling) return;
  polling = true;
  try {
    status = await api<EngineStatus>("/api/engine/status");
  } catch {
    /* Existing status stays visible during short network interruptions. */
  } finally {
    polling = false;
    render();
  }
}

async function loadSettings(): Promise<void> {
  if (settingsLoaded || !session) return;
  try {
    const settings = await api<VoiceSettings>("/api/settings");
    element<HTMLSelectElement>("voice").value = settings.voice;
    element<HTMLTextAreaElement>("persona").value = settings.prompt;
    element<HTMLSelectElement>("quantization").value = String(settings.bits);
    settingsLoaded = true;
    render();
  } catch (error) {
    text("settings-feedback", (error as Error).message);
  }
}

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
        ? `Connected securely. ${result.missingFiles.length} model files are missing. Run nox-voice prepare-model on your Mac.`
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
  drawer.showModal();
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

for (const action of ["start", "stop", "restart"])
  element(`${action}-engine`).addEventListener("click", async () => {
    if (action !== "start") audio.stop();
    actionError = "";
    pendingAction = true;
    render();
    try {
      await api(`/api/engine/${action}`, "POST");
      await poll();
    } catch (error) {
      actionError = (error as Error).message;
    } finally {
      pendingAction = false;
      render();
    }
  });

element("save-settings").addEventListener("click", async () => {
  saving = true;
  render();
  text("settings-feedback", "Applying changes…");
  const value: VoiceSettings = {
    voice: element<HTMLSelectElement>("voice").value,
    prompt: element<HTMLTextAreaElement>("persona").value,
    bits: Number(element<HTMLSelectElement>("quantization").value),
  };
  try {
    await api("/api/settings", "PUT", value);
    text("settings-feedback", "Your changes have been applied.");
  } catch (error) {
    text("settings-feedback", (error as Error).message);
  } finally {
    saving = false;
    await poll();
    render();
  }
});

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
element("logout").addEventListener("click", async () => {
  audio.stop();
  render();
  try {
    await api("/auth/logout", "POST");
    session = null;
    status = null;
    settingsLoaded = false;
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
