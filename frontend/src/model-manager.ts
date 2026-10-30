export type ModelInfo = {
  id: string;
  name: string;
  source: string;
  runtime: string;
  revision: string | null;
  downloadBytes: number;
  sizeApproximate: boolean;
  credentialService: string | null;
  localState: "not_downloaded" | "present" | "verified";
  loaded: boolean;
};
type DownloadFile = {
  name: string;
  totalBytes: number;
  downloadedBytes: number;
  state: string;
};
export type DownloadJob = {
  id: string;
  provider: string;
  name: string;
  state: string;
  downloadedBytes: number;
  totalBytes: number;
  bytesPerSecond: number;
  error: string | null;
  files: DownloadFile[];
};
type Credential = {
  id: string;
  name: string;
  fieldLabel: string;
  help: string;
  tokenUrl?: string;
  accessUrl?: string;
  configured: boolean;
  testSupported?: boolean;
  test: { state: string; message: string } | null;
};
type Api = <T>(path: string, method?: string, payload?: unknown) => Promise<T>;
const prefix = "/api/voice/v1";
const active = new Set([
  "queued",
  "resolving",
  "downloading",
  "verifying",
  "extracting",
  "cancelling",
]);
const labels: Record<string, string> = {
  queued: "Queued",
  resolving: "Checking model source",
  downloading: "Downloading",
  verifying: "Verifying files",
  extracting: "Preparing runtime and voices",
  cancelling: "Cancelling",
  cancelled: "Cancelled",
  completed: "Ready on disk",
  failed: "Download failed",
  interrupted: "Interrupted — ready to resume",
};
const byId = <T extends HTMLElement = HTMLElement>(id: string) =>
  document.getElementById(id) as T;
const node = <K extends keyof HTMLElementTagNameMap>(
  tag: K,
  text?: string,
  cls?: string,
) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (cls) element.className = cls;
  return element;
};
export function bytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const index = Math.max(
    0,
    Math.min(3, Math.floor(Math.log(value) / Math.log(1000))),
  );
  return `${(value / 1000 ** index).toFixed(index > 1 ? 1 : 0)} ${["B", "KB", "MB", "GB"][index]}`;
}
export function downloadIndicator(jobs: DownloadJob[]): {
  state: string;
  count: number;
  label: string;
} {
  const count = jobs.filter((job) => active.has(job.state)).length;
  if (count)
    return {
      state: "active",
      count,
      label: `${count} active download${count === 1 ? "" : "s"}`,
    };
  const latest = new Map(jobs.map((job) => [job.provider, job]));
  if (
    [...latest.values()].some((job) =>
      ["failed", "interrupted"].includes(job.state),
    )
  )
    return { state: "attention", count: 0, label: "Downloads need attention" };
  return { state: "idle", count: 0, label: "Open downloads" };
}
function icon(
  kind: "download" | "check" | "active" | "attention",
): SVGSVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [key, value] of Object.entries({
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "1.7",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    "aria-hidden": "true",
  }))
    svg.setAttribute(key, value);
  const path = document.createElementNS(svg.namespaceURI, "path");
  path.setAttribute(
    "d",
    {
      download: "M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4",
      check: "m5 12 4 4L19 6",
      active: "M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4",
      attention: "M12 8v5m0 4h.01M10 3 2 19h20L14 3Z",
    }[kind],
  );
  svg.append(path);
  return svg;
}

