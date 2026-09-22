/**
 * UrbanWind 平台 — 综合评估板块
 * 多情景（本地上传 CFD 数据 / GNN 在线预测）→ 加权平均 + 静风/强风频率
 * → 停放适宜性分级 → 地图叠图 / 统计 / 报告图 / 导出。
 */
'use strict';

// ── 全局状态 ────────────────────────────────────────────────────────────────

let evalMap = null;
let evalMapReady = false;
let evalOverlay = null;        // L.imageOverlay（单车面板）
let droneMap = null;           // 无人机面板独立地图
let droneMapReady = false;
let droneOverlay = null;
let evalScenes = [];           // 情景列表（两个子板块共用）
let evalResult = null;         // /api/eval/run 响应
let evalLayer = 'grade';       // 当前图层: grade|mean|calm|strong

const EVAL_GRADE_COLORS = { 0: '#38bdf8', 1: '#10b981', 2: '#f59e0b', 3: '#ef4444' };
const EVAL_GRADE_LABELS = { 0: '静风区', 1: '适宜', 2: '中风险', 3: '高风险' };

// ── 评估情境（单车 / 无人机）──────────────────────────────────────────────────
// 两个子板块共用同一套框架；只有阈值、图例文字与所在面板不同。

let assessCtx = 'bike';        // 当前情境：bike | drone
let evalGradeColors = Object.assign({}, EVAL_GRADE_COLORS);
let evalGradeLabels = Object.assign({}, EVAL_GRADE_LABELS);
// 记录每个情境下是否已有渲染好的结果（evalResult 为两边共享）
let evalCtxLabels = { bike: false, drone: false };
// 各情境的 meta 文字（两个面板各存一份，切回时恢复）
let evalCtxMeta = { bike: '', drone: '' };

/**
 * 按当前情境取元素。
 * 单车面板（#eval-module）用原始 id；无人机面板用 `drone-` 前缀 id
 * （eval-stat-high → drone-stat-high），避免两套面板重复 id。
 */
function $el(id) {
    if (assessCtx === 'bike') {
        return document.getElementById(id);
    }
    const map = {
        'eval-empty': 'drone-empty',
        'eval-result': 'drone-result',
        'eval-meta': 'drone-meta',
        'eval-scene-stats': 'drone-scene-stats',
        'eval-legend': 'drone-legend',
        'eval-report-img': 'drone-report-img',
        'eval-map-note': 'drone-map-note',
    };
    const mapped = map[id] || id;
    const droneId = mapped.startsWith('eval-') ? 'drone-' + mapped.slice(5) : mapped;
    return document.getElementById(droneId);
}

/** 按报告图 / 图例 / 统计卡的分级标签切换情境 */
function applyCtxLabels(ctx) {
    const labels = (ctx && ctx.grade_labels) || null;
    if (assessCtx === 'drone' && labels) {
        evalGradeLabels = labels;
    } else if (assessCtx === 'bike' && !labels) {
        evalGradeLabels = Object.assign({}, EVAL_GRADE_LABELS);
    } else if (labels) {
        evalGradeLabels = labels;
    } else {
        evalGradeLabels = Object.assign({}, EVAL_GRADE_LABELS);
    }
}

/** 地图/叠加层按情境各自独立 */
function currentEvalMap() {
    if (assessCtx === 'drone') return droneMap;
    return evalMap;
}

function currentEvalOverlay() {
    return assessCtx === 'drone' ? droneOverlay : evalOverlay;
}

function setCurrentEvalOverlay(layer) {
    if (assessCtx === 'drone') droneOverlay = layer;
    else evalOverlay = layer;
}

// ── 地图（懒初始化）────────────────────────────────────────────────────────

function ensureEvalMap() {
    // 按当前情境初始化对应面板的地图（两个子板块各自独立）
    if (assessCtx === 'drone') return ensureDroneMap();
    if (evalMapReady) {
        evalMap.invalidateSize();
        return;
    }
    evalMapReady = true;

    evalMap = L.map('eval-map', {
        center: [32.06, 118.78],
        zoom: 15,
        zoomControl: false,
        attributionControl: true,
    });

    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
        attribution: 'Tiles &copy; Esri — World Dark Gray Canvas',
        maxZoom: 20,
    }).addTo(evalMap);

    L.control.zoom({ position: 'topleft' }).addTo(evalMap);
    L.control.scale({ position: 'bottomleft', metric: true, imperial: false }).addTo(evalMap);

    setTimeout(() => evalMap.invalidateSize(), 100);

    if (!window._evalCasesLoaded) loadEvalCases();
}

