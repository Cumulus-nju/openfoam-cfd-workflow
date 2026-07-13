"""
参数化修正模块 — 在 GNN 风场上叠加树列公式修正。

行道树公式 (来自 tree-study-complete, R²=0.97):
  U(x,y; L,θ,U∞) = U∞ − D(x,y; L,θ)
  D = cos²θ · D⊥ + sin²θ · D∥

坐标系: O = 树列迎风侧边缘中点
  x = 顺风向(下游), y = 展向(沿树列)
"""

from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np


# ── 树列数据结构 ────────────────────────────────────────────────────────

@dataclass
class TreeRow:
    """单排行道树参数。"""
    cx: float          # 中心 x (地图坐标, m)
    cy: float          # 中心 y
    length: float      # 树列长度 L (m)
    angle_deg: float   # 树列朝向 (°), 0=N-S排列, 90=E-W排列
    radius: float = 1.0       # 树冠半径 (m)
    ground_clearance: float = 2.5  # 离地间隙 (m)

    def orientation_vector(self) -> tuple[float, float]:
        """树列方向单位向量。"""
        rad = np.deg2rad(self.angle_deg)
        return (np.sin(rad), np.cos(rad))  # (沿树列x, 沿树列y)


# ── 修正计算 ────────────────────────────────────────────────────────────

def _compute_tree_deficit(
    grid_x: np.ndarray,
    grid_y: np.ndarray,
    tree: TreeRow,
    wind_dir: tuple[float, float],
    inlet_speed: float,
) -> np.ndarray:
    """
    计算单排树列的减速场 D(x,y)。

    Returns: 2D array [H, W], 风速衰减量 (m/s), 非负
    """
    GX, GY = np.meshgrid(grid_x, grid_y)

    wdx, wdy = wind_dir
    norm = (wdx**2 + wdy**2)**0.5
    if norm > 0:
        wdx, wdy = wdx / norm, wdy / norm

    # 树列单位向量
    tree_rad = np.deg2rad(tree.angle_deg)
    tx = np.sin(tree_rad)   # 沿树列 x 分量
    ty = np.cos(tree_rad)   # 沿树列 y 分量

    # 平移: 相对树列中心
    dx = GX - tree.cx
    dy = GY - tree.cy

    # 旋转坐标系: 顺风向 = x', 展向 = y'
    # x' 沿风向, y' 沿展向(垂直风向)
    x_prime = dx * wdx + dy * wdy          # 投影到风向
    y_prime = -dx * wdy + dy * wdx         # 投影到垂直风向

    # 树列中点在 x' 方向的位置
    tree_center_xp = 0.0   # 树列中心在旋转坐标系中
    tree_center_yp = 0.0

    # 树列在 x',y' 系中的范围
    L = tree.length
    # 树列方向向量在旋转坐标系中
    tx_p = tx * wdx + ty * wdy    # 树列沿风向分量
    ty_p = -tx * wdy + ty * wdx   # 树列展向分量

    # 沿树列方向到树列中心的有符号距离
    along_tree = x_prime * tx_p + y_prime * ty_p
    along_tree_norm = along_tree / (tx_p**2 + ty_p**2)**0.5 if (tx_p**2 + ty_p**2) > 0 else along_tree

    # 到树列中垂线的最短距离 (展向距离)
    cross_tree = -x_prime * ty_p + y_prime * tx_p
    cross_tree_norm = cross_tree / (tx_p**2 + ty_p**2)**0.5 if (tx_p**2 + ty_p**2) > 0 else cross_tree

    # 风向与树列法向的夹角 θ
    # 树列法向 = 垂直树列方向 = (-ty, tx)
    normal_x, normal_y = -ty, tx
    cos_theta = abs(normal_x * wdx + normal_y * wdy)
    sin_theta = np.sqrt(max(0, 1 - cos_theta**2))

    # 顺风向位置 (沿风向从迎风边缘算起)
    # 需要找到树列在风向投影的起点
    x_downstream = x_prime

    # ── 垂直风分量 D⊥ ──
    sigma_y = max(0.439 * L - 1.6, 0.5)   # 尾流宽度
    U_ref = inlet_speed / 5.0               # 参考速度归一化
    D_perp = (4.36 * U_ref
              * np.exp(-(x_downstream - 3)**2 / max(12, 0.1))
              * np.exp(-cross_tree_norm**2 / (2 * sigma_y**2)))
    D_perp = np.where(x_downstream > 2, D_perp, 0.0)   # 只在圆柱后方

    # ── 平行风分量 D∥ ──
    D_par = (1.72 * U_ref
             * np.exp(-cross_tree_norm**2 / 2.9))
    # x 在树列范围内
    in_range = (along_tree_norm >= -L/2) & (along_tree_norm <= L/2)
    D_par_inside = D_par * in_range
    # x 在树列后方
    D_par_wake = (1.72 * U_ref
                  * np.exp(-cross_tree_norm**2 / 2.9)
                  * np.exp(-(along_tree_norm - L/2)**2 / 18))
    D_par_wake = np.where(along_tree_norm > L/2, D_par_wake, 0.0)

    D_par_total = D_par_inside + D_par_wake

    # ── 合成 ──
    D = cos_theta**2 * D_perp + sin_theta**2 * D_par_total

    return np.clip(D, 0, inlet_speed)


def apply_tree_correction(
    Ux_base: np.ndarray,
    Uy_base: np.ndarray,
    speed_base: np.ndarray,
    grid_x: np.ndarray,
    grid_y: np.ndarray,
    trees: list[TreeRow],
    wind_dir: tuple[float, float],
    inlet_speed: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    在 GNN 底图上叠加树列修正。

    Args:
        Ux_base, Uy_base, speed_base: GNN 预测的风场 (m/s)
        grid_x, grid_y: 1D 网格坐标
        trees: 树列列表
        wind_dir: (dx, dy)
        inlet_speed: 来流速度 m/s

    Returns:
        Ux_corrected, Uy_corrected, speed_corrected
    """
    H, W = len(grid_y), len(grid_x)
    total_deficit = np.zeros((H, W), dtype=np.float32)

    for tree in trees:
        D = _compute_tree_deficit(grid_x, grid_y, tree, wind_dir, inlet_speed)
        total_deficit += D

    # 按比例缩放 Ux, Uy
    speed_corrected = speed_base - total_deficit
    speed_corrected = np.clip(speed_corrected, 0.1, None)  # 风速不为负

    # 保持风向不变，缩放速度
    scale = np.where(speed_base > 0.1,
                     speed_corrected / np.maximum(speed_base, 0.1), 1.0)
    Ux_corrected = Ux_base * scale
    Uy_corrected = Uy_base * scale

    # NaN 处理
    nan_mask = np.isnan(speed_base)
    speed_corrected[nan_mask] = np.nan
    Ux_corrected[nan_mask] = np.nan
    Uy_corrected[nan_mask] = np.nan

    return Ux_corrected, Uy_corrected, speed_corrected
