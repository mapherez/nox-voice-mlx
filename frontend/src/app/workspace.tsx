import {
  createContext,
  useContext,
  useEffect,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { AppRuntime } from "./runtime";
import type { Observable } from "../services/observable";
import { canUseCurrentAudio } from "../providers";
import { activeDownloads } from "../features/models/library-types";

const RuntimeContext = createContext<AppRuntime | null>(null);
export function WorkspaceProvider({
  children,
  runtime: provided,
}: {
  children: ReactNode;
  runtime?: AppRuntime;
}) {
  const [runtime] = useState(() => provided ?? new AppRuntime());
  useEffect(() => runtime.attach(), [runtime]);
  return <RuntimeContext value={runtime}>{children}</RuntimeContext>;
}
export function useRuntime(): AppRuntime {
  const runtime = useContext(RuntimeContext);
  if (!runtime) throw new Error("Workspace provider is missing.");
  return runtime;
}
export function useSnapshot<T>(store: Observable<T>): T {
  return useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getSnapshot,
  );
}
export function useWorkspace() {
  const runtime = useRuntime();
  const workspace = useSnapshot(runtime),
    settings = useSnapshot(runtime.settings);
  const library = useSnapshot(runtime.library),
    audio = useSnapshot(runtime.audio);
  return {
    runtime,
    workspace,
    settings,
    library,
    audio,
    view: deriveView(workspace, settings, library, audio),
  };
}
function deriveView(
  workspace: ReturnType<AppRuntime["getSnapshot"]>,
  settings: ReturnType<AppRuntime["settings"]["getSnapshot"]>,
  library: ReturnType<AppRuntime["library"]["getSnapshot"]>,
  audio: ReturnType<AppRuntime["audio"]["getSnapshot"]>,
) {
  const { status, session, pendingAction, actionError } = workspace;
  const {
    configuration,
    providers,
    loaded,
    selected: selectedId,
    saving,
  } = settings;
  const currentId =
    status?.activeProvider ||
    status?.selectedProvider ||
    configuration?.selectedProvider ||
    providers[0]?.id;
  const current = providers.find((p) => p.id === currentId);
  const selected = providers.find((p) => p.id === selectedId);
  const model = library.models.find((m) => m.id === selectedId);
  const state = status?.state || "unreachable",
    phase = status?.phase || "Checking controller connection";
  const busyElsewhere = Boolean(status?.conversationActive && !audio.busy);
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
  const labels = {
    stopped: "No model loaded",
    starting: "Loading model",
    ready: "Model loaded",
    stopping: "Ejecting model",
    error: "Engine error",
    unreachable: "Controller unavailable",
  };
  const loadedName =
    providers.find((p) => p.id === status?.activeProvider)?.name ||
    "Voice model";
  const job = [...library.jobs]
    .reverse()
    .find((j) => j.provider === selected?.id && activeDownloads.has(j.state));
  const memoryLabel =
    status?.activeProvider && state === "ready"
      ? "Loaded: " + loadedName
      : state === "starting"
        ? "Loading model…"
        : state === "stopping"
          ? "Ejecting model…"
          : state === "unreachable"
            ? "Model status unavailable"
            : "No model loaded";
  const engineError =
    actionError || status?.error || status?.preparation?.error || "";
  const editingDisabled =
    !loaded ||
    Boolean(status?.conversationActive) ||
    audio.busy ||
    loading ||
    saving;
  return {
    current,
    selected,
    model,
    job,
    state,
    phase,
    busyElsewhere,
    loading,
    engineError,
    draftModel,
    stateLabel: labels[state],
    loadedName: status?.activeProvider ? loadedName : "No model loaded",
    memoryLabel,
    phaseLabel: preparing
      ? status?.preparation?.phase || "Preparing files"
      : phase,
    memory: status?.memoryBytes
      ? (status.memoryBytes / 1024 ** 3).toFixed(1) + " GB"
      : "—",
    editingDisabled,
    saveDisabled:
      editingDisabled ||
      !status?.controllerReachable ||
      !canUseCurrentAudio(selected),
    startDisabled:
      pendingAction ||
      loading ||
      Boolean(job) ||
      !loaded ||
      !status?.controllerReachable ||
      Boolean(status?.conversationActive) ||
      audio.busy ||
      (model ? model.localState === "not_downloaded" : missing) ||
      (state === "ready" && !draftModel),
    stopDisabled:
      !status?.controllerReachable ||
      state === "stopped" ||
      state === "stopping" ||
      pendingAction,
    restartDisabled:
      pendingAction ||
      loading ||
      Boolean(job) ||
      draftModel ||
      state !== "ready" ||
      Boolean(status?.conversationActive) ||
      audio.busy,
    conversationDisabled:
      !session ||
      state !== "ready" ||
      loading ||
      audio.busy ||
      busyElsewhere ||
      !(loaded && canUseCurrentAudio(current)),
  };
}