export function setupSettingsTabs(): (name: string) => void {
  const tabs = [
    ...document.querySelectorAll<HTMLButtonElement>("[data-settings-tab]"),
  ];
  function select(name: string): void {
    for (const tab of tabs) {
      const selected = tab.dataset.settingsTab === name;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      byId(`panel-${tab.dataset.settingsTab}`).hidden = !selected;
    }
    byId("settings").dataset.tab = name;
    byId("save-settings").hidden = name !== "voice";
    byId("model-lifecycle-actions").hidden = name !== "models";
    byId("model-memory-label").hidden = name !== "models";
    byId("settings").querySelector(".drawer-content")?.scrollTo(0, 0);
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => select(tab.dataset.settingsTab!));
    tab.addEventListener("keydown", (event) => {
      let next = index;
      if (["ArrowRight", "ArrowDown"].includes(event.key))
        next = (index + 1) % tabs.length;
      else if (["ArrowLeft", "ArrowUp"].includes(event.key))
        next = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      else return;
      event.preventDefault();
      select(tabs[next].dataset.settingsTab!);
      tabs[next].focus();
    });
  });
  const mobile = matchMedia("(max-width: 680px)");
  const orientation = () =>
    document
      .querySelector(".settings-tabs")
      ?.setAttribute(
        "aria-orientation",
        mobile.matches ? "horizontal" : "vertical",
      );
  mobile.addEventListener("change", orientation);
  orientation();
  select("models");
  return select;
}

export class ModelManagement {
  models: ModelInfo[] = [];
  jobs: DownloadJob[] = [];
  services: Credential[] = [];
  private generation = 0;
  private refreshing = false;
  private clearingDownloads = false;
  private credentialBusy = new Set<string>();
  private lastJobs = "";
  private expandedJobs = new Map<string, boolean>();
  private returnFocus: HTMLElement | null = null;
  private readonly dialog = byId<HTMLDialogElement>("downloads");

  constructor(
    private api: Api,
    private selected: () => string,
    private reachable: () => boolean,
    private changed: () => void,
    private selectTab: (tab: string) => void,
  ) {
    for (const id of ["open-downloads", "manage-downloads"])
      byId(id).addEventListener("click", () => this.openDownloads());
    byId("close-downloads").addEventListener("click", () =>
      this.dialog.close(),
    );
    byId("clear-downloads").addEventListener(
      "click",
      () => void this.clearDownloads(),
    );
    this.dialog.addEventListener("close", () => this.returnFocus?.focus());
    byId("prepare-model").addEventListener(
      "click",
      () => void this.startDownload(),
    );
    byId("open-model-folder").addEventListener(
      "click",
      () => void this.openFolder(),
    );
  }

  clear(): void {
    this.generation++;
    this.models = [];
    this.jobs = [];
    this.services = [];
    byId("credentials-list").replaceChildren();
    byId("download-list").replaceChildren();
    if (this.dialog.open) this.dialog.close();
    this.lastJobs = "";
  }

  selectedModel(): ModelInfo | undefined {
    return this.models.find((model) => model.id === this.selected());
  }

  async refresh(): Promise<void> {
    if (this.refreshing) return;
    this.refreshing = true;
    const generation = this.generation;
    try {
      const [models, downloads, credentials] = await Promise.all([
        this.api<{ models: ModelInfo[] }>(`${prefix}/models`),
        this.api<{ jobs: DownloadJob[] }>(`${prefix}/downloads`),
        this.api<{ services: Credential[] }>(`${prefix}/credentials`),
      ]);
      if (generation !== this.generation) return;
      this.models = models.models;
      this.jobs = downloads.jobs;
      this.services = credentials.services;
      byId("downloads-feedback").textContent = "";
      this.renderCredentials();
      this.render();
      this.changed();
    } catch (error) {
      if (generation === this.generation)
        byId("downloads-feedback").textContent = (error as Error).message;
    } finally {
      this.refreshing = false;
    }
  }

