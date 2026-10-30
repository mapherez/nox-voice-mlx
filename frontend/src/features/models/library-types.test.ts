import { describe, expect, it } from "vite-plus/test";
import { bytes, downloadIndicator, type DownloadJob } from "./library-types";

const job = (state: string, provider = "lfm3-audio"): DownloadJob => ({
  id: state,
  provider,
  name: provider,
  state,
  downloadedBytes: 0,
  totalBytes: 0,
  bytesPerSecond: 0,
  error: null,
  files: [],
});

describe("download status", () => {
  it("distinguishes active work, attention and idle", () => {
    expect(downloadIndicator([]).state).toBe("idle");
    expect(downloadIndicator([job("completed")]).state).toBe("idle");
    expect(downloadIndicator([job("interrupted")]).state).toBe("attention");
    expect(
      downloadIndicator([job("failed"), job("verifying", "personaplex")]),
    ).toEqual({ state: "active", count: 1, label: "1 active download" });
  });
  it("clears an old failure after a successful retry for the same model", () => {
    expect(downloadIndicator([job("failed"), job("completed")]).state).toBe(
      "idle",
    );
  });
  it("keeps unresolved failures for a different model visible", () => {
    expect(
      downloadIndicator([job("failed", "personaplex"), job("completed")]).state,
    ).toBe("attention");
  });
  it("formats finite byte counts without claiming progress for unknown totals", () => {
    expect(bytes(0)).toBe("0 B");
    expect(bytes(NaN)).toBe("0 B");
    expect(bytes(2_420_000_000)).toBe("2.4 GB");
  });
});
