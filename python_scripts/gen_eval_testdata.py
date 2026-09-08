"""生成两个合成情景 CSV（供接口测试）：
 - eval_s1: 规则网格 100x100，乱序行，N 风 5.0 m/s，含 wind_direction/inlet_speed 列
 - eval_s2: 点云 20000 点，E 风 6.0 m/s
"""
import numpy as np
import os

os.makedirs("D:/Phase2_CFD_ML/tmp_evaltest", exist_ok=True)

# s1: 规则网格（风从北吹，北侧强风、南侧被建筑遮蔽 → 简化为 x 方向条纹）
nx, ny = 100, 100
gx = np.linspace(0, 200, nx); gy = np.linspace(200, 0, ny)
GX, GY = np.meshgrid(gx, gy)
speed1 = 5.0 - 1.5 * np.exp(-((GX - 100) ** 2 + (GY - 60) ** 2) / 1800.0)  # 一片低速区
speed1 = np.where(GX > 160, 6.5, speed1)  # 东侧强风区
xv, yv, sv = GX.ravel(), GY.ravel(), speed1.ravel()
perm = np.random.RandomState(42).permutation(len(xv))
with open("D:/Phase2_CFD_ML/tmp_evaltest/eval_s1.csv", "w", encoding="utf-8") as f:
    f.write("x,y,Ux,Uy,speed,wind_direction,inlet_speed\n")
    for i in perm:
        f.write(f"{xv[i]:.4f},{yv[i]:.4f},0.0,{sv[i]:.4f},{sv[i]:.4f},N,5.0\n")

# s2: 点云（低速区位置不同 + 局部强风）
rng = np.random.RandomState(1)
n = 20000
px = rng.uniform(0, 220, n); py = rng.uniform(0, 220, n)
sp2 = 4.0 + 2.5 * np.exp(-((px - 50) ** 2 + (py - 150) ** 2) / 2500.0)
with open("D:/Phase2_CFD_ML/tmp_evaltest/eval_s2.csv", "w", encoding="utf-8") as f:
    f.write("x,y,Ux,Uy,speed\n")
    for i in range(n):
        f.write(f"{px[i]:.4f},{py[i]:.4f},{sp2[i]:.4f},0.0,{sp2[i]:.4f}\n")
print("生成完成")
