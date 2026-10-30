export type Session = {
  email: string;
  csrfToken: string;
  expiresAt: number;
  controllerUrl: string;
};
export type EngineStatus = {
  state:
    | "stopped"
    | "starting"
    | "ready"
    | "stopping"
    | "error"
    | "unreachable";
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
export type SettingsTab = "models" | "voice" | "credentials" | "general";
