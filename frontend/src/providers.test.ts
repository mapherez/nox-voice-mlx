import { describe, expect, it } from "vitest";
import {
  canUseCurrentAudio,
  legacyProvider,
  settingOptions,
  updatedProfile,
  type VoiceConfiguration,
} from "./providers";

describe("provider settings", () => {
  it("uses advertised choices and labels rather than provider-name branches", () => {
    const provider = legacyProvider();
    provider.id = "another-adapter";
    provider.settingsSchema.properties.voice = {
      enum: ["voice-a"],
      enumLabels: { "voice-a": "Voice supplied by the adapter" },
    };
    expect(settingOptions(provider, "voice")).toEqual([
      { value: "voice-a", label: "Voice supplied by the adapter" },
    ]);
    expect(settingOptions(provider, "unsupported")).toEqual([]);
  });

  it("preserves inactive profiles and the expected revision when editing a model", () => {
    const document: VoiceConfiguration = {
      schemaVersion: 1,
      revision: 7,
      selectedProvider: "personaplex",
      profiles: {
        personaplex: { voice: "NATF0", prompt: "Original", bits: 4 },
        "lfm3-audio": { voice: "male", vocabulary: "Some vocabulary" },
      },
    };
    const changed = updatedProfile(document, "personaplex", {
      voice: "NATM2",
      prompt: "Changed",
      bits: 8,
    });
    expect(changed.revision).toBe(7);
    expect(changed.profiles["lfm3-audio"]).toEqual(
      document.profiles["lfm3-audio"],
    );
    expect(document.profiles.personaplex.prompt).toBe("Original");
  });

  it("does not allow a provider with an incompatible stream through the old audio client", () => {
    expect(canUseCurrentAudio(undefined)).toBe(false);
    expect(canUseCurrentAudio(legacyProvider())).toBe(true);
    expect(canUseCurrentAudio({ ...legacyProvider(), selectable: false })).toBe(
      false,
    );
    expect(
      canUseCurrentAudio({
        ...legacyProvider(),
        capabilities: { legacyAudio: false },
      }),
    ).toBe(false);
  });

  it("keeps the original choices for controllers that predate the catalogue", () => {
    expect(legacyProvider().settingsSchema.properties.prompt).toBeDefined();
    expect(settingOptions(legacyProvider(), "voice")).toHaveLength(18);
    expect(
      settingOptions(legacyProvider(), "bits").map((option) => option.value),
    ).toEqual(["4", "5", "6", "8", "0"]);
  });

  it("allows the advertised audio-check client without enabling the legacy binary stream", () => {
    expect(
      canUseCurrentAudio({
        ...legacyProvider(),
        capabilities: { legacyAudio: false, audioChecks: true },
      }),
    ).toBe(true);
  });
});
