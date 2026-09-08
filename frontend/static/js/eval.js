/**
 * UrbanWind 平台 — 综合评估板块
 * 多情景（本地上传 CFD 数据 / GNN 在线预测）→ 加权平均 + 静风/强风频率
 * → 停放适宜性分级 → 地图叠图 / 统计 / 报告图 / 导出。
 */
'use strict';

// ── 全局状态 ────────────────────────────────────────────────────────────────

let evalMap = null;
let evalMapReady = false;
let evalOverlay = null;        // L.imageOverlay
let evalScenes = [];           // 情景列表（前端元数据）
let evalResult = null;         // /api/eval/run 响应
let evalLayer = 'grade';       // 当前图层: grade|mean|calm|strong

const EVAL_GRADE_COLORS = { 0: '#38bdf8', 1: '#10b981', 2: '#f59e0b', 3: '#ef4444' };
const EVAL_GRADE_LABELS = { 0: '静风区', 1: '适宜', 2: '中风险', 3: '高风险' };

// ── 地图（懒初始化）────────────────────────────────────────────────────────

function ensureEvalMap() {
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

// ── 数据源切换 ─────────────────────────────────────────────────────────────

function switchEvalSource(name) {
    document.getElementById('eval-tab-upload').classList.toggle('active', name === 'upload');
    document.getElementById('eval-tab-gnn').classList.toggle('active', name === 'gnn');
    document.getElementById('eval-upload-box').style.display = name === 'upload' ? '' : 'none';
    document.getElementById('eval-gnn-box').style.display = name === 'gnn' ? '' : 'none';
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
        select.innerHTML = '';
        const ph = document.createElement('option');
        ph.value = '';
        ph.textContent = cases.length ? '选择一个 CFD 案例' : '暂无案例（需 E 盘案例库在线，或用本地上传）';
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
        select.innerHTML = '<option value="">案例列表不可用（E 盘离线？）</option>';
        showToast('案例列表加载失败: ' + e.message, 'error');
    }
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
            body: JSON.stringify({ case_dir: 'E:/UrbanWind/cfd_cases/' + caseName, scenes }),
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
    const wrap = document.getElementById('eval-scenes-wrap');
    const box = document.getElementById('eval-scenes');
    const count = document.getElementById('eval-scene-count');
    const runBtn = document.getElementById('btn-eval-run');

    count.textContent = `${evalScenes.length} 个情景`;
    runBtn.disabled = evalScenes.length === 0;
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
    renderEvalSceneRows();
    document.getElementById('eval-empty').style.display = '';
    document.getElementById('eval-result').style.display = 'none';
    if (evalOverlay) { evalMap.removeLayer(evalOverlay); evalOverlay = null; }
    const note = document.getElementById('eval-map-note');
    if (note) note.style.display = 'none';
    try { await fetch('/api/eval/clear', { method: 'POST' }); } catch (e) { /* 忽略 */ }
    showToast('已清空情景', 'info');
}

function exportEvalScene(sceneId) {
    window.open(`/api/eval/export/${sceneId}`, '_blank');
}

// ── 运行综合评估 ─────────────────────────────────────────────────────────────

