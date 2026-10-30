import { useRuntime, useSnapshot } from "../../app/workspace";
import { CredentialsList } from "../credentials/CredentialsList";
export function CredentialsTab() {
  const runtime = useRuntime();
  const workspace = useSnapshot(runtime);
  return (
    <section
      id="panel-credentials"
      role="tabpanel"
      aria-labelledby="tab-credentials"
      className="settings-panel"
      hidden={workspace.tab !== "credentials"}>
      {" "}
      <div className="panel-heading">
        {" "}
        <div>
          {" "}
          <h3>Credentials</h3>{" "}
          <p>Connect the services your models and tools need.</p>{" "}
        </div>{" "}
      </div>{" "}
      <p className="field-help">
        {" "}
        Credentials are stored privately on the controller Mac. Saved tokens are
        never shown here.{" "}
      </p>{" "}
      <div id="credentials-list">
        <CredentialsList />
      </div>{" "}
    </section>
  );
}
