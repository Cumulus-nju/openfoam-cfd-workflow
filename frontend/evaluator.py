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

V_CRIT = 11.7        # 单车倾覆临界风速 m/s（bike_wind_overturning_model.tex）
GUST_FACTOR = 0.67   # 阵风修正 (7.8/11.7)
HIGH_FACTOR = 0.8    # 高风险阈值系数（相对阵风修正阈值）
MEDIUM_FACTOR = 0.5  # 中风险阈值系数
CALM_SPEED = 1.5     # 静风判据 m/s
STRONG_FREQ_HIGH = 0.30   # 高风险强风频率阈值
STRONG_FREQ_MED = 0.10    # 中风险强风频率阈值
GRID_MAX = 300       # 参考网格最大边长（超出则自动放大网格尺寸）
POINT_LIMIT = 2_000_000   # 单场景点数量上限

GRADE_COLORS = {0: "#38bdf8", 1: "#10b981", 2: "#f59e0b", 3: "#ef4444"}
GRADE_LABELS = {0: "静风区", 1: "适宜", 2: "中风险", 3: "高风险"}

WD_VEC = {"N": (0.0, 1.0), "S": (0.0, -1.0), "E": (1.0, 0.0), "W": (-1.0, 0.0)}


# ── CSV 解析 ──────────────────────────────────────────────────────────────────

def _guess_columns(header: List[str]) -> Dict[str, int]:
    """把表头映射到标准列名；支持中英文及常见别名。"""
    aliases = {
        "x": ["x", "px", "lon", "lng", "经度"],
        "y": ["y", "py", "lat", "纬度"],
        "ux": ["ux", "u_x", "u", "vx", "u1"],
        "uy": ["uy", "u_y", "v", "vy", "u2"],
        "uz": ["uz", "u_z", "w", "vz", "u3"],
        "speed": ["speed", "spd", "vel", "magnitude", "velocity", "风速", "wind_speed"],
        "wd": ["wind_direction", "wind_dir", "direction", "dir", "风向"],
        "speed_inlet": ["inlet_speed", "wind_speed_inlet", "ref_speed", "入口风速", "风速_inlet"],
    }
    mapping: Dict[str, int] = {}
    lower = [str(h).strip().lower() for h in header]
    for std, names in aliases.items():
        for i, h in enumerate(lower):
            if h in names:
                mapping[std] = i
                break
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
                    inlet_speed: Optional[float] = None) -> Dict[str, Any]:
    """解析 CSV 文本 → 标准场景数据（点云 + 规则网格检测）。"""
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
    auto_wd, auto_vi = wind_dir, inlet_speed
    speed_pos, ux_pos, uy_pos = mapping.get("speed"), mapping.get("ux"), mapping.get("uy")
    wd_pos, vi_pos = mapping.get("wd"), mapping.get("speed_inlet")

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
    tokens = [t for t in re.split(r"[_\-\s\.]+", stem) if t]
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
    # 合理范围校验（0.5–60 m/s，方向仅限四方位）
    if auto_vi is not None and not (0.5 <= auto_vi <= 60):
        auto_vi = None
    if auto_wd not in ("N", "S", "E", "W", ""):
        auto_wd = ""

    return {
        "x": x, "y": y, "ux": ux, "uy": uy, "speed": speed,
        "bounds": [float(x.min()), float(y.min()), float(x.max()), float(y.max())],
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
            results.append(parse_scene_csv(text, Path(info.filename).name))
    else:
        text = files.decode("utf-8", errors="replace")
        results.append(parse_scene_csv(text, filename))
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


# ── 报告图 ────────────────────────────────────────────────────────────────────

def _set_cn_font():
    import matplotlib
    matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["font.sans-serif"] = ["Noto Sans SC", "Microsoft YaHei", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False


def render_report(mean, calm_freq, strong_freq, grade, grid_x, grid_y,
                  stats: Dict[str, Any], scenes_meta: List[Dict[str, Any]],
                  title: str = "") -> str:
    """四面板报告图 → PNG base64。"""
    _set_cn_font()
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap, Normalize
    from matplotlib.patches import Patch

    H, W = grade.shape
    extent = [grid_x[0], grid_x[-1], grid_y[-1], grid_y[0]]
    import numpy.ma as ma
    mean_m = ma.masked_invalid(mean)
    calm_m = ma.masked_invalid(calm_freq)
    strong_m = ma.masked_invalid(strong_freq)
    grade_m = ma.masked_invalid(grade)

    fig, axes = plt.subplots(2, 2, figsize=(13, 10), dpi=105)
    fig.suptitle(title or "UrbanWind 综合评估 · 单车停放适宜性（多情景加权）",
                 fontsize=14, fontweight="bold")

    # ① 加权平均风速
    ax = axes[0][0]
    im = ax.imshow(mean_m, extent=extent, origin="upper", cmap="turbo",
                   vmin=max(stats["mean_min"], 0), vmax=stats["mean_max"] or 1)
    ax.set_title(f"① 加权平均风速 (m/s) · {stats['n_scenes']} 情景", fontsize=11)
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
    handles = [Patch(color=GRADE_COLORS[g], label=f"{GRADE_LABELS[g]} {fr.get(g, 0) * 100:.1f}%")
               for g in (0, 1, 2, 3)]
    ax.legend(handles=handles, loc="upper right", fontsize=9, framealpha=0.9)
    ax.set_title("④ 停放适宜性分级", fontsize=11)

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