function ensureDroneMap() {
    if (droneMapReady) {
        droneMap.invalidateSize();
        return;
    }
    droneMapReady = true;

    droneMap = L.map('drone-map', {
        center: [32.06, 118.78],
        zoom: 15,
        zoomControl: false,
        attributionControl: true,
    });

    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
        attribution: 'Tiles &copy; Esri — World Dark Gray Canvas',
        maxZoom: 20,
    }).addTo(droneMap);

    L.control.zoom({ position: 'topleft' }).addTo(droneMap);
    L.control.scale({ position: 'bottomleft', metric: true, imperial: false }).addTo(droneMap);

    setTimeout(() => droneMap.invalidateSize(), 100);
}

// ── 数据源切换 ─────────────────────────────────────────────────────────────

function switchEvalSource(name) {
    // 数据源 Tab 在两个子板块各有一套（单车 eval-*，无人机 drone-*）
    const up = $el('eval-tab-upload');
    const gn = $el('eval-tab-gnn');
    const upBox = $el('eval-upload-box');
    const gnBox = $el('eval-gnn-box');
    if (up) up.classList.toggle('active', name === 'upload');
    if (gn) gn.classList.toggle('active', name === 'gnn');
    if (upBox) upBox.style.display = name === 'upload' ? '' : 'none';
    if (gnBox) gnBox.style.display = name === 'gnn' ? '' : 'none';
}

// ── 情景：本地上传 ──────────────────────────────────────────────────────────

async function uploadEvalFiles(input) {
    const files = Array.from(input.files || []);
    if (!files.length) return;
    const fd = new FormData();
    for (const f of files) fd.append('files', f);
    showToast(`正在解析 ${files.length} 个文件...`, 'info');
    try {
        const resp = await fetch('/api/eval/upload', { method: 'POST', body: fd });
        const data = await resp.json();
        if (!resp.ok) { showToast(data.detail || '上传失败', 'error'); return; }
        if (!data.scenes || !data.scenes.length) { showToast('未解析出有效情景', 'error'); return; }
        addEvalScenes(data.scenes);
        showToast(`成功解析 ${data.scenes.length} 个情景`, 'success');
    } catch (e) {
        showToast('上传失败: ' + e.message, 'error');
    } finally {
        input.value = '';
    }
}

// ── 情景：GNN 批量预测 ───────────────────────────────────────────────────────

async function loadEvalCases() {
    const select = document.getElementById('eval-case-select');
    try {
        const resp = await fetch('/api/list-cases');
        const data = await resp.json();
        window._evalCasesLoaded = true;
        if (!data.success) throw new Error(data.detail || '加载失败');
        const cases = data.cases || [];
        window._evalCasesCache = cases;
        select.innerHTML = '';
        const ph = document.createElement('option');
        ph.value = '';
        ph.textContent = cases.length ? '选择一个 CFD 案例' : '暂无案例（请先在「城市风场模拟」中生成，或用本地上传）';
        select.appendChild(ph);
        for (const c of cases) {
            const opt = document.createElement('option');
            opt.value = c.name;
            opt.textContent = `${c.name} (${c.n_buildings} 建筑)`;
            select.appendChild(opt);
        }
        if (!cases.length) { window._evalCasesClean = true; }
    } catch (e) {
        window._evalCasesLoaded = true;
        select.innerHTML = '<option value="">案例列表不可用</option>';
        showToast('案例列表加载失败: ' + e.message, 'error');
    }
}

// 后端 /api/list-cases 已返回 case_dir；退回只传案例名由后端解析。前端不拼绝对路径。
function evalCaseDirFromName(name) {
    const c = (window._evalCasesCache || []).find(x => x.name === name);
    return (c && c.case_dir) ? c.case_dir : name;
}

