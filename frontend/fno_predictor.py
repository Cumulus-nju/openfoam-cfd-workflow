"""
UrbanWind — FLUME-FNO 代理模型预测器

把 `.dsh/urbanwind_fno` 里训好的 Local-FNO 模型接进网站：
    几何 + 风向 → 秒级预测行人高度风场

模型（FLUME-FNO 风场分支复现，Qin et al. 2026）：
    Local-FNO + gMLP，hidden 60、4 层、模态 8×8×4，3.78 M 参数
    输入 39 通道：3 坐标 + 18 水平 MDDF + 14 垂直 MDDF + SDF + 覆盖 + 2 风向
    输出 3 通道：Ux / Uy / Uz（按来流归一化）

依赖外部预计算：每个算例需要一个 NPZ，含几何特征与真值，
由 `urbanwind_fno/build_dataset.py` + `precompute_features.py` 生成。

环境变量：
    URBANWIND_FNO_DIR   FNO 工程目录（默认 C:/Users/Administrator/.dsh/urbanwind_fno）
    URBANWIND_FNO_CKPT  模型权重（默认 <FNO_DIR>/../../urbanwind_fno_data/runs/res2m/best.pt）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np

# ── 路径解析 ────────────────────────────────────────────────────────────────

def _default_fno_dir() -> Path:
    return Path(os.environ.get("URBANWIND_FNO_DIR",
                               Path.home() / ".dsh" / "urbanwind_fno"))


def _default_ckpt(fno_dir: Path) -> Path:
    env = os.environ.get("URBANWIND_FNO_CKPT", "").strip()
    if env:
        return Path(env)
    return Path(r"D:\urbanwind_fno_data\runs\res2m\best.pt")


FNO_DIR = _default_fno_dir()
CKPT = _default_ckpt(FNO_DIR)
DATA_DIR = Path(os.environ.get("URBANWIND_FNO_DATA", r"D:\urbanwind_fno_data"))

_torch = None
_model = None
_args = None


def available() -> tuple[bool, str]:
    """返回 (是否可用, 说明)。"""
    if not CKPT.exists():
        return False, f"未找到 FNO 权重：{CKPT}"
    if not (FNO_DIR / "model.py").exists():
        return False, f"未找到 FNO 工程目录：{FNO_DIR}（需含 model.py）"
    return True, "ok"


def _load():
    """懒加载模型与依赖。"""
    global _torch, _model, _args
    if _model is not None:
        return _model
    import torch  # noqa: PLC0415
    _torch = torch

    # 让 `from model import LocalFNO` 可用
    if str(FNO_DIR) not in sys.path:
        sys.path.insert(0, str(FNO_DIR))
    from model import LocalFNO  # noqa: PLC0415

    ck = torch.load(str(CKPT), map_location="cpu", weights_only=False)
    a = ck["args"]
    V = ck["V"]
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    m = LocalFNO(in_channels=ck["C_in"], out_channels=3, hidden=a["hidden"],
                 n_layers=a["layers"], modes=(a["modes"], a["modes"], min(4, V)))
    m.load_state_dict(ck["model"])
    m.to(dev).eval()
    _model = m
    _args = {"hidden": a["hidden"], "layers": a["layers"], "modes": a["modes"],
             "patch": a["patch"], "V": V, "device": dev, "epoch": ck.get("ep")}
    return _model


def model_info() -> dict:
    ok, msg = available()
    if not ok or _args is None:
        return {"ready": ok, "message": msg, "checkpoint": str(CKPT)}
    return {"ready": True, "checkpoint": str(CKPT), **_args}


# ── 单算例推理 ──────────────────────────────────────────────────────────────

def _build_input(case: dict, patch_v: int, wind_align: bool = False) -> np.ndarray:
    """复刻 dataset.PatchDataset._make_input 的通道拼装（不依赖其类）。"""
    nx, ny, nz = case["shape"]
    V = min(patch_v, nz, case["mddf_h"].shape[2])

    def norm_full(a):
        a = np.asarray(a, dtype=np.float32)
        r = float(a.max() - a.min())
        return (a - a.mean()) / (r / 2) if r > 1e-9 else np.zeros_like(a)

    def squash(x):
        return np.sign(x) * np.log1p(np.abs(x)) * 0.1

    gx = norm_full(case["xs"])[:nx]
    gy = norm_full(case["ys"])[:ny]
    gz = norm_full(case["zs"])[:V]
    if wind_align:
        invx, invy = case["inlet"][0], case["inlet"][1]
        n = float(np.hypot(invx, invy))
        if n > 1e-9:
            cth, sth = invx / n, invy / n
            X2, Y2 = np.meshgrid(gx, gy, indexing="ij")
            gx = norm_full((cth * X2 + sth * Y2)[:, 0])
            gy = norm_full((-sth * X2 + cth * Y2)[0, :])

    GX = np.broadcast_to(gx[:, None, None], (nx, ny, V))
    GY = np.broadcast_to(gy[None, :, None], (nx, ny, V))
    GZ = np.broadcast_to(gz[None, None, :], (nx, ny, V))

    parts = [
        np.stack([GX, GY, GZ], 0),
        np.moveaxis(squash(case["mddf_h"][:, :, :V, :]), -1, 0),
        np.moveaxis(squash(case["mddf_v"][:, :, :V, :]), -1, 0),
        np.clip(case["sdf"][:, :, :V] / 100.0, -5, 5)[None],
        case["cover"][:, :, :V][None],
    ]
    rad = np.deg2rad(case.get("wind_deg", 0.0))
    parts.append(np.full((1, nx, ny, V), np.cos(rad), dtype=np.float32))
    parts.append(np.full((1, nx, ny, V), np.sin(rad), dtype=np.float32))
    X = np.concatenate(parts, 0).astype(np.float32)
    return np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)


def load_case_npz(case_name: str) -> Optional[dict]:
    """读取算例 NPZ（含几何特征与真值）。"""
    f = DATA_DIR / "cases" / f"{case_name}.npz"
    if not f.exists():
        return None
    z = np.load(f, allow_pickle=False)
    if "mddf_h" not in z.files:
        return None
    meta = json.loads(str(z["meta"]))
    return {
        "name": case_name, "meta": meta,
        "shape": z["speed"].shape,
        "solid": z["solid"], "cover": z["cover"], "sdf": z["sdf"],
        "mddf_h": z["mddf_h"], "mddf_v": z["mddf_v"],
        "Ux": z["Ux"], "Uy": z["Uy"], "Uz": z["Uz"], "speed": z["speed"],
        "xs": z["xs"], "ys": z["ys"], "zs": z["zs"],
        "inlet": meta.get("inlet", [0, 0, 0]),
        "inlet_speed": float(meta.get("inlet_speed", 1.0)) or 1.0,
        "wind_deg": float(meta.get("wind_deg", 0.0)),
    }


def predict_case(case_name: str, stride: int = 16) -> Optional[dict]:
    """对已有 NPZ 的算例做全域预测。

    返回 dict，含预测场、真值、网格与指标（行人高度 k=0）。
    """
    ok, msg = available()
    if not ok:
        raise RuntimeError(msg)
    case = load_case_npz(case_name)
    if case is None:
        return None

    model = _load()
    torch = _torch
    a = _args
    V_model = a["V"]
    P = a["patch"]
    dev = a["device"]

    nx, ny = case["shape"][0], case["shape"][1]
    X = _build_input(case, V_model)
    V = X.shape[-1]

    acc = np.zeros((3, nx, ny, V), dtype=np.float64)
    cnt = np.zeros((1, nx, ny, V), dtype=np.float64)

    xs = list(range(0, max(nx - P, 0) + 1, stride))
    ys = list(range(0, max(ny - P, 0) + 1, stride))
    if xs[-1] != nx - P:
        xs.append(max(nx - P, 0))
    if ys[-1] != ny - P:
        ys.append(max(ny - P, 0))

    batch, coords = [], []
    for x0 in xs:
        for y0 in ys:
            x1, y1 = min(x0 + P, nx), min(y0 + P, ny)
            x0c, y0c = max(0, x1 - P), max(0, y1 - P)
            batch.append(X[:, x0c:x1, y0c:y1, :V])
            coords.append((x0c, x1, y0c, y1))
            if len(batch) == 8:
                _run(model, torch, dev, batch, coords, acc, cnt)
                batch, coords = [], []
    if batch:
        _run(model, torch, dev, batch, coords, acc, cnt)

    pred = (acc / np.maximum(cnt, 1e-9)).astype(np.float32)
    ns = case["inlet_speed"]
    mask = np.nan_to_num(case["cover"][:, :, :V], nan=0.0) > 0.5

    gt = np.stack([case["Ux"][:, :, :V], case["Uy"][:, :, :V],
                   case["Uz"][:, :, :V]], 0) / ns
    gt = np.nan_to_num(gt, nan=0.0)

    # 行人高度（k=0）指标，单位 m/s
    k = 0
    m2 = mask[:, :, k]
    p = pred[:, :, :, k][:, m2] * ns
    g = gt[:, :, :, k][:, m2] * ns
    def _fac2(pp, gg):
        W = 0.05
        r = np.divide(pp, gg, out=np.full_like(pp, np.nan), where=np.abs(gg) > 1e-9)
        return float(np.nanmean(((r >= 0.5) & (r <= 2.0)) |
                                ((np.abs(pp) <= W) & (np.abs(gg) <= W))))
    sse = ((p - g) ** 2).sum()
    sst = ((g - g.mean()) ** 2).sum() + 1e-12
    metrics = {
        "mae": float(np.abs(p - g).mean()),
        "rmse": float(np.sqrt(((p - g) ** 2).mean())),
        "r2": float(1 - sse / sst),
        "fac2": _fac2(p, g),
        "height_m": float(case["zs"][k]),
    }

    def _spd(field):
        return (np.linalg.norm(field[:, :, :, k], axis=0) * ns).astype(np.float32)

    true_spd = np.where(m2, _spd(gt), np.nan).astype(np.float32)
    pred_spd = np.where(m2, _spd(pred), np.nan).astype(np.float32)

    # ⚠️ JSON 不支持 NaN（建筑格点是 NaN），转成 None 以便序列化；
    #    前端按 null 识别为建筑/域外，正是需要的语义。
    def _clean(a):
        return [[None if not np.isfinite(v) else float(v) for v in row]
                for row in a]

    return {
        "case": case_name,
        "shape": [nx, ny],
        "xs": [float(v) for v in case["xs"]],
        "ys": [float(v) for v in case["ys"]],
        "true_speed": _clean(true_spd),
        "pred_speed": _clean(pred_spd),
        "true_peaks": {"min": float(np.nanmin(true_spd)),
                       "max": float(np.nanmax(true_spd))},
        "pred_peaks": {"min": float(np.nanmin(pred_spd)),
                       "max": float(np.nanmax(pred_spd))},
        "metrics": metrics,
        "inlet_speed": ns, "wind_deg": case["wind_deg"],
        "model": model_info(),
    }


def _run(model, torch, dev, batch, coords, acc, cnt):
    with torch.no_grad():
        Xb = torch.from_numpy(np.stack(batch)).to(dev)
        Pb = model(Xb).cpu().numpy()
    for p, (x0, x1, y0, y1) in zip(Pb, coords):
        acc[:, x0:x1, y0:y1, :] += p
        cnt[:, x0:x1, y0:y1, :] += 1.0
