"""
UrbanWind CFD — Configuration

Central configuration for paths, model settings, and CFD defaults.

所有路径均**自动定位**（相对于本文件）并可用环境变量覆盖，
因此 clone 到任何机器、任何盘符、任何目录都能直接运行，无需改代码。

环境变量（可选）：
    URBANWIND_CASES_DIR    CFD 案例目录（默认 <项目根>/cfd_cases）
    URBANWIND_OUTPUT_DIR   后处理输出目录（默认 <项目根>/model_outputs）
    URBANWIND_GNN_DIR      GNN 训练代码目录（含 model.py / dataset.py / config.py）
    URBANWIND_GNN_CKPT     GNN 权重文件（.pt）
    URBANWIND_LLM_SERVER   外部 llama.cpp server 地址（默认自动拉起内置 server）
    GAODE_API_KEY          高德地图 Web 服务 Key（不设则高德数据源不可用）
    URBANWIND_ADMIN_USER / URBANWIND_ADMIN_PASSWORD  首次启动创建的管理员账号
"""
from __future__ import annotations

import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
# 以本文件位置反推项目根：<root>/frontend/config.py → <root>
# 这样换机器 / 换盘符 / 改目录名都不用动代码。

FRONTEND_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = FRONTEND_ROOT.parent
MODELS_DIR = FRONTEND_ROOT / "models"
STATIC_DIR = FRONTEND_ROOT / "static"


def _env_path(name: str, default: Path) -> Path:
    """读环境变量路径；空值视作未设置。"""
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default


# CFD 案例与输出：默认落在项目根下（clone 即用），也可指向数据盘
CFD_CASES_DIR = _env_path("URBANWIND_CASES_DIR", PROJECT_ROOT / "cfd_cases")
OUTPUT_DIR = _env_path("URBANWIND_OUTPUT_DIR", PROJECT_ROOT / "model_outputs")

# GNN 代理模型（可选功能；缺失时相关接口返回 503 并给出配置提示）
GNN_DIR = _env_path("URBANWIND_GNN_DIR", PROJECT_ROOT / "gnn")
GNN_CHECKPOINT = _env_path(
    "URBANWIND_GNN_CKPT", GNN_DIR / "checkpoints" / "stage1_best.pt"
)

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
SNAPPY_MAX_LOCAL_CELLS = 5000000
SNAPPY_MAX_GLOBAL_CELLS = 80000000  # For large domains (>50M cells)

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

# 2026-09 实测：nchc 主端点已失效（SSL 中断），openstreetmap.ru 握手超时；
# 可用端点（按延迟排序）：z.overpass-api.de (1.8s) → overpass-api.de (7s) → kumi (12s) → osm.ch (快但数据少，作末位兜底)
OSM_OVERPASS_URL = "https://z.overpass-api.de/api/interpreter"
OSM_OVERPASS_FALLBACKS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
]
OSM_TIMEOUT = 90  # seconds (increased for slow connections)

# ── Server ───────────────────────────────────────────────────────────────────

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8765

# ── Auth (登录认证) ───────────────────────────────────────────────────────────

# 用户存储文件（gitignored）；首次启动自动创建默认管理员账号
AUTH_USERS_FILE = FRONTEND_ROOT / "users.json"
# 默认管理员账号（仅首次启动且 users.json 不存在时创建）
# 生产/公网部署请务必用环境变量覆盖，或创建后立即通过界面改密。
ADMIN_DEFAULT_USERNAME = os.environ.get("URBANWIND_ADMIN_USER", "admin")
ADMIN_DEFAULT_PASSWORD = os.environ.get("URBANWIND_ADMIN_PASSWORD", "urbanwind2026")
# 会话 Cookie 名称与有效期（秒）
SESSION_COOKIE_NAME = "uw_session"
SESSION_TTL = 7 * 24 * 3600   # 7 天
