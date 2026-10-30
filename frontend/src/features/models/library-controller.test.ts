import { describe, expect, it } from "vite-plus/test";
import { LibraryController } from "./library-controller";
import type { Api } from "../../services/client";
import type { DownloadJob } from "./library-types";
const job = (id: string, state: string): DownloadJob => ({
  id,
  state,
  provider: "lfm3-audio",
  name: "Fixture",
  downloadedBytes: 1,
  totalBytes: 1,
  bytesPerSecond: 0,
  error: null,
  files: [],
});
describe("download history races", () => {
  it("never restores cleared entries from an earlier in-flight poll", async () => {
    const active = job("active", "downloading"),
      completed = job("completed", "completed");
    let delay = false,
      resolvePoll!: (value: { jobs: DownloadJob[] }) => void;
    const api: Api = async <T>(path: string, method = "GET"): Promise<T> => {
      if (path.endsWith("/models")) return { models: [] } as T;
      if (path.endsWith("/credentials")) return { services: [] } as T;
      if (method === "DELETE") return { jobs: [active] } as T;
      if (delay)
        return (await new Promise<{ jobs: DownloadJob[] }>((resolve) => {
          resolvePoll = resolve;
        })) as T;
      return { jobs: [completed, active] } as T;
    };
    const library = new LibraryController(api);
    await library.refresh();
    delay = true;
    const poll = library.refresh();
    await library.clearHistory();
    resolvePoll({ jobs: [completed, active] });
    await poll;
    expect(library.getSnapshot().jobs).toEqual([active]);
    expect(library.getSnapshot().clearing).toBe(false);
  });
  it("retains rows and reports a malformed clear response", async () => {
    const completed = job("completed", "completed");
    const api: Api = async <T>(path: string, method = "GET"): Promise<T> =>
      (method === "DELETE"
        ? { unrelated: true }
        : path.endsWith("/downloads")
          ? { jobs: [completed] }
          : path.endsWith("/models")
            ? { models: [] }
            : { services: [] }) as T;
    const library = new LibraryController(api);
    await library.refresh();
    await library.clearHistory();
    expect(library.getSnapshot().jobs).toEqual([completed]);
    expect(library.getSnapshot().feedback).toContain(
      "invalid download history",
    );
  });
  it("ignores library responses received after its session was reset", async () => {
    let complete!: (value: unknown) => void;
    const delayed = new Promise((resolve) => {
      complete = resolve;
    });
    const api: Api = async <T>(path: string): Promise<T> =>
      (path.endsWith("/models")
        ? await delayed
        : path.endsWith("/downloads")
          ? { jobs: [] }
          : { services: [] }) as T;
    const library = new LibraryController(api),
      poll = library.refresh();
    library.reset();
    complete({ models: [{ id: "stale" }] });
    await poll;
    expect(library.getSnapshot().models).toEqual([]);
  });
});
