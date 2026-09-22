"""
UrbanWind CFD — 综合评估引擎（多情景风场 → 停放适宜性分析）

支持两类数据源，格式互认（系统导出格式 = 上传格式）：
  1. GNN 在线预测：predict-wind / predict-from-case 同源的规则网格 (250×250)
  2. 本地上传模拟数据：CSV（表头 x,y,Ux,Uy,speed[,wind_direction,inlet_speed]，
     z/Uz 列可选，点云或规则网格均可；OpenFOAM 切片脚本输出即此格式）
     —— 也支持 zip 打包多个 CSV，文件名可带风向风速（如 case_N_5.0.csv）

综合输出：
  - 加权平均风速场（权重可在前端逐情景调整）
  - 静风频率（v < calm_thresh）、强风频率（v > 阵风修正阈值）
  - 单车停放适宜性分级（0 静风区 / 1 适宜 / 2 中风险 / 3 高风险）
  - 分级统计 + 推荐/危险区域 Top 列表 + 多面板报告图（PNG base64）
"""
from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ── 共享参数（与 bike-siting 一致，可被请求覆盖） ────────────────────────────

V_CRIT = 11.7        # 单车倾覆临界风速 m/s，【阵风口径】（bike_wind_overturning_model.tex）
# ── gust_factor 的推导（2026-09-22 定）──────────────────────────────────────
# 用户确认：11.7 是【阵风】口径（使单车倾倒的是阵风），而模拟/上传数据是【平均】风速。
# 所以要换算：判决阈值（平均口径）= 11.7 ÷ G。
#
# G 有明确的物理形式：  G = 1 + k · TI
#     k  = 峰值因子，取 3（3s 阵风对应的常用值）
#     TI = 湍流强度
# 城市行人高度（约 1.5 m）的 TI 典型 0.2~0.35，取中值 0.3：
#     G = 1 + 3 × 0.3 = 1.9
#     → 判决阈值 = 11.7 ÷ 1.9 ≈ 6.2 m/s
#     → gust_factor = 1/G ≈ 0.53
#
# 敏感性：TI=0.25 → gust_factor 0.57（阈值 6.7）；TI=0.35 → 0.49（阈值 5.7）。
#   即合理带是 0.49~0.57。窄街谷/背风侧 TI 可能更高（到 0.4），系数应更低。
#   若案例导出了湍动能 k，可用 TI = sqrt(2k/3) / U 直接算出该场地的 TI 再精修。
#
# ⚠ 旧值 0.67（G≈1.49）是照开阔地取的，用于街道峡谷偏宽松，已弃用。
# ⚠ 历史遗留注释曾写作「阵风修正 (7.8/11.7)」，那是先有 7.8 再倒推的，
#   7.8 本身无出处；现已改为上面的物理推导。不要再倒推。
GUST_FACTOR = 0.53   # = 1/G，G ≈ 1.9（见上方推导）
HIGH_FACTOR = 0.8    # 高风险阈值系数（相对判决阈值）
MEDIUM_FACTOR = 0.5  # 中风险阈值系数
CALM_SPEED = 1.5     # 静风判据 m/s
STRONG_FREQ_HIGH = 0.30   # 高风险强风频率阈值
STRONG_FREQ_MED = 0.10    # 中风险强风频率阈值
GRID_MAX = 300       # 参考网格最大边长（超出则自动放大网格尺寸）
POINT_LIMIT = 2_000_000   # 单场景点数量上限

GRADE_COLORS = {0: "#38bdf8", 1: "#10b981", 2: "#f59e0b", 3: "#ef4444"}
GRADE_LABELS = {0: "静风区", 1: "适宜", 2: "中风险", 3: "高风险"}

# ── 评估情境（同一套多情景加权框架，按用途换阈值与措辞） ──────────────────────
# 单车：以倾覆临界风速为标尺（bike_wind_overturning_model.tex）
# 无人机：以机型抗风等级为标尺（沿航线各高度层风速 → 按抗风等级分级）
CONTEXTS: Dict[str, Dict[str, Any]] = {
    "bike": {
        "key": "bike",
        "label": "共享单车停放适宜性",
        "unit_actor": "单车",
        "ground": "停放",
        "v_crit": V_CRIT,              # 11.7
        "gust_factor": GUST_FACTOR,    # 0.67
        "high_factor": HIGH_FACTOR,    # 0.8
        "medium_factor": MEDIUM_FACTOR,# 0.5
        "calm_speed": CALM_SPEED,      # 1.5
        "grade_labels": dict(GRADE_LABELS),
        "title": "UrbanWind 综合评估 · 单车停放适宜性（多情景加权）",
        "metric_name": "加权平均风速",
    },
    "drone": {
        "key": "drone",
        "label": "无人机航线适飞性",
        "unit_actor": "无人机",
        "ground": "适飞",
        # 机型抗风等级：6 级（≤13.8 m/s）为多数消费级/行业级机型上限。
        # 与单车同一口径：厂商标称抗风能力是【阵风】口径，而模拟给的是平均风，
        # 故同样按 G≈1.9 换算 → 12.0 × 0.53 ≈ 6.4 m/s。
        "v_crit": 12.0,
        "gust_factor": GUST_FACTOR,
        "high_factor": 0.85,
        "medium_factor": 0.55,
        "calm_speed": 2.0,
        "grade_labels": {0: "风力不足/悬停受限", 1: "适飞", 2: "谨慎飞行", 3: "禁飞风险"},
        "title": "UrbanWind 综合评估 · 无人机航线适飞性（多情景加权）",
        "metric_name": "加权平均风速",
    },
}


