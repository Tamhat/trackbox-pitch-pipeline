# ---- build stage: install heavyweight deps separately so layer is cached ----
FROM python:3.12-slim AS base

# OpenCV needs libGL at runtime (headless build avoids the full GUI stack).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cached unless requirements.txt changes).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code.
COPY pitch_pipeline/ ./pitch_pipeline/
COPY synthetic_generator.py .
COPY run_pipeline.py .

# The container writes the synthetic video to /data so the volume is optional
# but recommended for inspecting the generated file.
ENV VIDEO_PATH=/data/synthetic_pitch_feed.mp4

# Sensible defaults — override via docker-compose environment section.
ENV TARGET_FPS=5
ENV FIELD_DETECTOR_TYPE=green_mask
ENV FIELD_DETECTOR_SPORT=football
ENV FIELD_DETECTOR_MIN_AREA=1000
ENV CONFIDENCE_THRESHOLD=0.5
ENV REPORTER_PROGRESS_INTERVAL=100
ENV REPORTER_TIMEOUT=5.0
ENV REPORTER_MAX_RETRIES=3
ENV LOG_LEVEL=INFO

# JOB_ID and MOCK_API_URL have no defaults — they must be supplied at runtime.

CMD ["python", "run_pipeline.py"]