  render(): void {
    byId<HTMLButtonElement>("clear-downloads").disabled =
      this.clearingDownloads ||
      !this.reachable() ||
      !this.jobs.some((job) => !active.has(job.state));
    const indicator = downloadIndicator(this.jobs);
    const trigger = byId("open-downloads");
    trigger.dataset.state = indicator.state;
    trigger.setAttribute("aria-label", indicator.label);
    trigger.title = indicator.label;
    const managerTrigger = byId("manage-downloads");
    managerTrigger.dataset.state = indicator.state;
    managerTrigger.title = indicator.label;
    managerTrigger.setAttribute("aria-label", indicator.label);
    managerTrigger.replaceChildren(
      icon(
        indicator.state === "attention"
          ? "attention"
          : indicator.state === "active"
            ? "active"
            : "download",
      ),
    );
    byId("downloads-icon").replaceChildren(
      icon(
        indicator.state === "attention"
          ? "attention"
          : indicator.state === "active"
            ? "active"
            : "download",
      ),
    );
    byId("download-count").textContent = String(indicator.count);
    byId("download-count").hidden = !indicator.count;
    const model = this.selectedModel();
    if (!model) {
      byId<HTMLButtonElement>("prepare-model").disabled = true;
      byId<HTMLButtonElement>("open-model-folder").disabled = true;
    }
    if (model) {
      const job = [...this.jobs]
        .reverse()
        .find(
          (value) => value.provider === model.id && active.has(value.state),
        );
      const local = model.localState !== "not_downloaded";
      byId("model-local-state").textContent = job
        ? labels[job.state]
        : model.localState === "verified"
          ? "Verified on disk"
          : local
            ? "Files on disk"
            : "Not downloaded";
      byId("model-local-state").dataset.ready = String(local);
      byId("model-description").textContent =
        `${model.runtime} · ${model.id === "lfm3-audio" ? "Transcription and speech checks. Executor support comes next." : "Continuous voice conversation. No external LLM required."}`;
      byId<HTMLAnchorElement>("model-source").href = model.source;
      byId("model-size").textContent =
        `${model.sizeApproximate ? "≈ " : ""}${bytes(model.downloadBytes)}`;
      byId("model-revision").textContent = model.revision
        ? model.revision.slice(0, 12)
        : "Resolved before download";
      byId("model-revision").title =
        model.revision ||
        "Exact files and size are resolved before downloading.";
      byId("download-label").textContent = job
        ? "Downloading…"
        : local
          ? "Verify / repair"
          : "Download";
      byId("prepare-model")
        .querySelector("svg")
        ?.replaceWith(icon(local ? "check" : "download"));
      byId<HTMLButtonElement>("prepare-model").disabled =
        !this.reachable() || Boolean(job) || model.loaded;
      byId<HTMLButtonElement>("open-model-folder").disabled = !this.reachable();
      const needsCredential =
        model.credentialService &&
        !this.services.find((service) => service.id === model.credentialService)
          ?.configured;
      byId("preparation-help").textContent = job
        ? "Follow progress in Downloads. You can keep using the app."
        : needsCredential
          ? "To download or repair PersonaPlex, add a Hugging Face token in Credentials and accept the model's access terms."
          : local
            ? "Files remain on disk after Eject. Integrity is checked before loading."
            : "Downloads all required files into this model's folder on your Mac.";
    }
    const serialized = JSON.stringify({
      jobs: this.jobs,
      reachable: this.reachable(),
    });
    if (serialized !== this.lastJobs) {
      this.renderDownloads();
      this.lastJobs = serialized;
    }
  }

  private openDownloads(): void {
    this.returnFocus = document.activeElement as HTMLElement;
    if (!this.dialog.open) this.dialog.showModal();
    this.renderDownloads();
    void this.refresh();
  }

  private async clearDownloads(): Promise<void> {
    if (this.clearingDownloads) return;
    this.clearingDownloads = true;
    const generation = this.generation;
    byId("downloads-feedback").textContent = "";
    this.render();
    try {
      const snapshot = await this.api<{ jobs: DownloadJob[] }>(
        `${prefix}/downloads`,
        "DELETE",
      );
      if (generation !== this.generation) return;
      // Ignore an earlier in-flight poll that could restore cleared rows.
      this.generation++;
      this.jobs = snapshot.jobs;
      const retained = new Set(this.jobs.map((job) => job.id));
      for (const key of this.expandedJobs.keys())
        if (!retained.has(key)) this.expandedJobs.delete(key);
      this.changed();
    } catch (error) {
      if (generation === this.generation)
        byId("downloads-feedback").textContent = (error as Error).message;
    } finally {
      this.clearingDownloads = false;
      this.render();
    }
  }

