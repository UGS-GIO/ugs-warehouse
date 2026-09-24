// One queue for every offline save, kept on the device.
//
// Saves run one at a time, in the order asked for, and the list survives a closed tab: on the next
// visit unfinished jobs pick up where they stopped (a whole file resumes from its partial bytes, an
// area or plate skips the tiles and blocks it already has). A job that fails for want of network
// waits for the connection to come back; any other failure stays listed with its reason, for retry.
import type { AreaPlan } from "./area";
import type { CogPlan } from "./block-store";
import { onlineManager } from "@tanstack/react-query";
import { isNetworkError } from "./online";
import * as opfs from "./opfs";
import { FileChangedError } from "./opfs-name";
import { isRecord } from "./guards";

export type JobSpec =
  | { kind: "file"; url: string; label: string; bytes?: number; replaces?: string[] }
  | { kind: "area"; plan: AreaPlan; label: string; bytes: number }
  | { kind: "cog"; plan: CogPlan; label: string; bytes: number }
  | { kind: "table"; plan: CogPlan; label: string; bytes: number }
  | { kind: "engine"; label: string; bytes?: number };

/** A save cut to an area: priced exactly before it is queued. */
export type AreaJobSpec = Extract<JobSpec, { kind: "area" | "cog" | "table" }>;

export type Job = JobSpec & {
  id: string;
  state: "queued" | "running" | "failed";
  error?: string;
  done?: number;   // bytes for a file, tiles or blocks for an area or plate
  total?: number;
};

const FILE = "queue.json";
// Network failures in a row before a job is marked failed rather than retried.
const MAX_MISSES = 5;
const misses = new Map<string, number>();

let jobs: Job[] = [];
let loaded: Promise<void> | null = null;
let current: Promise<void> | null = null;
const listeners = new Set<() => void>();
const settled = new Set<(job: Job) => void>();

// A fresh array on every change: useSyncExternalStore compares snapshots by identity.
const emit = () => { jobs = [...jobs]; listeners.forEach((fn) => fn()); };

export const subscribe = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; };
export const snapshot = () => jobs;

/** Called after each attempt at a job ends, done, failed or paused: what is stored has changed. */
export const onSettled = (fn: (job: Job) => void) => { settled.add(fn); };

// An area save is keyed by its size too, so saving a second area of the same layer is its own job.
export const keyOf = (s: JobSpec) =>
  s.kind === "file" ? `file:${s.url}` : s.kind === "engine" ? "engine" : `${s.kind}:${s.plan.url}:${s.bytes}`;

/** "41%", or a count when the job has no byte total. */
export function jobProgress(j: Job): string {
  if (j.state !== "running" || j.done === undefined) return "";
  if (j.kind === "file" || j.kind === "engine") return j.total ? `${Math.round((j.done / j.total) * 100)}%` : opfs.formatBytes(j.done);
  return j.total ? `${Math.round((j.done / j.total) * 100)}%` : "";
}

async function root() {
  return navigator.storage?.getDirectory?.().catch(() => null) ?? null;
}

// Writes chained, never overlapping: two open writers on one OPFS file can reject each other.
let writing = Promise.resolve();

function persist(): Promise<void> {
  writing = writing.then(async () => {
    const r = await root();
    if (!r) return;
    // Progress is not persisted: it is re-counted when the job resumes.
    const out = await (await r.getFileHandle(FILE, { create: true })).createWritable();
    await out.write(JSON.stringify(jobs.map(({ done: _d, total: _t, ...j }) => j)));
    await out.close();
  }).catch(() => {});
  return writing;
}

function load() {
  loaded ??= (async () => {
    const r = await root();
    const text = await r?.getFileHandle(FILE).then((h) => h.getFile()).then((f) => f.text()).catch(() => null);
    if (text == null) return;   // nothing on disk (or no OPFS): what is in memory stands
    const parsed: unknown = JSON.parse(text);
    const saved = Array.isArray(parsed) ? parsed.filter(isJob) : [];
    // A job "running" when the tab closed was cut off; it goes back in line.
    jobs = saved.map((j) => (j.state === "running" ? { ...j, state: "queued" } : j));
    emit();
  })().catch(() => {});
  return loaded;
}

const STATES: readonly unknown[] = ["queued", "running", "failed"];

