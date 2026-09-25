"""Start another Cloud Run job from inside one, fire-and-forget (the Pub/Sub push service starts the
ingest job the same way, service/main.py).

Env-driven: TOPIC_THUMBS_JOB is set only on the prod ingest + restyle jobs (cloudbuild.yaml), so a
local run or the review deployment starts nothing.
"""
from __future__ import annotations

import os
import sys

# The client's default is no timeout; a stalled call would hold the caller's task until its own
# timeout and then fail it, after the layer was already published.
RUN_TIMEOUT_SECONDS = 30


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
        from google.cloud import run_v2

        overrides = None
        if item_ids:
            # Args replace the job's `-m ugs_warehouse.vector.thumbs --all`; its command is `python`.
            overrides = run_v2.RunJobRequest.Overrides(
                container_overrides=[run_v2.RunJobRequest.Overrides.ContainerOverride(
                    args=["-m", "ugs_warehouse.vector.thumbs", *item_ids])],
                task_count=1)
        op = run_v2.JobsClient().run_job(request=run_v2.RunJobRequest(name=job, overrides=overrides),
                                         timeout=RUN_TIMEOUT_SECONDS)
        execution = (op.metadata.name if op.metadata else "") or "(started)"
        print(f"[thumbs] started {execution} for {', '.join(item_ids) if item_ids else 'all topics'}")
    except Exception as e:  # noqa: BLE001 — see docstring: logged as an ERROR, never fails the caller
        print(f"[thumbs] ERROR could not start {job}: {type(e).__name__}: {e}", file=sys.stderr)
