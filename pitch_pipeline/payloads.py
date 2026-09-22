"""
pitch_pipeline.payloads
------------------------
Pydantic models for every payload sent over the wire to the reporting service.

Why typed models instead of loose dicts?
  * The same validation discipline applied to inbound config is applied to
    outbound data.  A bug that sets ``frames_processed`` to a string, or omits
    ``job_id``, is caught at construction time — not silently sent to the API
    and swallowed.
  * ``model.model_dump()`` produces a clean, serialisable dict that maps
    directly to the JSON body — no ad-hoc key assembly scattered across call
    sites.
  * Adding a new field or changing a type is a single, visible, auditable
    change here rather than a hunt through string literals.

Endpoint mapping (see mock_api/app.py)
  POST /api/v1/jobs/progress   → ProgressPayload
  POST /api/v1/jobs/events     → EventPayload
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class ProgressPayload(BaseModel):
    """
    Periodic progress report posted while the pipeline is running.

    Posted every ``reporter.progress_interval_frames`` processed frames.
    """

    job_id: str
    frames_processed: int = Field(..., ge=0)
    boundaries_found: int = Field(..., ge=0)
    frames_skipped: int = Field(..., ge=0)
    # 0.0–1.0 fraction of the video consumed so far (best-effort; 0 if unknown)
    progress_pct: float = Field(..., ge=0.0, le=100.0)
    timestamp: datetime = Field(default_factory=_utc_now)

    model_config = {"frozen": True}


class EventPayload(BaseModel):
    """
    Lifecycle event posted at key pipeline transitions.

    ``event_type`` distinguishes the transition; ``detail`` carries
    event-specific context (error message, summary stats, etc.).
    """

    job_id: str
    event_type: Literal[
        "pipeline_started",
        "pipeline_completed",
        "pipeline_failed",
        "video_opened",
        "reporter_unreachable",
    ]
    detail: Optional[str] = None
    # Snapshot metrics — present on completed/failed, absent on started.
    frames_processed: Optional[int] = Field(default=None, ge=0)
    boundaries_found: Optional[int] = Field(default=None, ge=0)
    frames_skipped: Optional[int] = Field(default=None, ge=0)
    detection_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    elapsed_seconds: Optional[float] = Field(default=None, ge=0.0)
    timestamp: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def _metrics_required_on_terminal_events(self) -> "EventPayload":
        terminal = {"pipeline_completed", "pipeline_failed"}
        if self.event_type in terminal and self.frames_processed is None:
            raise ValueError(
                f"frames_processed is required for event_type={self.event_type!r}"
            )
        return self

    model_config = {"frozen": True}
