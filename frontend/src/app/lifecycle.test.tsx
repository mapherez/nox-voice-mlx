// @vitest-environment jsdom
import { StrictMode } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vite-plus/test";
import { AppRuntime } from "./runtime";
import { WorkspaceProvider } from "./workspace";
import { ThemeProvider } from "./theme";
import { App } from "./App";
import { legacyProvider } from "../providers";
vi.mock("../orb", () => ({
  VoiceOrb: class {
    themeChanged() {}
    dispose() {}
  },
}));
const session = {
  email: "fixture@example.test",
  csrfToken: "fixture",
  expiresAt: 9999999999,
  controllerUrl: "https://fixture.invalid",
};
const saved = {
  schemaVersion: 1,
  revision: 1,
  selectedProvider: "personaplex",
  profiles: {
    personaplex: { voice: "NATF0", prompt: "Saved persona", bits: 4 },
  },
};
beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
    configurable: true,
    value() {
      this.open = true;
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, "close", {
    configurable: true,
    value() {
      this.open = false;
      this.dispatchEvent(new Event("close"));
    },
  });
  HTMLElement.prototype.scrollTo = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) =>
      Response.json(
        path === "/api/session"
          ? session
          : path === "/api/engine/status"
            ? {
                state: "ready",
                phase: "Ready",
                controllerReachable: true,
                selectedProvider: "personaplex",
              }
            : path.endsWith("/providers")
              ? { version: 1, providers: [legacyProvider()] }
              : path.endsWith("/settings")
                ? saved
                : path.endsWith("/models")
                  ? { models: [] }
                  : path.endsWith("/downloads")
                    ? { jobs: [] }
                    : path.endsWith("/credentials")
                      ? { services: [] }
                      : {
                          controllerUrl: session.controllerUrl,
                          autostartEnabled: false,
                          missingFiles: [],
                        },
      ),
    ),
  );
});
afterEach(() => {
  cleanup();
  history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
describe("React resource ownership", () => {
  it("preserves an active engine through rerenders and modal changes, then stops it on unmount", async () => {
    const runtime = new AppRuntime(),
      original = runtime.audio.plex;
    let active = false;
    vi.spyOn(original, "active", "get").mockImplementation(() => active);
    const start = vi.spyOn(original, "start").mockImplementation(async () => {
      active = true;
    });
    const stop = vi.spyOn(original, "stop").mockImplementation(() => {
      active = false;
    });
    const rendered = render(
      <StrictMode>
        <ThemeProvider>
          <WorkspaceProvider runtime={runtime}>
            <App />
          </WorkspaceProvider>
        </ThemeProvider>
      </StrictMode>,
    );
    await vi.waitFor(() =>
      expect(runtime.settings.getSnapshot().loaded).toBe(true),
    );
    expect(start).not.toHaveBeenCalled();
    // StrictMode has already exercised one attach/cleanup before this session.
    stop.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
    fireEvent.click(screen.getByRole("tab", { name: "Voice" }));
    fireEvent.change(screen.getByLabelText("Persona"), {
      target: { value: "Draft while connected" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Close settings" }));
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Start conversation" }),
      );
    });
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
    await act(async () => {
      await runtime.poll();
    });
    expect(screen.getByLabelText<HTMLTextAreaElement>("Persona").value).toBe(
      "Draft while connected",
    );
    fireEvent.click(screen.getByRole("button", { name: "Close settings" }));
    expect(runtime.audio.plex).toBe(original);
    expect(start).toHaveBeenCalledOnce();
    expect(stop).not.toHaveBeenCalled();
    expect(runtime.audio.getSnapshot().active).toBe(true);
    rendered.unmount();
    expect(stop).toHaveBeenCalledOnce();
    expect(runtime.audio.getSnapshot().active).toBe(false);
  });
  it("shows the Google denial notice after an unauthenticated bootstrap", async () => {
    history.replaceState(null, "", "/?login=denied");
    vi.mocked(fetch).mockImplementation(async () =>
      Response.json({ error: "Sign in" }, { status: 401 }),
    );
    render(
      <StrictMode>
        <ThemeProvider>
          <WorkspaceProvider>
            <App />
          </WorkspaceProvider>
        </ThemeProvider>
      </StrictMode>,
    );
    await vi.waitFor(() =>
      expect(
        screen.getByText(
          "This Google account is not authorized for this workspace.",
        ),
      ).toBeTruthy(),
    );
    expect(location.search).toBe("");
    expect(screen.getByText("Continue with Google")).toBeTruthy();
  });
  it("cancels pending page requests on unmount and does not dispatch writes on mount", async () => {
    const runtime = new AppRuntime();
    const rendered = render(
      <StrictMode>
        <WorkspaceProvider runtime={runtime}>
          <span>Fixture</span>
        </WorkspaceProvider>
      </StrictMode>,
    );
    await vi.waitFor(() =>
      expect(runtime.settings.getSnapshot().loaded).toBe(true),
    );
    const fetcher = vi.mocked(fetch);
    expect(
      fetcher.mock.calls.every(([, options]) => options?.method === "GET"),
    ).toBe(true);
    rendered.unmount();
    expect(runtime.getSnapshot().session).toBeNull();
    expect(runtime.settings.getSnapshot().loaded).toBe(false);
  });
});
