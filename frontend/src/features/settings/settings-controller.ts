import { ApiError } from "../../api";
import {
  legacyProvider,
  updatedProfile,
  type ProviderCatalogue,
  type ProviderDescription,
  type VoiceConfiguration,
  type VoiceSettings,
} from "../../providers";
import type { Api } from "../../services/client";
import { Observable, errorMessage, isAborted } from "../../services/observable";
type Profile = Record<string, unknown>;
type State = {
  providers: ProviderDescription[];
  configuration: VoiceConfiguration | null;
  selected: string;
  drafts: Record<string, Profile>;
  loaded: boolean;
  loading: boolean;
  saving: boolean;
  feedback: string;
  conflict: boolean;
};
const initial = (): State => ({
  providers: [],
  configuration: null,
  selected: "personaplex",
  drafts: {},
  loaded: false,
  loading: false,
  saving: false,
  feedback: "",
  conflict: false,
});
export class SettingsController extends Observable<State> {
  private generation = 0;
  constructor(
    private api: Api,
    private applied: () => void,
  ) {
    super(initial());
  }
  reset(): void {
    this.generation++;
    this.update(initial());
  }
  async load(force = false): Promise<void> {
    if (this.state.loading || (this.state.loaded && !force)) return;
    const generation = this.generation;
    this.update({ loading: true });
    try {
      let providers: ProviderDescription[],
        configuration: VoiceConfiguration | null,
        drafts: Record<string, Profile>;
      try {
        const [catalogue, saved] = await Promise.all([
          this.api<ProviderCatalogue>("/api/voice/v1/providers"),
          this.api<VoiceConfiguration>("/api/voice/v1/settings"),
        ]);
        if (catalogue.version !== 1 || saved.schemaVersion !== 1)
          throw new Error(
            "This controller uses an unsupported settings version.",
          );
        providers = catalogue.providers;
        configuration = saved;
        drafts = saved.profiles;
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 404) throw error;
        providers = [legacyProvider()];
        configuration = null;
        drafts = {
          personaplex: { ...(await this.api<VoiceSettings>("/api/settings")) },
        };
      }
      if (generation !== this.generation) return;
      this.update({
        providers,
        configuration,
        drafts,
        selected: configuration?.selectedProvider || providers[0].id,
        loaded: true,
        conflict: false,
        feedback: force ? "Saved settings reloaded." : this.state.feedback,
      });
      this.applied();
    } catch (error) {
      if (generation === this.generation && !isAborted(error))
        this.update({ feedback: errorMessage(error) });
    } finally {
      if (generation === this.generation) this.update({ loading: false });
    }
  }
  setFeedback(feedback: string): void {
    this.update({ feedback });
  }
  select(selected: string): void {
    this.update({ selected });
  }
  edit(field: string, value: unknown): void {
    const selected = this.state.selected;
    this.update({
      drafts: {
        ...this.state.drafts,
        [selected]: { ...this.state.drafts[selected], [field]: value },
      },
    });
  }
  async save(): Promise<boolean> {
    if (this.state.saving) return false;
    const generation = this.generation;
    const { selected, drafts, configuration, providers } = this.state;
    const draft = drafts[selected] || {};
    const checking = providers.find((p) => p.id === selected)?.capabilities
      .audioChecks;
    const value = checking
      ? {
          inputLanguage: draft.inputLanguage ?? "",
          outputLanguage: draft.outputLanguage ?? "",
          voice: draft.voice ?? "",
          vocabulary: draft.vocabulary ?? "",
        }
      : {
          voice: draft.voice ?? "",
          prompt: draft.prompt ?? "",
          bits: Number(draft.bits ?? 4),
        };
    this.update({ saving: true, feedback: "Applying changes…" });
    try {
      const saved = configuration
        ? await this.api<VoiceConfiguration>(
            "/api/voice/v1/settings",
            "PUT",
            updatedProfile(configuration, selected, value),
          )
        : (await this.api("/api/settings", "PUT", value), null);
      if (generation !== this.generation) return false;
      this.update({
        configuration: saved,
        conflict: false,
        feedback: "Your settings have been saved.",
      });
      this.applied();
      return true;
    } catch (error) {
      if (generation === this.generation && !isAborted(error))
        this.update({
          feedback: errorMessage(error),
          conflict: error instanceof ApiError && error.status === 409,
        });
      return false;
    } finally {
      if (generation === this.generation) this.update({ saving: false });
    }
  }
}