def resolve_context(name: str) -> Dict[str, Any]:
    """取评估情境参数；未知名称回落到单车。"""
    return CONTEXTS.get((name or "bike").strip().lower(), CONTEXTS["bike"])


WD_VEC = {"N": (0.0, 1.0), "S": (0.0, -1.0), "E": (1.0, 0.0), "W": (-1.0, 0.0)}

# 中文风向名 → 字母。16 方位里 "北风" 是 "西北风/东北风" 的前缀，
# 所以按名字长度从长到短排，先匹配长的（西北风 优于 西风/北风）。
_CN_WIND_DIRS = [
    ("西北偏北", "NNW"), ("西北偏西", "WNW"),
    ("东北偏北", "NNE"), ("东北偏东", "ENE"),
    ("西南偏南", "SSW"), ("西南偏西", "WSW"),
    ("东南偏南", "SSE"), ("东南偏东", "ESE"),
    ("西北风", "NW"), ("东北风", "NE"), ("西南风", "SW"), ("东南风", "SE"),
    ("北风", "N"), ("南风", "S"), ("东风", "E"), ("西风", "W"),
]
_VALID_WD = {"N", "S", "E", "W", "NE", "NW", "SE", "SW",
             "NNE", "ENE", "ESE", "SSE", "SSW", "WSW", "WNW", "NNW"}


# ── CSV 解析 ──────────────────────────────────────────────────────────────────

def _guess_columns(header: List[str]) -> Dict[str, int]:
    """把表头映射到标准列名；支持中英文及常见别名。

    注意 x/y 只认「米/投影坐标」语义的列名；lon/lng/lat 单独走 lat/lng，
    这样「经纬度 + 米坐标」同时存在的文件也能正确区分（地图叠加要用后者）。
    """
    aliases = {
        "x": ["x", "px", "x_m", "easting"],
        "y": ["y", "py", "y_m", "northing"],
        "ux": ["ux", "u_x", "u", "vx", "u1"],
        "uy": ["uy", "u_y", "v", "vy", "u2"],
        "uz": ["uz", "u_z", "w", "vz", "u3"],
        "speed": ["speed", "spd", "vel", "magnitude", "velocity", "风速", "wind_speed"],
        "wd": ["wind_direction", "wind_dir", "direction", "dir", "风向"],
        "speed_inlet": ["inlet_speed", "wind_speed_inlet", "ref_speed", "入口风速", "风速_inlet"],
        # 地理坐标（可选；有的话地图才能叠加、才能在上面画航线）
        "lat": ["lat", "latitude", "纬度"],
        "lng": ["lng", "lon", "long", "longitude", "经度"],
    }
    mapping: Dict[str, int] = {}
    lower = [str(h).strip().lower() for h in header]
    for std, names in aliases.items():
        for i, h in enumerate(lower):
            if h in names:
                mapping[std] = i
                break
    # 兼容旧写法：表头用 lon/lng/经度 当 x、lat/纬度 当 y 且没有独立 lat/lng 列时，
    # 仍按米坐标处理（老文件不能失效）
    if "x" not in mapping and "lng" in mapping:
        mapping["x"] = mapping["lng"]
        mapping.pop("lng", None)
    if "y" not in mapping and "lat" in mapping:
        mapping["y"] = mapping["lat"]
        mapping.pop("lat", None)
    return mapping


