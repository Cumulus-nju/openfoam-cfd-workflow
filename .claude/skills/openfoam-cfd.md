# OpenFOAM CFD on WSL — 避坑指南

OpenFOAM v2312 (ESI) on WSL2 Ubuntu 22.04. 适用场景: case 生成 → snappyHexMesh → simpleFoam → 数据提取 → 可视化.

## ⚠️ WSL 命令铁律

**绝对不要** `wsl bash -c "..."` — 变量被吃、路径被翻译、中文乱码.

```bash
# ① 写脚本到 WSL 文件系统 (绕过所有转义)
Write: \\wsl.localhost\Ubuntu-22.04\home\<user>\script.sh

# ② 用 login shell 执行,禁用路径翻译
MSYS_NO_PATHCONV=1 wsl -d Ubuntu-22.04 bash -l /home/<user>/script.sh
```

**原因**: Git bash 会把 `/home/...` 翻译成 `C:/Program Files/Git/home/...`. `MSYS_NO_PATHCONV=1` 禁掉. `bash -l` 保证 HOME 等变量正常. `\\wsl.localhost\` 直写 ext4 文件系统,避开所有 shell 转义层.

## OpenFOAM 环境激活

bashrc 的 `BASH_SOURCE` 检测在非交互 shell 里会失效. 写脚本时先手动 export 再 source:

```bash
#!/bin/bash
export WM_PROJECT_DIR=/home/<user>/OpenFOAM/OpenFOAM-v2312
. "$WM_PROJECT_DIR/etc/bashrc"
# 之后 blockMesh / simpleFoam 等都在 PATH 里了
```

## snappyHexMesh v2312 特有坑

以下参数 v1912 没有或可选,但 v2312 **必须显式写**:

| 必须项 | 位置 | 值 |
|--------|------|-----|
| `mergeTolerance` | snappyHexMeshDict 顶层 | `1e-6;` |
| `refinementRegions { }` | castellatedMeshControls 内 | 空字典即可 |
| `allowFreeStandingZoneFaces` | castellatedMeshControls 内 | `true;` |

geometry 和 refinementSurfaces 的 key **必须一致**,且 key **不带 `.stl` 后缀**:

```
geometry {
    building1 { type triSurfaceMesh; file "building1.stl"; }
}
refinementSurfaces {
    building1 { level (2 3); }   // key 与上面一致,无 .stl
}
```

如果看到 `"Not all entries in refinementSurfaces dictionary were used"`,就是 key 不匹配.

## BC 文件注意事项

- patch 名与 geometry key 一致 (无 `.stl`)
- `symmetry` 不是 `symmetryPlane` (v2312 改名)
- 用简单 `fixedValue` inlet,不用 `atmBoundaryLayer*`
- fvSolution 去掉 `consistent yes` (v2312 不支持)
- 空 face 的 patch (比如太小的 STL 没被 mesh 切出来) v2312 可以容忍,不删也行

## simpleFoam 监控

- 写入间隔 `writeInterval 100`,第一个 checkpoint 约 10-15 分钟到达
- `residualControl` 设 1e-4,通常在 500-800 步收敛,不一定跑满 endTime
- 内存: 2-5M cell 的 mesh 约需 2-4 GB

## 数据提取

### probes (最可靠)
controlDict 里配 probes,输出到 `postProcessing/<name>/0/U`. 解析: 取最后一行,用 `re.findall(r'\(([^)]+)\)', line)` 提取各点 U 向量.

### polyMesh 裸读 (不需要 scipy)
Python 直读 `constant/polyMesh/points` + `faces` + `owner` + `neighbour`:
- points: 找到计数行 `(\d+)\s*\(`,解析后续 `(x y z)` 元组
- faces: regex `(\d+)\(([^)]+)\)`,前面的数字是顶点数不是索引
- cell center = 属于该 cell 的所有 face center 的平均值
- 读取 U 时 **先检查 `nonuniform` 再检查 `uniform`** — 因为字符串 `'uniform' in 'nonuniform'` 是 True
- 括号匹配用深度计数 (`depth += 1` / `depth -= 1`),不要用 `find(')')`

### foamToVTK 备选
`foamToVTK -time <t> -fields '(U)'` 生成 VTK. 但 meshio 无法处理 polyhedra 格点,所以优先用裸读方案.

## 可视化原则

- 探头数据 (probes) 比网格插值数据准,柱状图用探头值
- V 速度 (沿风向分量) 用 **对称色标** (`RdBu_r`, vmin/vmax ±max_abs),让回流区显示为红色
- 风速热力图用 `YlOrRd`,建筑用黑色矩形覆盖

## Debug 速查

| 症状 | 原因 | 修法 |
|------|------|------|
| 变量在 bash 里为空 |用了 `bash -c` | 改用脚本 + `bash -l` |
| `mergeTolerance` not found | v2312 必需 | 加 `mergeTolerance 1e-6;` |
| `refinementRegions` not found | v2312 必需 | 加 `refinementRegions { }` |
| 所有 refinement surface unused | geometry key 和 refinement key 不匹配 | 两边都用不带 `.stl` 的名字 |
| U 场全是常量 | `'uniform' in 'nonuniform'` 命中了 | 先检查 nonuniform |
| 面索引越界 | 解析 faces 时错把顶点计数当索引 | regex 分开捕获顶点数和索引列表 |