async function runGnnScenes() {
    const caseName = document.getElementById('eval-case-select').value;
    const speed = parseFloat(document.getElementById('eval-gnn-speed').value);
    if (!caseName) { showToast('请先选择 CFD 案例', 'error'); return; }
    if (!speed || speed <= 0) { showToast('请输入有效风速', 'error'); return; }

    const scenes = ['N', 'S', 'E', 'W'].map(d => ({ wind_direction: d, inlet_speed: speed }));
    showToast(`GNN 批量预测中（${scenes.length} 个情景，每个约几秒）...`, 'info');
    const btn = document.getElementById('eval-gnn-speed');
    btn.disabled = true;
    try {
        const resp = await fetch('/api/eval/gnn', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ case_dir: evalCaseDirFromName(caseName), scenes }),
        });
        const data = await resp.json();
        if (!resp.ok) { showToast(data.detail || 'GNN 预测失败', 'error'); return; }
        addEvalScenes(data.scenes);
        showToast(`已生成 ${data.scenes.length} 个 GNN 情景`, 'success');
    } catch (e) {
        showToast('GNN 预测失败: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

// ── 情景列表渲染 ─────────────────────────────────────────────────────────────

function addEvalScenes(scenes) {
    for (const s of scenes) {
        evalScenes.push({
            scene_id: s.scene_id,
            wind_direction: s.wind_direction,
            inlet_speed: s.inlet_speed,
            weight: 1.0,
            source: s.source,
            filename: s.filename,
            n_points: s.n_points,
            regular: s.regular,
            speed_range: s.speed_range || null,
        });
    }
    renderEvalSceneRows();
}

function renderEvalSceneRows() {
    const wrap = $el('eval-scenes-wrap');
    const box = $el('eval-scenes');
    const count = document.getElementById('eval-scene-count');
    const runBtn = document.getElementById('btn-eval-run');

    // 情景数同时写「模块工具栏」与「全局工具条」两处显示
    const countTxt = `${evalScenes.length} 个情景`;
    if (count) count.textContent = countTxt;
    const gcount = document.getElementById('assess-scene-count');
    if (gcount) gcount.textContent = countTxt;

    // 运行按钮在阶段2已上移到全局工具条（#btn-assess-run），两个都同步禁用态
    const gRunBtn = document.getElementById('btn-assess-run');
    const disabled = evalScenes.length === 0;
    if (runBtn) runBtn.disabled = disabled;
    if (gRunBtn) gRunBtn.disabled = disabled;

    if (!wrap || !box) return;
    if (!evalScenes.length) { wrap.style.display = 'none'; return; }
    wrap.style.display = '';

    box.innerHTML = '';
    evalScenes.forEach((s, idx) => {
        const row = document.createElement('div');
        row.className = 'eval-scene-row';
        const src = s.source === 'gnn' ? '🌪' : '📤';
        const label = (s.filename || '').replace(/\.(csv|zip|txt)$/i, '').slice(0, 18)
            || `${s.wind_direction}风 ${s.inlet_speed}m/s`;
        row.innerHTML = `
            <div class="eval-scene-head">
                <span class="eval-src">${src}</span>
                <span class="eval-scene-name" title="${s.filename || ''}">${label}</span>
                <span class="eval-scene-pts">${s.n_points.toLocaleString()} 点</span>
            </div>
            <div class="eval-scene-body">
                <span class="eval-windtag">${s.wind_direction} · ${s.inlet_speed} m/s</span>
                <span class="eval-wtag">权重</span>
                <input type="number" class="eval-weight" value="${s.weight}" min="0" max="20" step="0.1"
                       onchange="changeEvalWeight(${idx}, this.value)">
                <button class="btn btn-sm btn-icon" title="导出情景 CSV（可再上传）"
                        onclick="exportEvalScene('${s.scene_id}')">⬇</button>
                <button class="btn btn-sm btn-icon btn-danger-icon" title="删除情景"
                        onclick="removeEvalScene(${idx})">✕</button>
            </div>`;
        box.appendChild(row);
    });
}

function changeEvalWeight(idx, val) {
    const w = parseFloat(val);
    evalScenes[idx].weight = isNaN(w) || w < 0 ? 1.0 : w;
}

function removeEvalScene(idx) {
    evalScenes.splice(idx, 1);
    renderEvalSceneRows();
}

async function clearEvalScenes() {
    if (!evalScenes.length) return;
    evalScenes = [];
    evalResult = null;
    evalCtxLabels = { bike: false, drone: false };
    evalCtxMeta = { bike: '', drone: '' };
    renderEvalSceneRows();
    // 两个子板块的空态/结果区一起复位
    ['eval-empty', 'drone-empty'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = '';
    });
    ['eval-result', 'drone-result'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });
    [[evalMap, evalOverlay], [droneMap, droneOverlay]].forEach(([m, o]) => {
        if (o && m) m.removeLayer(o);
    });
    evalOverlay = null; droneOverlay = null;
    ['eval-map-note', 'drone-map-note'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });
    try { await fetch('/api/eval/clear', { method: 'POST' }); } catch (e) { /* 忽略 */ }
    showToast('已清空情景', 'info');
}

