import { useWorkspace } from "../../app/workspace";
export function SettingsFooter() {
  const { runtime, workspace, settings, view } = useWorkspace();
  return (
    <footer
      className="drawer-footer settings-footer"
      aria-label="Settings actions">
      {" "}
      <div>
        {" "}
        <strong
          id="model-memory-label"
          className="memory-status-label"
          hidden={workspace.tab !== "models"}>
          {view.memoryLabel}
        </strong>{" "}
        <p id="settings-feedback" className="field-help" role="status">
          {settings.feedback}
        </p>{" "}
        <button
          type="button"
          id="reload-settings"
          className="text-button"
          hidden={!settings.conflict}
          onClick={() => {
            void runtime.settings.load(true);
          }}>
          {" "}
          Reload saved settings{" "}
        </button>{" "}
        <span className="privacy-note">
          Conversation audio is not recorded.
        </span>{" "}
      </div>{" "}
      <div id="model-lifecycle-actions" hidden={workspace.tab !== "models"}>
        {" "}
        <div className="engine-buttons">
          {" "}
          <button
            type="button"
            className="button primary"
            id="start-engine"
            disabled={view.startDisabled}
            onClick={() => {
              void runtime.engine("start");
            }}>
            {" "}
            Load{" "}
          </button>{" "}
          <button
            type="button"
            className="button secondary"
            id="stop-engine"
            disabled={view.stopDisabled}
            onClick={() => {
              void runtime.engine("stop");
            }}>
            {" "}
            Eject{" "}
          </button>{" "}
          <button
            type="button"
            className="text-button"
            id="restart-engine"
            disabled={view.restartDisabled}
            onClick={() => {
              void runtime.engine("restart");
            }}>
            {" "}
            Reload{" "}
          </button>{" "}
        </div>{" "}
      </div>{" "}
      <button
        type="button"
        id="save-settings"
        className="button secondary"
        hidden={workspace.tab !== "voice"}
        disabled={view.saveDisabled}
        onClick={() => {
          void runtime.saveSettings();
        }}>
        {" "}
        Save settings{" "}
      </button>{" "}
    </footer>
  );
}
