⚠️ **STATUS: PROOF OF CONCEPT — DO NOT DEPLOY TO PRODUCTION** ⚠️

# Automated Pitch Boundary & Camera Crop Engine (Prototype)

## Overview

This repository contains the v0.1 prototype for the automated pitch-boundary and camera-crop initiative. It is a
computer-vision pipeline designed to ingest multi-camera match video, detect the playing-field boundary in each
frame, and derive a recommended camera crop layout from that boundary.

Currently, this is a synchronous, single-file script primarily used by the research team to validate the detection
approach before it gets built out into a production pipeline.

## Technology Stack

- **Language:** Python 3.12+
- **Field Detection:** Mock segmentation mask (color-threshold placeholder standing in for a real SAM-style model).
- **Geometry:** Shapely, for polygon derivation and spatial checks.
- **Video I/O:** OpenCV (`cv2.VideoCapture`).

## Features

- **Synthetic Feed Generation:** Generates a dummy match-style video so the script runs standalone with no external
  assets.
- **Field-Boundary Detection:** Extracts a mask per frame and derives a boundary polygon from it.
- **Crop Recommendation Inputs:** The boundary polygons produced here are meant to feed a downstream crop-layout
  step (not yet implemented in this prototype).
- **Execution Metrics:** Reports how many frames were processed and how many boundaries were found.

## Installation

Ensure you have a virtual environment set up, then install the dependencies:

```bash
pip install -r requirements.txt
```

## Usage

To run the pipeline with a generated synthetic feed, execute the entry point:

```bash
python synthetic_field_prototype.py
```
