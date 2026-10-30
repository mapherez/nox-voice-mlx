import { useEffect, useState } from "react";
import { useRuntime, useSnapshot } from "../../app/workspace";
export function AssistantIndicator() {
  const { audio } = useRuntime();
  const { active } = useSnapshot(audio);
  const [speaking, setSpeaking] = useState(false);
  useEffect(() => {
    const timer = window.setInterval(
      () => setSpeaking(audio.assistantEnergy() > 0.025),
      150,
    );
    return () => clearInterval(timer);
  }, [audio]);
  return (
    <div
      className="assistant-indicator"
      id="assistant-indicator"
      data-speaking={speaking}>
      <span />
      <span id="assistant-label">
        {active
          ? speaking
            ? "Assistant speaking"
            : "Assistant is ready"
          : "Made for natural conversation"}
      </span>
    </div>
  );
}