  private async startDownload(): Promise<void> {
    const model = this.selectedModel();
    if (!model) return;
    if (
      model.credentialService &&
      !this.services.find((service) => service.id === model.credentialService)
        ?.configured
    ) {
      this.selectTab("credentials");
      byId<HTMLInputElement>(`credential-${model.credentialService}`)?.focus();
      return;
    }
    byId<HTMLButtonElement>("prepare-model").disabled = true;
    byId("model-feedback").textContent = "";
    try {
      await this.api(`${prefix}/downloads`, "POST", { provider: model.id });
      await this.refresh();
      this.openDownloads();
    } catch (error) {
      byId("model-feedback").textContent = (error as Error).message;
    } finally {
      this.render();
    }
  }

  private async openFolder(): Promise<void> {
    byId<HTMLButtonElement>("open-model-folder").disabled = true;
    try {
      const result = await this.api<{ message: string }>(
        `${prefix}/providers/${encodeURIComponent(this.selected())}/open-folder`,
        "POST",
      );
      byId("model-feedback").textContent = result.message;
    } catch (error) {
      byId("model-feedback").textContent = (error as Error).message;
    } finally {
      this.render();
    }
  }

  private renderDownloads(): void {
    const list = byId("download-list");
    const focused = list.contains(document.activeElement)
      ? document.activeElement?.id
      : null;
    list.replaceChildren();
    if (!this.jobs.length)
      list.append(
        node(
          "p",
          "No download activity. Manage models in Settings.",
          "downloads-empty",
        ),
      );
    for (const job of [...this.jobs].reverse()) {
      const row = node("article", undefined, "download-job");
      row.dataset.state = job.state;
      const heading = node("div", undefined, "section-heading");
      heading.append(
        node("strong", job.name),
        node("span", labels[job.state] || job.state, "download-state"),
      );
      const progress = node("progress");
      progress.max = job.totalBytes || 1;
      if (job.totalBytes) progress.value = job.downloadedBytes;
      progress.setAttribute("aria-label", `${job.name} download progress`);
      row.append(
        heading,
        progress,
        node(
          "p",
          `${bytes(job.downloadedBytes)}${job.totalBytes ? ` / ${bytes(job.totalBytes)}` : " · Size unavailable"}${job.bytesPerSecond ? ` · ${bytes(job.bytesPerSecond)}/s` : ""}`,
          "field-help",
        ),
      );
      if (job.files.length) {
        const details = node("details");
        details.open = this.expandedJobs.get(job.id) ?? active.has(job.state);
        details.addEventListener("toggle", () =>
          this.expandedJobs.set(job.id, details.open),
        );
        details.append(
          node(
            "summary",
            `${job.files.filter((file) => file.state === "downloaded").length} of ${job.files.length} files downloaded`,
          ),
        );
        for (const file of job.files) {
          const item = node("div", undefined, "download-file");
          item.append(
            node("span", file.name),
            node(
              "small",
              `${bytes(file.downloadedBytes)} / ${bytes(file.totalBytes)}`,
            ),
          );
          details.append(item);
        }
        row.append(details);
      }
      if (job.error) row.append(node("p", job.error, "inline-error"));
      if (
        active.has(job.state) ||
        ["failed", "cancelled", "interrupted"].includes(job.state)
      ) {
        const action = active.has(job.state) ? "cancel" : "retry";
        const button = node(
          "button",
          action === "cancel" ? "Cancel download" : "Retry / resume",
          "text-button",
        );
        button.id = `download-${job.id}-${action}`;
        button.disabled = job.state === "cancelling" || !this.reachable();
        button.addEventListener("click", async () => {
          button.disabled = true;
          byId("downloads-feedback").textContent = "";
          try {
            await this.api(`${prefix}/downloads/${job.id}/${action}`, "POST");
            await this.refresh();
          } catch (error) {
            byId("downloads-feedback").textContent = (error as Error).message;
            button.disabled = false;
          }
        });
        row.append(button);
      }
      list.append(row);
    }
    if (focused) byId(focused)?.focus({ preventScroll: true });
  }