function exportEvalScene(sceneId) {
    window.open(`/api/eval/export/${sceneId}`, '_blank');
}

// ── 运行综合评估 ─────────────────────────────────────────────────────────────

// 运行按钮分布在全局工具条(#btn-assess-run)与模块内(#btn-eval-run)，统一设置状态
function setEvalRunButtons(disabled, text) {
    ['btn-assess-run', 'btn-eval-run'].forEach(id => {
        const b = document.getElementById(id);
        if (!b) return;
        b.disabled = disabled;
        if (text !== undefined) b.textContent = text;
    });
}

async function runEval() {
    if (!evalScenes.length) { showToast('请先添加情景', 'error'); return; }
    const runBtn = document.getElementById('btn-eval-run');
    const oldText = runBtn ? runBtn.textContent : '🎯 运行综合评估';
    setEvalRunButtons(true, '⏳ 评估中...');

    try {
        const resp = await fetch('/api/eval/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scene_ids: evalScenes.map(s => s.scene_id),
                weights: evalScenes.map(s => s.weight),
                context: assessCtx,          // bike | drone：后端据此换阈值与措辞
            }),
        });
        const data = await resp.json();
        if (!resp.ok) { showToast(data.detail || '评估失败', 'error'); return; }
        evalResult = data;
        renderEvalResult(data);
        showToast('综合评估完成', 'success');
    } catch (e) {
        showToast('评估失败: ' + e.message, 'error');
    } finally {
        setEvalRunButtons(false, oldText);
    }
}

// ── 结果渲染 ─────────────────────────────────────────────────────────────────

// ── 情境切换时的面板同步 ─────────────────────────────────────────────────────

/** 统计卡下方的文字标签按情境更新（两个面板各写各的） */
function syncStatLabels(rootSel) {
    const grid = document.querySelector(rootSel + ' .siting-stats-grid');
    if (!grid) return;
    // 顺序：stat-high(grade3) → medium(2) → low(1) → calm(0)
    ['stat-high', 'stat-medium', 'stat-low', 'stat-calm'].forEach((cls, i) => {
        const lab = grid.querySelector('.' + cls + ' .stat-label');
        if (lab) lab.textContent = evalGradeLabels[3 - i] || lab.textContent;
    });
}

/**
 * 切换子板块后刷新两个面板的显示。
 * evalResult 是共享状态：任意一边运行过后，两边都应能看到结果，
 * 只是分级标签随各自情境不同（单车=倾覆阈值，无人机=机型抗风等级）。
 */
function refreshAssessPanels() {
    if (typeof evalCtxLabels === 'undefined') return;
    const resBox = document.getElementById('eval-result');
    const emptyBox = document.getElementById('eval-empty');
    if (resBox) resBox.style.display = evalCtxLabels.bike ? '' : 'none';
    if (emptyBox) emptyBox.style.display = evalCtxLabels.bike ? 'none' : '';
    syncStatLabels('#eval-result');
    const bikeMeta = document.getElementById('eval-meta');
    if (bikeMeta && evalCtxLabels.bike) bikeMeta.textContent = evalCtxMeta.bike;

    const dResBox = document.getElementById('drone-result');
    const dEmptyBox = document.getElementById('drone-empty');
    if (dResBox) dResBox.style.display = evalCtxLabels.drone ? '' : 'none';
    if (dEmptyBox) dEmptyBox.style.display = evalCtxLabels.drone ? 'none' : '';
    syncStatLabels('#drone-result');
    const droneMeta = document.getElementById('drone-meta');
    if (droneMeta && evalCtxLabels.drone) droneMeta.textContent = evalCtxMeta.drone;

    // 图例 / 叠图按当前情境重绘（叠图落在 currentEvalMap 上）
    if (typeof evalResult !== 'undefined' && evalResult && typeof setEvalLayer === 'function') {
        setEvalLayer(typeof evalLayer !== 'undefined' ? evalLayer : 'grade');
    }
}

