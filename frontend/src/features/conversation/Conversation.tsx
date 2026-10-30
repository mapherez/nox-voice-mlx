import { useWorkspace } from "../../app/workspace";
import { useMediaQuery } from "../../components/ui/use-media-query";
import { AudioChecks } from "./AudioChecks";
import { VoiceOrb } from "./VoiceOrb";
import { AssistantIndicator } from "./AssistantIndicator";
import { TaskActivity } from "./TaskActivity";
export function Conversation() {
  const { runtime, workspace, audio, view } = useWorkspace();
  const touch = useMediaQuery("(pointer: coarse), (max-width: 1024px)");
  let title = "A space to talk.",
    subtitle = "Sign in to start a conversation.";
  if (workspace.session) {
    if (audio.active) {
      title = "You're connected.";
      subtitle = audio.checking
        ? touch
          ? "Press and hold the button below to speak. Release to transcribe."
          : "Hold Space or the button below to speak. Release to transcribe."
        : "Speak naturally. You can interrupt at any time.";
    } else if (audio.busy) {
      title = "Getting connected.";
      subtitle = "Preparing your audio connection…";
    } else if (view.busyElsewhere) {
      title = "A conversation is active.";
      subtitle = "End the conversation on your other device to continue here.";
    } else if (view.state === "ready" && !view.loading) {
      title = "Ready when you are.";
      subtitle = audio.checking
        ? "Use LFM3 for audio checks, or enable answers in Executor & MCP settings."
        : "Start a conversation. Make it your own.";
    } else if (view.loading) {
      title = view.state !== "stopping" ? "Getting ready." : "Winding down.";
      subtitle = view.phase;
    } else
      subtitle =
        view.state === "unreachable"
          ? "Your native controller is currently unavailable."
          : "Load a model in Settings to begin.";
  }
  return (
    <main className="conversation">
      {" "}
      <div className="model-label">
        {" "}
        <span className="mini-spark" aria-hidden="true">
          ✧
        </span>{" "}
        <span id="active-model-name">{view.current?.name || "NoX Voice"}</span>{" "}
        <span className="model-divider"></span> LOCAL ENGINE{" "}
      </div>{" "}
      <h1 id="conversation-title">{title}</h1>{" "}
      <p className="subtitle" id="conversation-subtitle">
        {subtitle}
      </p>{" "}
      <VoiceOrb /> <AssistantIndicator />{" "}
      <div
        id="login-panel"
        className="login-panel"
        hidden={Boolean(workspace.session)}>
        {" "}
        <a className="button primary google-button" href="/auth/google/start">
          {" "}
          <span className="google-mark" aria-hidden="true">
            G
          </span>{" "}
          Continue with Google <span aria-hidden="true">↗</span>{" "}
        </a>{" "}
        <p className="fine-print">
          {" "}
          Access is limited to your authorized account.{" "}
        </p>{" "}
      </div>{" "}
      <div
        className="conversation-actions"
        id="conversation-actions"
        hidden={!workspace.session}>
        {" "}
        <button
          type="button"
          id="start-conversation"
          className="button primary start-button"
          hidden={audio.active}
          disabled={view.conversationDisabled}
          onClick={() => {
            void runtime.audio.start();
          }}>
          {" "}
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            aria-hidden="true">
            {" "}
            <rect x="8" y="3" width="8" height="12" rx="4" />{" "}
            <path d="M5 10a7 7 0 0 0 14 0M12 17v4m-4 0h8" />{" "}
          </svg>{" "}
          <span id="start-conversation-label">
            {audio.checking ? "Connect LFM3" : "Start conversation"}
          </span>{" "}
        </button>{" "}
        <button
          type="button"
          id="mute"
          className="button secondary"
          hidden={!audio.active}
          aria-pressed={audio.muted}
          onClick={() => runtime.audio.toggleMute()}>
          {audio.muted ? "Unmute" : "Mute"}
        </button>{" "}
        <button
          type="button"
          id="end-conversation"
          className="button end-button"
          hidden={!audio.active}
          onClick={() => runtime.endConversation()}>
          {" "}
          <span className="stop-square" aria-hidden="true"></span> End
          conversation{" "}
        </button>{" "}
      </div>{" "}
      <AudioChecks /> <TaskActivity />
      <button
        type="button"
        id="engine-hint"
        className="text-button"
        hidden={!workspace.session || audio.busy || view.state === "ready"}
        onClick={() => runtime.openSettings()}>
        {view.state === "unreachable"
          ? "Open Settings to check your controller connection ↗"
          : "Open Settings to load a model ↗"}
      </button>{" "}
      <div
        id="notice"
        className="notice"
        role="status"
        aria-live="polite"
        hidden={!workspace.notice}>
        {workspace.notice}
      </div>{" "}
      <div
        id="conversation-metrics"
        className="conversation-metrics"
        hidden={!(workspace.session && audio.active && !audio.checking)}>
        {" "}
        <span>
          {" "}
          <span id="latency">{audio.latency}</span>{" "}
          <small>ms latency</small>{" "}
        </span>{" "}
        <span className="metric-divider"></span>{" "}
        <span>
          {" "}
          <span id="rtf">{audio.rtf}</span> <small>realtime factor</small>{" "}
        </span>{" "}
      </div>{" "}
    </main>
  );
}