async function runEval() {
    if (!evalScenes.length) { showToast('请先添加情景', 'error'); return; }
    const runBtn = document.getElementById('btn-eval-run');
    runBtn.disabled = true;
    const oldText = runBtn.textContent;
    runBtn.textContent = '⏳ 评估中...';

    try {
        const resp = await fetch('/api/eval/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scene_ids: evalScenes.map(s => s.scene_id),
                weights: evalScenes.map(s => s.weight),
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
        runBtn.disabled = false;
        runBtn.textContent = oldText;
    }
}

// ── 结果渲染 ─────────────────────────────────────────────────────────────────

function renderEvalResult(r) {
    const empty = document.getElementById('eval-empty');
    const resultBox = document.getElementById('eval-result');
    empty.style.display = 'none';
    resultBox.style.display = '';

    // 统计卡（分级占比）
    const fr = r.stats.grade_frac || {};
    document.getElementById('eval-stat-high').textContent = ((fr['3'] || 0) * 100).toFixed(1) + '%';
    document.getElementById('eval-stat-medium').textContent = ((fr['2'] || 0) * 100).toFixed(1) + '%';
    document.getElementById('eval-stat-low').textContent = ((fr['1'] || 0) * 100).toFixed(1) + '%';
    document.getElementById('eval-stat-calm').textContent = ((fr['0'] || 0) * 100).toFixed(1) + '%';

    document.getElementById('eval-meta').textContent =
        `${r.stats.n_scenes} 个情景 · 加权平均风速 ${r.stats.mean_min?.toFixed?.(1) ?? r.stats.mean_min} ~ ${r.stats.mean_max?.toFixed?.(1) ?? r.stats.mean_max} m/s · ` +
        `阵风修正阈值 ${r.stats.v_eff.toFixed(2)} m/s（V_crit 11.7 × 0.67）`;

    // 每情景统计
    const ss = document.getElementById('eval-scene-stats');
    ss.innerHTML = '<div class="eval-scenes-title">🌪 各情景风速</div>';
    const tbl = document.createElement('table');
    tbl.className = 'eval-stats-table';
    tbl.innerHTML = '<tr><th>情景</th><th>最小</th><th>平均</th><th>最大</th></tr>';
    for (const s of r.scene_stats || []) {
        tbl.innerHTML += `<tr><td>${s.wind_direction} · ${s.inlet_speed}m/s</td><td>${s.min}</td><td>${s.mean}</td><td>${s.max}</td></tr>`;
    }
    ss.appendChild(tbl);

    // 图例随图层更新（setEvalLayer 内处理）
    // 报告图
    document.getElementById('eval-report-img').src = r.report_png;

    // Top 推荐/危险
    const recsS = document.getElementById('eval-recs-suitable');
    const recsR = document.getElementById('eval-recs-risky');
    recsS.innerHTML = renderTopList(r.top_suitable, '适宜区');
    recsR.innerHTML = renderTopList(r.top_risky, '风险区');

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
    document.querySelectorAll('.eval-layer-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.layer === kind));
    if (!evalResult) return;

    let grid = null, label = '';
    if (kind === 'grade') { grid = evalResult.grade_grid; label = '综合分级'; }
    else if (kind === 'mean') { grid = evalResult.mean_grid; label = '加权平均风速'; }
    else if (kind === 'calm') { grid = evalResult.calm_freq_grid; label = '静风频率'; }
    else { grid = evalResult.strong_freq_grid; label = '强风频率'; }

    // 图例随图层更新
    const legend = document.getElementById('eval-legend');
    if (kind === 'grade') {
        legend.innerHTML = [0, 1, 2, 3].map(g =>
            `<span><i style="background:${EVAL_GRADE_COLORS[g]}"></i>${EVAL_GRADE_LABELS[g]}</span>`).join('');
    } else {
        legend.innerHTML = '<span style="font-size:11px;color:var(--text-muted)">'
            + (kind === 'mean' ? '颜色越暖 = 风速越大 (m/s)'
                : kind === 'calm' ? '颜色越红 = 静风频率越高 (%情景时间静风)'
                : '颜色越红 = 强风频率越高 (%情景时间超过阵风阈值)')
            + '</span>';
    }

    const dataUrl = drawEvalGridToCanvas(grid, kind);
    if (!dataUrl) { clearEvalOverlay(); return; }

    const latlng = evalResult.grid_bounds_latlng;
    const note = document.getElementById('eval-map-note');
    if (latlng) {
        // 经纬度参考可用 → 叠到地图
        if (note) note.style.display = 'none';
        const bounds = [[latlng[1], latlng[0]], [latlng[3], latlng[2]]];
        if (evalOverlay) evalMap.removeLayer(evalOverlay);
        evalOverlay = L.imageOverlay(dataUrl, bounds, { opacity: 0.55 }).addTo(evalMap);
        if (!evalMap.getBounds().contains(L.latLngBounds(bounds).getCenter())) {
            evalMap.fitBounds(L.latLngBounds(bounds));
        }
    } else {
        // 无经纬度 → 地图仅提示（评估结果见面板/报告图）
        clearEvalOverlay();
        if (note) note.style.display = 'block';
    }
}

function clearEvalOverlay() {
    if (evalOverlay && evalMap) { evalMap.removeLayer(evalOverlay); evalOverlay = null; }
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
                const c = EVAL_GRADE_COLORS[Math.round(v)] || '#94a3b8';
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
