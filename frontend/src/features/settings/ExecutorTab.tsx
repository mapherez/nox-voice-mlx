import { useRuntime, useSnapshot } from "../../app/workspace";
import type { MCPProfile } from "./executor-controller";

export function ExecutorTab() {
  const runtime = useRuntime(),
    workspace = useSnapshot(runtime),
    state = useSnapshot(runtime.executor);
  const draft = state.draft;
  const disabled = state.busy || Boolean(workspace.status?.conversationActive);
  const connectionEdit = (id: string, values: Partial<MCPProfile>) =>
    runtime.executor.edit((settings) => ({
      ...settings,
      connections: settings.connections.map((connection) =>
        connection.id === id ? { ...connection, ...values } : connection,
      ),
    }));
  return (
    <section
      id="panel-executor"
      role="tabpanel"
      aria-labelledby="tab-executor"
      hidden={workspace.tab !== "executor"}>
      <h3>Executor &amp; MCP</h3>
      <p className="field-help">
        LFM3 uses your LLM for answers and connected tools for actions. End the
        conversation before saving or changing connections. PersonaPlex works
        independently.
      </p>
      {!draft ? (
        <button
          type="button"
          className="button secondary"
          onClick={() => {
            void runtime.executor.load();
          }}>
          Load executor settings
        </button>
      ) : (
        <>
          <fieldset disabled={disabled}>
            <legend>Conversation</legend>
            <label className="executor-toggle">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(event) =>
                  runtime.executor.edit((value) => ({
                    ...value,
                    enabled: event.currentTarget.checked,
                  }))
                }
              />{" "}
              Enable LFM3 answers and actions
            </label>
            <label className="field-label" htmlFor="executor-url">
              LLM base URL
            </label>
            <input
              id="executor-url"
              type="url"
              value={draft.llm.baseUrl}
              onChange={(event) =>
                runtime.executor.edit((value) => ({
                  ...value,
                  llm: { ...value.llm, baseUrl: event.currentTarget.value },
                }))
              }
            />
            <p className="field-help">
              Address on the Mac controller, including /v1. The LLM service is
              managed separately.
            </p>
            <label className="field-label" htmlFor="executor-model">
              Executor model
            </label>
            <input
              id="executor-model"
              list="executor-models"
              value={draft.llm.model}
              onChange={(event) =>
                runtime.executor.edit((value) => ({
                  ...value,
                  llm: { ...value.llm, model: event.currentTarget.value },
                }))
              }
            />
            <datalist id="executor-models">
              {state.models.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </datalist>
            <label className="executor-toggle">
              <input
                type="checkbox"
                checked={draft.llm.credential === "executor"}
                onChange={(event) =>
                  runtime.executor.edit((value) => ({
                    ...value,
                    llm: {
                      ...value.llm,
                      credential: event.currentTarget.checked ? "executor" : "",
                    },
                  }))
                }
              />{" "}
              Use the Executor LLM key from Credentials
            </label>
            <label className="field-label" htmlFor="executor-instructions">
              Assistant instructions
            </label>
            <textarea
              id="executor-instructions"
              rows={3}
              maxLength={8192}
              value={draft.llm.instructions}
              onChange={(event) =>
                runtime.executor.edit((value) => ({
                  ...value,
                  llm: {
                    ...value.llm,
                    instructions: event.currentTarget.value,
                  },
                }))
              }
            />
            <button
              type="button"
              className="button secondary"
              disabled={state.dirty}
              onClick={() => {
                void runtime.executor.testLLM();
              }}>
              Test LLM
            </button>
          </fieldset>
          <fieldset disabled={disabled}>
            <legend>MCP connections</legend>
            <p className="field-help">
              Streamable HTTP endpoints on the controller's network. Save first,
              then connect. Testing discovers tools without running them.
            </p>
            {draft.connections.map((profile) => {
              const connection = state.connections.find(
                (item) => item.id === profile.id,
              );
              return (
                <div className="executor-connection" key={profile.id}>
                  <label className="field-label" htmlFor={profile.id + "-name"}>
                    Name
                  </label>
                  <input
                    id={profile.id + "-name"}
                    value={profile.name}
                    maxLength={100}
                    onChange={(event) =>
                      connectionEdit(profile.id, {
                        name: event.currentTarget.value,
                      })
                    }
                  />
                  <label className="field-label" htmlFor={profile.id + "-url"}>
                    MCP URL
                  </label>
                  <input
                    id={profile.id + "-url"}
                    type="url"
                    value={profile.url}
                    onChange={(event) =>
                      connectionEdit(profile.id, {
                        url: event.currentTarget.value,
                      })
                    }
                  />
                  <label className="executor-toggle">
                    <input
                      type="checkbox"
                      checked={Boolean(profile.credential)}
                      onChange={(event) =>
                        connectionEdit(profile.id, {
                          credential: event.currentTarget.checked
                            ? "mcp-" + profile.id
                            : "",
                        })
                      }
                    />{" "}
                    Use an API key from Credentials
                  </label>
                  <p className="field-help">
                    {connection?.connected
                      ? `${connection.server?.name || profile.name} · ${connection.tools.length} ${connection.tools.length === 1 ? "tool" : "tools"}`
                      : "Disconnected"}
                  </p>
                  <div className="engine-buttons">
                    <button
                      type="button"
                      className="button secondary"
                      disabled={state.dirty}
                      onClick={() => {
                        void runtime.executor.connection(
                          profile.id,
                          connection?.connected ? "disconnect" : "connect",
                        );
                      }}>
                      {connection?.connected ? "Disconnect" : "Connect"}
                    </button>
                    <button
                      type="button"
                      className="text-button"
                      disabled={state.dirty}
                      onClick={() => {
                        void runtime.executor.connection(profile.id, "test");
                      }}>
                      Test
                    </button>
                    <button
                      type="button"
                      className="text-button"
                      disabled={state.dirty || !connection?.connected}
                      onClick={() => {
                        void runtime.executor.connection(profile.id, "refresh");
                      }}>
                      Refresh tools
                    </button>
                    <button
                      type="button"
                      className="text-button"
                      onClick={() =>
                        runtime.executor.edit((value) => ({
                          ...value,
                          connections: value.connections.filter(
                            (item) => item.id !== profile.id,
                          ),
                        }))
                      }>
                      Remove
                    </button>
                  </div>
                  {connection?.tools.length ? (
                    <details>
                      <summary>Discovered tools</summary>
                      <ul>
                        {connection.tools.map((tool) => (
                          <li key={tool.name}>
                            {tool.title}
                            {tool.supported
                              ? ""
                              : " — requires an unsupported protocol feature"}
                          </li>
                        ))}
                      </ul>
                    </details>
                  ) : null}
                </div>
              );
            })}
            <button
              type="button"
              className="button secondary"
              disabled={draft.connections.length >= 16}
              onClick={() =>
                runtime.executor.edit((value) => ({
                  ...value,
                  connections: [
                    ...value.connections,
                    {
                      id: "c-" + crypto.randomUUID().slice(0, 12),
                      name: "New connection",
                      url: "http://127.0.0.1:8001/mcp",
                      role: "direct",
                      credential: "",
                      timeoutSeconds: 120,
                    },
                  ],
                }))
              }>
              Add connection
            </button>
          </fieldset>
          <details>
            <summary>Generation and resource limits</summary>
            <fieldset disabled={disabled}>
              <legend>Applies to the next conversation</legend>
              <label className="field-label" htmlFor="executor-temperature">
                Temperature
              </label>
              <input
                id="executor-temperature"
                type="number"
                min={0}
                max={2}
                step={0.1}
                value={
                  Number.isFinite(draft.llm.temperature)
                    ? draft.llm.temperature
                    : ""
                }
                onChange={(event) =>
                  runtime.executor.edit((value) => ({
                    ...value,
                    llm: {
                      ...value.llm,
                      temperature: event.currentTarget.valueAsNumber,
                    },
                  }))
                }
              />
              <label className="field-label" htmlFor="executor-tokens">
                Maximum output tokens
              </label>
              <input
                id="executor-tokens"
                type="number"
                min={64}
                max={32768}
                value={
                  Number.isFinite(draft.llm.maxTokens)
                    ? draft.llm.maxTokens
                    : ""
                }
                onChange={(event) =>
                  runtime.executor.edit((value) => ({
                    ...value,
                    llm: {
                      ...value.llm,
                      maxTokens: event.currentTarget.valueAsNumber,
                    },
                  }))
                }
              />
              <label className="field-label" htmlFor="executor-timeout">
                LLM timeout (seconds)
              </label>
              <input
                id="executor-timeout"
                type="number"
                min={5}
                max={1800}
                value={
                  Number.isFinite(draft.llm.timeoutSeconds)
                    ? draft.llm.timeoutSeconds
                    : ""
                }
                onChange={(event) =>
                  runtime.executor.edit((value) => ({
                    ...value,
                    llm: {
                      ...value.llm,
                      timeoutSeconds: event.currentTarget.valueAsNumber,
                    },
                  }))
                }
              />
              {(
                [
                  ["maxTasks", "Activity entries", 2, 256],
                  ["maxPending", "Pending tasks", 1, 32],
                  ["contextBytes", "LLM context budget (bytes)", 8192, 2097152],
                  [
                    "memoryBytes",
                    "Activity memory budget (bytes)",
                    1048576,
                    67108864,
                  ],
                  ["taskTimeoutSeconds", "Task deadline (seconds)", 10, 7200],
                ] as const
              ).map(([key, label, min, max]) => (
                <label className="field-label" key={key}>
                  {label}
                  <input
                    type="number"
                    min={min}
                    max={max}
                    value={
                      Number.isFinite(draft.limits[key])
                        ? draft.limits[key]
                        : ""
                    }
                    onChange={(event) =>
                      runtime.executor.edit((value) => ({
                        ...value,
                        limits: {
                          ...value.limits,
                          [key]: event.currentTarget.valueAsNumber,
                        },
                      }))
                    }
                  />
                </label>
              ))}
              <p className="field-help">
                Budgets bound resource use. There is no fixed limit on reasoning
                rounds or tool calls. A full activity store requires a new
                conversation.
              </p>
            </fieldset>
          </details>
        </>
      )}
    </section>
  );
}
