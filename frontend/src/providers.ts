export type VoiceSettings = { voice: string; prompt: string; bits: number };

export type VoiceConfiguration = {
  schemaVersion: 1;
  revision: number;
  selectedProvider: string;
  profiles: Record<string, Record<string, unknown>>;
};

export type ProviderDescription = {
  id: string;
  modelId: string;
  name: string;
  runtime: string;
  selectable: boolean;
  implemented: boolean;
  availability: string;
  reason: string;
  capabilities: {
    legacyAudio: boolean;
    audioChecks?: boolean;
    executor?: boolean;
    pushToTalk?: boolean;
    assetPreparation?: boolean;
  };
  settingsSchema: {
    properties: Record<
      string,
      { enum?: (string | number)[]; enumLabels?: Record<string, string> }
    >;
  };
  languageNotice: string;
};

export type ProviderCatalogue = {
  version: 1;
  selectedProvider: string;
  providers: ProviderDescription[];
};

export function settingOptions(provider: ProviderDescription, field: string) {
  const schema = provider.settingsSchema.properties[field];
  return (schema?.enum || []).map((value) => ({
    value: String(value),
    label: schema.enumLabels?.[String(value)] || String(value),
  }));
}

export function canUseCurrentAudio(provider: ProviderDescription | undefined) {
  return Boolean(
    provider?.selectable &&
    (provider.capabilities.legacyAudio || provider.capabilities.audioChecks),
  );
}

export function updatedProfile(
  current: VoiceConfiguration,
  selectedProvider: string,
  value: Record<string, unknown>,
): VoiceConfiguration {
  return {
    ...current,
    selectedProvider,
    profiles: {
      ...current.profiles,
      [selectedProvider]: { ...current.profiles[selectedProvider], ...value },
    },
  };
}

// Older controllers have no capability catalogue. Keep their existing UI usable
// while the controller and Docker image are updated independently.
export function legacyProvider(): ProviderDescription {
  const voices = [
    ["NATF", 4, "Natural female"],
    ["NATM", 4, "Natural male"],
    ["VARF", 5, "Variety female"],
    ["VARM", 5, "Variety male"],
  ] as const;
  const names = voices.flatMap(([prefix, count]) =>
    Array.from({ length: count }, (_, index) => `${prefix}${index}`),
  );
  const labels = Object.fromEntries(
    voices.flatMap(([prefix, count, label]) =>
      Array.from({ length: count }, (_, index) => [
        `${prefix}${index}`,
        `${label} ${index + 1}${prefix === "NATF" && index === 0 ? " · Kendra" : ""}`,
      ]),
    ),
  );
  return {
    id: "personaplex",
    modelId: "nvidia/personaplex-7b-v1",
    name: "PersonaPlex 7B",
    runtime: "MLX",
    selectable: true,
    implemented: true,
    availability: "legacy",
    reason: "Update the native controller to enable versioned model profiles.",
    capabilities: { legacyAudio: true },
    settingsSchema: {
      properties: {
        voice: { enum: names, enumLabels: labels },
        prompt: {},
        bits: {
          enum: [4, 5, 6, 8, 0],
          enumLabels: { "0": "Full precision" },
        },
      },
    },
    languageNotice:
      "The official conversational model supports English. Other languages are experimental.",
  };
}

/** Only scalar settings are valid input values; never stringify an object. */
export function profileText(value: unknown): string {
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : "";
}
