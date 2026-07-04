# OpenFOAM CFD on WSL — 完整指南

OpenFOAM v1912（Ubuntu 24.04 apt 安装）on WSL2。适用场景：OSM 选址 → case 生成 → snappyHexMesh → simpleFoam → 数据提取 → 可视化。

最后更新：2026-07-04（BC bug修复 + 后处理v2 + SIMPLE重启陷阱 + 本地/云端服务器同步）

## ⚠️ 已知 Bug: dict_generator.py 入流速度（2026-07-04 已修复）

**位置**: `frontend/of_generator/dict_generator.py:686`

**Bug**: `uniform ({ux} {uy} 0)` → `ux, uy` 是 `dir_map` 返回的方向单位向量，未乘 `u_in`，导致所有 Web 前端生成的案例入流恒为 **1 m/s**（k/ε 却按正确风速算，BC 内部不一致）。

**修复**: `{ux} {uy}` → `{ux * u_in} {uy * u_in}`（已 push GitHub）

**影响**: 6 个 Web 前端案例。172 个参数化案例（`gen_parametric_v2.py`）不受影响。

## ⚠️ SIMPLE 重启陷阱

**不要**从旧 BC 的解用 `startFrom latestTime` 重启 → 残差已低，求解器秒收敛但新 BC 未传播（false convergence）。

**正确做法**:
1. 修改 BC 后，`startFrom startTime; startTime 0;`
2. 删除旧时间目录（`rm -rf [1-9]*`）
3. 从零运行 simpleFoam

---

## ⚠️ WSL 命令铁律

**绝对不要** `wsl bash -c "..."` — 变量被吃、路径被翻译、中文乱码。

```bash
# ① 写脚本到 WSL 文件系统（绕过所有转义）
Write: \wsl.localhost\Ubuntu-24.04\root\script.sh

# ② 用 login shell 执行，禁用路径翻译
MSYS_NO_PATHCONV=1 wsl -d Ubuntu-24.04 bash -l /root/script.sh
```

**原因**：Git bash 会把 `/home/...` 翻译成 `C:/Program Files/Git/home/...`。`MSYS_NO_PATHCONV=1` 禁掉。`bash -l` 保证 HOME 等变量正常。

## WSL 内存配置

WSL2 默认内存上限很低（8GB）。编辑 `%USERPROFILE%\.wslconfig`：

```
[wsl2]
memory=12GB       # 不超过物理内存的 75%
swap=16GB
processors=8
```

修改后必须 `wsl --shutdown` 才能生效。

## OpenFOAM 环境

Ubuntu 24.04 通过 apt 安装的是 **OpenFOAM v1912**（不是 v2312！）。激活方式：

```bash
source /usr/share/openfoam/etc/bashrc
```

二进制在 `/usr/bin/`（blockMesh、simpleFoam、snappyHexMesh 等）。

## snappyHexMesh：v1912 vs v2312 关键差异

以下参数 dict_generator 已自动处理，但手动写 case 时注意：

| 项目 | v2312 | v1912（你的版本） |
|------|-------|-------------------|
| 几何引用 | `name xxx;` | `file "xxx.stl";` |
| 湍流模型 | `model kEpsilon;` | `RASModel kEpsilon;` |
| 边界类型 | `symmetryPlane` | `symmetry`（blockMesh + BC 都要统一） |
| 自由立面 | 无 | `allowFreeStandingZoneFaces true;`（**必须**） |
| 全局网格上限 | 可省略 | `maxGlobalCells` 必须≥背景网格数 |
| fvSchemes 格式 | 内联括号 | 每个子字典括号单独一行 |
| 函数对象 | 支持 probes/cuttingPlane | **禁用**（用 Python 后处理替代） |
| Feature ID | 任意 | 数字开头加 `s_` 前缀 |

## OSM 坐标投影

OSM 导入的建筑坐标是 WGS84 经纬度。**STL 生成和 CFD 域计算时必须投影到米**，否则建筑只有毫米级。

投影在 `geojson_to_stl.py` 和 `dict_generator._project_bbox()` 中自动完成：
- 参考点：域中心经纬度
- x = (lon - center_lon) × 111320 × cos(center_lat)
- y = (lat - center_lat) × 111320
- 地图显示保持 WGS84（Leaflet）

