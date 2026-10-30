import { useRef, type KeyboardEvent } from "react";
import { useRuntime, useSnapshot } from "../../app/workspace";
import { useMediaQuery } from "../../components/ui/use-media-query";
import type { SettingsTab } from "../session/types";
const tabs: SettingsTab[] = ["models", "voice", "credentials", "general"];
export function SettingsTabs() {
  const runtime = useRuntime(),
    workspace = useSnapshot(runtime);
  const mobile = useMediaQuery("(max-width: 680px)");
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  function navigate(event: KeyboardEvent, index: number) {
    let next = index;
    if (["ArrowRight", "ArrowDown"].includes(event.key))
      next = (index + 1) % tabs.length;
    else if (["ArrowLeft", "ArrowUp"].includes(event.key))
      next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault();
    runtime.selectTab(tabs[next]);
    buttons.current[next]?.focus();
  }
  return (
    <div
      className="settings-tabs"
      role="tablist"
      aria-label="Settings sections"
      aria-orientation={mobile ? "horizontal" : "vertical"}>
      {" "}
      <button
        type="button"
        id="tab-models"
        role="tab"
        aria-controls="panel-models"
        aria-selected={workspace.tab === "models"}
        tabIndex={workspace.tab === "models" ? 0 : -1}
        data-settings-tab="models"
        onClick={() => runtime.selectTab("models")}
        onKeyDown={(event) => navigate(event, 0)}
        ref={(node) => {
          buttons.current[0] = node;
        }}>
        {" "}
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true">
          {" "}
          <rect x="3" y="3" width="18" height="7" rx="2" />{" "}
          <rect x="3" y="14" width="18" height="7" rx="2" />{" "}
          <path d="M7 6.5h.01M7 17.5h.01" />{" "}
        </svg>{" "}
        <span>Models</span>{" "}
      </button>{" "}
      <button
        type="button"
        id="tab-voice"
        role="tab"
        aria-controls="panel-voice"
        aria-selected={workspace.tab === "voice"}
        tabIndex={workspace.tab === "voice" ? 0 : -1}
        data-settings-tab="voice"
        onClick={() => runtime.selectTab("voice")}
        onKeyDown={(event) => navigate(event, 1)}
        ref={(node) => {
          buttons.current[1] = node;
        }}>
        {" "}
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true">
          {" "}
          <path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3ZM5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8" />{" "}
        </svg>{" "}
        <span>Voice</span>{" "}
      </button>{" "}
      <button
        type="button"
        id="tab-credentials"
        role="tab"
        aria-controls="panel-credentials"
        aria-selected={workspace.tab === "credentials"}
        tabIndex={workspace.tab === "credentials" ? 0 : -1}
        data-settings-tab="credentials"
        onClick={() => runtime.selectTab("credentials")}
        onKeyDown={(event) => navigate(event, 2)}
        ref={(node) => {
          buttons.current[2] = node;
        }}>
        {" "}
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true">
          {" "}
          <circle cx="8" cy="8" r="5" />{" "}
          <path d="m12 12 9 9m-4-4 3-3m-6 0 3-3" />{" "}
        </svg>{" "}
        <span>Credentials</span>{" "}
      </button>{" "}
      <button
        type="button"
        id="tab-general"
        role="tab"
        aria-controls="panel-general"
        aria-selected={workspace.tab === "general"}
        tabIndex={workspace.tab === "general" ? 0 : -1}
        data-settings-tab="general"
        onClick={() => runtime.selectTab("general")}
        onKeyDown={(event) => navigate(event, 3)}
        ref={(node) => {
          buttons.current[3] = node;
        }}>
        {" "}
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true">
          {" "}
          <path d="M4 6h16M4 12h16M4 18h16" /> <circle
            cx="9"
            cy="6"
            r="2"
          />{" "}
          <circle cx="15" cy="12" r="2" /> <circle cx="9" cy="18" r="2" />{" "}
        </svg>{" "}
        <span>General</span>{" "}
      </button>{" "}
    </div>
  );
}