def _parse_float(v: str) -> Optional[float]:
    try:
        f = float(str(v).strip())
        if np.isnan(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def parse_scene_csv(text: str, filename: str = "", wind_dir: str = "",
                    inlet_speed: Optional[float] = None,
                    extra_hint: str = "") -> Dict[str, Any]:
    """解析 CSV 文本 → 标准场景数据（点云 + 规则网格检测）。

    extra_hint：额外的命名线索。OpenFOAM 导出的文件名常是无信息的
    （如统一叫 cell_data_1.5m.csv），风向风速只在**上级目录名**里，
    所以调用方把目录名一并传进来一起识别。
    """
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if len(lines) < 2:
        raise ValueError("CSV 内容为空或只有一行")

    # 分隔符：逗号优先，空格/制表符兼容
    first = lines[0]
    delim = "\t" if "\t" in first and "," not in first else ("," if "," in first else None)
    if delim is None:
        # 空格分隔（纯数字多空格）
        first_cells = first.split()
        mapping: Dict[str, int] = {}
        header_mode = False
    else:
        first_cells = [c.strip() for c in first.split(delim)]
        header_mode = any(re.search(r"[A-Za-z\u4e00-\u9fff]", c) for c in first_cells)

    if header_mode:
        mapping = _guess_columns(first_cells)
        if "x" not in mapping or "y" not in mapping:
            raise ValueError(f"CSV 表头缺少 x/y 列: {first_cells}")
        if "speed" not in mapping and "ux" not in mapping and "uy" not in mapping:
            raise ValueError("CSV 表头缺少风速列 (speed 或 Ux/Uy)")
        data_lines = lines[1:]
    else:
        mapping = {}
        data_lines = lines

    xs, ys, uxs, uys, spds = [], [], [], [], []
    lats, lngs = [], []
    auto_wd, auto_vi = wind_dir, inlet_speed
    speed_pos, ux_pos, uy_pos = mapping.get("speed"), mapping.get("ux"), mapping.get("uy")
    wd_pos, vi_pos = mapping.get("wd"), mapping.get("speed_inlet")
    lat_pos, lng_pos = mapping.get("lat"), mapping.get("lng")

    for ln in data_lines:
        cells = [c.strip() for c in ln.split(delim)] if delim else ln.split()
        if len(cells) < 2:
            continue
        if header_mode:
            if "x" not in mapping or len(cells) <= max(mapping.values()):
                continue
            x = _parse_float(cells[mapping["x"]]); y = _parse_float(cells[mapping["y"]])
            ux = _parse_float(cells[ux_pos]) if (ux_pos is not None and ux_pos < len(cells)) else None
            uy = _parse_float(cells[uy_pos]) if (uy_pos is not None and uy_pos < len(cells)) else None
            sp = _parse_float(cells[speed_pos]) if (speed_pos is not None and speed_pos < len(cells)) else None
            if wd_pos is not None and wd_pos < len(cells) and not auto_wd:
                auto_wd = str(cells[wd_pos]).strip().upper()[:1]
            if vi_pos is not None and vi_pos < len(cells) and auto_vi is None:
                auto_vi = _parse_float(cells[vi_pos])
            if lat_pos is not None and lat_pos < len(cells):
                la = _parse_float(cells[lat_pos])
                if la is not None:
                    lats.append(la)
            if lng_pos is not None and lng_pos < len(cells):
                lo = _parse_float(cells[lng_pos])
                if lo is not None:
                    lngs.append(lo)
        else:
            # 无表头：≥4 列 → x,y,Ux,Uy(,speed)；3 列 → x,y,speed
            if len(cells) >= 4:
                x = _parse_float(cells[0]); y = _parse_float(cells[1])
                ux = _parse_float(cells[2]); uy = _parse_float(cells[3])
                sp = _parse_float(cells[4]) if len(cells) >= 5 else None
            elif len(cells) == 3:
                x = _parse_float(cells[0]); y = _parse_float(cells[1])
                ux = uy = None; sp = _parse_float(cells[2])
            else:
                continue
        if x is None or y is None:
            continue
        if sp is None:
            if ux is None or uy is None:
                continue
            sp = float(np.hypot(ux, uy)) if (ux is not None and uy is not None) else None
            if sp is None:
                continue
        xs.append(x); ys.append(y); spds.append(sp)
        uxs.append(ux if ux is not None else 0.0)
        uys.append(uy if uy is not None else 0.0)

    if len(xs) < 10:
        raise ValueError(f"有效数据点不足（仅 {len(xs)} 个）")
    if len(xs) > POINT_LIMIT:
        raise ValueError(f"数据点过多（{len(xs):,} > {POINT_LIMIT:,}），请降低分辨率或分区上传")

    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    speed = np.asarray(spds, dtype=np.float64)
    ux = np.asarray(uxs, dtype=np.float64)
    uy = np.asarray(uys, dtype=np.float64)

    # 文件名识别风向风速（严格 token 匹配，避免 eval_s2.csv 这类误判）：
    #  basename 以 _ / - / . / 空格 分隔的 token 恰好为单字母 N/S/E/W，
    #  且相邻 token 为数字（如 case_N_5.0.csv、case_5_N.csv、N5.csv）
    auto_wd = auto_wd or ""
    stem = Path(filename).stem if filename else ""
    # 合并线索：文件名 + 上级目录（目录在前，文件名优先识别？→ 方向取先命中的，
    # 文件名通常更精确，所以先试文件名，没有再试目录）
    hint = stem
    if extra_hint:
        hint = stem + " " + extra_hint

    # ① 中文风向 + 相邻风速（如 西区_西北风_7ms、西区_西风_7ms）
    #    16 方位中文名里的 "北风/南风/东风/西风" 是前缀，必须用全名精确匹配
    for cn_name, letter in _CN_WIND_DIRS:
        if cn_name in hint:
            if not auto_wd:
                auto_wd = letter
            if auto_vi is None:
                m = re.search(re.escape(cn_name) + r"[_\-\s]*(\d+(?:\.\d+)?)", hint)
                if m:
                    auto_vi = float(m.group(1))
            break

    tokens = [t for t in re.split(r"[_\-\s\.]+", hint) if t]
    for i, tok in enumerate(tokens):
        if len(tok) == 1 and tok.upper() in ("N", "S", "E", "W"):
            # 方向 token 后跟数字，或前面是数字
            if i + 1 < len(tokens) and re.fullmatch(r"\d+(?:\.\d+)?", tokens[i + 1]):
                if not auto_wd:
                    auto_wd = tok.upper()
                if auto_vi is None:
                    auto_vi = float(tokens[i + 1])
            elif i > 0 and re.fullmatch(r"\d+(?:\.\d+)?", tokens[i - 1]):
                if not auto_wd:
                    auto_wd = tok.upper()
                if auto_vi is None:
                    auto_vi = float(tokens[i - 1])
    # 合理范围校验（0.5–60 m/s；方向限 16 方位）
    if auto_vi is not None and not (0.5 <= auto_vi <= 60):
        auto_vi = None
    if auto_wd and auto_wd.upper() not in _VALID_WD:
        auto_wd = ""

    return {
        "x": x, "y": y, "ux": ux, "uy": uy, "speed": speed,
        "bounds": [float(x.min()), float(y.min()), float(x.max()), float(y.max())],
        # 有经纬度列时记录地理位置，前端才能把结果叠到地图上 / 在地图上画航线
        "latlng_bounds": (
            [float(min(lats)), float(min(lngs)), float(max(lats)), float(max(lngs))]
            if (len(lats) >= 10 and len(lngs) >= 10 and
                max(lats) != min(lats) and max(lngs) != min(lngs))
            else None
        ),
        "regular": False, "grid_x": None, "grid_y": None,
        "wind_direction": auto_wd.upper() if auto_wd else "N",
        "inlet_speed": auto_vi if auto_vi else 5.0,
    }


def parse_upload(files: bytes, filename: str) -> List[Dict[str, Any]]:
    """解析上传内容（单个 CSV 或 zip 包）→ 场景列表（未含 scene_id）。"""
    results = []
    if filename.lower().endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(files))
        except zipfile.BadZipFile as e:
            raise ValueError(f"ZIP 文件损坏: {e}")
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith((".csv", ".txt")):
                continue
            text = zf.read(info).decode("utf-8", errors="replace")
            # zip 内的路径常带目录（如 西区_西北风_7ms/cell_data.csv），目录名里有风向风速
            parts = [p for p in Path(info.filename).parts]
            hint = " ".join(parts[:-1]) if len(parts) > 1 else ""
            results.append(parse_scene_csv(text, Path(info.filename).name, extra_hint=hint))
    else:
        text = files.decode("utf-8", errors="replace")
        # 单文件上传时文件名常无信息（如 cell_data_1.5m.csv），
        # 若调用方传了带目录的路径，就用上层目录名一起识别
        p = Path(filename)
        hint = " ".join(p.parts[:-1]) if len(p.parts) > 1 else ""
        results.append(parse_scene_csv(text, p.name, extra_hint=hint))
    return results