/**
 * A job read back from queue.json. Checked to the depth the runner relies on (kind, ids, the file
 * or plan it saves); one written by an older build, or damaged, is dropped rather than run.
 */
function isJob(v: unknown): v is Job {
  if (!isRecord(v) || typeof v.id !== "string" || typeof v.label !== "string" || !STATES.includes(v.state)) return false;
  if (v.kind === "engine") return true;
  if (v.kind === "file") return typeof v.url === "string";
  const plan = v.plan;
  if (!isRecord(plan) || typeof plan.url !== "string" || typeof v.bytes !== "number") return false;
  if (v.kind === "area") return Array.isArray(plan.tiles) && isRecord(plan.meta);
  if (v.kind === "cog" || v.kind === "table") {
    return Array.isArray(plan.blocks) && typeof plan.size === "number" && typeof plan.block === "number";
  }
  return false;
}

/**
 * Change the queue as it is on disk, under a lock: another tab may have changed it since this one
 * last read it, and writing this tab's copy back would drop that tab's jobs or revive finished ones.
 */
async function change(fn: () => void): Promise<void> {
  const apply = async () => {
    // Progress lives only in memory (persist leaves it out); keep it across the re-read.
    const progress = new Map(jobs.map((j) => [j.id, { done: j.done, total: j.total }]));
    loaded = null;
    await load();
    jobs = jobs.map((j) => ({ ...j, ...progress.get(j.id) }));
    fn();
    emit();
    await persist();
  };
  if (navigator.locks) await navigator.locks.request("ugs-offline-queue-file", apply);
  else await apply();
}

/** Add saves to the end of the queue. One already queued for the same thing is not added twice. */
export async function enqueue(specs: JobSpec[]): Promise<void> {
  await change(() => {
    const have = new Set(jobs.map(keyOf));
    for (const s of specs) {
      const k = keyOf(s);
      if (have.has(k)) {
        jobs = jobs.map((j) => (keyOf(j) === k && j.state === "failed" ? { ...j, state: "queued", error: undefined } : j));
        continue;
      }
      have.add(k);
      jobs.push({ ...s, id: crypto.randomUUID(), state: "queued" });
    }
  });
  // Without persistent storage the browser may clear these files under pressure, which is the trip
  // they were saved for.
  await navigator.storage?.persist?.().catch(() => false);
  void run();
}

export async function retry(id: string): Promise<void> {
  misses.delete(id);
  await change(() => { jobs = jobs.map((j) => (j.id === id ? { ...j, state: "queued", error: undefined } : j)); });
  void run();
}

/** Take a job off the queue, with what it had saved so far. */
export async function remove(id: string): Promise<void> {
  let job: Job | undefined;
  await change(() => {
    job = jobs.find((j) => j.id === id);
    if (job && job.state !== "running") jobs = jobs.filter((j) => j.id !== id);
    else job = undefined;
  });
  if (!job) return;
  if (job.kind === "file") await opfs.discardPartial(job.url);
  // An area save's unfinished version goes too, unless another queued save is still filling it.
  if ("plan" in job) await sweepAreaSaves();
}

/** Remove area saves no queued job will finish. */
async function sweepAreaSaves(): Promise<void> {
  const busy = (kinds: Job["kind"][]) => new Set(jobs.flatMap((j) => ("plan" in j && kinds.includes(j.kind) ? [j.plan.url] : [])));
  await (await import("./block-store")).sweepCogAreas(busy(["cog", "table"])).catch(() => {});
  await (await import("./area-store")).sweepAreas(busy(["area"])).catch(() => {});
}

type Status = Pick<Job, "state" | "error" | "done" | "total">;

const patch = (id: string, p: Partial<Status>) => { jobs = jobs.map((j) => (j.id === id ? { ...j, ...p } : j)); };

/** Progress: this tab's memory only. */
function update(id: string, p: Partial<Status>) {
  patch(id, p);
  emit();
}

/** A fresh plan for an area job whose file changed under it: same area, the new version's offsets. */
async function replan(job: Job): Promise<Job | null> {
  if (job.kind === "area" && job.plan.bbox) {
    const plan = await (await import("./area")).planArea(job.plan.url, job.plan.bbox);
    return { ...job, plan, bytes: plan.bytes };
  }
  if ((job.kind === "cog" || job.kind === "table") && job.plan.bbox) {
    const plan = job.kind === "table"
      ? await (await import("./table-area")).planTableArea(job.plan.url, job.plan.bbox)
      : await (await import("./cog-area")).planCogArea(job.plan.url, job.plan.bbox);
    return { ...job, plan, bytes: plan.bytes };
  }
  return null;
}