function renderEvalResult(r) {
    // 情境（单车/无人机）由后端回传，决定分级标签与所在面板
    const info = r.context || null;
    if (info && info.key) assessCtx = info.key;
    if (info) applyCtxLabels(info);

    const empty = $el('eval-empty');
    const resultBox = $el('eval-result');
    if (empty) empty.style.display = 'none';
    if (resultBox) resultBox.style.display = '';

    // 统计卡（分级占比）——标签按情境换
    const fr = r.stats.grade_frac || {};
    const setStat = (id, gradeIdx) => {
        const el = $el(id);
        if (el) el.textContent = ((fr[String(gradeIdx)] || 0) * 100).toFixed(1) + '%';
    };
    setStat('eval-stat-high', 3);
    setStat('eval-stat-medium', 2);
    setStat('eval-stat-low', 1);
    setStat('eval-stat-calm', 0);

    // 统计卡下方文字标签由 refreshAssessPanels/syncStatLabels 统一按情境写
    evalCtxLabels[assessCtx] = true;
    syncStatLabels(assessCtx === 'drone' ? '#drone-result' : '#eval-result');

    const meta = $el('eval-meta');
    if (meta) {
        const name = (info && info.label) || '共享单车停放适宜性';
        const crit = assessCtx === 'drone' ? '机型抗风等级修正阈值' : '阵风修正阈值';
        const txt =
            `${name} · ${r.stats.n_scenes} 个情景 · 加权平均风速 ` +
            `${r.stats.mean_min?.toFixed?.(1) ?? r.stats.mean_min} ~ ${r.stats.mean_max?.toFixed?.(1) ?? r.stats.mean_max} m/s · ` +
            `${crit} ${r.stats.v_eff.toFixed(2)} m/s`;
        meta.textContent = txt;
        evalCtxMeta[assessCtx] = txt;   // 供切回该子板块时恢复
    }

    // 每情景统计
    const ss = $el('eval-scene-stats');
    if (ss) {
        ss.innerHTML = '<div class="eval-scenes-title">🌪 各情景风速</div>';
        const tbl = document.createElement('table');
        tbl.className = 'eval-stats-table';
        tbl.innerHTML = '<tr><th>情景</th><th>最小</th><th>平均</th><th>最大</th></tr>';
        for (const s of r.scene_stats || []) {
            tbl.innerHTML += `<tr><td>${s.wind_direction} · ${s.inlet_speed}m/s</td><td>${s.min}</td><td>${s.mean}</td><td>${s.max}</td></tr>`;
        }
        ss.appendChild(tbl);
    }

    // 图例随图层更新（setEvalLayer 内处理）
    // 报告图
    const rep = $el('eval-report-img');
    if (rep) rep.src = r.report_png;

    // Top 推荐/危险（措辞按情境）
    const recsS = $el('eval-recs-suitable');
    const recsR = $el('eval-recs-risky');
    const suitLabel = assessCtx === 'drone' ? '适飞区' : '适宜区';
    const riskLabel = assessCtx === 'drone' ? '禁飞风险区' : '风险区';
    if (recsS) recsS.innerHTML = renderTopList(r.top_suitable, suitLabel);
    if (recsR) recsR.innerHTML = renderTopList(r.top_risky, riskLabel);

    // 地图叠图（默认分级）
    setEvalLayer('grade');
}

function renderTopList(list, label) {
    if (!list || !list.length) return `<div class="rec-item"><span class="rec-rank">—</span><span>未检出显著${label}</span></div>`;
    return list.map((t, i) => {
        const rx = Math.round(t.cx), ry = Math.round(t.cy);
        const frac = (t.frac * 100).toFixed(0);
        return `<div class="rec-item"><span class="rec-rank">Top${i + 1}</span><span>(${rx}, ${ry}) m · 候选${label}占比 ${frac}%</span></div>`;
    }).join('');
}

// ── 地图图层切换 ─────────────────────────────────────────────────────────────