# ── 参考网格与重采样 ──────────────────────────────────────────────────────────

def detect_grid(scene: Dict[str, Any]):
    """检测规则网格。返回 (sx, sy, vals) —— 全部升序轴；非规则网格返回 (None, None, None)。

    用 searchsorted 索引重组矩阵，不依赖原始行序（CSV 行序任意也能正确还原）。
    """
    x = scene["x"]; y = scene["y"]; speed = scene["speed"]
    if len(x) < 100:
        return None, None, None
    rx = np.round(x, 4); ry = np.round(y, 4)
    sx = np.unique(rx); sy = np.unique(ry)
    if len(sx) < 5 or len(sy) < 5 or len(sx) * len(sy) != len(x):
        return None, None, None
    dx = np.diff(sx); dy = np.diff(sy)
    if not (np.allclose(dx, dx[0], atol=1e-3) and np.allclose(dy, dy[0], atol=1e-3)):
        return None, None, None
    ix = np.searchsorted(sx, rx); iy = np.searchsorted(sy, ry)
    vals = np.full((len(sy), len(sx)), np.nan, dtype=np.float64)
    vals[iy, ix] = speed
    return sx, sy, vals


def build_reference_grid(scenes: List[Dict[str, Any]]) -> Tuple[np.ndarray, np.ndarray]:
    """由全部场景决定参考网格（并集 bounds + 自适应分辨率，边长 ≤ GRID_MAX）。"""
    xmin = min(s["bounds"][0] for s in scenes)
    ymin = min(s["bounds"][1] for s in scenes)
    xmax = max(s["bounds"][2] for s in scenes)
    ymax = max(s["bounds"][3] for s in scenes)
    w = xmax - xmin; h = ymax - ymin
    if w <= 0 or h <= 0:
        raise ValueError("场景范围无效")

    # 建议 cell：规则网格用其步长，点云按 300 格/方向估算
    cells = []
    for s in scenes:
        sx, sy, _ = detect_grid(s)
        if sx is not None:
            cells.append(abs(sx[1] - sx[0]))
        else:
            cells.append(max(w, h) / 300.0)
    cell = min(cells)
    # 保底分辨率（边长 ≤ GRID_MAX，最小 0.5m）
    cell = max(cell, max(w, h) / GRID_MAX, 0.5)
    nx = max(2, int(round(w / cell)) + 1)
    ny = max(2, int(round(h / cell)) + 1)
    if nx > GRID_MAX or ny > GRID_MAX:
        nx = GRID_MAX; ny = GRID_MAX
    grid_x = np.linspace(xmin, xmax, nx)
    grid_y = np.linspace(ymax, ymin, ny)  # 递减（第 0 行为北，与地图习惯一致）
    return grid_x, grid_y


