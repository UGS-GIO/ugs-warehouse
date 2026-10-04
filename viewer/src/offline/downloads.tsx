// The download queue as a list: what is saving, what is waiting, what failed and why.
import { useOffline } from "./store";
import * as store from "./store";
import { useJobs } from "./use-offline";
import * as opfs from "./opfs";
import * as queue from "./queue";
import { type Job, jobProgress } from "./queue";

const BTN = "rounded border border-border px-2 py-0.5 text-sm hover:bg-hover disabled:opacity-50 pointer-coarse:min-h-11 pointer-coarse:px-3";

export function Downloads() {
  const jobs = useJobs();
  // What a stopped download already holds on the device, so keeping or removing it is informed.
  const { partials } = useOffline();
  const partialOf = (j: Job) => (j.kind === "file" && j.state !== "running"
    ? partials.find((p) => p.url === j.url)?.bytes ?? 0 : 0);
  if (!jobs.length) return null;
  return (
    <section className="flex flex-col gap-1">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
        Downloads · {jobs.length}
      </h2>
      <p className="text-sm text-muted-foreground">
        One at a time, in order. If you close the page or lose signal, they carry on from where they
        stopped next time.
      </p>
      <ul className="divide-y divide-border rounded-md border border-border">
        {jobs.map((j) => (
          <li key={j.id} className="flex items-center gap-2 px-3 py-2">
            <div className="min-w-0 flex-1">
              <span className="block wrap-anywhere">{j.label}</span>
              <div className={`text-sm ${j.state === "failed" ? "text-destructive" : "text-muted-foreground"}`}>
                {j.state === "failed" ? j.error
                  : j.state === "running" ? `Saving ${jobProgress(j)}`
                  : "Waiting"}
                {j.bytes ? ` · ${opfs.formatBytes(j.bytes)}` : ""}
                {partialOf(j) > 0 && ` · ${opfs.formatBytes(partialOf(j))} saved so far`}
              </div>
            </div>
            {j.state === "failed" && (
              <button type="button" className={BTN} onClick={() => void queue.retry(j.id)}>Retry</button>
            )}
            {j.state !== "running" && (
              <button type="button" className={BTN} onClick={() => void store.removeJob(j.id)}
                aria-label={`Remove ${j.label} from downloads`}>Remove</button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
