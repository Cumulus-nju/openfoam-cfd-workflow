"""
GNN 风场快速预测器 — 从建筑几何 + 风参数 → 秒级推理风场。

用法:
    predictor = GNNSurrogate("path/to/checkpoint.pt")
    Ux, Uy, speed = predictor.predict(buildings, wind_dir, speed)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch

# 复用训练代码的特征工程
_GNN_DIR = Path(r"E:\UrbanWind\gnn")
if str(_GNN_DIR) not in sys.path:
    sys.path.insert(0, str(_GNN_DIR))

from config import (  # noqa: E402
    NODE_FEATURES, NODE_LABELS, GRID_SIZE,
    WIND_DIR_VECTORS, DEFAULT_INLET_SPEED,
)
import dataset as _ds  # noqa: E402  (avoid from-import segfault on Python 3.13)


# ── 特征工程（从 dataset.py 移植，避免跨模块依赖内部实现） ──────────────

def compute_upwind_features(bld_mask: np.ndarray, bld_height: np.ndarray,
                            wind_dir: tuple[float, float],
                            cell_size: float, max_trace_m: float = 200.0
                            ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Scan-line 上游特征（含累积衰减高度）。"""
    H, W = bld_mask.shape
    CUM_DECAY = 50.0
    decay = np.exp(-cell_size / CUM_DECAY)

    wdx, wdy = wind_dir
    if wdx**2 + wdy**2 < 1e-6:
        z = np.zeros((H, W), dtype=np.float32)
        f = np.full((H, W), max_trace_m, dtype=np.float32)
        return z, f, z.copy(), z.copy(), z.copy()

    # 预计算建筑截面宽度
    bld_width = np.zeros((H, W), dtype=np.float32)
    if abs(wdx) >= abs(wdy):
        for c in range(W):
            rs = None
            for r in range(H):
                if bld_mask[r, c]:
                    if rs is None: rs = r
                else:
                    if rs is not None:
                        bld_width[rs:r, c] = r - rs; rs = None
            if rs is not None:
                bld_width[rs:H, c] = H - rs
    else:
        for r in range(H):
            rs = None
            for c in range(W):
                if bld_mask[r, c]:
                    if rs is None: rs = c
                else:
                    if rs is not None:
                        bld_width[r, rs:c] = c - rs; rs = None
            if rs is not None:
                bld_width[r, rs:W] = W - rs

    upwind_h = np.zeros((H, W), dtype=np.float32)
    upwind_dist = np.full((H, W), max_trace_m, dtype=np.float32)
    upwind_width = np.zeros((H, W), dtype=np.float32)
    upwind_cumh = np.zeros((H, W), dtype=np.float32)
    INF = max_trace_m

    if abs(wdx) >= abs(wdy):
        if wdx > 0:  # East wind
            for r in range(H):
                ld, lh, lw, cum = INF, 0.0, 0.0, 0.0
                for c in range(W - 1, -1, -1):
                    cum *= decay
                    if bld_mask[r, c]:
                        ld = 0.0; lh = bld_height[r, c]; lw = bld_width[r, c]
                        cum += bld_height[r, c]
                    else:
                        ld = min(INF, ld + cell_size)
                    upwind_h[r, c] = lh; upwind_dist[r, c] = ld
                    upwind_width[r, c] = lw; upwind_cumh[r, c] = cum
        else:  # West wind
            for r in range(H):
                ld, lh, lw, cum = INF, 0.0, 0.0, 0.0
                for c in range(W):
                    cum *= decay
                    if bld_mask[r, c]:
                        ld = 0.0; lh = bld_height[r, c]; lw = bld_width[r, c]
                        cum += bld_height[r, c]
                    else:
                        ld = min(INF, ld + cell_size)
                    upwind_h[r, c] = lh; upwind_dist[r, c] = ld
                    upwind_width[r, c] = lw; upwind_cumh[r, c] = cum
    else:
        if wdy > 0:  # North wind
            for c in range(W):
                ld, lh, lw, cum = INF, 0.0, 0.0, 0.0
                for r in range(H - 1, -1, -1):
                    cum *= decay
                    if bld_mask[r, c]:
                        ld = 0.0; lh = bld_height[r, c]; lw = bld_width[r, c]
                        cum += bld_height[r, c]
                    else:
                        ld = min(INF, ld + cell_size)
                    upwind_h[r, c] = lh; upwind_dist[r, c] = ld
                    upwind_width[r, c] = lw; upwind_cumh[r, c] = cum
        else:  # South wind
            for c in range(W):
                ld, lh, lw, cum = INF, 0.0, 0.0, 0.0
                for r in range(H):
                    cum *= decay
                    if bld_mask[r, c]:
                        ld = 0.0; lh = bld_height[r, c]; lw = bld_width[r, c]
                        cum += bld_height[r, c]
                    else:
                        ld = min(INF, ld + cell_size)
                    upwind_h[r, c] = lh; upwind_dist[r, c] = ld
                    upwind_width[r, c] = lw; upwind_cumh[r, c] = cum

    upwind_gap = np.zeros((H, W), dtype=np.float32)
    return upwind_h, upwind_dist, upwind_width, upwind_cumh, upwind_gap