## Overpass API（中国大陆）

主端点 `z.overpass-api.de`，备选 `overpass-api.de`、`kumi.systems`。已在 `osm_adapter.py` 中实现自动切换。

## 网格精度参考

| 精度 | 背景网格量（1km² 域） | 需要内存 | 适用场景 |
|------|---------------------|---------|---------|
| 5m | ~3.5M | ~4GB | 快速调试 |
| 4m | ~7M | ~8GB | 批量训练数据 |
| 3.5m | ~10M | ~12GB | 日常分析 |
| 3m | ~16M | ~18GB | 精细分析 |
| 2m | ~50M+ | 50GB+ | 高保真（需大内存） |

- snappyHexMesh 在建筑表面自动 2-3 级加密，有效精度是背景的 1/4-1/8
- ML 训练：3-4m 背景 + snappy 加密足够。**数量 > 单案例精度**
- RANS 湍流模型误差（~20-30%）远大于网格误差，不必过度追求细网格

## 性能预估

以 10M 背景网格、250 栋建筑为例（16GB 内存、8 核）：

| 阶段 | 耗时 |
|------|------|
| blockMesh | 5-10 分钟 |
| snappyHexMesh | 30-90 分钟 |
| simpleFoam | 1-4 小时 |
| 后处理 | 2-5 分钟 |

**性能瓶颈**：WSL 通过 `/mnt/` 访问 Windows 盘是 9p 协议，I/O 极慢。优化方案：
- 小案例（<5M 网格）：直接跑在 `/mnt/e/`，方便管理
- 大案例（>10M 网格）：复制到 WSL 内部 ext4（`/root/cases/`）跑，I/O 快 50 倍，跑完复制回 E 盘

## 数据提取

### polyMesh 裸读（推荐，不需要 scipy）
Python 直读 `constant/polyMesh/points` + `faces` + `owner` + `neighbour`：
- faces：regex `(\d+)\(([^)]+)\)`，前面的数字是顶点数不是索引
- cell center = 属于该 cell 的所有 face center 的平均值
- 读取 U 时 **先检查 `nonuniform` 再检查 `uniform`** — `'uniform' in 'nonuniform'` 是 True
- 括号匹配用深度计数（`depth += 1` / `depth -= 1`），不要用 `find(')')`

### 后处理脚本
通用脚本 `postprocess.py`（在 `scripts/`）：
```bash
python scripts/postprocess.py <case_dir> [time]
```
每个案例独立输出到 `model_outputs/<case_name>/`：
- `wind_field_1.5m.npz` — 250×250 插值网格 (GX, GY, Ux, Uy, Uz, speed) ML 训练用
- `npy/*.npy` — 各分量独立 .npy 文件
- `cell_data_1.5m.csv` — z≈1.5m 原始 cell 中心数据
- `buildings.json` — 建筑足迹 + 属性（高度/层数/面积）
- `case_info.json` — 案例元数据 + 风速统计
- `<case>_combined.png` — 三联图（风速热力图 + V-速度流线图 + 速度分布直方图）

**坐标异常过滤**: 使用中位数参考点 + 质心距离过滤，自动排除 geojson 中跨城市异常建筑。

## 完整工作流

```
网页 http://127.0.0.1:8765
  → 导入 OSM（自动投影到米）
  → 设置网格精度滑块
  → 生成 CFD 案例 → E 盘
  ↓
WSL 自动运行：blockMesh → snappyHexMesh → simpleFoam
  ↓
postprocess.py 提取 + 可视化
  → E:\UrbanWind\model_outputs\<case_name>\
```

## CFD 自动运行脚本模板

以下脚本一键执行 blockMesh → snappyHexMesh → simpleFoam：

```bash
#!/bin/bash
# 写脚本到 WSL 内部执行（遵循 WSL 命令铁律）
cat > /root/run_cfd.sh << 'XEOF'
#!/bin/bash
source /usr/share/openfoam/etc/bashrc 2>/dev/null
CASE=/mnt/e/UrbanWind/cfd_cases/my_campus   # ← 改这里
LOG=/mnt/e/UrbanWind/pipeline.log           # ← 改这里
> $LOG
exec 2>&1
exec > >(tee -a $LOG)
echo "=== UrbanWind CFD ==="
echo "Started: $(date)"
cd $CASE
echo "=== [1/4] blockMesh ===" && blockMesh
echo "=== [2/4] snappyHexMesh -overwrite ===" && snappyHexMesh -overwrite
echo "=== [3/4] checkMesh ===" && checkMesh 2>&1 | tail -30
echo "=== [4/4] simpleFoam ===" && simpleFoam
echo "=== PIPELINE COMPLETE: $(date) ==="
XEOF
chmod +x /root/run_cfd.sh
bash -l /root/run_cfd.sh
```

