import { expect, it, vi } from "vite-plus/test";
import type { TaskSummary } from "./task-types";

vi.mock("../../audio", () => ({
  ConversationAudio: class {
    busy = false;
    active = false;
    muted = false;
    stop() {}
  },
}));
vi.mock("../../lfm3-audio", () => ({
  LFM3Audio: class {
    active = true;
    busy = true;
    muted = false;
    recording = false;
    activity = "ready";
    latestTurn = 2;
    speak = vi.fn();
    stop() {}
    constructor(
      _end: unknown,
      _state: unknown,
      _transcript: unknown,
      _progress: unknown,
      readonly onTask: (task: TaskSummary) => void,
    ) {}
  },
}));
import { AudioSession } from "./audio-session";

const task = (id: string, turnId = 2): TaskSummary => ({
  taskId: id,
  sessionId: "session",
  turnId,
  state: "succeeded",
  transcript: "Question",
  output: "Actual answer",
  speech: "Short answer.",
  error: "",
  stepCount: 0,
});
type FakeLFM = {
  onTask: (task: TaskSummary) => void;
  activity: string;
  muted: boolean;
  speak: ReturnType<typeof vi.fn>;
};

it("presents an idle current-turn answer once and keeps older answers visible without interrupting a new turn", () => {
  const session = new AudioSession(vi.fn(), vi.fn()),
    lfm = session.lfm as unknown as FakeLFM;
  lfm.onTask(task("current"));
  lfm.onTask(task("current"));
  expect(lfm.speak).toHaveBeenCalledTimes(1);
  lfm.activity = "recording";
  lfm.onTask(task("while-speaking"));
  lfm.activity = "ready";
  lfm.onTask(task("old-turn", 1));
  expect(lfm.speak).toHaveBeenCalledTimes(1);
  expect(session.getSnapshot().tasks).toHaveLength(3);
});

it("keeps muted results visible and clears activity on session reset", () => {
  const session = new AudioSession(vi.fn(), vi.fn()),
    lfm = session.lfm as unknown as FakeLFM;
  lfm.muted = true;
  lfm.onTask(task("muted"));
  expect(lfm.speak).not.toHaveBeenCalled();
  expect(session.getSnapshot().tasks).toHaveLength(1);
  session.clearActivity();
  expect(session.getSnapshot().tasks).toEqual([]);
});
