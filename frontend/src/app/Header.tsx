import { useWorkspace } from "./workspace";
import { downloadIndicator } from "../features/models/library-types";
import { Icon } from "../components/ui/Icon";
export function Header() {
  const { runtime, workspace, library, view } = useWorkspace();
  const indicator = downloadIndicator(library.jobs);
  return (
    <header className="header" aria-label="Workspace">
      {" "}
      <a className="brand" href="/" aria-label="NoX Voice home">
        {" "}
        <span className="brand-mark" aria-hidden="true">
          {" "}
          n <span>·</span>{" "}
        </span>{" "}
        <span>
          {" "}
          NoX <strong>Voice</strong>{" "}
        </span>{" "}
      </a>{" "}
      <div
        className="header-actions"
        id="signed-in-actions"
        hidden={!workspace.session}>
        {" "}
        <button
          type="button"
          className="engine-badge"
          id="engine-badge"
          aria-label="Open engine settings"
          onClick={() => runtime.openSettings()}
          data-state={view.state}>
          {" "}
          <span className="status-dot"></span>{" "}
          <span id="header-state">{view.stateLabel}</span>{" "}
        </button>{" "}
        <button
          type="button"
          id="open-downloads"
          className="icon-button downloads-trigger"
          aria-label={indicator.label}
          title={indicator.label}
          onClick={() => runtime.openDownloads()}
          data-state={indicator.state}>
          {" "}
          <span id="downloads-icon">
            <Icon
              name={indicator.state === "attention" ? "attention" : "download"}
            />
          </span>{" "}
          <span
            id="download-count"
            className="download-count"
            hidden={!indicator.count}>
            {String(indicator.count)}
          </span>{" "}
        </button>{" "}
        <button
          type="button"
          className="icon-button settings-trigger"
          id="open-settings"
          aria-label="Open settings"
          title="Settings"
          onClick={() => runtime.openSettings()}>
          {" "}
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            aria-hidden="true">
            {" "}
            <path d="m9.5 3-.8 2.5-2.5.6-2.1 2.7 1.4 2.2-.1 2.5-1.4 2.2 2 2.8 2.6.6 1.5 2.1h3.8l1.4-2.2 2.6-.6 2-2.8-1.4-2.1v-2.6l1.4-2.1-2-2.8-2.6-.6L14.5 3z" />{" "}
            <circle cx="12" cy="12" r="3.1" />{" "}
          </svg>{" "}
        </button>{" "}
      </div>{" "}
      <span
        className="header-note"
        id="header-note"
        hidden={Boolean(workspace.session)}>
        {" "}
        Your personal voice workspace{" "}
      </span>{" "}
    </header>
  );
}
