import { useWorkspace } from "../../app/workspace";
import { canUseCurrentAudio } from "../../providers";
import {
  bytes,
  downloadLabels,
  downloadIndicator,
} from "../models/library-types";
import { ModelMemory } from "../models/ModelMemory";
import { Icon } from "../../components/ui/Icon";
export function ModelsTab() {
  const { runtime, workspace, settings, library, view } = useWorkspace();
  const { model, job } = view;
  const local = Boolean(model && model.localState !== "not_downloaded");
  const reachable = Boolean(
    workspace.session && workspace.status?.controllerReachable,
  );
  const indicator = downloadIndicator(library.jobs);
  const needsCredential =
    model?.credentialService &&
    !library.services.find((service) => service.id === model.credentialService)
      ?.configured;
  return (
    <section
      id="panel-models"
      role="tabpanel"
      aria-labelledby="tab-models"
      className="settings-panel"
      hidden={workspace.tab !== "models"}>
      {" "}
      <div className="panel-heading">
        {" "}
        <div>
          {" "}
          <h3>Voice models</h3>{" "}
          <p>Download once. Load when you are ready to talk.</p>{" "}
        </div>{" "}
        <button
          type="button"
          id="manage-downloads"
          className="icon-button"
          aria-label={indicator.label}
          title={indicator.label}
          onClick={() => runtime.openDownloads()}
          data-state={indicator.state}>
          <Icon
            name={indicator.state === "attention" ? "attention" : "download"}
          />
        </button>{" "}
      </div>{" "}
      <label className="field-label" htmlFor="model-provider">
        {" "}
        Selected model{" "}
      </label>{" "}
      <select
        id="model-provider"
        disabled={view.editingDisabled}
        value={settings.selected}
        onChange={(event) =>
          runtime.settings.select(event.currentTarget.value)
        }>
        {settings.providers.length ? (
          settings.providers.map((provider) => (
            <option
              key={provider.id}
              value={provider.id}
              disabled={!canUseCurrentAudio(provider)}>
              {provider.name +
                " · " +
                provider.runtime +
                (provider.implemented ? "" : " · coming later")}
            </option>
          ))
        ) : (
          <option value="personaplex">PersonaPlex 7B</option>
        )}
      </select>{" "}
      <p id="model-provider-help" className="field-help" role="status">
        {settings.configuration
          ? settings.providers
              .filter((p) => !p.implemented)
              .map((p) => p.reason)
              .join(" ")
          : view.selected?.reason || ""}
      </p>{" "}
      <article className="model-card">
        {" "}
        <div className="section-heading">
          {" "}
          <h3 id="engine-model-name">
            {view.selected?.name || "PersonaPlex 7B"}
          </h3>{" "}
          <span
            id="model-local-state"
            className="pill subtle"
            data-ready={local}>
            {job
              ? downloadLabels[job.state]
              : model?.localState === "verified"
                ? "Verified on disk"
                : local
                  ? "Files on disk"
                  : model
                    ? "Not downloaded"
                    : "Checking files"}
          </span>{" "}
        </div>{" "}
        <p id="model-description" className="section-description">
          {model
            ? model.runtime +
              " · " +
              (model.id === "lfm3-audio"
                ? "Transcription and speech checks. Executor support comes next."
                : "Continuous voice conversation. No external LLM required.")
            : ""}
        </p>{" "}
        <dl className="model-facts">
          {" "}
          <div>
            {" "}
            <dt>Source</dt>{" "}
            <dd>
              {" "}
              <a
                id="model-source"
                href={
                  model?.source ||
                  "https://huggingface.co/nvidia/personaplex-7b-v1"
                }
                target="_blank"
                rel="noopener noreferrer">
                {" "}
                Hugging Face ↗{" "}
              </a>{" "}
            </dd>{" "}
          </div>{" "}
          <div>
            {" "}
            <dt>Download size</dt>{" "}
            <dd id="model-size">
              {model
                ? (model.sizeApproximate ? "≈ " : "") +
                  bytes(model.downloadBytes)
                : "—"}
            </dd>{" "}
          </div>{" "}
          <div>
            {" "}
            <dt>Revision</dt>{" "}
            <dd
              id="model-revision"
              title={
                model?.revision ||
                "Exact files and size are resolved before downloading."
              }>
              {model?.revision
                ? model.revision.slice(0, 12)
                : "Resolved before download"}
            </dd>{" "}
          </div>{" "}
        </dl>{" "}
        <div id="model-preparation" className="model-actions">
          {" "}
          <button
            type="button"
            id="prepare-model"
            className="button secondary"
            disabled={
              !model ||
              !reachable ||
              Boolean(job) ||
              Boolean(model?.loaded) ||
              library.preparing
            }
            onClick={() => {
              void runtime.download();
            }}>
            {" "}
            <Icon name={local ? "check" : "download"} />{" "}
            <span id="download-label">
              {job ? "Downloading…" : local ? "Verify / repair" : "Download"}
            </span>{" "}
          </button>{" "}
          <button
            type="button"
            id="open-model-folder"
            className="button secondary"
            title="Open this model&#x27;s folder in Finder on the controller Mac"
            disabled={!model || !reachable || library.openingFolder}
            onClick={() => {
              void runtime.library.openFolder(settings.selected);
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
              <path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />{" "}
            </svg>{" "}
            Open folder{" "}
          </button>{" "}
        </div>{" "}
        <p id="preparation-help" className="field-help" role="status">
          {!model
            ? ""
            : job
              ? "Follow progress in Downloads. You can keep using the app."
              : needsCredential
                ? "To download or repair PersonaPlex, add a Hugging Face token in Credentials and accept the model's access terms."
                : local
                  ? "Files remain on disk after Eject. Integrity is checked before loading."
                  : "Downloads all required files into this model's folder on your Mac."}
        </p>{" "}
        <p id="model-feedback" className="field-help" role="status">
          {library.modelFeedback}
        </p>{" "}
      </article>{" "}
      <ModelMemory />{" "}
    </section>
  );
}
