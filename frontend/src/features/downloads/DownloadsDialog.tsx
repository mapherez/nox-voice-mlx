import { useRuntime, useSnapshot } from "../../app/workspace";
import { Dialog } from "../../components/ui/Dialog";
import { Icon } from "../../components/ui/Icon";
import { activeDownloads } from "../models/library-types";
import { DownloadJob } from "./DownloadJob";
export function DownloadsDialog() {
  const runtime = useRuntime(),
    workspace = useSnapshot(runtime),
    library = useSnapshot(runtime.library);
  return (
    <Dialog
      id="downloads"
      className="downloads-dialog"
      labelledBy="downloads-title"
      open={workspace.downloadsOpen}
      onClose={runtime.closeDownloads}>
      <header className="drawer-header" aria-label="Downloads">
        <div>
          <span className="eyebrow">ON YOUR MAC</span>
          <h2 id="downloads-title">Downloads</h2>
        </div>
        <div className="downloads-header-actions">
          <button
            type="button"
            id="clear-downloads"
            className="icon-button"
            aria-label="Clear download history"
            title="Clear history"
            disabled={
              library.clearing ||
              !workspace.status?.controllerReachable ||
              !library.jobs.some((job) => !activeDownloads.has(job.state))
            }
            onClick={() => {
              void runtime.library.clearHistory();
            }}>
            <Icon name="clear" width={1.6} />
          </button>
          <button
            type="button"
            id="close-downloads"
            className="icon-button"
            aria-label="Close downloads"
            onClick={runtime.closeDownloads}>
            <Icon name="close" width={1.6} />
          </button>
        </div>
      </header>
      <div className="downloads-content">
        <p className="field-help">
          Downloads continue on your Mac when you close this window. Retry an
          interrupted download to resume it.
        </p>
        <p id="downloads-feedback" className="inline-error" role="status">
          {library.feedback}
        </p>
        <div id="download-list">
          {library.jobs.length ? (
            [...library.jobs]
              .reverse()
              .map((job) => <DownloadJob key={job.id} job={job} />)
          ) : (
            <p className="downloads-empty">
              No download activity. Manage models in Settings.
            </p>
          )}
        </div>
      </div>
    </Dialog>
  );
}