function setEvalLayer(kind) {
    evalLayer = kind;
    // 图层按钮有两套（单车/无人机），只切换当前面板内的
    const scope = document.querySelector(assessCtx === 'drone' ? '#drone-layer-switch' : '#eval-layer-switch');
    (scope ? scope.querySelectorAll('.eval-layer-btn') : []).forEach(b =>
        b.classList.toggle('active', b.dataset.layer === kind));
    if (!evalResult) return;

    let grid = null, label = '';
    if (kind === 'grade') { grid = evalResult.grade_grid; label = '综合分级'; }
    else if (kind === 'mean') { grid = evalResult.mean_grid; label = '加权平均风速'; }
    else if (kind === 'calm') { grid = evalResult.calm_freq_grid; label = '静风频率'; }
    else { grid = evalResult.strong_freq_grid; label = '强风频率'; }

    // 图例随图层更新（分级图例的文字按情境）
    const legend = $el('eval-legend');
    if (legend) {
        if (kind === 'grade') {
            legend.innerHTML = [0, 1, 2, 3].map(g =>
                `<span><i style="background:${evalGradeColors[g]}"></i>${evalGradeLabels[g]}</span>`).join('');
        } else {
            legend.innerHTML = '<span style="font-size:11px;color:var(--text-muted)">'
                + (kind === 'mean' ? '颜色越暖 = 风速越大 (m/s)'
                    : kind === 'calm' ? '颜色越红 = 静风频率越高 (%情景时间静风)'
                    : '颜色越红 = 强风频率越高 (%情景时间超过阵风阈值)')
                + '</span>';
        }
    }

    const dataUrl = drawEvalGridToCanvas(grid, kind);
    if (!dataUrl) { clearEvalOverlay(); return; }

    const map = currentEvalMap();
    const latlng = evalResult.grid_bounds_latlng;
    const note = $el('eval-map-note');
    if (latlng && map) {
        // 经纬度参考可用 → 叠到地图
        if (note) note.style.display = 'none';
        const bounds = [[latlng[1], latlng[0]], [latlng[3], latlng[2]]];
        const old = currentEvalOverlay();
        if (old) map.removeLayer(old);
        setCurrentEvalOverlay(L.imageOverlay(dataUrl, bounds, { opacity: 0.55 }).addTo(map));
        if (!map.getBounds().contains(L.latLngBounds(bounds).getCenter())) {
            map.fitBounds(L.latLngBounds(bounds));
        }
    } else {
        // 无经纬度 → 地图仅提示（评估结果见面板/报告图）
        clearEvalOverlay();
        if (note) note.style.display = 'block';
    }
}

function clearEvalOverlay() {
    const map = currentEvalMap();
    const old = currentEvalOverlay();
    if (old && map) { map.removeLayer(old); setCurrentEvalOverlay(null); }
}

function drawEvalGridToCanvas(grid, kind) {
    if (!grid || !grid.length) return null;
    const H = grid.length, W = grid[0].length;
    const canvas = document.createElement('canvas');
    canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext('2d');
    const imgData = ctx.createImageData(W, H);

    // 值域
    let vmin = Infinity, vmax = -Infinity;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
        const v = grid[y][x];
        if (v === null || v === undefined || isNaN(v)) continue;
        if (v < vmin) vmin = v; if (v > vmax) vmax = v;
    }
    if (vmin === Infinity) return null;
    if (kind === 'calm' || kind === 'strong') { vmin = 0; vmax = 1; }

    for (let y = 0; y < H; y++) {
        for (let x = 0; x < W; x++) {
            const v = grid[y][x];
            const idx = (y * W + x) * 4;
            if (v === null || v === undefined || isNaN(v)) { imgData.data[idx + 3] = 0; continue; }
            let r, g, b;
            if (kind === 'grade') {
                const c = evalGradeColors[Math.round(v)] || '#94a3b8';
                r = parseInt(c.slice(1, 3), 16); g = parseInt(c.slice(3, 5), 16); b = parseInt(c.slice(5, 7), 16);
            } else {
                const t = Math.max(0, Math.min(1, (v - vmin) / ((vmax - vmin) || 1)));
                if (kind === 'mean') {
                    if (t < 0.33) { const s = t / 0.33; r = 30; g = 100 + Math.round(155 * s); b = 200 + Math.round(55 * s); }
                    else if (t < 0.66) { const s = (t - 0.33) / 0.33; r = 30 + Math.round(225 * s); g = 255 - Math.round(55 * s); b = 255 - Math.round(200 * s); }
                    else { const s = (t - 0.66) / 0.34; r = 255; g = 200 - Math.round(150 * s); b = 55 - Math.round(55 * s); }
                } else {
                    // 频率图：低=蓝，高=红（hot 风格）
                    r = Math.round(30 + 225 * t); g = Math.round(30 + 200 * (1 - Math.abs(2 * t - 1) * 0.9)); b = Math.round(200 - 170 * t);
                }
            }
            imgData.data[idx] = r; imgData.data[idx + 1] = g; imgData.data[idx + 2] = b;
            imgData.data[idx + 3] = 200;
        }
    }
    ctx.putImageData(imgData, 0, 0);
    return canvas.toDataURL('image/png');
}

// ── 报告下载 ─────────────────────────────────────────────────────────────────

function downloadEvalReport() {
    if (!evalResult || !evalResult.report_png) { showToast('暂无报告', 'error'); return; }
    const a = document.createElement('a');
    a.href = evalResult.report_png;
    a.download = 'urbanwind_eval_report.png';
    document.body.appendChild(a);
    a.click();
    a.remove();
}
