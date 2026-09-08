"""evaluator 核心单元测试（合成数据，不依赖 E 盘/GNN）。"""
import sys, io, json
sys.path.insert(0, "D:/Phase2_CFD_ML")
import numpy as np
from frontend import evaluator as ev

ok = []

def check(name, cond, detail=""):
    ok.append(cond)
    print(("PASS" if cond else "FAIL"), "-", name, detail)

# ── 1. 规则网格场景（乱序 ravel）─────────────────────────────────────────
nx, ny = 120, 100
gx = np.linspace(0, 240, nx); gy = np.linspace(240, 0, ny)   # gy 递减
GX, GY = np.meshgrid(gx, gy)                                  # (ny, nx)，行对应 gy
speedA = 4.0 + 1.5 * np.cos(GX / 40.0) * np.sin(GY / 35.0)   # 平滑变化
# 故意打乱行序（模拟上传 CSV 任意顺序）
xv = GX.ravel(); yv = GY.ravel(); sv = speedA.ravel()
perm = np.random.RandomState(7).permutation(len(xv))
scA = {"x": xv[perm], "y": yv[perm], "ux": np.zeros(len(xv)), "uy": np.zeros(len(xv)),
       "speed": sv[perm], "bounds": [float(xv.min()), float(yv.min()), float(xv.max()), float(yv.max())],
       "wind_direction": "N", "inlet_speed": 5.0}
sx, sy, vals = ev.detect_grid(scA)
check("detect_grid 乱序还原", sx is not None and vals.shape == (ny, nx),
      f"shape={vals.shape if vals is not None else None}")
if vals is not None:
    check("detect_grid 数值一致", np.allclose(vals[::-1], speedA, atol=1e-9))  # vals 行=sy升序

# ── 2. 点云场景 ─────────────────────────────────────────────────────────
rng = np.random.RandomState(3)
npts = 30000
px = rng.uniform(0, 250, npts); py = rng.uniform(0, 250, npts)
spB = 2.0 + 2.5 * np.exp(-((px - 120) ** 2 + (py - 130) ** 2) / 3000.0)
scB = {"x": px, "y": py, "ux": np.zeros(npts), "uy": np.zeros(npts), "speed": spB,
       "bounds": [0.0, 0.0, 250.0, 250.0], "wind_direction": "E", "inlet_speed": 6.0}
check("detect_grid 点云→非规则", ev.detect_grid(scB)[0] is None)

# ── 3. 参考网格与重采样 ──────────────────────────────────────────────────
gxr, gyr = ev.build_reference_grid([scA, scB])
check("参考网格尺寸约束", len(gxr) <= ev.GRID_MAX and len(gyr) <= ev.GRID_MAX,
      f"nx={len(gxr)} ny={len(gyr)}")
sA = ev.resample_scene(scA, gxr, gyr)
sB = ev.resample_scene(scB, gxr, gyr)
check("重采样形状", sA.shape == (len(gyr), len(gxr)) and sB.shape == sA.shape)
check("重采样有数值", not np.all(np.isnan(sA)) and not np.all(np.isnan(sB)))
print("  重采样范围 A:", np.nanmin(sA).round(2), "-", np.nanmax(sA).round(2),
      "B:", np.nanmin(sB).round(2), "-", np.nanmax(sB).round(2))

# ── 4. 聚合 + 分级 ──────────────────────────────────────────────────────
res = ev.aggregate([{"speed_grid": sA, "wind_direction": "N", "inlet_speed": 5.0},
                    {"speed_grid": sB, "wind_direction": "E", "inlet_speed": 6.0}],
                   [1.0, 1.0])
check("mean 合理", 0 < np.nanmin(res["mean"]) < np.nanmax(res["mean"]) < 10)
check("频率范围", 0 <= np.nanmax(res["calm_freq"]) <= 1 and 0 <= np.nanmax(res["strong_freq"]) <= 1)
check("graded 仅 0-3", set(np.unique(res["grade"][~np.isnan(res["grade"])]).astype(int)) <= {0, 1, 2, 3})
check("分级比例和≈1", abs(sum(res["stats"]["grade_frac"].values()) - 1) < 0.02,
      str({k: round(v, 3) for k, v in res["stats"]["grade_frac"].items()}))

# ── 5. 报告图 ───────────────────────────────────────────────────────────
png = ev.render_report(res["mean"], res["calm_freq"], res["strong_freq"], res["grade"],
                       gxr, gyr, res["stats"],
                       [{"wind_direction": "N", "inlet_speed": 5.0},
                        {"wind_direction": "E", "inlet_speed": 6.0}])
check("报告图 base64 PNG", png.startswith("data:image/png;base64,") and len(png) > 20000,
      f"len={len(png)//1024}KB")

# ── 6. CSV 导出/上传往返 ─────────────────────────────────────────────────
csvA = ev.scene_to_csv(scA)
scA2 = ev.parse_scene_csv(csvA, "case_N_5.0.csv")
check("导出→解析往返 点数一致", len(scA2["x"]) == len(scA["x"]))
check("往返 风向风速自动识别", scA2["wind_direction"] == "N" and abs(scA2["inlet_speed"] - 5.0) < 1e-9)
check("往返 speed 一致", np.allclose(np.sort(scA2["speed"]), np.sort(scA["speed"]), atol=1e-6))
check("无表头4列解析", len(ev.parse_scene_csv("0,0,1.0,0.5,1.118\n" + "\n".join(
    f"{i},{i},{1.0},{0.5},{1.118}" for i in range(1, 30)), "a.csv")["x"]) == 30)

# ── 7. zip 上传 ─────────────────────────────────────────────────────────
buf = io.BytesIO()
import zipfile
with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("case_N_5.0.csv", csvA)
    zf.writestr("case_E_6.0.csv", ev.scene_to_csv(scB))
scenes_zip = ev.parse_upload(buf.getvalue(), "all.zip")
check("zip 多情景解析", len(scenes_zip) == 2 and scenes_zip[0]["wind_direction"] == "N"
      and scenes_zip[1]["wind_direction"] == "E", f"n={len(scenes_zip)}")

print("\n====", f"{sum(ok)}/{len(ok)} 通过", "====")
sys.exit(0 if all(ok) else 1)