# ── 风向上跳跃图（复用训练缓存） ──────────────────────────────────────────

def _get_wind_graph(size: int, wind_dir: tuple[float, float]) -> torch.Tensor:
    """构建风向上 strided graph，从 dataset.wind_grid_graph 导入。"""
    return _ds.wind_grid_graph(size, wind_dir)


# ── 主预测器 ────────────────────────────────────────────────────────────

class GNNSurrogate:
    """GNN 风场代理模型 — 秒级推理。"""

    def __init__(self, checkpoint_path: str | Path, device: str = "cuda"):
        self.device = device if torch.cuda.is_available() else "cpu"

        from model import WindGNN  # noqa: E402

        self.model = WindGNN(conv_type="sage").to(self.device)
        ckpt = torch.load(str(checkpoint_path), map_location=self.device,
                          weights_only=True)
        self.model.load_state_dict(ckpt["model_state_dict"])
        self.model.eval()
        print(f"GNN model loaded from {checkpoint_path}")

    def predict(self,
                buildings: list[dict],
                wind_direction: str | tuple[float, float],
                inlet_speed: float,
                grid_x: np.ndarray,
                grid_y: np.ndarray,
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Args:
            buildings: [{"polygon_local": [[x,y],...], "height": float}, ...]
            wind_direction: "N"/"E"/"S"/"W" 或 (dx, dy)
            inlet_speed: 入流速度 m/s
            grid_x, grid_y: 1D 网格坐标 (米)

        Returns:
            Ux, Uy, speed: 2D arrays [H, W]，单位 m/s
        """
        # 解析风向
        if isinstance(wind_direction, str):
            wd = WIND_DIR_VECTORS.get(wind_direction, (0.0, 0.0))
        else:
            wd = wind_direction
        wdx, wdy = wd

        H, W = len(grid_y), len(grid_x)
        cell_size = abs(grid_x[1] - grid_x[0])

        # 特征工程
        bld_h, dist, bld_mask = _ds.rasterize_buildings(buildings, grid_x, grid_y)
        up_h, up_d, up_w, up_cumh, up_gap = compute_upwind_features(
            bld_mask, bld_h, wd, cell_size)
        cross_d = _ds.compute_crosswind_dist(bld_mask, bld_h, wd, cell_size)
        density = _ds.compute_bld_density(bld_mask, cell_size)

        # 构建全图节点特征
        GX, GY = np.meshgrid(grid_x, grid_y)
        cx = (GX.min() + GX.max()) / 2
        sx = (GX.max() - GX.min()) / 2 or 1.0
        cy = (GY.min() + GY.max()) / 2
        sy = (GY.max() - GY.min()) / 2 or 1.0

        n = H * W
        x = torch.zeros((n, NODE_FEATURES), dtype=torch.float32)
        x[:, 0] = torch.tensor((GX.ravel() - cx) / sx, dtype=torch.float32)
        x[:, 1] = torch.tensor((GY.ravel() - cy) / sy, dtype=torch.float32)
        x[:, 2] = torch.tensor((bld_h.ravel() / max(bld_h.max(), 1.0)), dtype=torch.float32)
        x[:, 3] = torch.tensor((dist.ravel() / max(abs(dist).max(), 1.0)), dtype=torch.float32)
        x[:, 4] = torch.tensor(bld_mask.ravel(), dtype=torch.float32)
        x[:, 5] = wdx
        x[:, 6] = wdy
        x[:, 7] = inlet_speed / 10.0
        # wind fetch
        if wdx > 0.5:       fetch = GX.ravel().max() - GX.ravel()
        elif wdx < -0.5:    fetch = GX.ravel() - GX.ravel().min()
        elif wdy > 0.5:     fetch = GY.ravel().max() - GY.ravel()
        elif wdy < -0.5:    fetch = GY.ravel() - GY.ravel().min()
        else:               fetch = np.zeros_like(GX.ravel())
        x[:, 8] = torch.tensor(fetch / (fetch.max() or 1.0), dtype=torch.float32)
        x[:, 9] = torch.tensor((up_h.ravel() / max(up_h.max(), 1.0)), dtype=torch.float32)
        x[:, 10] = torch.tensor((up_d.ravel() / 200.0), dtype=torch.float32)
        x[:, 11] = torch.tensor((up_w.ravel() / max(up_w.max(), 1.0)), dtype=torch.float32)
        x[:, 12] = torch.tensor((up_cumh.ravel() / max(up_cumh.max(), 1.0)), dtype=torch.float32)
        x[:, 13] = torch.tensor((cross_d.ravel() / 200.0), dtype=torch.float32)
        x[:, 14] = torch.tensor(density.ravel(), dtype=torch.float32)
        x[:, 15] = torch.tensor((up_gap.ravel() / 200.0), dtype=torch.float32)

        # 构建图
        edge_index = _get_wind_graph(max(H, W), wd)
        if H != W:
            # 矩形网格需要重构图
            edge_index = _build_rect_graph(H, W, wd)

        # 推理
        with torch.no_grad():
            pred = self.model(x.to(self.device), edge_index.to(self.device))
            pred = pred.cpu().numpy()

        Ux_pred = pred[:, 0].reshape(H, W) * inlet_speed  # 反归一化
        Uy_pred = pred[:, 1].reshape(H, W) * inlet_speed
        speed = np.sqrt(Ux_pred**2 + Uy_pred**2)

        # 硬修正：仅对上游完全无建筑的格点 (upwind_h=0, |dist|>80m)
        far = dist > 80.0
        no_upwind = up_h < 0.01
        free = no_upwind & far & (bld_mask < 0.5)
        alpha = 0.4  # 40% U_inf blend for free-stream cells
        Ux_fs = wdx * inlet_speed
        Uy_fs = wdy * inlet_speed
        Ux_pred[free] = (1 - alpha) * Ux_pred[free] + alpha * Ux_fs
        Uy_pred[free] = (1 - alpha) * Uy_pred[free] + alpha * Uy_fs
        speed[free] = np.sqrt(Ux_pred[free]**2 + Uy_pred[free]**2)

        # Mask 建筑内部
        speed[bld_mask > 0] = np.nan
        Ux_pred[bld_mask > 0] = np.nan
        Uy_pred[bld_mask > 0] = np.nan

        return Ux_pred, Uy_pred, speed


def _build_rect_graph(H: int, W: int, wind_dir: tuple[float, float]) -> torch.Tensor:
    """为矩形网格构建 8 邻域 + 风向上跳跃边（简化版）。"""
    from config import WIND_STRIDES_ALONG, WIND_STRIDES_CROSS

    def _cardinal(wd):
        if abs(wd[0]) >= abs(wd[1]):
            return "E" if wd[0] > 0 else "W"
        return "N" if wd[1] > 0 else "S"

    cardinal = _cardinal(wind_dir)

    base = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    strided = []
    if cardinal == "N":
        for k in WIND_STRIDES_ALONG: strided.extend([(-k, 0), (k, 0)])
        for k in WIND_STRIDES_CROSS: strided.extend([(0, -k), (0, k)])
    elif cardinal == "S":
        for k in WIND_STRIDES_ALONG: strided.extend([(k, 0), (-k, 0)])
        for k in WIND_STRIDES_CROSS: strided.extend([(0, -k), (0, k)])
    elif cardinal == "E":
        for k in WIND_STRIDES_ALONG: strided.extend([(0, k), (0, -k)])
        for k in WIND_STRIDES_CROSS: strided.extend([(-k, 0), (k, 0)])
    else:
        for k in WIND_STRIDES_ALONG: strided.extend([(0, -k), (0, k)])
        for k in WIND_STRIDES_CROSS: strided.extend([(-k, 0), (k, 0)])

    all_offsets = base + strided
    src, dst = [], []
    for r in range(H):
        for c in range(W):
            s = r * W + c
            for dr, dc in all_offsets:
                nr, nc = r + dr, c + dc
                if 0 <= nr < H and 0 <= nc < W:
                    dst.append(nr * W + nc)
                    src.append(s)
    return torch.tensor([src, dst], dtype=torch.long)
