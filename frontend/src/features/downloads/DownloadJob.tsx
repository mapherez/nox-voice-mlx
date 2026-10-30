import { useState } from "react";
import { useRuntime, useSnapshot } from "../../app/workspace";
import {
  activeDownloads,
  downloadLabels,
  bytes,
  type DownloadJob as Job,
} from "../models/library-types";
export function DownloadJob({ job }: { job: Job }) {
  const runtime = useRuntime(),
    workspace = useSnapshot(runtime),
    library = useSnapshot(runtime.library);
  const active = activeDownloads.has(job.state);
  const [expanded, setExpanded] = useState(active);
  const action = active ? "cancel" : "retry";
  return (
    <article className="download-job" data-state={job.state}>
      <div className="section-heading">
        <strong>{job.name}</strong>
        <span className="download-state">
          {downloadLabels[job.state] || job.state}
        </span>
      </div>
      <progress
        max={job.totalBytes || 1}
        value={job.totalBytes ? job.downloadedBytes : undefined}
        aria-label={job.name + " download progress"}
      />
      <p className="field-help">
        {bytes(job.downloadedBytes) +
          (job.totalBytes
            ? " / " + bytes(job.totalBytes)
            : " · Size unavailable") +
          (job.bytesPerSecond ? " · " + bytes(job.bytesPerSecond) + "/s" : "")}
      </p>
      {!!job.files.length && (
        <details
          open={expanded}
          onToggle={(event) => setExpanded(event.currentTarget.open)}>
          <summary>
            {job.files.filter((file) => file.state === "downloaded").length +
              " of " +
              job.files.length +
              " files downloaded"}
          </summary>
          {job.files.map((file) => (
            <div className="download-file" key={file.name}>
              <span>{file.name}</span>
              <small>
                {bytes(file.downloadedBytes) + " / " + bytes(file.totalBytes)}
              </small>
            </div>
          ))}
        </details>
      )}
      {job.error && <p className="inline-error">{job.error}</p>}
      {(active ||
        ["failed", "cancelled", "interrupted"].includes(job.state)) && (
        <button
          type="button"
          id={"download-" + job.id + "-" + action}
          className="text-button"
          disabled={
            job.state === "cancelling" ||
            !workspace.status?.controllerReachable ||
            library.jobBusy.has(job.id)
          }
          onClick={() => {
            void runtime.library.jobAction(job.id, action);
          }}>
          {action === "cancel" ? "Cancel download" : "Retry / resume"}
        </button>
      )}
    </article>
  );
}
