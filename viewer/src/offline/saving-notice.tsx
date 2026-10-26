// On-screen status while the download queue works. Leaving the page pauses a save rather than
// losing it (offline/queue.ts resumes it next visit), so this says that plainly instead of warning,
// beside a way to stop every save for good.
import { BTN } from "./downloads";
import { jobProgress } from "./queue";
import { cancelAllJobs, useJobs } from "./store";

export function SavingNotice() {
  const jobs = useJobs();
  const running = jobs.find((j) => j.state === "running");
  if (!running) return null;
  const waiting = jobs.filter((j) => j.state === "queued").length;
  return (
    <div role="status"
      className="fixed inset-x-4 bottom-3 z-50 mx-auto flex w-fit items-center gap-3 rounded-md bg-card px-3 py-2 text-sm shadow-lg ring-1 ring-border">
      <p>
        Saving {running.label} {jobProgress(running)}{waiting ? `, ${waiting} more waiting` : ""}.
        Closing the page pauses it until you come back.
      </p>
      <button type="button" className={`${BTN} shrink-0`} onClick={() => void cancelAllJobs()}>Cancel all</button>
    </div>
  );
}
