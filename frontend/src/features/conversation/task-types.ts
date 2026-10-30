export const taskLabels = {
  queued: "Queued",
  planning: "Thinking",
  executing: "Running a tool",
  awaiting_input: "Needs your input",
  cancel_requested: "Stopping",
  succeeded: "Completed",
  partially_succeeded: "Partially completed",
  failed: "Failed",
  cancelled: "Cancelled",
  outcome_unknown: "Outcome unknown",
} as const;
export type TaskSummary = {
  taskId: string;
  sessionId: string;
  turnId: number;
  state: keyof typeof taskLabels;
  transcript: string;
  output: string;
  speech: string;
  error: string;
  stepCount: number;
};
export function isTaskSummary(value: unknown): value is TaskSummary {
  if (!value || typeof value !== "object") return false;
  const task = value as Partial<TaskSummary>;
  return (
    typeof task.taskId === "string" &&
    /^[a-f0-9]{32}$/.test(task.taskId) &&
    typeof task.sessionId === "string" &&
    typeof task.state === "string" &&
    Object.hasOwn(taskLabels, task.state) &&
    Number.isInteger(task.turnId) &&
    typeof task.transcript === "string" &&
    typeof task.output === "string" &&
    typeof task.speech === "string" &&
    typeof task.error === "string" &&
    Number.isInteger(task.stepCount)
  );
}