执行：`MSYS_NO_PATHCONV=1 wsl -d Ubuntu-24.04 bash /mnt/e/UrbanWind/run_mycampus.sh`

求解完成后，后处理：
```bash
python scripts/postprocess.py <case_dir> [time]
# 输出 → model_outputs/<case_name>/<case_name>_combined.png
```

## 可视化原则

- 探头数据比网格插值数据准，柱状图用探头值
- V 速度（沿风向分量）用对称色标（`RdBu_r`），让回流区显示为红色
- 建筑用深色多边形覆盖，青色边框
- 网格必须覆盖数据 + 建筑范围
- geojson 坐标是 WGS84，要直接使用（CFD 域已用相同数值）

## 云端部署

云服务器 `120.26.31.146`（60GB / 32核，Alibaba Cloud），SSH 密钥 `Desktop/openfoam.pem`。

- 前端代码: `/opt/urbanwind/frontend/`，启动脚本 `scripts/cloud_start.sh`
- CFD 案例: `/data/<case_name>/`
- Web 前端: `http://120.26.31.146:8765`
- **注意**: 代码修改后必须在云端同步重启：杀 uvicorn 进程 + 清 `__pycache__` + 重拉代码或手动修

```bash
ssh -i Desktop/openfoam.pem root@120.26.31.146
```

## 本地 vs 云端代码同步

两套代码独立维护，修复 bug 时必须两边都改：
| | 本地 | 云端 |
|------|------|------|
| 路径 | `D:\Phase2_CFD_ML\frontend\` | `/opt/urbanwind/frontend/` |
| OpenFOAM | v1912 (Ubuntu 24.04) | v2312 |
| 案例存储 | `E:\UrbanWind\cfd_cases\` | `/data/` |
| Web 端口 | `127.0.0.1:8765` | `0.0.0.0:8765` |

---

| 症状 | 原因 | 修法 |
|------|------|------|
| 变量在 bash 里为空 | 用了 `bash -c` | 改用脚本 + `bash -l` |
| `allowFreeStandingZoneFaces` not found | OF1912 需要 | 加 `allowFreeStandingZoneFaces true;` |
| `RASModel` not found | 用了 `model` | 改为 `RASModel kEpsilon;` |
| `symmetryPlane` 报错 | OF1912 不同 | blockMesh + BC 统一用 `symmetry` |
| 所有 refinement surface unused | geometry key 和 refinement key 不匹配 | 两边都用不带 `.stl` 的名字 |
| U 场全是常量 | `'uniform' in 'nonuniform'` 命中了 | 先检查 nonuniform |
| STL 建筑只有毫米级 | 没有投影 | 检查 `center_lat`/`center_lon` 是否传入 |
| OOM / 进程被杀 | 内存不够 | 降低网格精度或缩小选区 |
| 图上没有建筑 | 坐标不匹配 | geojson 直接用原始坐标，不要二次投影 |
| 面索引越界 | 解析 faces 时错把顶点计数当索引 | regex 分开捕获顶点数和索引列表 |
| `sha1` 错误 | 函数对象不兼容 | 移除 controlDict 中的 functions 块 |
| blockMesh 卡住 | I/O 慢 | 大案例移到 WSL ext4 跑 |
| 入流风速恒为 1 m/s | `{ux} {uy}` 缺 `* u_in` | 检查 `dict_generator.py:686`，确认已修复 |
| SIMPLE 重启秒收敛但流场不变 | `latestTime` 重启 → false convergence | 删旧时间目录 + `startFrom startTime` 从零跑 |
| 代码修复后不生效 | Python 进程未重启或 `__pycache__` 残留 | 杀进程 + `find . -name __pycache__ -exec rm -rf {} +` + 重启 |
