import { expect, it, vi } from "vite-plus/test";
import {
  ExecutorController,
  type ExecutorSettings,
} from "./executor-controller";
import type { Api } from "../../services/client";

const settings: ExecutorSettings = {
  schemaVersion: 1,
  revision: 0,
  enabled: false,
  llm: {
    baseUrl: "http://127.0.0.1:1234/v1",
    model: "fixture",
    credential: "",
    instructions: "Be concise.",
    temperature: 0.3,
    maxTokens: 2048,
    timeoutSeconds: 120,
  },
  connections: [],
  limits: {
    maxTasks: 32,
    maxPending: 4,
    memoryBytes: 8388608,
    contextBytes: 131072,
    taskTimeoutSeconds: 600,
  },
};

it("loads without mutations, preserves edits during refresh and saves an explicit revision", async () => {
  const api = vi.fn(async (path: string, method = "GET", payload?: unknown) =>
    path.endsWith("/connections")
      ? { connections: [] }
      : method === "PUT"
        ? { ...(payload as ExecutorSettings), revision: 1 }
        : structuredClone(settings),
  );
  const controller = new ExecutorController(api as Api);
  await controller.load();
  expect(api.mock.calls.every((call) => call[1] === undefined)).toBe(true);
  controller.edit((value) => ({
    ...value,
    llm: { ...value.llm, model: "chosen" },
  }));
  await controller.load();
  expect(controller.getSnapshot().draft?.llm.model).toBe("chosen");
  await controller.save();
  expect(api.mock.calls.find((call) => call[1] === "PUT")?.[2]).toMatchObject({
    revision: 0,
    llm: { model: "chosen" },
  });
});

it("ignores a late settings response after logout and rejects a malformed response", async () => {
  let resolve!: (value: unknown) => void;
  const api = vi.fn((path: string) =>
    path.endsWith("/connections")
      ? Promise.resolve({ connections: [] })
      : new Promise((done) => {
          resolve = done;
        }),
  );
  const controller = new ExecutorController(api as Api);
  const pending = controller.load();
  controller.reset();
  resolve(settings);
  await pending;
  expect(controller.getSnapshot().draft).toBeNull();
  const malformed = new ExecutorController((async () => ({})) as Api);
  await malformed.load();
  expect(malformed.getSnapshot().draft).toBeNull();
  expect(malformed.getSnapshot().feedback).toContain(
    "invalid executor settings",
  );
});
