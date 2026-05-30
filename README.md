# OpenFOAM CFD Workflow

Urban wind field simulation pipeline: case generation → snappyHexMesh → simpleFoam → data extraction → visualization.

## 环境要求

- WSL2 Ubuntu 22.04 + OpenFOAM v2312 (ESI, 源码编译)
- Windows Python 3 + numpy, scipy, matplotlib
- Claude Code (使用 skill)

## 快速开始

1. 在 Claude Code 中加载 skill: 将本 repo 的 `.claude/skills/` 放到你项目的 `.claude/skills/` 目录下

2. Claude Code 对话中输入 `/openfoam-cfd`, agent 将按照 skill 中的避坑指南执行任务

## 脚本说明

| 脚本 | 用途 | 运行环境 |
|------|------|---------|
| `scripts/generate_case.py` | 生成 STL + 全部 OpenFOAM 配置文件 | Windows |
| `scripts/extract_slice.py` | 从 polyMesh + U 场提取 z=1.5m 切片数据 | WSL |
| `scripts/plot_results.py` | 三联图: 风速热力图 + 流线图 + 柱状图 | Windows |
| `scripts/visualize_bikes.py` | 单车点风速柱状图 + 布局俯视图 | Windows |

## 典型工作流

```
generate_case.py → blockMesh → snappyHexMesh → simpleFoam
                                     ↓
                            extract_slice.py (WSL)
                                     ↓
                            plot_results.py (Windows)
```

## Skill 内容

`.claude/skills/openfoam-cfd.md` 包含:
- WSL 命令执行铁律 (不用 bash -c)
- snappyHexMesh v2312 特有坑
- BC 文件注意事项
- 数据提取: probes / polyMesh 裸读 / foamToVTK
- 可视化原则 (对称色标 / 探头优于插值)
- Debug 速查表
