import { useLayoutEffect, useRef } from "react";
import { useRuntime, useSnapshot } from "../../app/workspace";
import { Dialog } from "../../components/ui/Dialog";
import { SettingsTabs } from "./SettingsTabs";
import { ModelsTab } from "./ModelsTab";
import { VoiceTab } from "./VoiceTab";
import { CredentialsTab } from "./CredentialsTab";
import { GeneralTab } from "./GeneralTab";
import { SettingsFooter } from "./SettingsFooter";
import { ExecutorTab } from "./ExecutorTab";
export function SettingsDialog() {
  const runtime = useRuntime(),
    workspace = useSnapshot(runtime);
  const content = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    content.current?.scrollTo(0, 0);
  }, [workspace.tab]);
  return (
    <Dialog
      id="settings"
      className="settings-drawer"
      labelledBy="settings-title"
      open={workspace.settingsOpen}
      onClose={runtime.closeSettings}
      dismissOutsideX
      tab={workspace.tab}>
      <header className="drawer-header" aria-label="Settings">
        {" "}
        <div>
          {" "}
          <span className="eyebrow">YOUR WORKSPACE</span>{" "}
          <h2 id="settings-title">Settings</h2>{" "}
        </div>{" "}
        <button
          type="button"
          id="close-settings"
          className="icon-button"
          aria-label="Close settings"
          onClick={runtime.closeSettings}>
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
            <path d="m6 6 12 12M18 6 6 18" />{" "}
          </svg>{" "}
        </button>{" "}
      </header>
      <div className="settings-layout">
        <SettingsTabs />
        <div className="drawer-content" ref={content}>
          <ModelsTab />
          <VoiceTab />
          <CredentialsTab />
          <GeneralTab />
          <ExecutorTab />
        </div>
      </div>
      <SettingsFooter />
    </Dialog>
  );
}
