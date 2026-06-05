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
cd D:\Phase2_CFD_ML
python -m frontend.main
```

浏览器自动打开 `http://127.0.0.1:8765`

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

> **注意**: 运行 CFD 需要 WSL2 + OpenFOAM v2312。
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
│   │   └── js/app.js
│   └── models/              # LLM 模型文件 (gitignored)
├── cfd_cases/               # 生成的案例 (gitignored)
├── requirements.txt
└── download_model.py
```

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
| CFD 求解器 | OpenFOAM v2312 (simpleFoam, k-ε RANS) |

## License

Academic research tool — NJU-Helsinki Institute
