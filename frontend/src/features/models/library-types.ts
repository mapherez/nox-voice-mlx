export type ModelInfo = {
  id: string;
  name: string;
  source: string;
  runtime: string;
  revision: string | null;
  downloadBytes: number;
  sizeApproximate: boolean;
  credentialService: string | null;
  localState: "not_downloaded" | "present" | "verified";
  loaded: boolean;
};
type DownloadFile = {
  name: string;
  totalBytes: number;
  downloadedBytes: number;
  state: string;
};
export type DownloadJob = {
  id: string;
  provider: string;
  name: string;
  state: string;
  downloadedBytes: number;
  totalBytes: number;
  bytesPerSecond: number;
  error: string | null;
  files: DownloadFile[];
};
export type Credential = {
  id: string;
  name: string;
  fieldLabel: string;
  help: string;
  tokenUrl?: string;
  accessUrl?: string;
  configured: boolean;
  testSupported?: boolean;
  test: { state: string; message: string } | null;
};
export type Api = <T>(
  path: string,
  method?: string,
  payload?: unknown,
) => Promise<T>;
export const prefix = "/api/voice/v1";
export const activeDownloads = new Set([
  "queued",
  "resolving",
  "downloading",
  "verifying",
  "extracting",
  "cancelling",
]);
export const downloadLabels: Record<string, string> = {
  queued: "Queued",
  resolving: "Checking model source",
  downloading: "Downloading",
  verifying: "Verifying files",
  extracting: "Preparing runtime and voices",
  cancelling: "Cancelling",
  cancelled: "Cancelled",
  completed: "Ready on disk",
  failed: "Download failed",
  interrupted: "Interrupted — ready to resume",
};
export function bytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const index = Math.max(
    0,
    Math.min(3, Math.floor(Math.log(value) / Math.log(1000))),
  );
  return `${(value / 1000 ** index).toFixed(index > 1 ? 1 : 0)} ${["B", "KB", "MB", "GB"][index]}`;
}
export function downloadIndicator(jobs: DownloadJob[]): {
  state: string;
  count: number;
  label: string;
} {
  const count = jobs.filter((job) => activeDownloads.has(job.state)).length;
  if (count)
    return {
      state: "active",
      count,
      label: `${count} active download${count === 1 ? "" : "s"}`,
    };
  const latest = new Map(jobs.map((job) => [job.provider, job]));
  if (
    [...latest.values()].some((job) =>
      ["failed", "interrupted"].includes(job.state),
    )
  )
    return { state: "attention", count: 0, label: "Downloads need attention" };
  return { state: "idle", count: 0, label: "Open downloads" };
}
