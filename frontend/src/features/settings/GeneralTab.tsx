import { useRuntime, useSnapshot } from "../../app/workspace";
import { useTheme } from "../../app/theme";
import { TechnicalEvents } from "./TechnicalEvents";
export function GeneralTab() {
  const runtime = useRuntime(),
    workspace = useSnapshot(runtime);
  const diagnostics = useSnapshot(runtime.diagnostics),
    theme = useTheme();
  return (
    <section
      id="panel-general"
      role="tabpanel"
      aria-labelledby="tab-general"
      className="settings-panel"
      hidden={workspace.tab !== "general"}>
      {" "}
      <div className="panel-heading">
        {" "}
        <div>
          {" "}
          <h3>General</h3> <p>Make this workspace yours.</p>{" "}
        </div>{" "}
      </div>{" "}
      <section
        className="settings-section"
        aria-labelledby="appearance-heading">
        {" "}
        <h3 id="appearance-heading">Appearance</h3>{" "}
        <label className="field-label" htmlFor="theme">
          Color theme
        </label>{" "}
        <select
          id="theme"
          value={theme.theme}
          onChange={(event) =>
            theme.change(event.currentTarget.value as typeof theme.theme)
          }>
          {" "}
          <option value="system">System</option>{" "}
          <option value="light">Light</option>{" "}
          <option value="dark">Dark</option>{" "}
        </select>{" "}
      </section>{" "}
      <section
        className="settings-section"
        aria-labelledby="connection-heading">
        {" "}
        <div className="section-heading">
          {" "}
          <h3 id="connection-heading">Connection</h3>{" "}
          <span className="pill subtle">mTLS</span>{" "}
        </div>{" "}
        <label className="field-label" htmlFor="controller-url">
          {" "}
          Native controller{" "}
        </label>{" "}
        <input
          id="controller-url"
          type="url"
          readOnly
          placeholder="Configured in deployment"
          value={diagnostics.url || workspace.session?.controllerUrl || ""}
        />{" "}
        <p className="field-help">
          {" "}
          The address and certificates are configured in your Docker
          deployment.{" "}
        </p>{" "}
        <button
          type="button"
          id="test-connection"
          className="button secondary full-width"
          disabled={diagnostics.testing}
          onClick={() => {
            void runtime.testConnection();
          }}>
          {" "}
          Test connection <span aria-hidden="true">↗</span>{" "}
        </button>{" "}
        <p id="connection-feedback" className="field-help" role="status">
          {diagnostics.feedback}
        </p>{" "}
        <div className="detail-row">
          {" "}
          <span>Controller startup</span>{" "}
          <strong id="autostart-state">{diagnostics.autostart}</strong>{" "}
        </div>{" "}
        <details>
          {" "}
          <summary>Set up automatic startup</summary>{" "}
          <p className="field-help">
            {" "}
            On your Mac, run <code>nox-voice autostart enable</code> to start
            the controller after login. Use{" "}
            <code>nox-voice autostart disable</code> to return to manual
            startup. The model starts only when you request it.{" "}
          </p>{" "}
        </details>{" "}
        <details
          id="events-details"
          onToggle={(event) => {
            if (event.currentTarget.open) void runtime.diagnostics.loadEvents();
          }}>
          {" "}
          <summary>Technical events</summary>{" "}
          <ol id="events" className="events">
            <TechnicalEvents />
          </ol>{" "}
        </details>{" "}
      </section>{" "}
      <section
        className="settings-section account-section"
        aria-labelledby="account-heading">
        {" "}
        <h3 id="account-heading">Account</h3>{" "}
        <div className="account-row">
          {" "}
          <span className="avatar" id="avatar" aria-hidden="true">
            {workspace.session?.email[0].toUpperCase() || "N"}
          </span>{" "}
          <div>
            {" "}
            <strong id="account-email">
              {workspace.session?.email || "Signed in"}
            </strong>{" "}
            <span>Connected with Google</span>{" "}
          </div>{" "}
        </div>{" "}
        <button
          type="button"
          id="logout"
          className="button secondary full-width"
          onClick={() => {
            void runtime.logout();
          }}>
          {" "}
          Sign out{" "}
        </button>{" "}
      </section>{" "}
    </section>
  );
}
