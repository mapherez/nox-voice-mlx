import type { Api } from "../../services/client";
import { Observable, errorMessage, isAborted } from "../../services/observable";
export type TechnicalEvent = { time: number; message: string };
type State = {
  testing: boolean;
  feedback: string;
  url: string;
  autostart: string;
  events: TechnicalEvent[] | null;
  eventsError: string;
};
const initial = (): State => ({
  testing: false,
  feedback: "",
  url: "",
  autostart: "—",
  events: null,
  eventsError: "",
});
export class DiagnosticsController extends Observable<State> {
  private generation = 0;
  constructor(private api: Api) {
    super(initial());
  }
  reset(): void {
    this.generation++;
    this.update(initial());
  }
  async test(): Promise<boolean> {
    if (this.state.testing) return false;
    const generation = this.generation;
    this.update({ testing: true, feedback: "Testing secure connection…" });
    try {
      const result = await this.api<{
        controllerUrl: string;
        autostartEnabled: boolean;
        missingFiles: string[];
      }>("/api/diagnostics");
      if (generation !== this.generation) return false;
      this.update({
        url: result.controllerUrl,
        autostart: result.autostartEnabled ? "Automatic after login" : "Manual",
        feedback: result.missingFiles.length
          ? "Connected securely. " +
            result.missingFiles.length +
            " selected-model files need preparation. Use Settings or the model preparation command in the setup guide."
          : "Connected. The controller's identity and your client certificate are verified.",
      });
      return true;
    } catch (error) {
      if (generation === this.generation && !isAborted(error))
        this.update({ feedback: errorMessage(error) });
      return false;
    } finally {
      if (generation === this.generation) this.update({ testing: false });
    }
  }
  async loadEvents(): Promise<void> {
    const generation = this.generation;
    try {
      const { events } = await this.api<{ events: TechnicalEvent[] }>(
        "/api/events",
      );
      if (generation === this.generation)
        this.update({ events: events.slice(-20).reverse(), eventsError: "" });
    } catch (error) {
      if (generation === this.generation && !isAborted(error))
        this.update({
          eventsError: "Events are unavailable while the Mac is disconnected.",
        });
    }
  }
}
