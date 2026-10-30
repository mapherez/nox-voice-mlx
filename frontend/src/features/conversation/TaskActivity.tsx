import { useState } from "react";
import { useWorkspace } from "../../app/workspace";
import { taskLabels } from "./task-types";

const terminal = new Set([
  "succeeded",
  "partially_succeeded",
  "failed",
  "cancelled",
  "outcome_unknown",
]);
export function TaskActivity() {
  const { runtime, workspace, audio } = useWorkspace();
  const [detail, setDetail] = useState<{ id: string; text: string } | null>(
    null,
  );
  const [feedback, setFeedback] = useState("");
  async function inspect(sessionId: string, taskId: string) {
    try {
      const value = await runtime.client.request(
        `/api/voice/v1/sessions/${sessionId}/tasks/${taskId}`,
      );
      if (
        runtime.audio.getSnapshot().tasks.some((task) => task.taskId === taskId)
      )
        setDetail({ id: taskId, text: JSON.stringify(value, null, 2) });
      setFeedback("");
    } catch {
      setFeedback(
        "Task details are unavailable. The controller may have ended this session.",
      );
    }
  }
  if (!workspace.session || !audio.checking || !audio.tasks.length) return null;
  return (
    <section className="task-activity" aria-label="Task activity">
      <h3>Activity</h3>
      <p className="field-help" role="status">
        {feedback}
      </p>
      {audio.tasks.map((task) => (
        <article className="task-entry" key={task.taskId}>
          <div className="task-heading">
            <strong>{taskLabels[task.state]}</strong>
            <span className="field-help">
              {task.stepCount} tool {task.stepCount === 1 ? "call" : "calls"}
            </span>
          </div>
          <p className="task-transcript">{task.transcript}</p>
          <p className="task-output">{task.output || task.error}</p>
          <div className="engine-buttons">
            {!terminal.has(task.state) ? (
              <button
                type="button"
                className="text-button"
                disabled={!audio.active || task.state === "cancel_requested"}
                onClick={() => runtime.audio.lfm.cancelTask(task.taskId)}>
                Cancel task
              </button>
            ) : null}
            {task.speech ? (
              <button
                type="button"
                className="text-button"
                disabled={!audio.active || audio.recording}
                onClick={() => runtime.audio.speak(task.speech)}>
                Read response
              </button>
            ) : null}
            <button
              type="button"
              className="text-button"
              onClick={() => {
                void inspect(task.sessionId, task.taskId);
              }}>
              View full result
            </button>
          </div>
          {detail?.id === task.taskId ? (
            <details open>
              <summary>Transcript, steps and receipts</summary>
              <pre className="task-receipts">{detail.text}</pre>
            </details>
          ) : null}
        </article>
      ))}
    </section>
  );
}
