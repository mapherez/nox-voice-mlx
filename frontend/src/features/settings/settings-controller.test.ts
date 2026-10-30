import { describe, expect, it, vi } from "vite-plus/test";
import { SettingsController } from "./settings-controller";
import type { Api } from "../../services/client";
import { legacyProvider, type VoiceConfiguration } from "../../providers";
const saved: VoiceConfiguration = {
  schemaVersion: 1,
  revision: 3,
  selectedProvider: "personaplex",
  profiles: {
    personaplex: { voice: "NATF0", prompt: "Saved", bits: 4 },
    "lfm3-audio": { voice: "male" },
  },
};
describe("settings ownership", () => {
  it("keeps drafts when views change or settings are polled again", async () => {
    const api: Api = async <T>(path: string): Promise<T> =>
      (path.endsWith("/providers")
        ? { version: 1, providers: [legacyProvider()] }
        : saved) as T;
    const controller = new SettingsController(api, vi.fn());
    await controller.load();
    controller.edit("prompt", "Unsaved edit");
    controller.select("lfm3-audio");
    controller.select("personaplex");
    await controller.load();
    expect(controller.getSnapshot().drafts.personaplex.prompt).toBe(
      "Unsaved edit",
    );
    expect(
      controller.getSnapshot().configuration?.profiles.personaplex.prompt,
    ).toBe("Saved");
  });
  it("cannot repopulate settings from a response belonging to a previous session", async () => {
    let complete!: (value: VoiceConfiguration) => void;
    const pending = new Promise<VoiceConfiguration>((resolve) => {
      complete = resolve;
    });
    const api: Api = async <T>(path: string): Promise<T> =>
      (path.endsWith("/providers")
        ? { version: 1, providers: [legacyProvider()] }
        : await pending) as T;
    const controller = new SettingsController(api, vi.fn());
    const request = controller.load();
    controller.reset();
    complete(saved);
    await request;
    expect(controller.getSnapshot().loaded).toBe(false);
    expect(controller.getSnapshot().providers).toEqual([]);
  });
});
