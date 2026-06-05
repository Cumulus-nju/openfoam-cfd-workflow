"""
UrbanWind CFD — Configuration

Central configuration for paths, model settings, and CFD defaults.
"""
from __future__ import annotations

import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path("D:/Phase2_CFD_ML")
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
MODELS_DIR = FRONTEND_ROOT / "models"
STATIC_DIR = FRONTEND_ROOT / "static"
CFD_CASES_DIR = PROJECT_ROOT / "cfd_cases"
OUTPUT_DIR = PROJECT_ROOT / "model_outputs"

# Model
MODEL_FILE = MODELS_DIR / "qwen2.5-0.5b-instruct-q4_k_m.gguf"
MODEL_URL = "https://hf-mirror.com/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf"

# Model inference settings
LLM_MAX_TOKENS = 512
LLM_TEMPERATURE = 0.1        # Low temperature for structured output
LLM_TOP_P = 0.9
LLM_CONTEXT_LENGTH = 2048
LLM_N_THREADS = 2            # CPU threads for inference

# ── CFD Defaults ─────────────────────────────────────────────────────────────

# Domain padding around buildings (meters)
DOMAIN_PADDING_UPSTREAM = 80   # Upwind
DOMAIN_PADDING_DOWNSTREAM = 120 # Downwind
DOMAIN_PADDING_SIDE = 60       # Lateral
DOMAIN_PADDING_TOP_FACTOR = 3.0 # Domain height = max_building_height × factor

# Mesh resolution
BACKGROUND_CELL_SIZE = 2.0      # meters per cell in background mesh
SNAPPY_REFINEMENT_LEVELS = (2, 3)  # (min, max) refinement levels

# Wind defaults
DEFAULT_WIND_SPEED = 5.0       # m/s at reference height
DEFAULT_WIND_DIRECTION = "N"   # North wind (flowing southward)
DEFAULT_REFERENCE_HEIGHT = 10.0 # meters

# Bike defaults
BIKE_FOOTPRINT_LENGTH = 2.0    # meters (bike parking spot)
BIKE_FOOTPRINT_WIDTH = 0.6
BIKE_PROBE_HEIGHT = 1.5        # pedestrian breathing height

# Solver
SOLVER = "simpleFoam"
TURBULENCE_MODEL = "kEpsilon"
END_TIME = 1500
WRITE_INTERVAL = 100

# ── OSM Defaults ─────────────────────────────────────────────────────────────

OSM_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_TIMEOUT = 30  # seconds

# ── Server ───────────────────────────────────────────────────────────────────

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8765
