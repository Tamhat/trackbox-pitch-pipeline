# DECISIONS.md — Pitch Boundary Pipeline

## 1. Assumptions & open questions

### Assumptions made
| Area | Assumption | Rationale |
|---|---|---|
| Video stability | Frames with no visible pitch (camera cuts, close-ups) are a normal, expected condition — not an error. | The prototype's own `synthetic_generator.py` deliberately injects them. |
| Frame dimensions | Bounding-box intersection is computed against a fixed 1280×720 boundary. | The synthetic feed is generated at that resolution. |
| Detection output | A `None` return from the detector means "no pitch found in this frame" — not a hardware fault. | Matches prototype behaviour; faults surface as exceptions, not `None`. |
| Reporting service availability | The mock API may be briefly unavailable at startup (container scheduling). | Handled via `depends_on: condition: service_healthy` in docker-compose. |
| Single-camera feed | The pipeline processes one video file per invocation. | Prototype scope; multi-camera support would require a job queue. |

### Questions I would ask the product / ML team before going to production
1. **What is the minimum acceptable detection rate?** Currently there is no alerting if the pipeline processes 1 800 frames and finds zero boundaries — that could mean a bug or simply a very noisy feed. A configurable minimum-rate threshold with a `pipeline_warning` event would close this gap.
2. **Should the pipeline resume from a checkpoint on restart?** The current design re-processes from frame 0 on every run. For feeds measured in hours that needs a seek-to-last-checkpoint mechanism.
3. **What does the downstream crop step actually need from the polygon output?** Right now the polygons are held in memory and discarded. If the crop engine needs them serialised (e.g. to a database or object store) that changes the output contract of `process_video`.
4. **Is `target_fps` a hard cap or a best-effort guidance?** At low native frame rates the stride calculation may produce `stride=1` (i.e. no sampling at all). The current code handles this gracefully but the caller should be aware.
5. **What is the SLA for a single run?** This determines whether the frame-sampling rate is tight enough, or whether parallel processing across video segments is needed.

---

## 2. Validation strictness vs. fallback

### Fail fast (no fallback)
- **Configuration** (`PipelineConfig`, `FieldDetectorConfig`, `ReporterConfig`): Any invalid or missing value raises `pydantic.ValidationError` and the process exits with code 1 before touching a single frame. Silent fall-backs to defaults for values that affect pipeline behaviour (HSV bounds, detector type, min_area) were explicitly rejected — a wrong default produces wrong results that look correct.
- **Video file not openable**: Raises `PipelineError` immediately. There is no meaningful work to do without an input.
- **Unknown detector type**: Raises `PipelineError` at construction time. Proceeding would produce no detections and no error, which is the worst possible failure mode in a batch environment.

### Tolerated / recoverable
- **Individual frame read errors mid-stream**: Logged as WARNING, counted in `frames_skipped`, loop continues. A single corrupted frame in a 1 800-frame video should not abort the run.
- **Detector returning `None`**: Expected for camera cuts / occlusions. Counted, logged at DEBUG, never pollutes metrics.
- **Reporting service unreachable** (`BEST_EFFORT` mode): Logged as WARNING, pipeline continues. A transient network blip between containers must not kill a long-running video job. An operator can reconstruct the run outcome from the container logs (which are structured JSON) even if some API posts were dropped.

The reporting mode can be switched to `STRICT` via `ReportingMode.STRICT` if a specific deployment requires guaranteed delivery — the flag is exposed in `PipelineReporter.__init__`.

---

## 3. Performance trade-offs

### Frame sampling (primary lever)
The prototype's `time.sleep(0.005)` per frame was removed — it was a simulation artefact with no place in production code. The real throughput lever is the sampling stride:

- `target_fps=5` on a 30 fps feed → stride 6 → ~300 frames processed instead of 1 800 → ~6× speedup with no accuracy loss for boundary detection (boundaries don't change that fast).
- Seeking is done via `cap.set(CAP_PROP_POS_FRAMES, n)` so the decoder skips the intervening frames entirely at the OS/codec level.

### Intersection area
Computed once per detected polygon and stored in `FrameResult`. The prototype computed it on every polygon in the result list after the loop; if a downstream step were to recompute it per-frame, it would be O(n) instead of O(1).

### Caching / memoisation
The `_FRAME_BOUNDS` polygon is a class-level constant, not re-constructed per frame.

### What was deliberately left out
- **Parallel processing** (splitting the video into N segments and processing on N workers): meaningful for feeds > 30 minutes; out of scope for this prototype.
- **GPU-accelerated decoding**: relevant once a real SAM-style model replaces the colour-threshold detector.
- **Progress callbacks in the analyzer**: the current design replays approximate progress checkpoints after the run rather than emitting them mid-loop. A real-time callback (e.g. `on_progress: Callable[[int, int], None]`) would be the right next step and is a small, additive change.

---

## 4. AI / LLM disclosure

I used Claude (via the Kiro IDE) throughout this exercise. Here is an honest account of what it did and what I directed:

| What the LLM produced | What I decided / directed / verified |
|---|---|
| Boilerplate Pydantic model structure for `PipelineConfig` and nested models | I specified *which* fields to require vs. default, the `frozen=True` constraint, and the HSV validator logic |
| The `_JsonFormatter` class structure | I specified the JSON-lines output requirement, the `job_id` injection, and the `_STANDARD_LOG_KEYS` exclusion list |
| The `PipelineReporter` retry loop skeleton | I specified the `BEST_EFFORT` / `STRICT` isolation contract, the exponential back-off formula, and that `ReporterError` must be a distinct type |
| The `FieldDetector` Protocol definition | I chose structural sub-typing (Protocol) over ABC to avoid forcing inheritance on future model-backed detectors |
| Docstrings and inline comments | I reviewed every comment for accuracy and removed/rewrote several that described the wrong behaviour |
| The `docker-compose.yml` healthcheck | I specified that the runner must wait for the API to be healthy, not just started |

In summary: the LLM handled repetitive structure and boilerplate; I drove all architectural decisions, specified the failure taxonomy, and reviewed every output for correctness before accepting it.