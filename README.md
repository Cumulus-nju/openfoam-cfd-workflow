# UrbanWind CFD — 城市微风场智能建模前端

基于 OpenFOAM + LLM 的城市风场 CFD 案例自动生成工具。
输入建筑布局（OSM地图 / DXF图纸 / 文字描述），AI 自动补全建筑物属性，
一键生成完整 OpenFOAM CFD 案例。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 下载 AI 模型

```powershell
# PowerShell — 从 HuggingFace 镜像下载 (约 469MB)
mkdir -p frontend\models
Invoke-WebRequest -Uri "https://hf-mirror.com/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf" -OutFile "frontend\models\qwen2.5-0.5b-instruct-q4_k_m.gguf"
```

或者运行下载脚本：
```bash
python download_model.py
```

### 3. 启动服务

```bash
python -m frontend.main
```

浏览器自动打开 `http://127.0.0.1:8765`

> **无需修改任何代码**：项目路径按 `config.py` 自身位置自动推导，
> clone 到任意目录 / 盘符 / 机器都能直接跑。

### 4. 配置（可选，全部走环境变量）

不设任何环境变量也能运行（案例与输出默认落在**项目根**下的 `cfd_cases/`、`model_outputs/`）。
需要把数据放到别处、或启用可选功能时，再设对应变量：

| 环境变量 | 作用 | 默认值 |
|---|---|---|
| `URBANWIND_CASES_DIR` | CFD 案例目录 | `<项目根>/cfd_cases` |
| `URBANWIND_OUTPUT_DIR` | 后处理输出目录 | `<项目根>/model_outputs` |
| `URBANWIND_GNN_DIR` | GNN 训练代码目录（含 `model.py`/`dataset.py`/`config.py`） | `<项目根>/gnn` |
| `URBANWIND_GNN_CKPT` | GNN 权重 `.pt` 文件 | `<GNN_DIR>/checkpoints/stage1_best.pt` |
| `GAODE_API_KEY` | 高德 Web 服务 Key（不设则高德数据源不可用） | 空 |
| `URBANWIND_ADMIN_USER` / `URBANWIND_ADMIN_PASSWORD` | 首次启动创建的管理员 | `admin` / `urbanwind2026` |
| `UWB_FOLDER_PICKER` | 设为 `0` 禁用服务端文件夹选择框（无桌面环境部署） | `1` |

PowerShell 示例：

```powershell
$env:URBANWIND_CASES_DIR = "E:\UrbanWind\cfd_cases"
$env:URBANWIND_GNN_DIR   = "E:\UrbanWind\gnn"
$env:GAODE_API_KEY       = "你的高德Key"
python -m frontend.main
```

> **可选依赖说明**：GNN 风场预测 / 单车选址 / 综合评估（GNN 情景）三项功能
> 需要 `URBANWIND_GNN_DIR` 与权重文件。缺失时这些接口返回 503 并在
> `/api/config` 里给出提示，其余功能（多源导入、LLM 优化、案例生成、
> 本地上传评估）不受影响。

### 5. 登录

- 首次启动自动创建默认管理员：**admin / urbanwind2026**
- 登录页支持**注册新账号**（注册后自动登录）
- 会话有效期 7 天（HttpOnly Cookie），右上角可退出登录
- 全部 `/api/*` 接口与页面均需登录；`/api/health` 与登录/注册接口除外
- 账号数据存于 `frontend/users.json`（已 gitignore，密码为 PBKDF2 哈希）
- 修改密码：右上角菜单 →「修改密码」；管理员可在 `/api/auth/users` 查看注册用户

### 6. 综合评估（📍 场景栏）

多情景风环境分析 → 共享单车停放适宜性分级：

- **数据源**：① 本地上传模拟数据（CSV / zip，与系统导出格式互认：`x,y,Ux,Uy,speed[,wind_direction,inlet_speed]`，OpenFOAM 切片脚本输出可直接上传；文件名 `case_N_5.0.csv` 自动识别风向风速）；② GNN 在线批量预测（选案例 + 风速 → 自动跑 N/S/E/W 四风向）
- **分析**：各情景按其权重**加权平均风速**、**静风频率**（v<1.5 m/s）、**强风频率**（v>阵风修正阈值 7.84 m/s）→ 停放适宜性分级（🔴高风险 / 🟠中风险 / 🟢适宜 / 🔵静风区）
- **产出**：地图图层切换（分级/平均/静风频率/强风频率）、分级占比统计、每情景风速表、最适宜/最高风险 Top 区域、四面板评估报告图（可下载 PNG）
- **导出**：任意情景可导出标准 CSV（再上传即恢复，保证"系统导出格式=上传格式"）
- API：`POST /api/eval/upload`、`POST /api/eval/gnn`、`POST /api/eval/run`、`GET /api/eval/export/{id}`、`POST /api/eval/clear`

