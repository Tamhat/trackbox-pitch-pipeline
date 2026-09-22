"""
pitch_pipeline.config
---------------------
Validated configuration model for the pipeline.

Pydantic is used deliberately: any missing required field, wrong type, or
out-of-range value raises a ValidationError at *import / load time*, before a
single frame is touched.  There are no silent fall-backs to defaults for values
that materially affect pipeline behaviour.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class FieldDetectorConfig(BaseModel):
    """Configuration for the field-detection step."""

    # Which detector implementation to load.  Adding a new sport/strategy means
    # registering a new literal here and a corresponding entry in the detector
    # registry — the pipeline code itself stays untouched.
    type: Literal["green_mask"] = "green_mask"

    sport: Literal["football", "soccer", "rugby", "hockey"] = "football"

    # Minimum contour area (px²) to be treated as a real field region.
    # Intentionally has no default: callers must decide, because the right value
    # depends on resolution and sport.
    min_area: int = Field(..., gt=0, description="Minimum contour area in pixels²")

    # HSV bounds for the green-mask detector.  Each must be a 3-element list
    # with values in [0, 255].
    hsv_lower: list[int] = Field(
        default=[35, 40, 40],
        min_length=3,
        max_length=3,
        description="HSV lower bound [H, S, V]",
    )
    hsv_upper: list[int] = Field(
        default=[85, 255, 255],
        min_length=3,
        max_length=3,
        description="HSV upper bound [H, S, V]",
    )

    @field_validator("hsv_lower", "hsv_upper", mode="after")
    @classmethod
    def _validate_hsv(cls, v: list[int]) -> list[int]:
        if any(not (0 <= x <= 255) for x in v):
            raise ValueError("HSV values must be in [0, 255]")
        return v

    @model_validator(mode="after")
    def _lower_before_upper(self) -> "FieldDetectorConfig":
        for lo, hi, ch in zip(self.hsv_lower, self.hsv_upper, ("H", "S", "V")):
            if lo >= hi:
                raise ValueError(
                    f"hsv_lower[{ch}]={lo} must be strictly less than hsv_upper[{ch}]={hi}"
                )
        return self


class CropSearchConfig(BaseModel):
    """Parameters for the (future) crop-layout step."""

    aspect_ratio: str = Field(
        default="16:9",
        pattern=r"^\d+:\d+$",
        description="Aspect ratio in W:H format, e.g. '16:9'",
    )
    padding_px: int = Field(default=20, ge=0)


class ReporterConfig(BaseModel):
    """Where and how to reach the reporting service."""

    base_url: str = Field(
        ...,
        description="Base URL of the mock-API reporting service, e.g. http://mock_api:5000",
    )
    # How often (in frames) to POST a progress update.
    progress_interval_frames: int = Field(
        default=100,
        gt=0,
        description="POST a progress update every N processed frames",
    )
    # Per-request timeout in seconds.
    timeout_seconds: float = Field(default=5.0, gt=0)
    # How many times to retry a failed POST before giving up.
    max_retries: int = Field(default=3, ge=0)

    @field_validator("base_url", mode="after")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


class PipelineConfig(BaseModel):
    """
    Top-level validated configuration for the pipeline.

    Load from a dict (e.g. parsed from JSON/YAML/env) and pass directly to
    FieldBoundaryAnalyzer.  A ValidationError here means the process should
    exit immediately with a clear message — never swallow it.
    """

    video_path: str = Field(..., description="Path to the input video file")
    target_fps: int = Field(
        ...,
        gt=0,
        description=(
            "Frames per second to *sample* from the video.  "
            "Must be > 0 and <= the video's native FPS."
        ),
    )
    confidence_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum confidence to accept a detection (reserved for model-backed detectors)",
    )
    field_detector: FieldDetectorConfig
    crop_search: CropSearchConfig = Field(default_factory=CropSearchConfig)
    reporter: ReporterConfig

    # Job identifier forwarded in every API call so the platform can correlate
    # events to a specific pipeline run.
    job_id: str = Field(..., description="Unique identifier for this pipeline run")

    model_config = {"frozen": True}  # immutable after construction