def _resample_regular(sx, sy_asc, vals_asc, grid_x, grid_y) -> np.ndarray:
    """规则网格双线性插值到参考网格（scipy RegularGridInterpolator，越界=NaN）。

    sx / sy_asc 升序，vals_asc 行序与 sy_asc 对应；grid_y 递减（北→南）。
    """
    from scipy.interpolate import RegularGridInterpolator
    interp = RegularGridInterpolator(
        (np.asarray(sy_asc, dtype=np.float64), np.asarray(sx, dtype=np.float64)),
        np.asarray(vals_asc, dtype=np.float64),
        bounds_error=False, fill_value=np.nan,
    )
    GY, GX = np.meshgrid(np.asarray(grid_y[::-1], dtype=np.float64),
                         np.asarray(grid_x, dtype=np.float64), indexing="ij")
    out = interp(np.column_stack([GY.ravel(), GX.ravel()]))
    return out.reshape(len(grid_y), len(grid_x))[::-1, :]


def _resample_nearest(sx, sy, vals, grid_x, grid_y) -> np.ndarray:
    """点云 → 参考网格（最近邻，cKDTree）。"""
    from scipy.spatial import cKDTree
    pts = np.column_stack([sx, sy])
    tree = cKDTree(pts)
    GX, GY = np.meshgrid(grid_x, grid_y)
    _, idx = tree.query(np.column_stack([GX.ravel(), GY.ravel()]))
    out = vals[idx].reshape(grid_y.shape[0], grid_x.shape[0])
    return out


def resample_scene(scene: Dict[str, Any], grid_x: np.ndarray, grid_y: np.ndarray) -> np.ndarray:
    """把场景速度场重采样到参考网格（输出行序与 grid_y 一致：第 0 行 = 北）。"""
    sx, sy, vals = detect_grid(scene)
    if sx is not None:
        # 与参考完全一致 → 直接取（vals 行序=sy 升序，需要反转回 grid_y 递减序）
        if len(sx) == len(grid_x) and len(sy) == len(grid_y) and \
           np.allclose(sx, grid_x, atol=1e-6) and np.allclose(sy, grid_y[::-1], atol=1e-6):
            return vals[::-1, :]
        return _resample_regular(sx, sy, vals, grid_x, grid_y)
    return _resample_nearest(scene["x"], scene["y"], scene["speed"], grid_x, grid_y)


# ── 综合评估 ──────────────────────────────────────────────────────────────────

