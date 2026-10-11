"""One log format for the services and jobs: JSON lines on Cloud Run, plain text elsewhere.

Cloud Logging reads `severity` and `message` from a JSON line, and keeps any other key as a
filterable field (`jsonPayload.topic`). Pass fields with `extra=`:
`log.info("...", extra={"topic": t})`.
"""
from __future__ import annotations

import json
import logging
import os
import sys

_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {k: v for k, v in vars(record).items()
                  if k not in _STANDARD and not k.startswith("_")}
        message = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            message += "\n" + record.exc_text
        if record.stack_info:
            message += "\n" + self.formatStack(record.stack_info)
        # The standard keys go last so an `extra` field cannot replace them.
        doc = {**fields, "severity": record.levelname, "message": message, "logger": record.name}
        return json.dumps(doc, default=str)


def setup(level: int = logging.INFO) -> None:
    """Send the root logger to stdout, as JSON when running on Cloud Run."""
    handler = logging.StreamHandler(sys.stdout)
    on_cloud_run = bool(os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"))
    handler.setFormatter(JsonFormatter() if on_cloud_run
                         else logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=level, handlers=[handler], force=True)
