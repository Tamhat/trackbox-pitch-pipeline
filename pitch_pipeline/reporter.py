"""
pitch_pipeline.reporter
------------------------
HTTP client that reports pipeline progress and lifecycle events to the mock
reporting service (mock_api/app.py).

Isolation contract (Part 4 requirement)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
A failure to reach the reporting service must NEVER be confused with a failure
in the video pipeline itself.  This module upholds that contract by:

  1. Catching every ``requests`` exception internally and re-raising it only
     as ``ReporterError`` — a distinct exception type the caller can handle
     separately from ``PipelineError``.
  2. Providing a ``ReportingMode`` enum so the caller can choose whether a
     reporting failure is fatal (``STRICT``) or a logged warning that doesn't
     abort the run (``BEST_EFFORT``).  The entry point uses ``BEST_EFFORT``
     by default so a transient network blip doesn't kill a long-running job.
  3. Logging every HTTP attempt and outcome as structured JSON so an operator
     can see exactly which POST failed and why — without attaching a debugger.

Retry logic
~~~~~~~~~~~
Each POST is retried up to ``config.reporter.max_retries`` times with a short
exponential back-off (0.5s, 1s, 2s, …).  On final failure the exception
propagates to the caller as ``ReporterError``.
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Union

import requests

from pitch_pipeline.config import ReporterConfig
from pitch_pipeline.payloads import EventPayload, ProgressPayload

logger = logging.getLogger(__name__)

# Endpoints (relative to base_url)
_PROGRESS_PATH = "/api/v1/jobs/progress"
_EVENTS_PATH = "/api/v1/jobs/events"


class ReporterError(RuntimeError):
    """Raised when a reporting POST fails after all retries are exhausted."""


class ReportingMode(str, Enum):
    """Controls how the caller reacts to a ``ReporterError``."""

    STRICT = "strict"
    """A reporting failure is fatal — propagate ``ReporterError`` to the caller."""

    BEST_EFFORT = "best_effort"
    """Log the failure as a WARNING and continue — the pipeline run is not aborted."""


class PipelineReporter:
    """
    Posts progress and event payloads to the mock reporting API.

    Parameters
    ----------
    config:
        The validated ``ReporterConfig`` section of the pipeline config.
    mode:
        ``BEST_EFFORT`` (default) — network failures are logged but not fatal.
        ``STRICT``      — network failures raise ``ReporterError``.
    """

    def __init__(
        self,
        config: ReporterConfig,
        mode: ReportingMode = ReportingMode.BEST_EFFORT,
    ) -> None:
        self._config = config
        self._mode = mode
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def report_progress(self, payload: ProgressPayload) -> None:
        """POST a progress update.  Honours ``mode`` on failure."""
        self._post(_PROGRESS_PATH, payload, label="progress")

    def report_event(self, payload: EventPayload) -> None:
        """POST a lifecycle event.  Honours ``mode`` on failure."""
        self._post(_EVENTS_PATH, payload, label="event")

    def close(self) -> None:
        """Release the underlying HTTP session."""
        self._session.close()

    def __enter__(self) -> "PipelineReporter":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post(
        self,
        path: str,
        payload: Union[ProgressPayload, EventPayload],
        label: str,
    ) -> None:
        url = self._config.base_url + path
        body = payload.model_dump(mode="json")

        last_exc: Exception | None = None
        attempts = self._config.max_retries + 1  # e.g. max_retries=3 → 4 tries

        for attempt in range(1, attempts + 1):
            try:
                resp = self._session.post(
                    url,
                    json=body,
                    timeout=self._config.timeout_seconds,
                )
                resp.raise_for_status()
                logger.debug(
                    "Report posted",
                    extra={
                        "label": label,
                        "url": url,
                        "status_code": resp.status_code,
                        "attempt": attempt,
                    },
                )
                return  # success — done

            except requests.RequestException as exc:
                last_exc = exc
                logger.warning(
                    "Report POST failed",
                    extra={
                        "label": label,
                        "url": url,
                        "attempt": attempt,
                        "max_attempts": attempts,
                        "error": str(exc),
                    },
                )
                if attempt < attempts:
                    backoff = 0.5 * (2 ** (attempt - 1))  # 0.5s, 1s, 2s, …
                    logger.debug(
                        "Retrying after back-off",
                        extra={"backoff_seconds": backoff, "label": label},
                    )
                    time.sleep(backoff)

        # All retries exhausted.
        err = ReporterError(
            f"Failed to POST {label} to {url} after {attempts} attempt(s): {last_exc}"
        )

        if self._mode is ReportingMode.STRICT:
            raise err

        # BEST_EFFORT: log and move on — pipeline continues.
        logger.warning(
            "Reporting failure absorbed (best-effort mode) — pipeline continues",
            extra={"label": label, "url": url, "error": str(last_exc)},
        )
