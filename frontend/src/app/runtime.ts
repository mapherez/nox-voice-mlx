import { ApiClient } from "../services/client";
import { Observable, errorMessage, isAborted } from "../services/observable";
import { SettingsController } from "../features/settings/settings-controller";
import { LibraryController } from "../features/models/library-controller";
import { DiagnosticsController } from "../features/settings/diagnostics-controller";
import { AudioSession } from "../features/conversation/audio-session";
import { HoldControls } from "../features/conversation/hold-controls";
import type {
  Session,
  EngineStatus,
  SettingsTab,
} from "../features/session/types";

type State = {
  session: Session | null;
  status: EngineStatus | null;
  notice: string;
  actionError: string;
  pendingAction: boolean;
  settingsOpen: boolean;
  downloadsOpen: boolean;
  tab: SettingsTab;
  focusCredential: string | null;
};
export class AppRuntime extends Observable<State> {
  readonly client: ApiClient;
  readonly settings: SettingsController;
  readonly library: LibraryController;
  readonly diagnostics: DiagnosticsController;
  readonly audio: AudioSession;
  readonly hold: HoldControls;
  private generation = 0;
  private attachment = 0;
  private polling: Promise<void> | null = null;
  constructor() {
    super({
      session: null,
      status: null,
      notice: "",
      actionError: "",
      pendingAction: false,
      settingsOpen: false,
      downloadsOpen: false,
      tab: "models",
      focusCredential: null,
    });
    this.client = new ApiClient(() => this.clearSession());
    this.audio = new AudioSession(
      (message) => this.notify(message),
      () => {
        void this.poll();
      },
    );
    this.settings = new SettingsController(this.client.request, () =>
      this.syncAudio(),
    );
    this.library = new LibraryController(this.client.request);
    this.diagnostics = new DiagnosticsController(this.client.request);
    this.hold = new HoldControls(
      this.audio.lfm,
      () => this.state.settingsOpen || this.state.downloadsOpen,
      (message) => this.notify(message),
    );
  }
  notify(message: string): void {
    this.update({ notice: message });
  }
  syncAudio(): void {
    const { configuration, providers } = this.settings.getSnapshot();
    const id =
      this.state.status?.activeProvider ||
      this.state.status?.selectedProvider ||
      configuration?.selectedProvider ||
      providers[0]?.id;
    this.audio.select(
      Boolean(providers.find((p) => p.id === id)?.capabilities.audioChecks),
    );
  }
  private clearSession(): void {
    this.generation++;
    this.polling = null;
    this.client.reset();
    this.audio.stop();
    this.hold.reset();
    this.settings.reset();
    this.library.reset();
    this.diagnostics.reset();
    this.update({
      session: null,
      status: null,
      settingsOpen: false,
      downloadsOpen: false,
      pendingAction: false,
      actionError: "",
      notice: "",
      focusCredential: null,
      tab: "models",
    });
  }
  async initialize(): Promise<void> {
    const generation = this.generation;
    try {
      const session = await this.client.request<Session>("/api/session");
      if (generation !== this.generation) return;
      this.client.authenticate(session.csrfToken);
      this.update({ session });
      await Promise.all([this.poll(), this.settings.load()]);
      if (generation === this.generation) this.syncAudio();
    } catch (error) {
      if (generation === this.generation && !isAborted(error))
        this.clearSession();
    }
  }
  poll(): Promise<void> {
    if (!this.state.session) return Promise.resolve();
    if (this.polling) return this.polling;
    const generation = this.generation;
    const request = (async () => {
      try {
        const status =
          await this.client.request<EngineStatus>("/api/engine/status");
        if (generation !== this.generation) return;
        this.update({ status });
        this.syncAudio();
        await this.library.refresh();
      } catch {
        /* Retain status during transient network interruptions. */
      }
    })();
    this.polling = request;
    void request.finally(() => {
      if (this.polling === request) this.polling = null;
    });
    return request;
  }
  openSettings(): void {
    if (!this.state.session) return;
    this.update({ settingsOpen: true });
    void this.settings.load();
    void this.testConnection();
  }
  closeSettings = (): void => {
    this.update({ settingsOpen: false, focusCredential: null });
  };
  selectTab(tab: SettingsTab): void {
    this.update({ tab });
  }
  openDownloads(): void {
    this.update({ downloadsOpen: true });
    void this.library.refresh();
  }
  closeDownloads = (): void => {
    this.update({ downloadsOpen: false });
  };
  async download(): Promise<void> {
    const selected = this.settings.getSnapshot().selected;
    const library = this.library.getSnapshot();
    const model = library.models.find((value) => value.id === selected);
    if (!model) return;
    if (
      model.credentialService &&
      !library.services.find(
        (service) => service.id === model.credentialService,
      )?.configured
    ) {
      this.update({
        tab: "credentials",
        focusCredential: model.credentialService,
      });
      return;
    }
    if (await this.library.download(selected)) this.openDownloads();
  }
  async testConnection(): Promise<void> {
    if (await this.diagnostics.test()) await this.settings.load();
  }
  async saveSettings(): Promise<boolean> {
    const saved = await this.settings.save();
    await this.poll();
    return saved;
  }
  async engine(action: "start" | "stop" | "restart"): Promise<void> {
    if (this.state.pendingAction) return;
    const generation = this.generation;
    if (action !== "start") {
      this.hold.reset();
      this.audio.stop();
    }
    this.update({ pendingAction: true, actionError: "" });
    try {
      const settings = this.settings.getSnapshot();
      if (
        action === "start" &&
        settings.configuration &&
        settings.selected !== settings.configuration.selectedProvider
      )
        if (!(await this.saveSettings())) return;
      if (generation !== this.generation) return;
      const versioned = Boolean(this.settings.getSnapshot().configuration);
      const operation = versioned
        ? { start: "load", stop: "eject", restart: "restart" }[action]
        : action;
      await this.client.request(
        (versioned ? "/api/voice/v1" : "/api") + "/engine/" + operation,
        "POST",
      );
      await this.poll();
    } catch (error) {
      if (generation === this.generation && !isAborted(error))
        this.update({ actionError: errorMessage(error) });
    } finally {
      if (generation === this.generation) this.update({ pendingAction: false });
    }
  }
  endConversation(): void {
    this.hold.reset();
    this.audio.stop();
    this.notify("");
    void this.poll();
  }
  async logout(): Promise<void> {
    const generation = this.generation;
    this.hold.reset();
    this.audio.stop();
    try {
      await this.client.request("/auth/logout", "POST");
      if (generation === this.generation) this.clearSession();
    } catch (error) {
      if (generation === this.generation && !isAborted(error))
        this.settings.setFeedback(errorMessage(error));
    }
  }
  attach(): () => void {
    const detachHold = this.hold.attach();
    const visibility = () => {
      if (document.hidden && this.audio.getSnapshot().busy) {
        this.hold.reset();
        this.audio.stop();
        this.notify(
          "Conversation ended when the page moved to the background.",
        );
      }
      if (!document.hidden) void this.poll();
    };
    const pagehide = () => {
      this.hold.reset();
      this.audio.stop();
    };
    const timer = window.setInterval(() => {
      if (!document.hidden) void this.poll();
    }, 2000);
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("pagehide", pagehide);
    const attachment = ++this.attachment;
    const loginResult = new URLSearchParams(location.search).get("login");
    void this.initialize().then(() => {
      if (attachment !== this.attachment || !loginResult) return;
      this.notify(
        loginResult === "denied"
          ? "This Google account is not authorized for this workspace."
          : "Sign-in could not be completed. Please try again.",
      );
      history.replaceState(null, "", "/");
    });
    return () => {
      this.attachment++;
      clearInterval(timer);
      detachHold();
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("pagehide", pagehide);
      this.clearSession();
    };
  }
}
