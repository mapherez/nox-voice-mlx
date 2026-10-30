import { useState } from "react";
import { useWorkspace } from "../../app/workspace";
import { useMediaQuery } from "../../components/ui/use-media-query";
export function AudioChecks() {
  const { runtime, workspace, audio } = useWorkspace();
  const [speech, setSpeech] = useState("");
  const touch = useMediaQuery("(pointer: coarse), (max-width: 1024px)");
  const activity = {
    disconnected: "Disconnected",
    connecting: "Connecting…",
    ready: "Ready to listen",
    recording: "Listening — release to transcribe",
    transcribing: "Transcribing…",
    speaking: touch
      ? "Reading aloud — press and hold the button to interrupt"
      : "Reading aloud — hold Space to interrupt",
  }[audio.activity];
  return (
    <section
      id="audio-checks"
      className="audio-checks"
      aria-label="Audio checks"
      hidden={!(workspace.session && audio.active && audio.checking)}>
      {" "}
      <p className="field-help">
        {" "}
        Audio checks only: transcribe your speech or read text aloud.
        Conversation and app actions will be added in the next step.{" "}
      </p>{" "}
      <button
        type="button"
        id="hold-to-talk"
        className="button primary"
        aria-pressed={audio.recording}
        disabled={!audio.active || audio.muted}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          event.preventDefault();
          if (runtime.hold.beginPointer(event.pointerId))
            event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerUp={(event) => runtime.hold.endPointer(event.pointerId)}
        onPointerCancel={(event) => runtime.hold.endPointer(event.pointerId)}
        onLostPointerCapture={(event) =>
          runtime.hold.endPointer(event.pointerId)
        }>
        {touch ? "Press here to speak" : "Hold Space or press here to speak"}
      </button>{" "}
      <p id="audio-check-state" role="status" aria-live="polite">
        {activity}
      </p>{" "}
      <div className="transcript-result">
        {" "}
        <span className="eyebrow">TRANSCRIPT</span>{" "}
        <p id="transcript" aria-live="polite">
          {audio.transcript || "Your transcript will appear here."}
        </p>{" "}
      </div>{" "}
      <label className="field-label" htmlFor="speech-text">
        {" "}
        Text to read aloud{" "}
      </label>{" "}
      <textarea
        id="speech-text"
        rows={3}
        maxLength={2000}
        placeholder="Enter text to check the selected voice."
        value={speech}
        onChange={(event) =>
          setSpeech(event.currentTarget.value)
        }></textarea>{" "}
      <div className="engine-buttons">
        {" "}
        <button
          type="button"
          id="read-aloud"
          className="button secondary"
          disabled={!audio.active || audio.recording || !speech.trim()}
          onClick={() => runtime.audio.speak(speech)}>
          {" "}
          Read aloud{" "}
        </button>{" "}
        <button
          type="button"
          id="stop-speech"
          className="button secondary"
          disabled={audio.activity !== "speaking"}
          onClick={() => runtime.audio.interruptSpeech()}>
          {" "}
          Stop speech{" "}
        </button>{" "}
      </div>{" "}
    </section>
  );
}
