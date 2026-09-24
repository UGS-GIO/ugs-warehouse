// On-screen status while the download queue works. Leaving the page pauses a save rather than
// losing it (offline/queue.ts resumes it next visit), so this says that plainly instead of warning.
import { jobProgress } from "./queue";
import { useJobs } from "./use-offline";

export function SavingNotice() {
  const jobs = useJobs();
  const running = jobs.find((j) => j.state === "running");
  if (!running) return null;
  const waiting = jobs.filter((j) => j.state === "queued").length;
  return (
    <div role="status"
      className="fixed inset-x-0 bottom-3 z-50 mx-auto w-fit max-w-[calc(100%-2rem)] rounded-md bg-card px-3 py-2 text-sm shadow-lg ring-1 ring-border">
      Saving {running.label} {jobProgress(running)}{waiting ? `, ${waiting} more waiting` : ""}.
      Closing the page pauses it until you come back.
    </div>
  );
}