  private renderCredentials(): void {
    if (
      this.services.length &&
      !byId("credentials-list").querySelector(".credential-card")
    )
      byId("credentials-list").replaceChildren();
    for (const service of this.services) {
      let section = byId(`service-${service.id}`);
      if (!section) {
        section = node("section", undefined, "credential-card");
        section.id = `service-${service.id}`;
        const heading = node("div", undefined, "section-heading");
        const state = node("span", undefined, "pill subtle");
        state.id = `credential-state-${service.id}`;
        heading.append(node("h3", service.name), state);
        const label = node("label", service.fieldLabel, "field-label");
        label.htmlFor = `credential-${service.id}`;
        const input = node("input");
        input.id = `credential-${service.id}`;
        input.type = "password";
        input.autocomplete = "off";
        input.spellcheck = false;
        input.maxLength = 512;
        input.placeholder = "Paste a new token to save or replace it";
        const links = node("p", undefined, "credential-links");
        for (const [title, url] of [
          ["Create a token ↗", service.tokenUrl],
          ["Model access terms ↗", service.accessUrl],
        ]) {
          if (!url) continue;
          const link = node("a", title);
          link.href = url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          links.append(link);
        }
        const buttons = node("div", undefined, "credential-actions");
        for (const action of ["save", "test", "remove"]) {
          const button = node(
            "button",
            { save: "Save token", test: "Test access", remove: "Remove" }[
              action
            ],
            action === "save" ? "button secondary" : "text-button",
          );
          button.id = `credential-${action}-${service.id}`;
          button.hidden = action === "test" && service.testSupported === false;
          button.addEventListener(
            "click",
            () => void this.credentialAction(service.id, action),
          );
          buttons.append(button);
        }
        const feedback = node("p", undefined, "field-help");
        feedback.id = `credential-feedback-${service.id}`;
        feedback.setAttribute("role", "status");
        section.append(
          heading,
          node("p", service.help, "field-help"),
          label,
          input,
          links,
          buttons,
          feedback,
        );
        byId("credentials-list").append(section);
      }
      byId(`credential-state-${service.id}`).textContent = service.configured
        ? "Token saved"
        : "Not configured";
      for (const action of ["save", "test", "remove"])
        byId<HTMLButtonElement>(`credential-${action}-${service.id}`).disabled =
          this.credentialBusy.has(service.id) ||
          !this.reachable() ||
          (action !== "save" && !service.configured);
      if (!this.credentialBusy.has(service.id))
        byId(`credential-feedback-${service.id}`).textContent =
          service.test?.message || "";
    }
  }

  private async credentialAction(
    service: string,
    action: string,
  ): Promise<void> {
    if (this.credentialBusy.has(service)) return;
    const input = byId<HTMLInputElement>(`credential-${service}`);
    if (action === "save" && !input.value.trim()) {
      input.focus();
      return;
    }
    const payload =
      action === "save" ? { secret: input.value.trim() } : undefined;
    input.value = "";
    this.credentialBusy.add(service);
    this.renderCredentials();
    const feedback = byId(`credential-feedback-${service}`);
    feedback.textContent =
      action === "test"
        ? "Testing identity and model access…"
        : "Saving change…";
    let message = "";
    try {
      const result = await this.api<{ message?: string }>(
        `${prefix}/credentials/${service}${action === "test" ? "/test" : ""}`,
        action === "save" ? "PUT" : action === "remove" ? "DELETE" : "POST",
        payload,
      );
      await this.refresh();
      message =
        result.message ||
        (action === "remove"
          ? "Credential removed."
          : "Token saved privately on the Mac.");
    } catch (error) {
      message = (error as Error).message;
    } finally {
      this.credentialBusy.delete(service);
      this.renderCredentials();
      feedback.textContent = message;
    }
  }
}