---

## 工作流程

```
┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
│  多源输入     │ ──▶ │  LLM 智能优化 │ ──▶ │  OpenFOAM 案例生成 │
│              │     │              │     │                  │
│ • OSM 地图    │     │ • 建筑类型推断 │     │ • STL 几何文件     │
│ • DXF 图纸    │     │ • 高度/层数补全│     │ • blockMeshDict   │
│ • 文字描述    │     │ • 单车点自动布 │     │ • snappyHexMesh   │
│              │     │ • 中文语音编辑 │     │ • 边界条件/求解器   │
└──────────────┘     └──────────────┘     └──────────────────┘
```

### Web UI 功能

- **左侧栏** — 建筑列表 + 属性编辑器
- **中间地图** — Leaflet 暗色地图，显示建筑足迹
- **右侧 AI 面板** — 自然语言编辑（支持中文）
- **导入弹窗** — OSM 地名/bbox、DXF 上传、文字描述
- **生成弹窗** — 设置风速、风向、单车数量

---

## 系统要求

| 项目 | 最低配置 |
|------|----------|
| 操作系统 | Windows 10/11 |
| Python | 3.10+ |
| 内存 | 8GB RAM |
| 磁盘 | 2GB 空闲空间 |
| GPU | 可选（LLM 可纯 CPU 推理） |

> **注意**: 运行 CFD 需要 WSL2 + OpenFOAM v1912。
> 前端本身只需 Python，生成案例后在 WSL 中执行 `blockMesh → snappyHexMesh → simpleFoam`。

---

## 项目结构

```
Phase2_CFD_ML/
├── frontend/
│   ├── app.py              # FastAPI 服务入口
│   ├── config.py            # 全局配置
│   ├── schema.py            # 统一 GeoJSON Schema
│   ├── main.py              # 启动入口
│   ├── input_adapters/      # 多源输入适配器
│   │   ├── osm_adapter.py   #   OpenStreetMap
│   │   ├── dxf_adapter.py   #   DXF 图纸
│   │   └── manual_adapter.py #  文字/结构化输入
│   ├── llm_engine/          # LLM 推理引擎
│   │   ├── engine.py        #   llama.cpp 封装
│   │   ├── prompts.py       #   提示词模板
│   │   ├── geometry_infer.py #  几何属性推理
│   │   └── interactive_edit.py # 交互式编辑
│   ├── of_generator/        # OpenFOAM 案例生成器
│   │   ├── geojson_to_stl.py #  GeoJSON → STL
│   │   ├── bike_placer.py   #  单车点智能布局
│   │   ├── dict_generator.py #  OpenFOAM 字典
│   │   └── case_assembler.py #  案例组装
│   ├── static/              # Web 前端
│   │   ├── index.html
│   │   ├── css/app.css
│   │   └── js/{app,siting,eval}.js
│   └── models/              # LLM 模型文件 (gitignored)
├── cfd_cases/               # 生成的案例（默认位置，可改 URBANWIND_CASES_DIR）
├── gnn/                     # GNN 训练代码 + 权重（可选，可改 URBANWIND_GNN_DIR）
├── requirements.txt
└── download_model.py
```

> 目录结构可按需调整：代码里没有任何绝对路径，
> `cfd_cases/`、`model_outputs/`、`gnn/` 都可用环境变量指到别处（含数据盘）。

---

## 团队协作

1. `git clone` 本项目
2. `pip install -r requirements.txt`
3. 下载模型文件
4. `python -m frontend.main`
5. 在 Web UI 中操作，导出案例到 `cfd_cases/`
6. 在 WSL 中运行 OpenFOAM 求解

---

## 技术栈

| 层 | 技术 |
|----|------|
| 后端框架 | FastAPI + uvicorn |
| AI 引擎 | llama.cpp + Qwen2.5-0.5B GGUF |
| 前端 | Vanilla HTML/CSS/JS + Leaflet.js |
| 设计 | Dark Glassmorphism, Noto Sans SC |
| CFD 求解器 | OpenFOAM v1912 (simpleFoam, k-ε RANS) |

## License

Academic research tool — NJU-Helsinki Institute
