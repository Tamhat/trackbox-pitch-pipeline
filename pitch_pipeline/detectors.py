"""
pitch_pipeline.detectors
------------------------
Defines the one seam that actually needs to flex: how a single video frame is
turned into a field-boundary polygon.

Design notes
~~~~~~~~~~~~
* ``FieldDetector`` is a ``typing.Protocol`` (structural sub-typing).  Any
  callable object that has a ``detect(frame)`` method returning an optional
  Shapely Polygon satisfies the contract — no inheritance required.  This keeps
  the pipeline code decoupled from the detection implementation without
  imposing a class hierarchy.

* ``GreenMaskDetector`` is the colour-threshold implementation ported from the
  prototype.  It is the *only* concrete detector in v0.1; model-backed
  detectors (SAM, custom segmenters) plug in here without touching anything
  else.

* A ``build_detector`` factory centralises the mapping from the config's
  ``type`` string to a concrete class so the registry lives in one place.
"""

from __future__ import annotations

import logging
from typing import Optional, Protocol, runtime_checkable

import cv2
import numpy as np
from shapely.geometry import Polygon

from pitch_pipeline.config import FieldDetectorConfig

logger = logging.getLogger(__name__)


@runtime_checkable
class FieldDetector(Protocol):
    """
    Structural protocol for field-boundary detectors.

    A detector receives a single BGR video frame (H×W×3 uint8 NumPy array) and
    returns either a valid Shapely Polygon representing the detected field
    boundary, or ``None`` when no boundary could be found in that frame.

    Any object that implements this ``detect`` signature satisfies the
    protocol — inheritance is not required.
    """

    def detect(self, frame: np.ndarray) -> Optional[Polygon]:
        """
        Analyse one video frame and return a field-boundary polygon.

        Parameters
        ----------
        frame:
            BGR image as a uint8 NumPy array of shape (H, W, 3).

        Returns
        -------
        Polygon | None
            A valid Shapely Polygon when a boundary is found, ``None``
            otherwise (blank frame, camera cut, no visible pitch, etc.).
        """
        ...


class GreenMaskDetector:
    """
    Colour-threshold field detector.

    Converts the frame to HSV, applies a configurable green-range mask, finds
    the largest external contour, and converts it to a Shapely Polygon.

    This is intentionally the simplest possible implementation: it is a
    stand-in for the real SAM-style segmenter and is kept here so the
    pipeline can be exercised end-to-end without a GPU or ML model.
    """

    def __init__(self, cfg: FieldDetectorConfig) -> None:
        self._min_area: int = cfg.min_area
        self._lower = np.array(cfg.hsv_lower, dtype=np.uint8)
        self._upper = np.array(cfg.hsv_upper, dtype=np.uint8)

    def detect(self, frame: np.ndarray) -> Optional[Polygon]:
        """Return the largest green-region polygon, or None."""
        try:
            mask = self._extract_mask(frame)
            return self._mask_to_polygon(mask)
        except Exception:
            # Surface the exception as a log warning so callers see something
            # went wrong at the detector level without crashing the loop.
            logger.warning("GreenMaskDetector.detect raised an unexpected error", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_mask(self, frame: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        return cv2.inRange(hsv, self._lower, self._upper)

    def _mask_to_polygon(self, mask: np.ndarray) -> Optional[Polygon]:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        largest = max(contours, key=cv2.contourArea)
        if cv2.contourArea(largest) < self._min_area:
            logger.debug(
                "Largest contour area %.1f below min_area threshold %d — skipping",
                cv2.contourArea(largest),
                self._min_area,
            )
            return None

        pts = largest.reshape(-1, 2)
        if len(pts) < 3:
            return None

        poly = Polygon(pts)
        if not poly.is_valid:
            # Attempt to recover a valid polygon via buffer(0) — common fix for
            # self-intersecting contours produced by noisy masks.
            poly = poly.buffer(0)
            if not poly.is_valid or poly.is_empty:
                logger.debug("Contour polygon invalid even after buffer(0) — discarding")
                return None

        return poly


# ---------------------------------------------------------------------------
# Registry / factory
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type] = {
    "green_mask": GreenMaskDetector,
}


def build_detector(cfg: FieldDetectorConfig) -> FieldDetector:
    """
    Instantiate the correct ``FieldDetector`` implementation for the given
    configuration.

    Raises
    ------
    ValueError
        If ``cfg.type`` does not map to any registered detector.  This is a
        configuration error and should propagate to the caller as a fatal
        failure (handled at the entry-point level).
    """
    cls = _REGISTRY.get(cfg.type)
    if cls is None:
        known = ", ".join(sorted(_REGISTRY))
        raise ValueError(
            f"Unknown detector type {cfg.type!r}. Known types: {known}"
        )
    logger.debug("Building detector type=%r sport=%r", cfg.type, cfg.sport)
    return cls(cfg)
