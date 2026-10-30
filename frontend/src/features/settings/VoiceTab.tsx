import { useWorkspace } from "../../app/workspace";
import { settingOptions, profileText } from "../../providers";
export function VoiceTab() {
  const { runtime, workspace, settings, view } = useWorkspace();
  const draft = settings.drafts[settings.selected] || {};
  return (
    <section
      id="panel-voice"
      role="tabpanel"
      aria-labelledby="tab-voice"
      className="settings-panel"
      hidden={workspace.tab !== "voice"}>
      {" "}
      <div className="panel-heading">
        {" "}
        <div>
          {" "}
          <h3>Voice &amp; conversation</h3>{" "}
          <p>
            {" "}
            Settings for the selected model. Each model keeps its own
            profile.{" "}
          </p>{" "}
        </div>{" "}
      </div>{" "}
      <p id="voice-model-label" className="pill subtle">
        {view.selected?.name || "Select a model in Models"}
      </p>{" "}
      <section
        id="persona-settings"
        className="settings-section"
        aria-labelledby="voice-heading"
        hidden={!view.selected?.settingsSchema.properties.prompt}>
        {" "}
        <h3 id="voice-heading">Voice &amp; persona</h3>{" "}
        <p className="section-description">
          {" "}
          Choose how your assistant sounds and responds.{" "}
        </p>{" "}
        <label className="field-label" htmlFor="voice">
          Assistant voice
        </label>{" "}
        <select
          id="voice"
          disabled={view.editingDisabled}
          value={profileText(draft.voice)}
          onChange={(event) =>
            runtime.settings.edit("voice", event.currentTarget.value)
          }>
          {view.selected &&
            settingOptions(view.selected, "voice").map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
        </select>{" "}
        <label className="field-label" htmlFor="persona">
          Persona
        </label>{" "}
        <textarea
          id="persona"
          rows={4}
          maxLength={4096}
          spellCheck={false}
          disabled={view.editingDisabled}
          value={profileText(draft.prompt)}
          onChange={(event) =>
            runtime.settings.edit("prompt", event.currentTarget.value)
          }></textarea>{" "}
      </section>{" "}
      <section
        id="transcription-settings"
        className="settings-section"
        aria-labelledby="transcription-heading"
        hidden={!view.selected?.capabilities.audioChecks}>
        {" "}
        <h3 id="transcription-heading">Transcription &amp; speech</h3>{" "}
        <label className="field-label" htmlFor="input-language">
          {" "}
          Input language{" "}
        </label>{" "}
        <select
          id="input-language"
          disabled={view.editingDisabled}
          value={profileText(draft.inputLanguage)}
          onChange={(event) =>
            runtime.settings.edit("inputLanguage", event.currentTarget.value)
          }>
          {view.selected &&
            settingOptions(view.selected, "inputLanguage").map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
        </select>{" "}
        <label className="field-label" htmlFor="vocabulary">
          {" "}
          Vocabulary hints{" "}
        </label>{" "}
        <textarea
          id="vocabulary"
          rows={3}
          maxLength={4096}
          placeholder="Names and terms the transcriber should recognise."
          disabled={view.editingDisabled}
          value={profileText(draft.vocabulary)}
          onChange={(event) =>
            runtime.settings.edit("vocabulary", event.currentTarget.value)
          }></textarea>{" "}
        <p className="field-help">
          {" "}
          Hints can improve recognition; they do not guarantee exact
          spelling.{" "}
        </p>{" "}
        <label className="field-label" htmlFor="output-language">
          {" "}
          Speech language{" "}
        </label>{" "}
        <select
          id="output-language"
          disabled={view.editingDisabled}
          value={profileText(draft.outputLanguage)}
          onChange={(event) =>
            runtime.settings.edit("outputLanguage", event.currentTarget.value)
          }>
          {view.selected &&
            settingOptions(view.selected, "outputLanguage").map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
        </select>{" "}
        <label className="field-label" htmlFor="speech-voice">
          Speech voice
        </label>{" "}
        <select
          id="speech-voice"
          disabled={view.editingDisabled}
          value={profileText(draft.voice)}
          onChange={(event) =>
            runtime.settings.edit("voice", event.currentTarget.value)
          }>
          {view.selected &&
            settingOptions(view.selected, "voice").map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
        </select>{" "}
      </section>{" "}
      <section className="settings-section">
        {" "}
        <div
          id="precision-settings"
          hidden={!view.selected?.settingsSchema.properties.bits}>
          {" "}
          <label className="field-label" htmlFor="quantization">
            {" "}
            Quantization{" "}
            <span className="field-caption">Engine precision</span>{" "}
          </label>{" "}
          <select
            id="quantization"
            disabled={view.editingDisabled}
            value={profileText(draft.bits)}
            onChange={(event) =>
              runtime.settings.edit("bits", Number(event.currentTarget.value))
            }>
            {view.selected &&
              settingOptions(view.selected, "bits").map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
          </select>{" "}
          <p className="field-help">
            {" "}
            Changing precision reloads the model. End the conversation
            first.{" "}
          </p>{" "}
        </div>{" "}
        <p id="model-language-help" className="field-help">
          {view.selected?.languageNotice || ""}
        </p>{" "}
      </section>{" "}
    </section>
  );
}