async function perform(job: Job) {
  // Saves report every network chunk (~20 KB), which re-renders every download control; a few
  // updates a second is all a percentage needs. The last one always goes through.
  let last = 0;
  const progress = (done: number, total?: number) => {
    const now = Date.now();
    if (now - last < 250 && done !== total) return;
    last = now;
    update(job.id, { done, total });
  };
  if (job.kind === "file") {
    await opfs.save(job.url, { onProgress: progress });
    for (const u of job.replaces ?? []) await opfs.remove(u);
  } else if (job.kind === "area") {
    const { saveArea } = await import("./area");
    await saveArea(job.plan, progress);
  } else if (job.kind === "cog" || job.kind === "table") {
    const { saveCogArea } = await import("./block-store");
    await saveCogArea(job.plan, progress);
  } else {
    const { saveEngine } = await import("./engine");
    await saveEngine(progress);
  }
}

/**
 * Work through the queue, one job at a time, until nothing runnable is left. Held under a Web Lock
 * so two open tabs don't download the same job twice; the other tab's run waits its turn.
 */
export function run(): Promise<void> {
  // Already running: that run may already be past its last look at the queue, so ask it to go
  // round once more when it ends rather than assume it will see a job queued just now.
  if (current) { rerun = true; return current; }
  current = (async () => {
    try {
      do {
        rerun = false;
        if (navigator.locks) await navigator.locks.request("ugs-offline-queue", work);
        else await work();
      } while (rerun);
    } finally {
      current = null;
    }
  })();
  return current;
}

let rerun = false;

let swept = false;

async function work(): Promise<void> {
  // Another tab may have run jobs while this one waited for the lock: start from what is on disk.
  loaded = null;
  await load();
  // Once per visit, under the lock so no other tab is mid-download: partials no job will resume,
  // and area saves that were removed from the queue or failed part-way.
  if (!swept) {
    swept = true;
    await opfs.sweepPartials(new Set(jobs.flatMap((j) => (j.kind === "file" ? [j.url] : [])))).catch(() => 0);
    await sweepAreaSaves();
  }
  for (;;) {
    const job = jobs.find((j) => j.state === "queued");
    if (!job) break;
    if (!onlineManager.isOnline()) break;   // resumed when the connection returns (below)
    await change(() => patch(job.id, { state: "running", error: undefined }));
    try {
      try {
        await perform(job);
      } catch (e) {
        // Republished mid-save: cut the same area again from the new version, once.
        const again = e instanceof FileChangedError ? await replan(job) : null;
        if (!again) throw e;
        await change(() => { jobs = jobs.map((j) => (j.id === job.id ? again : j)); });
        await perform(again);
      }
      misses.delete(job.id);
      await change(() => { jobs = jobs.filter((j) => j.id !== job.id); });
    } catch (e) {
      const tries = (misses.get(job.id) ?? 0) + 1;
      if (isNetworkError(e) && tries < MAX_MISSES) {
        // Lost the connection, or the server is out of reach: back in line, and try again when
        // the browser says it is online, or after a pause (a dead hotspot still reads as online).
        misses.set(job.id, tries);
        await change(() => patch(job.id, { state: "queued" }));
        setTimeout(() => { void run(); }, 15_000 * 2 ** tries);
        rerun = false;   // a deliberate pause: the timer or the connection returning resumes it
        break;
      }
      misses.delete(job.id);
      await change(() => patch(job.id, { state: "failed", error: e instanceof Error ? e.message : String(e) }));
    } finally {
      settled.forEach((fn) => fn(job));
    }
  }
}

onlineManager.subscribe((online) => { if (online) void run(); });

// Closing, reloading or leaving the page stops a download; the next visit resumes it, but only
// once someone opens the app again. So while one is running, ask the browser to confirm first.
// Browsers show their own wording, and iOS Safari often skips the prompt; the on-screen notice
// (saving-notice.tsx) says it too.
if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", (e) => {
    if (!jobs.some((j) => j.state === "running")) return;
    e.preventDefault();
    e.returnValue = "";   // older Chromium needs this set to show the prompt at all
  });
}