def aggregate(scenes: List[Dict[str, Any]], weights: List[float],
              v_crit: float = V_CRIT, gust_factor: float = GUST_FACTOR,
              high_factor: float = HIGH_FACTOR, medium_factor: float = MEDIUM_FACTOR,
              calm_speed: float = CALM_SPEED,
              strong_high: float = STRONG_FREQ_HIGH,
              strong_med: float = STRONG_FREQ_MED,
              ) -> Dict[str, Any]:
    """
    scenes: 已 resample 后的字典列表，每项含 speed_grid (H,W) 与 wind_direction/inlet_speed
    weights: 每情景权重（len 一致）
    """
    if not scenes:
        raise ValueError("没有可评估的情景")
    n = len(scenes)
    w = np.asarray(weights, dtype=np.float64)
    if len(w) != n:
        raise ValueError("weights 与 scenes 数量不一致")
    w = w / w.sum()
    H, W = scenes[0]["speed_grid"].shape

    stack = np.stack([s["speed_grid"] for s in scenes])  # [n, H, W]
    # 有效数据掩码：全部情景都为 NaN 的点视为无数据（输出 NaN，而不是 0）
    valid = ~np.isnan(stack)
    wsum = np.nansum(valid * w[:, None, None], axis=0)
    mean = np.nansum(np.where(valid, stack, 0.0) * w[:, None, None], axis=0)
    mean = np.where(wsum > 0, mean / np.where(wsum > 0, wsum, 1.0), np.nan)

    calm = ((stack < calm_speed).astype(np.float64) * valid)
    v_eff = v_crit * gust_factor
    strong = ((stack > v_eff).astype(np.float64) * valid)
    wsum_safe = np.where(wsum > 0, wsum, 1.0)
    calm_freq = np.where(wsum > 0, np.nansum(calm * w[:, None, None], axis=0) / wsum_safe, np.nan)
    strong_freq = np.where(wsum > 0, np.nansum(strong * w[:, None, None], axis=0) / wsum_safe, np.nan)

    # 分级：0 静风 / 1 适宜 / 2 中风险 / 3 高风险
    grade = np.full((H, W), np.nan, dtype=np.float64)
    hi_th = high_factor * v_eff
    med_th = medium_factor * v_eff
    grade = np.where(mean >= hi_th, 3.0, 0.0)
    grade = np.where((grade == 0) & (strong_freq >= strong_high), 3.0, grade)
    grade = np.where((grade == 0) & (mean >= med_th), 2.0, grade)
    grade = np.where((grade == 0) & (strong_freq >= strong_med), 2.0, grade)
    grade = np.where((grade == 0) & (mean >= calm_speed), 1.0, grade)

    # 统计（剔除 NaN/建筑）
    valid = ~np.isnan(mean)
    stats = {
        "n_scenes": n,
        "v_eff": float(v_eff),
        "calm_speed": calm_speed,
        "mean_min": float(np.nanmin(mean)) if valid.any() else None,
        "mean_max": float(np.nanmax(mean)) if valid.any() else None,
        "calm_freq_max": float(np.nanmax(calm_freq)) if valid.any() else None,
        "strong_freq_max": float(np.nanmax(strong_freq)) if valid.any() else None,
        "grade_frac": {
            int(g): float((grade[valid] == g).mean()) for g in (0, 1, 2, 3)
        },
    }
    return {
        "mean": mean, "calm_freq": calm_freq, "strong_freq": strong_freq,
        "grade": grade, "stats": stats,
    }


def top_regions(grade: np.ndarray, label: int, k: int = 5,
                block: int = 10) -> List[Dict[str, float]]:
    """按 block×block 粗化找分级区域的 Top 质心（米坐标）。"""
    H, W = grade.shape
    results = []
    mask = grade == label
    for by in range(0, H, block):
        for bx in range(0, W, block):
            sub = mask[by:by + block, bx:bx + block]
            if sub.size == 0 or sub.sum() < 0.5 * sub.size:
                continue
            yy, xx = np.where(sub)
            results.append({
                "cx": float(bx + xx.mean()),
                "cy": float(by + yy.mean()),
                "frac": float(sub.mean()),
            })
    results.sort(key=lambda r: -r["frac"])
    return results[:k]


# ── 无人机航线评估 ────────────────────────────────────────────────────────────

# 近地（行人高度 ~1.5-2m）→ 飞行高度的风速换算，用幂律剖面。
# 城市地貌 α 取 0.22（GB 50009  B 类）；z0 取参考高度。
ALPHA_URBAN = 0.22
Z_REF = 10.0          # CFD/气象参考高度 (m)


def _altitude_factor(altitude_m: float, z_ref: float = Z_REF,
                     alpha: float = ALPHA_URBAN) -> float:
    """高度修正系数：把参考高度的风速换算到目标飞行高度。

    幂律剖面 v(z) = v_ref * (z / z_ref)^alpha。
    飞行高度低于参考高度时系数 <1（贴地风更小），高于则 >1。
    """
    if altitude_m <= 0:
        return 1.0
    return float((altitude_m / z_ref) ** alpha)


def _effective_threshold(ctx: Dict[str, Any]) -> float:
    """把情境的 v_crit 换算成「平均风速口径」的判决阈值。

    gust_factor 存的是 1/G（见文件头 GUST_FACTOR 的说明）。
    """
    return float(ctx["v_crit"]) * float(ctx.get("gust_factor", 1.0))


