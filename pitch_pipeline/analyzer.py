"""
pitch_pipeline.analyzer
------------------------
The FieldBoundaryAnalyzer: the reusable library class that drives the pipeline.

Design decisions encoded here
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Frame sampling (Part 2 — efficiency)
    Processing time must scale with *how much* of the video we actually need,
    not with the total file length.  We achieve this by seeking: if the video's
    native FPS is 30 and target_fps is 5, we grab every 6th frame via
    ``cap.set(cv2.CAP_PROP_POS_FRAMES, …)`` and skip the rest entirely — the
    decoder never touches the intervening frames.  Spatial intersection area is
    computed once per detected polygon, not repeatedly.

Failure taxonomy (Part 3 — resilience)
    FATAL (raises immediately, pipeline aborts):
      - Video file cannot be opened → nothing to do, fail fast.
      - Detector construction fails → configuration error, no point continuing.

    RECOVERABLE (logged as WARNING, frame is skipped):
      - Individual frame read error (ret=False mid-stream) → log + continue.
      - Detector raises on a single frame → GreenMaskDetector already catches
        this internally and returns None; the analyzer treats None as "no
        boundary found" and moves on.
      - Invalid / empty polygon from detector → discard silently at DEBUG,
        count as a missed frame.

    Silent absorption is explicitly avoided:
      - Every skipped frame is counted.
      - The final summary always reports frames_processed, boundaries_found,
        and frames_skipped so an operator can see at a glance if the error rate
        was meaningful.
      - An unexpected exception in the main loop is caught, re-raised as
        PipelineError (fatal), *and* logged with full traceback so it appears
        in the JSON log stream.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np
from shapely.geometry import Polygon

from pitch_pipeline.config import PipelineConfig
from pitch_pipeline.detectors import FieldDetector, build_detector

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PipelineError(RuntimeError):
    """Raised for fatal pipeline failures that should abort the run."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FrameResult:
    """Successful detection result for a single sampled frame."""
    frame_index: int          # 0-based index in the *original* video stream
    polygon: Polygon
    intersection_area: float  # area of poly ∩ frame bounding box (px²)


@dataclass
class PipelineResult:
    """Aggregated outcome of a complete pipeline run."""
    frames_processed: int = 0
    frames_skipped: int = 0      # frames where detection returned nothing
    boundaries_found: int = 0
    detections: list[FrameResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def detection_rate(self) -> float:
        """Fraction of processed frames that yielded a valid boundary."""
        if self.frames_processed == 0:
            return 0.0
        return self.boundaries_found / self.frames_processed


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class FieldBoundaryAnalyzer:
    """
    Processes a video file frame-by-frame (with sampling) and returns
    per-frame field-boundary polygons plus run metrics.

    Parameters
    ----------
    config:
        Validated ``PipelineConfig``.  The analyzer treats the config as
        immutable (Pydantic ``frozen=True`` ensures this).
    detector:
        Optional pre-built ``FieldDetector``.  When omitted the analyzer
        builds one from ``config.field_detector`` via the registry.  Injecting
        a detector makes unit-testing straightforward without touching config.
    """

    # Bounding box used for intersection-area calculation.  Fixed at
    # standard-HD resolution; production code would derive this from the
    # video's actual frame dimensions.
    _FRAME_BOUNDS: Polygon = Polygon([(0, 0), (1280, 0), (1280, 720), (0, 720)])

    def __init__(
        self,
        config: PipelineConfig,
        detector: Optional[FieldDetector] = None,
    ) -> None:
        self._config = config
        # Build the detector here (fatal if the type is unknown).
        try:
            self._detector: FieldDetector = detector or build_detector(config.field_detector)
        except ValueError as exc:
            raise PipelineError(f"Cannot build field detector: {exc}") from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_video(self) -> PipelineResult:
        """
        Run the full pipeline on ``config.video_path``.

        Returns
        -------
        PipelineResult
            Aggregated metrics and per-frame detections.

        Raises
        ------
        PipelineError
            If the video file cannot be opened, or an unrecoverable error
            occurs during processing.
        """
        video_path = self._config.video_path
        logger.info("Opening video", extra={"video_path": video_path})

        cap = self._open_capture(video_path)
        try:
            return self._run_loop(cap)
        except PipelineError:
            raise
        except Exception as exc:
            # Unexpected exception — wrap and re-raise so callers always see
            # PipelineError for fatal failures.
            logger.error(
                "Unexpected error in processing loop",
                exc_info=True,
                extra={"video_path": video_path},
            )
            raise PipelineError(f"Processing loop failed: {exc}") from exc
        finally:
            cap.release()
            logger.debug("VideoCapture released", extra={"video_path": video_path})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_capture(self, video_path: str) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise PipelineError(
                f"Could not open video file: {video_path!r}. "
                "Check that the file exists and is a supported format."
            )

        native_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        logger.info(
            "Video opened",
            extra={
                "video_path": video_path,
                "native_fps": native_fps,
                "total_frames": total_frames,
                "target_fps": self._config.target_fps,
            },
        )
        return cap

    def _run_loop(self, cap: cv2.VideoCapture) -> PipelineResult:
        result = PipelineResult()
        t_start = time.monotonic()

        native_fps = cap.get(cv2.CAP_PROP_FPS) or float(self._config.target_fps)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        # --- Frame-sampling stride -------------------------------------------
        # How many source frames to advance per "sample".  E.g. native=30,
        # target=5 → stride=6 → we seek to frames 0, 6, 12, 18, …
        stride = max(1, round(native_fps / self._config.target_fps))
        logger.info(
            "Sampling parameters computed",
            extra={
                "native_fps": native_fps,
                "target_fps": self._config.target_fps,
                "stride": stride,
                "estimated_samples": total_frames // stride if total_frames else "unknown",
            },
        )

        frame_pos = 0  # current position we're seeking to

        while True:
            # Seek directly to the target frame — the decoder skips everything
            # in between, so runtime scales with samples_needed not file_length.
            if total_frames and frame_pos >= total_frames:
                break

            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_pos)
            ret, frame = cap.read()

            if not ret:
                if total_frames and frame_pos < total_frames - stride:
                    # Mid-stream read failure on a frame we expected to exist.
                    logger.warning(
                        "Frame read failed mid-stream",
                        extra={"frame_index": frame_pos},
                    )
                # Either end-of-stream or a read error — either way stop.
                break

            result.frames_processed += 1
            poly = self._detector.detect(frame)

            if poly is not None:
                # Intersection area is computed once here, not cached and
                # recomputed per caller.
                intersection_area = float(poly.intersection(self._FRAME_BOUNDS).area)
                result.detections.append(
                    FrameResult(
                        frame_index=frame_pos,
                        polygon=poly,
                        intersection_area=intersection_area,
                    )
                )
                result.boundaries_found += 1
                logger.debug(
                    "Boundary detected",
                    extra={
                        "frame_index": frame_pos,
                        "intersection_area": round(intersection_area, 2),
                    },
                )
            else:
                result.frames_skipped += 1
                logger.debug(
                    "No boundary found in frame",
                    extra={"frame_index": frame_pos},
                )

            frame_pos += stride

        result.elapsed_seconds = time.monotonic() - t_start

        logger.info(
            "Processing complete",
            extra={
                "frames_processed": result.frames_processed,
                "boundaries_found": result.boundaries_found,
                "frames_skipped": result.frames_skipped,
                "detection_rate": round(result.detection_rate, 4),
                "elapsed_seconds": round(result.elapsed_seconds, 3),
            },
        )
        return result
