import { ConversationAudio } from "../../audio";
import { LFM3Audio, type AudioCheckState } from "../../lfm3-audio";
import { Observable, errorMessage } from "../../services/observable";
import type { TaskSummary } from "./task-types";
type State = {
  active: boolean;
  busy: boolean;
  muted: boolean;
  checking: boolean;
  recording: boolean;
  activity: AudioCheckState;
  transcript: string;
  latency: string;
  rtf: string;
  executorEnabled: boolean;
  tasks: TaskSummary[];
};
export class AudioSession extends Observable<State> {
  readonly plex: ConversationAudio;
  readonly lfm: LFM3Audio;
  private current: ConversationAudio | LFM3Audio;
  private generation = 0;
  private presented = new Set<string>();
  constructor(
    private notice: (message: string) => void,
    private ended: () => void,
  ) {
    super({
      active: false,
      busy: false,
      muted: false,
      checking: false,
      recording: false,
      activity: "disconnected",
      transcript: "",
      latency: "—",
      rtf: "—",
      executorEnabled: false,
      tasks: [],
    });
    const onEnd = (message: string) => {
      this.notice(message);
      this.publish();
      this.ended();
    };
    this.plex = new ConversationAudio(
      onEnd,
      (stats) =>
        this.update({
          latency: stats.avg_latency_ms.toFixed(0),
          rtf: stats.rtf.toFixed(2) + "×",
        }),
      (message) => {
        this.notice(message);
        this.publish();
      },
    );
    this.lfm = new LFM3Audio(
      onEnd,
      () => this.publish(),
      (transcript) => this.update({ transcript }),
      (message) => this.notice(message),
      (task) => {
        const tasks = this.state.tasks.filter(
          (value) => value.taskId !== task.taskId,
        );
        this.update({ tasks: [...tasks, task] });
        if (task.speech && !this.presented.has(task.taskId)) {
          this.presented.add(task.taskId);
          if (
            this.lfm.active &&
            !this.lfm.muted &&
            this.lfm.activity === "ready" &&
            task.turnId === this.lfm.latestTurn
          )
            this.lfm.speak(task.speech);
        }
      },
    );
    this.current = this.plex;
  }
  select(checking: boolean): void {
    if (this.current.busy) return;
    const next = checking ? this.lfm : this.plex;
    if (next !== this.current) {
      this.current = next;
      this.publish();
    }
  }
  publish(): void {
    if (!this.current) return;
    this.update({
      active: this.current.active,
      busy: this.current.busy,
      muted: this.current.muted,
      checking: this.current === this.lfm,
      recording: this.lfm.recording,
      activity: this.lfm.activity,
      executorEnabled: this.lfm.executorEnabled,
    });
  }
  assistantEnergy = (): number => this.current.assistantEnergy();
  async start(): Promise<void> {
    const generation = ++this.generation;
    this.notice("");
    this.update({ latency: "—", rtf: "—" });
    if (this.current === this.lfm) this.clearActivity();
    // Invoke directly from the click, before an await/effect loses the gesture.
    const promise = this.current.start();
    this.publish();
    try {
      await promise;
    } catch (error) {
      if (generation === this.generation)
        this.notice(
          errorMessage(error) ||
            "Could not start audio. Check microphone permission.",
        );
    } finally {
      if (generation === this.generation) this.publish();
    }
  }
  stop(): void {
    this.generation++;
    this.plex.stop();
    this.lfm.stop();
    this.publish();
  }
  toggleMute(): void {
    this.current.toggleMute();
    this.publish();
  }
  speak(text: string): void {
    this.lfm.speak(text);
  }
  interruptSpeech(): void {
    this.lfm.interruptSpeech();
  }
  clearActivity(): void {
    this.presented.clear();
    this.update({ tasks: [] });
  }
}
