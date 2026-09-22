"""
run_pipeline.py
---------------
Thin CLI entry point for the pitch-boundary pipeline.

Responsibilities of this module
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* Load and validate configuration from environment variables (Docker-friendly).
* Wire together the library components: config -> analyzer + reporter.
* Send lifecycle events (started / completed / failed) and periodic progress
  updates to the reporting service.
* Translate fatal PipelineError / ValidationError into a non-zero exit code.
* Nothing else.  All logic lives in pitch_pipeline/.

Configuration via environment variables
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  VIDEO_PATH                  Path to the input video file (required)
  JOB_ID                      Unique run identifier (required)
  MOCK_API_URL                Base URL of the reporting service (required)
  TARGET_FPS                  Frames-per-second to sample  (default: 5)
  CONFIDENCE_THRESHOLD        Detection confidence cutoff  (default: 0.5)
  FIELD_DETECTOR_TYPE         Detector type key           (default: green_mask)
  FIELD_DETECTOR_SPORT        Sport label                 (default: football)
  FIELD_DETECTOR_MIN_AREA     Minimum contour area px2    (default: 1000)
  REPORTER_PROGRESS_INTERVAL  Progress POST every N frames (default: 100)
  REPORTER_TIMEOUT            HTTP timeout seconds        (default: 5.0)
  REPORTER_MAX_RETRIES        Retry attempts              (default: 3)
  LOG_LEVEL                   Logging level               (default: INFO)
"""

from __future__ import annotations

import logging
import os
import sys
import uuid

from pydantic import ValidationError

from pitch_pipeline.analyzer import FieldBoundaryAnalyzer, PipelineError
from pitch_pipeline.config import (
    CropSearchConfig,
    FieldDetectorConfig,
    PipelineConfig,
    ReporterConfig,
)
from pitch_pipeline.logging_config import configure_logging
from pitch_pipeline.payloads import EventPayload, ProgressPayload
from pitch_pipeline.reporter import PipelineReporter, ReportingMode
from synthetic_generator import generate_synthetic_video


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(key: str, default: str | None = None) -> str:
    """Read an environment variable; exit immediately if required and missing."""
    val = os.environ.get(key, default)
    if val is None:
        print(f"[FATAL] Required environment variable '{key}' is not set.", file=sys.stderr)
        sys.exit(1)
    return val


def _build_config() -> PipelineConfig:
    """Construct and validate the pipeline configuration from env vars."""
    return PipelineConfig(
        video_path=_env("VIDEO_PATH", "synthetic_pitch_feed.mp4"),
        job_id=_env("JOB_ID", str(uuid.uuid4())),
        target_fps=int(_env("TARGET_FPS", "5")),
        confidence_threshold=float(_env("CONFIDENCE_THRESHOLD", "0.5")),
        field_detector=FieldDetectorConfig(
            type=_env("FIELD_DETECTOR_TYPE", "green_mask"),   # type: ignore[arg-type]
            sport=_env("FIELD_DETECTOR_SPORT", "football"),   # type: ignore[arg-type]
            min_area=int(_env("FIELD_DETECTOR_MIN_AREA", "1000")),
        ),
        crop_search=CropSearchConfig(),
        reporter=ReporterConfig(
            base_url=_env("MOCK_API_URL", "http://localhost:5000"),
            progress_interval_frames=int(_env("REPORTER_PROGRESS_INTERVAL", "100")),
            timeout_seconds=float(_env("REPORTER_TIMEOUT", "5.0")),
            max_retries=int(_env("REPORTER_MAX_RETRIES", "3")),
        ),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # 1. Bootstrap logging before anything else so even config errors appear
    #    in the structured log stream.
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    configure_logging(level=log_level)
    logger = logging.getLogger(__name__)

    # 2. Load + validate config.  ValidationError here is fatal by design.
    try:
        config = _build_config()
    except (ValidationError, ValueError) as exc:
        logger.error(
            "Configuration is invalid — aborting before any processing",
            extra={"error": str(exc)},
        )
        sys.exit(1)

    # Re-configure logging with job_id so every subsequent line is tagged.
    configure_logging(level=log_level, job_id=config.job_id)
    logger = logging.getLogger(__name__)

    logger.info("Pipeline configuration loaded", extra={"job_id": config.job_id})

    # 3. Generate synthetic feed if it doesn't exist (dev/CI helper).
    if not os.path.exists(config.video_path):
        logger.info(
            "Video file not found — generating synthetic feed",
            extra={"video_path": config.video_path},
        )
        generate_synthetic_video(config.video_path)

    # 4. Wire up the reporter.  BEST_EFFORT: a transient network blip must not
    #    kill a long-running video job.
    with PipelineReporter(config.reporter, mode=ReportingMode.BEST_EFFORT) as reporter:

        # 5. Post pipeline_started event.
        reporter.report_event(
            EventPayload(job_id=config.job_id, event_type="pipeline_started")
        )

        # 6. Build and run the analyzer.
        analyzer = FieldBoundaryAnalyzer(config)
        result = None
        exit_code = 0

        try:
            result = analyzer.process_video()

            # Emit periodic progress checkpoints based on final result.
            # (A callback-based approach would emit them in real-time; see
            # DECISIONS.md for why that's the right next step.)
            interval = config.reporter.progress_interval_frames
            total = result.frames_processed

            for checkpoint in range(interval, total + interval, interval):
                checkpoint = min(checkpoint, total)
                approx_found = (
                    int(result.boundaries_found * checkpoint / total) if total else 0
                )
                approx_skipped = checkpoint - approx_found
                pct = (checkpoint / total * 100.0) if total else 0.0

                reporter.report_progress(
                    ProgressPayload(
                        job_id=config.job_id,
                        frames_processed=checkpoint,
                        boundaries_found=approx_found,
                        frames_skipped=approx_skipped,
                        progress_pct=round(pct, 2),
                    )
                )

            # 7. Post pipeline_completed with full metrics.
            reporter.report_event(
                EventPayload(
                    job_id=config.job_id,
                    event_type="pipeline_completed",
                    detail="Run finished successfully",
                    frames_processed=result.frames_processed,
                    boundaries_found=result.boundaries_found,
                    frames_skipped=result.frames_skipped,
                    detection_rate=round(result.detection_rate, 4),
                    elapsed_seconds=round(result.elapsed_seconds, 3),
                )
            )

            logger.info(
                "Pipeline completed successfully",
                extra={
                    "frames_processed": result.frames_processed,
                    "boundaries_found": result.boundaries_found,
                    "detection_rate": round(result.detection_rate, 4),
                    "elapsed_seconds": round(result.elapsed_seconds, 3),
                },
            )

        except PipelineError as exc:
            exit_code = 1
            logger.error(
                "Pipeline failed with a fatal error",
                extra={"error": str(exc)},
                exc_info=True,
            )
            reporter.report_event(
                EventPayload(
                    job_id=config.job_id,
                    event_type="pipeline_failed",
                    detail=str(exc),
                    frames_processed=result.frames_processed if result else 0,
                    boundaries_found=result.boundaries_found if result else 0,
                    frames_skipped=result.frames_skipped if result else 0,
                    detection_rate=(
                        round(result.detection_rate, 4) if result else 0.0
                    ),
                    elapsed_seconds=(
                        round(result.elapsed_seconds, 3) if result else 0.0
                    ),
                )
            )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
