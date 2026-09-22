"""
pitch_pipeline.logging_config
------------------------------
Configures structured JSON logging for unattended / batch operation.

Why JSON logs?
  In a containerised batch environment nobody is watching stdout in real time.
  Logs end up in a log aggregator (CloudWatch, Datadog, Loki, …).  JSON lines
  let the aggregator index every field without a regex parser, so an operator
  can filter on job_id, level, or frame_index in seconds rather than writing
  grep pipelines.

Usage
-----
Call ``configure_logging()`` once at process start (entry point).  Every
logger in the ``pitch_pipeline`` namespace, and the root logger, will then emit
one JSON object per line to stdout.

The ``job_id`` context field is optional: pass it to ``configure_logging`` so
every log line is automatically tagged with the run identifier.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from typing import Optional


class _JsonFormatter(logging.Formatter):
    """Formats a LogRecord as a single JSON line."""

    def __init__(self, job_id: Optional[str] = None) -> None:
        super().__init__()
        self._job_id = job_id

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if self._job_id:
            entry["job_id"] = self._job_id

        # Extra fields injected via logger.xxx(..., extra={...})
        for key, val in record.__dict__.items():
            if key not in _STANDARD_LOG_KEYS and not key.startswith("_"):
                entry[key] = val

        if record.exc_info:
            entry["exception"] = "".join(traceback.format_exception(*record.exc_info))

        return json.dumps(entry, default=str)


# Keys that belong to LogRecord itself — we don't want to re-emit them.
_STANDARD_LOG_KEYS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
})


def configure_logging(
    level: str = "INFO",
    job_id: Optional[str] = None,
) -> None:
    """
    Install a JSON formatter on the root logger.

    Parameters
    ----------
    level:
        Minimum log level string, e.g. ``"DEBUG"``, ``"INFO"``, ``"WARNING"``.
    job_id:
        Optional run identifier embedded in every log line for correlation.
    """
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {level!r}")

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter(job_id=job_id))

    root = logging.getLogger()
    root.setLevel(numeric_level)
    # Replace any existing handlers (avoids duplicate output when called
    # multiple times in tests).
    root.handlers.clear()
    root.addHandler(handler)