def sample_route(wind: np.ndarray, grid_x: np.ndarray, grid_y: np.ndarray,
                 waypoints: List[Tuple[float, float]], n_samples: int = 200,
                 altitude: float = 60.0, alpha: float = ALPHA_URBAN,
                 z_ref: float = Z_REF) -> List[Dict[str, Any]]:
    """沿航线（waypoints 折线，米坐标）等距采样加权风速场。

    返回每个采样点的 {x, y, s(沿程距离), v_raw, v_alt}。
    v_raw = 该点风场值；v_alt = 按幂律剖面换算到飞行高度后的值。
    """
    if len(waypoints) < 2:
        raise ValueError("航线至少需要 2 个航点")

    pts = np.asarray(waypoints, dtype=np.float64)
    seg = np.diff(pts, axis=0)
    seg_len = np.hypot(seg[:, 0], seg[:, 1])
    total = float(seg_len.sum())
    if total <= 0:
        raise ValueError("航线长度为零")
    if n_samples < 2:
        n_samples = 2

    # 沿折线等距取 n_samples 个点
    targets = np.linspace(0.0, total, n_samples)
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    out = []
    k = 0
    for s in targets:
        while k < len(seg_len) - 1 and s > cum[k + 1]:
            k += 1
        t = 0.0 if seg_len[k] == 0 else (s - cum[k]) / seg_len[k]
        t = min(max(t, 0.0), 1.0)
        px = pts[k][0] + t * seg[k][0]
        py = pts[k][1] + t * seg[k][1]
        v = _bilinear(wind, grid_x, grid_y, px, py)
        out.append({
            "x": float(px), "y": float(py), "s": float(s),
            "v_raw": None if v is None or np.isnan(v) else float(v),
            "v_alt": None if v is None or np.isnan(v) else float(v) * _altitude_factor(altitude, z_ref, alpha),
        })
    return out


def _bilinear(grid: np.ndarray, grid_x: np.ndarray, grid_y: np.ndarray,
              px: float, py: float) -> Optional[float]:
    """在参考网格上双线性插值（grid 行序与 grid_y 一致，第 0 行=北）。

    越界或任一角点为 NaN 时返回 None。
    """
    gx = np.asarray(grid_x, dtype=np.float64)
    gy = np.asarray(grid_y, dtype=np.float64)      # 递减
    H, W = grid.shape
    if not (gx[0] <= px <= gx[-1]) or not (gy[-1] <= py <= gy[0]):
        return None
    xi = int(np.searchsorted(gx, px, side="right") - 1)
    xi = min(max(xi, 0), W - 2) if W > 1 else 0
    # gy 递减 → 反转用升序查找
    gy_asc = gy[::-1]
    yi_asc = int(np.searchsorted(gy_asc, py, side="right") - 1)
    yi_asc = min(max(yi_asc, 0), len(gy_asc) - 2) if len(gy_asc) > 1 else 0
    yi = H - 1 - yi_asc          # 转回 grid 的行索引（第 0 行=北）

    x1 = min(xi + 1, W - 1)
    y1 = max(yi - 1, 0)          # 纬度增大 → 行号减小
    dx = 0.0 if gx[x1] == gx[xi] else (px - gx[xi]) / (gx[x1] - gx[xi])
    dy = 0.0 if gy[y1] == gy[yi] else (py - gy[yi]) / (gy[y1] - gy[yi])

    vals = [grid[yi][xi], grid[yi][x1], grid[y1][xi], grid[y1][x1]]
    if any(v is None or np.isnan(v) for v in vals):
        return None
    v00, v01, v10, v11 = vals
    top = v00 * (1 - dx) + v01 * dx
    bot = v10 * (1 - dx) + v11 * dx
    return float(top * (1 - dy) + bot * dy)


def grade_route(samples: List[Dict[str, Any]], mean_th: float, strong_th: float,
                calm_th: float = 2.0) -> Dict[str, Any]:
    """按采样风速给航段分级（0 风力不足 / 1 适飞 / 2 谨慎 / 3 禁飞风险）。

    分级依据（与单车同构）：
      v >= mean_th           → 3 禁飞风险
      v >= strong_th         → 2 谨慎飞行
      v <  calm_th           → 0 风力不足/悬停受限
      否则                    → 1 适飞
    """
    segs = []
    counts = {0: 0, 1: 0, 2: 0, 3: 0}
    for smp in samples:
        v = smp["v_alt"]
        if v is None:
            g = None
        elif v >= mean_th:
            g = 3
        elif v >= strong_th:
            g = 2
        elif v < calm_th:
            g = 0
        else:
            g = 1
        if g is not None:
            counts[g] += 1
        segs.append({**smp, "grade": g})
    n = sum(counts.values()) or 1
    valid = [s["v_alt"] for s in segs if s["v_alt"] is not None]
    return {
        "segments": segs,
        "grade_counts": {str(k): v for k, v in counts.items()},
        "grade_frac": {str(k): v / n for k, v in counts.items()},
        "v_min": float(min(valid)) if valid else None,
        "v_max": float(max(valid)) if valid else None,
        "v_mean": float(sum(valid) / len(valid)) if valid else None,
        "n_valid": len(valid),
        "n_total": len(segs),
    }


# ── 报告图 ────────────────────────────────────────────────────────────────────

