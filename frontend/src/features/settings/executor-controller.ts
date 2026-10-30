import type { Api } from "../../services/client";
import { Observable, errorMessage, isAborted } from "../../services/observable";

export type MCPProfile = {
  id: string;
  name: string;
  url: string;
  role: "direct" | "suite";
  credential: string;
  timeoutSeconds: number;
};
export type ExecutorSettings = {
  schemaVersion: 1;
  revision: number;
  enabled: boolean;
  llm: {
    baseUrl: string;
    model: string;
    credential: "" | "executor";
    instructions: string;
    temperature: number;
    maxTokens: number;
    timeoutSeconds: number;
  };
  connections: MCPProfile[];
  limits: {
    maxTasks: number;
    maxPending: number;
    memoryBytes: number;
    contextBytes: number;
    taskTimeoutSeconds: number;
  };
};
export type MCPConnection = {
  id: string;
  connected: boolean;
  name: string;
  identity: string;
  server: { name?: string; version?: string };
  tools: { name: string; title: string; supported: boolean }[];
};
type State = {
  draft: ExecutorSettings | null;
  dirty: boolean;
  conflict: boolean;
  busy: boolean;
  feedback: string;
  connections: MCPConnection[];
  models: string[];
};
const initial = (): State => ({
  draft: null,
  dirty: false,
  conflict: false,
  busy: false,
  feedback: "",
  connections: [],
  models: [],
});
const prefix = "/api/voice/v1/executor";

export class ExecutorController extends Observable<State> {
  private generation = 0;
  constructor(private api: Api) {
    super(initial());
  }
  reset(): void {
    this.generation++;
    this.update(initial());
  }
  edit(update: (settings: ExecutorSettings) => ExecutorSettings): void {
    if (this.state.draft)
      this.update({
        draft: update(structuredClone(this.state.draft)),
        dirty: true,
        feedback: "",
      });
  }
  async load(force = false): Promise<void> {
    const generation = this.generation;
    try {
      const [settings, catalogue] = await Promise.all([
        this.api<ExecutorSettings>(prefix + "/settings"),
        this.api<{ connections: MCPConnection[] }>(prefix + "/connections"),
      ]);
      if (
        settings.schemaVersion !== 1 ||
        !settings.llm ||
        !settings.limits ||
        !Array.isArray(settings.connections) ||
        !Array.isArray(catalogue.connections)
      )
        throw new Error("The controller returned invalid executor settings.");
      if (generation !== this.generation) return;
      this.update({
        connections: catalogue.connections,
        ...(!this.state.dirty || force
          ? { draft: settings, dirty: false, conflict: false }
          : {}),
      });
    } catch (error) {
      if (generation === this.generation && !isAborted(error))
        this.update({ feedback: errorMessage(error) });
    }
  }
  private async perform(action: () => Promise<void>): Promise<void> {
    if (this.state.busy) return;
    const generation = this.generation;
    this.update({ busy: true, feedback: "" });
    try {
      await action();
    } catch (error) {
      if (generation === this.generation && !isAborted(error))
        this.update({
          feedback: errorMessage(error),
          conflict:
            typeof error === "object" &&
            error !== null &&
            "status" in error &&
            error.status === 409,
        });
    } finally {
      if (generation === this.generation) this.update({ busy: false });
    }
  }
  async save(): Promise<void> {
    const draft = this.state.draft,
      generation = this.generation;
    if (!draft) return;
    if (
      [
        draft.llm.temperature,
        draft.llm.maxTokens,
        draft.llm.timeoutSeconds,
        ...Object.values(draft.limits),
      ].some((value) => !Number.isFinite(value))
    ) {
      this.update({
        feedback:
          "Complete the numeric fields in Generation and resource limits before saving.",
      });
      return;
    }
    await this.perform(async () => {
      const saved = await this.api<ExecutorSettings>(
        prefix + "/settings",
        "PUT",
        draft,
      );
      if (generation !== this.generation) return;
      this.update({
        draft: saved,
        dirty: false,
        conflict: false,
        feedback:
          "Saved. Applies to the next conversation. MCP connections must be connected explicitly.",
      });
      await this.load();
    });
  }
  async testLLM(): Promise<void> {
    const generation = this.generation;
    await this.perform(async () => {
      const result = await this.api<{
        models: string[];
        selectedModelAvailable: boolean;
        message: string;
      }>(prefix + "/test", "POST");
      if (generation === this.generation)
        this.update({
          models: result.models,
          feedback: result.selectedModelAvailable
            ? result.message
            : "Endpoint reachable. Select an available model and save before starting a conversation.",
        });
    });
  }
  async connection(
    id: string,
    action: "connect" | "disconnect" | "refresh" | "test",
  ): Promise<void> {
    const generation = this.generation;
    await this.perform(async () => {
      await this.api(prefix + "/connections/" + id + "/" + action, "POST");
      if (generation !== this.generation) return;
      this.update({
        feedback:
          action === "test"
            ? "Connection and catalogue verified. No tool was executed."
            : "MCP connection updated.",
      });
      await this.load();
    });
  }
}
