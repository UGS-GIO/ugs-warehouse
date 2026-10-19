"""Start another Cloud Run job from inside one, fire-and-forget (the Pub/Sub push service starts the
ingest job the same way, service/main.py).

Env-driven: TOPIC_THUMBS_JOB is set only on the prod ingest + restyle jobs (cloudbuild.yaml), so a
local run or the review deployment starts nothing.
"""
from __future__ import annotations

import os
import sys
import zlib
from typing import TypeVar

T = TypeVar("T")

# The client's default is no timeout; a stalled call would hold the caller's task until its own
# timeout and then fail it, after the layer was already published.
RUN_TIMEOUT_SECONDS = 30


def task() -> tuple[int, int]:
    """(index, count) of this Cloud Run task; (0, 1) outside a sharded job."""
    return (int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0")),
            int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1")))


def shard(items: list[T]) -> list[T]:
    """This task's disjoint stride of `items` (`--tasks=N`). Every task must see the same ordered
    list; a count of 1 returns it unchanged."""
    i, n = task()
    return items[i::n] if n > 1 else items


def owns(key: str) -> bool:
    """Whether this task owns `key` (`--tasks=N`), by a stable hash of the key alone. Unlike
    `shard`, it never depends on list order or on state another task may change mid-run. Always
    True for a count of 1."""
    i, n = task()
    return n <= 1 or zlib.crc32(key.upper().encode()) % n == i


def task_shard(items: list[T]) -> tuple[list[T], int, int]:
    """`shard(items)` with this task's (index, count)."""
    return shard(items), *task()


def run_job(name: str, args: list[str] | None = None, *, tasks: int | None = None,
            env: dict[str, str] | None = None, timeout: float | None = None) -> str:
    """Start the Cloud Run job at resource path `name`; `args` replace its configured ones. Returns
    the execution's full name, or '' when the API gives none. Raises on an API error."""
    from google.cloud import run_v2

    overrides = run_v2.RunJobRequest.Overrides
    env_vars = [run_v2.EnvVar(name=k, value=v) for k, v in (env or {}).items()]
    containers = [overrides.ContainerOverride(args=args, env=env_vars)] if args is not None or env_vars else None
    request = run_v2.RunJobRequest(name=name, overrides=overrides(
        container_overrides=containers, task_count=tasks) if containers or tasks else None)
    op = run_v2.JobsClient().run_job(request=request, **({"timeout": timeout} if timeout else {}))
    return (op.metadata.name if op.metadata else "") or ""


def start_topic_thumbs(item_ids: list[str] | None = None) -> None:
    """Start ugs-topics-thumbs so a new or changed layer gets its preview in minutes rather than at the
    nightly run: `item_ids` narrows it to those topics in one task; None runs the job as deployed
    (--all, content-hash skip makes the unchanged topics cheap). An empty list starts nothing.

    Never raises. The caller's layer is already published, and failing it would fire the job-failure
    alert over a preview; a failed start is logged as an ERROR and the nightly run catches it up."""
    job = os.environ.get("TOPIC_THUMBS_JOB", "")  # projects/<p>/locations/<r>/jobs/ugs-topics-thumbs
    if not job:
        print("[thumbs] TOPIC_THUMBS_JOB unset; not starting topic thumbnails")
        return
    if item_ids is not None and not item_ids:
        return
    try:
        # Args replace the job's `-m ugs_warehouse.vector.thumbs --all`; its command is `python`.
        args = ["-m", "ugs_warehouse.vector.thumbs", *item_ids] if item_ids else None
        execution = run_job(job, args, tasks=1 if item_ids else None,
                            timeout=RUN_TIMEOUT_SECONDS) or "(started)"
        print(f"[thumbs] started {execution} for {', '.join(item_ids) if item_ids else 'all topics'}")
    except Exception as e:  # noqa: BLE001 — see docstring: logged as an ERROR, never fails the caller
        print(f"[thumbs] ERROR could not start {job}: {type(e).__name__}: {e}", file=sys.stderr)