def _set_cn_font():
    import matplotlib
    matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["font.sans-serif"] = ["Noto Sans SC", "Microsoft YaHei", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False


def render_report(mean, calm_freq, strong_freq, grade, grid_x, grid_y,
                  stats: Dict[str, Any], scenes_meta: List[Dict[str, Any]],
                  title: str = "", ctx: Optional[Dict[str, Any]] = None) -> str:
    """四面板报告图 → PNG base64。ctx 决定标题/图例措辞（单车 or 无人机）。"""
    _set_cn_font()
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap, Normalize
    from matplotlib.patches import Patch

    ctx = ctx or CONTEXTS["bike"]
    grade_labels = ctx.get("grade_labels", GRADE_LABELS)

    H, W = grade.shape
    extent = [grid_x[0], grid_x[-1], grid_y[-1], grid_y[0]]
    import numpy.ma as ma
    mean_m = ma.masked_invalid(mean)
    calm_m = ma.masked_invalid(calm_freq)
    strong_m = ma.masked_invalid(strong_freq)
    grade_m = ma.masked_invalid(grade)

    fig, axes = plt.subplots(2, 2, figsize=(13, 10), dpi=105)
    fig.suptitle(title or ctx.get("title", "UrbanWind 综合评估（多情景加权）"),
                 fontsize=14, fontweight="bold")

    # ① 加权平均风速
    ax = axes[0][0]
    im = ax.imshow(mean_m, extent=extent, origin="upper", cmap="turbo",
                   vmin=max(stats["mean_min"], 0), vmax=stats["mean_max"] or 1)
    ax.set_title(f"① {ctx.get('metric_name', '加权平均风速')} (m/s) · {stats['n_scenes']} 情景", fontsize=11)
    fig.colorbar(im, ax=ax, shrink=0.85)

    # ② 静风频率
    ax = axes[0][1]
    im = ax.imshow(calm_m * 100, extent=extent, origin="upper", vmin=0, vmax=100, cmap="cool")
    fig.colorbar(im, ax=ax, shrink=0.85)
    ax.set_title(f"② 静风频率 (%) · v < {stats['calm_speed']:.1f} m/s", fontsize=11)

    # ③ 强风频率
    ax = axes[1][0]
    im = ax.imshow(strong_m * 100, extent=extent, origin="upper", vmin=0, vmax=100, cmap="hot")
    fig.colorbar(im, ax=ax, shrink=0.85)
    ax.set_title(f"③ 强风频率 (%) · v > {stats['v_eff']:.2f} m/s（阵风修正阈值）", fontsize=11)

    # ④ 分级
    ax = axes[1][1]
    cmap = ListedColormap([GRADE_COLORS[g] for g in (0, 1, 2, 3)])
    im = ax.imshow(grade_m, extent=extent, origin="upper", cmap=cmap,
                   norm=Normalize(vmin=-0.5, vmax=3.5))
    fr = stats.get("grade_frac", {})
    handles = [Patch(color=GRADE_COLORS[g],
                     label=f"{grade_labels.get(g, GRADE_LABELS[g])} {fr.get(g, 0) * 100:.1f}%")
               for g in (0, 1, 2, 3)]
    ax.legend(handles=handles, loc="upper right", fontsize=9, framealpha=0.9)
    ax.set_title(f"④ {ctx.get('ground', '停放')}适宜性分级", fontsize=11)

    for ax in axes.ravel():
        ax.set_xlabel("x (m)", fontsize=9)
        ax.set_ylabel("y (m)", fontsize=9)
        ax.tick_params(labelsize=8)
    fig.tight_layout(rect=(0, 0, 1, 0.96))

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ── 导出（输出格式 = 上传格式） ───────────────────────────────────────────────

def scene_to_csv(scene: Dict[str, Any]) -> str:
    """把场景导出为标准 CSV（含风向风速列，可再次上传）。"""
    lines = ["x,y,Ux,Uy,speed,wind_direction,inlet_speed"]
    x = scene["x"]; y = scene["y"]
    ux = scene.get("ux", np.zeros_like(x)); uy = scene.get("uy", np.zeros_like(y))
    sp = scene["speed"]
    wd = scene.get("wind_direction", "N"); vi = scene.get("inlet_speed", 5.0)
    for i in range(len(x)):
        lines.append(f"{x[i]:.6f},{y[i]:.6f},{ux[i]:.6f},{uy[i]:.6f},{sp[i]:.6f},{wd},{vi}")
    return "\n".join(lines) + "\n"


def grid_to_points(x: np.ndarray, y: np.ndarray, ux: np.ndarray, uy: np.ndarray,
                   speed: np.ndarray, wd: str, vi: float) -> Dict[str, Any]:
    """规则网格 → 场景 dict（GNN 结果走此入口，与上传数据同构）。"""
    return {
        "x": x.ravel(), "y": y.ravel(), "ux": ux.ravel(), "uy": uy.ravel(),
        "speed": speed.ravel(),
        "bounds": [float(x.min()), float(y.min()), float(x.max()), float(y.max())],
        "regular": True, "grid_x": x, "grid_y": y,
        "wind_direction": wd, "inlet_speed": vi,
    }
