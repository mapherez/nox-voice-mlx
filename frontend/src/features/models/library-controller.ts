import type { Api } from "../../services/client";
import { Observable, errorMessage, isAborted } from "../../services/observable";
import {
  prefix,
  type ModelInfo,
  type DownloadJob,
  type Credential,
} from "./library-types";
type State = {
  models: ModelInfo[];
  jobs: DownloadJob[];
  services: Credential[];
  feedback: string;
  modelFeedback: string;
  clearing: boolean;
  preparing: boolean;
  openingFolder: boolean;
  credentialBusy: ReadonlySet<string>;
  credentialFeedback: Record<string, string>;
  jobBusy: ReadonlySet<string>;
};
const initial = (): State => ({
  models: [],
  jobs: [],
  services: [],
  feedback: "",
  modelFeedback: "",
  clearing: false,
  preparing: false,
  openingFolder: false,
  credentialBusy: new Set(),
  credentialFeedback: {},
  jobBusy: new Set(),
});
export class LibraryController extends Observable<State> {
  private generation = 0;
  private historyRevision = 0;
  private refreshing: Promise<void> | null = null;
  constructor(private api: Api) {
    super(initial());
  }
  reset(): void {
    this.generation++;
    this.historyRevision++;
    this.refreshing = null;
    this.update(initial());
  }
  refresh(): Promise<void> {
    if (this.refreshing) return this.refreshing;
    const generation = this.generation,
      revision = this.historyRevision;
    const request = (async () => {
      try {
        const [models, downloads, credentials] = await Promise.all([
          this.api<{ models: ModelInfo[] }>(prefix + "/models"),
          this.api<{ jobs: DownloadJob[] }>(prefix + "/downloads"),
          this.api<{ services: Credential[] }>(prefix + "/credentials"),
        ]);
        if (generation !== this.generation) return;
        const credentialFeedback = { ...this.state.credentialFeedback };
        for (const service of credentials.services)
          if (!this.state.credentialBusy.has(service.id))
            credentialFeedback[service.id] = service.test?.message || "";
        this.update({
          models: models.models,
          services: credentials.services,
          credentialFeedback,
          feedback: "",
          ...(revision === this.historyRevision
            ? { jobs: downloads.jobs }
            : {}),
        });
      } catch (error) {
        if (generation === this.generation && !isAborted(error))
          this.update({ feedback: errorMessage(error) });
      }
    })();
    this.refreshing = request;
    void request.finally(() => {
      if (this.refreshing === request) this.refreshing = null;
    });
    return request;
  }
  async clearHistory(): Promise<void> {
    if (this.state.clearing) return;
    const generation = this.generation;
    this.update({ clearing: true, feedback: "" });
    try {
      const snapshot = await this.api<{ jobs: DownloadJob[] }>(
        prefix + "/downloads",
        "DELETE",
      );
      if (generation !== this.generation) return;
      if (!Array.isArray(snapshot.jobs))
        throw new Error(
          "The server returned an invalid download history response.",
        );
      this.historyRevision++;
      this.update({ jobs: snapshot.jobs });
    } catch (error) {
      if (generation === this.generation && !isAborted(error))
        this.update({ feedback: errorMessage(error) });
    } finally {
      if (generation === this.generation) this.update({ clearing: false });
    }
  }
  async download(provider: string): Promise<boolean> {
    if (this.state.preparing) return false;
    const generation = this.generation;
    this.update({ preparing: true, modelFeedback: "" });
    try {
      await this.api(prefix + "/downloads", "POST", { provider });
      await this.refresh();
      return generation === this.generation;
    } catch (error) {
      if (generation === this.generation && !isAborted(error))
        this.update({ modelFeedback: errorMessage(error) });
      return false;
    } finally {
      if (generation === this.generation) this.update({ preparing: false });
    }
  }
  async openFolder(provider: string): Promise<void> {
    if (this.state.openingFolder) return;
    const generation = this.generation;
    this.update({ openingFolder: true });
    try {
      const result = await this.api<{ message: string }>(
        prefix + "/providers/" + encodeURIComponent(provider) + "/open-folder",
        "POST",
      );
      if (generation === this.generation)
        this.update({ modelFeedback: result.message });
    } catch (error) {
      if (generation === this.generation && !isAborted(error))
        this.update({ modelFeedback: errorMessage(error) });
    } finally {
      if (generation === this.generation) this.update({ openingFolder: false });
    }
  }
  async jobAction(id: string, action: "cancel" | "retry"): Promise<void> {
    if (this.state.jobBusy.has(id)) return;
    const generation = this.generation;
    this.update({
      jobBusy: new Set([...this.state.jobBusy, id]),
      feedback: "",
    });
    try {
      await this.api(prefix + "/downloads/" + id + "/" + action, "POST");
      await this.refresh();
    } catch (error) {
      if (generation === this.generation && !isAborted(error))
        this.update({ feedback: errorMessage(error) });
    } finally {
      if (generation === this.generation)
        this.update({
          jobBusy: new Set(
            [...this.state.jobBusy].filter((value) => value !== id),
          ),
        });
    }
  }
  async credentialAction(
    service: string,
    action: "save" | "test" | "remove",
    secret?: string,
  ): Promise<void> {
    if (
      this.state.credentialBusy.has(service) ||
      (action === "save" && !secret?.trim())
    )
      return;
    const generation = this.generation;
    const message =
      action === "test"
        ? "Testing identity and model access…"
        : "Saving change…";
    this.update({
      credentialBusy: new Set([...this.state.credentialBusy, service]),
      credentialFeedback: {
        ...this.state.credentialFeedback,
        [service]: message,
      },
    });
    let feedback = "";
    try {
      const result = await this.api<{ message?: string }>(
        prefix + "/credentials/" + service + (action === "test" ? "/test" : ""),
        action === "save" ? "PUT" : action === "remove" ? "DELETE" : "POST",
        action === "save" ? { secret: secret!.trim() } : undefined,
      );
      await this.refresh();
      feedback =
        result.message ||
        (action === "remove"
          ? "Credential removed."
          : "Token saved privately on the Mac.");
    } catch (error) {
      if (!isAborted(error)) feedback = errorMessage(error);
    } finally {
      if (generation === this.generation)
        this.update({
          credentialBusy: new Set(
            [...this.state.credentialBusy].filter((value) => value !== service),
          ),
          credentialFeedback: {
            ...this.state.credentialFeedback,
            [service]: feedback,
          },
        });
    }
  }
}
