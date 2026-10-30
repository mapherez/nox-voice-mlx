import { useWorkspace } from "../../app/workspace";
export function ModelMemory() {
  const { view } = useWorkspace();
  return (
    <section className="settings-section engine-section">
      {" "}
      <div className="section-heading">
        {" "}
        <h3>Model memory</h3>{" "}
        <span id="drawer-state" className="pill">
          {view.stateLabel}
        </span>{" "}
      </div>{" "}
      <div className="engine-summary">
        {" "}
        <span className="engine-symbol" aria-hidden="true">
          ✧
        </span>{" "}
        <div>
          {" "}
          <strong id="loaded-model-name">{view.loadedName}</strong>{" "}
          <span id="engine-phase">{view.phaseLabel}</span>{" "}
        </div>{" "}
        <span id="engine-memory">{view.memory}</span>{" "}
      </div>{" "}
      <div
        id="engine-progress"
        className="engine-progress"
        hidden={!view.loading}>
        {" "}
        <span></span>{" "}
      </div>{" "}
      <p
        id="engine-error"
        className="inline-error"
        role="status"
        hidden={!view.engineError}>
        {view.engineError}
      </p>{" "}
      <p className="field-help">
        {" "}
        Eject releases model memory and keeps your downloaded files. Downloads
        never load a model automatically.{" "}
      </p>{" "}
    </section>
  );
}
