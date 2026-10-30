import { useEffect, useRef } from "react";
import { useRuntime, useSnapshot } from "../../app/workspace";
import type { Credential } from "../models/library-types";

export function CredentialsList() {
  const { library } = useRuntime();
  const { services } = useSnapshot(library);
  if (!services.length)
    return <p className="field-help">Connecting to your controller…</p>;
  return services.map((service) => (
    <CredentialCard key={service.id} service={service} />
  ));
}
function CredentialCard({ service }: { service: Credential }) {
  const runtime = useRuntime(),
    workspace = useSnapshot(runtime),
    library = useSnapshot(runtime.library);
  const input = useRef<HTMLInputElement>(null);
  const busy = library.credentialBusy.has(service.id);
  const reachable = Boolean(
    workspace.session && workspace.status?.controllerReachable,
  );
  useEffect(() => {
    if (
      workspace.focusCredential === service.id &&
      workspace.tab === "credentials"
    )
      input.current?.focus();
  }, [workspace.focusCredential, workspace.tab, service.id]);
  function act(action: "save" | "test" | "remove") {
    const secret = input.current?.value.trim();
    if (action === "save" && !secret) {
      input.current?.focus();
      return;
    }
    if (input.current) input.current.value = "";
    void runtime.library.credentialAction(
      service.id,
      action,
      action === "save" ? secret : undefined,
    );
  }
  return (
    <section className="credential-card" id={"service-" + service.id}>
      <div className="section-heading">
        <h3>{service.name}</h3>
        <span className="pill subtle" id={"credential-state-" + service.id}>
          {service.configured ? "Token saved" : "Not configured"}
        </span>
      </div>
      <p className="field-help">{service.help}</p>
      <label className="field-label" htmlFor={"credential-" + service.id}>
        {service.fieldLabel}
      </label>
      <input
        id={"credential-" + service.id}
        ref={input}
        type="password"
        autoComplete="off"
        spellCheck={false}
        maxLength={512}
        placeholder="Paste a new token to save or replace it"
      />
      <p className="credential-links">
        {service.tokenUrl && (
          <a href={service.tokenUrl} target="_blank" rel="noopener noreferrer">
            Create a token ↗
          </a>
        )}
        {service.accessUrl && (
          <a href={service.accessUrl} target="_blank" rel="noopener noreferrer">
            Model access terms ↗
          </a>
        )}
      </p>
      <div className="credential-actions">
        <button
          type="button"
          id={"credential-save-" + service.id}
          className="button secondary"
          disabled={busy || !reachable}
          onClick={() => act("save")}>
          Save token
        </button>
        <button
          type="button"
          id={"credential-test-" + service.id}
          className="text-button"
          hidden={service.testSupported === false}
          disabled={busy || !reachable || !service.configured}
          onClick={() => act("test")}>
          Test access
        </button>
        <button
          type="button"
          id={"credential-remove-" + service.id}
          className="text-button"
          disabled={busy || !reachable || !service.configured}
          onClick={() => act("remove")}>
          Remove
        </button>
      </div>
      <p
        className="field-help"
        role="status"
        id={"credential-feedback-" + service.id}>
        {library.credentialFeedback[service.id] ?? service.test?.message ?? ""}
      </p>
    </section>
  );
}
