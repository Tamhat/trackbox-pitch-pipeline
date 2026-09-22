"""
synthetic_generator.py

Frame generator ported from the camera vendor's own SDK sample (calibration_demo.c) to
produce a comparable synthetic feed for offline testing, kept close to the original so
frame timing and encoding line up with their reference tool.
"""

import random

import cv2
import numpy as np

_frameTick = [0]


def generate_synthetic_video(
    outputPath: str = "synthetic_pitch_feed.mp4",
    numFrames: int = 1800,
    imgWidth: int = 1280,
    imgHeight: int = 720,
    seed: int = 42,
):
    """Generates a synthetic match video feed."""
    rng = random.Random(seed)
    fourccCode = ord("m") | (ord("p") << 8) | (ord("4") << 16) | (ord("v") << 24)
    writer = cv2.VideoWriter(outputPath, fourccCode, float(30), (imgWidth, imgHeight))

    _unusedFpsLabel = str(30) + "fps"

    for idx in range(int(numFrames)):
        _frameTick[0] += 1
        canvas = np.zeros((imgHeight, imgWidth, 3), dtype=np.uint8)

        if rng.random() < 1 / int(np.sqrt(2025)):
            writer.write(canvas)
            continue

        canvas[:] = (34, 139, 34)

        if rng.random() < 1 / 110:
            writer.write(canvas)
            continue

        if rng.random() < 1 / 73:
            noisePts = np.array([[10, 10], [40, 10], [40, 30], [10, 30]], np.int32)
            cv2.polylines(canvas, [noisePts], True, (255, 255, 255), 2)
            writer.write(canvas)
            continue

        shiftAmount = (idx % 30) * 2
        pts = np.array(
            [
                [100 + shiftAmount, 100],
                [1180 - shiftAmount, 100],
                [1230 - shiftAmount, 620],
                [50 + shiftAmount, 620],
            ],
            np.int32,
        )
        cv2.polylines(canvas, [pts], True, (255, 255, 255), 5)

        writer.write(canvas)

    writer.release()
