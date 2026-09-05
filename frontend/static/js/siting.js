/**
 * UrbanWind 平台 — 单车选址板块
 * 基于 CFD/GNN 风场预测结果，对候选单车停放点做风暴露评估与风险分级。
 */
'use strict';

// ── 全局状态 ────────────────────────────────────────────────────────────────

let sitingMap = null;
let sitingMarkers = null;   // L.layerGroup
let sitingMapReady = false;
let sitingCasesLoaded = false;
let sitingResult = null;    // 最近一次评估结果

const RISK_COLORS = {
    high: '#ef4444',
    medium: '#f59e0b',
    low: '#10b981',
    calm: '#38bdf8',
    unknown: '#94a3b8',
};

const RISK_LABELS = {
    high: '高风险',
    medium: '中风险',
    low: '低风险',
    calm: '静风区',
    unknown: '未知',
};

// ── 地图（懒初始化）────────────────────────────────────────────────────────

function ensureSitingMap() {
    if (sitingMapReady) {
        sitingMap.invalidateSize();
        return;
    }
    sitingMapReady = true;

    sitingMap = L.map('siting-map', {
        center: [32.06, 118.78],
        zoom: 15,
        zoomControl: false,
        attributionControl: true,
    });

    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
        attribution: 'Tiles &copy; Esri — World Dark Gray Canvas',
        maxZoom: 20,
    }).addTo(sitingMap);

    L.control.zoom({ position: 'topleft' }).addTo(sitingMap);
    L.control.scale({ position: 'bottomleft', metric: true, imperial: false }).addTo(sitingMap);

    sitingMarkers = L.layerGroup().addTo(sitingMap);

    setTimeout(() => sitingMap.invalidateSize(), 100);

    // 首次进入时加载案例列表
    if (!sitingCasesLoaded) {
        loadSitingCases();
    }
}

// ── 案例列表 ────────────────────────────────────────────────────────────────

let sitingCaseCenter = null;   // 当前案例中心 [lon, lat]，用于局部米坐标 → 经纬度换算
window._sitingCasesCache = [];

async function loadSitingCases() {
    const select = document.getElementById('siting-case-select');
    try {
        const resp = await fetch('/api/list-cases');
        const data = await resp.json();
        if (!data.success) throw new Error(data.detail || '加载失败');

        const cases = data.cases || [];
        window._sitingCasesCache = cases;
        select.innerHTML = '';

        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = cases.length ? '选择一个 CFD 案例' : '暂无案例（请先在「城市风场模拟」中生成）';
        select.appendChild(placeholder);

        for (const c of cases) {
            const opt = document.createElement('option');
            opt.value = c.name;
            const bikes = c.n_bikes > 0 ? ` · ${c.n_bikes} 单车点` : '';
            opt.textContent = `${c.name} (${c.n_buildings} 建筑${bikes})`;
            opt.disabled = !c.has_plan;
            if (!c.has_plan) {
                opt.textContent = `${c.name} (缺少 site_plan.geojson)`;
            }
            select.appendChild(opt);
        }

        // 默认选中第一个可用案例
        const first = cases.find(c => c.has_plan);
        if (first) {
            select.value = first.name;
            onSitingCaseChange();
        }
        sitingCasesLoaded = true;
    } catch (e) {
        select.innerHTML = '<option value="">案例加载失败</option>';
        showToast('案例列表加载失败: ' + e.message, 'error');
    }
}

function onSitingCaseChange() {
    // 清掉旧结果
    clearSitingResults();

    const name = document.getElementById('siting-case-select').value;
    const info = document.getElementById('siting-case-info');
    if (!name) {
        info.textContent = '请选择 CFD 案例';
        sitingCaseCenter = null;
        return;
    }

    // 更新案例中心（局部坐标换算基准）
    sitingCaseCenter = null;
    const c = window._sitingCasesCache.find(x => x.name === name);
    if (c && c.center) {
        sitingCaseCenter = c.center;
        info.textContent = `${c.n_buildings} 建筑 · ${c.n_bikes} 单车点${c.modified ? ' · ' + c.modified : ''}`;
    } else {
        info.textContent = `${name}（无地理坐标，仅能查看评估结果）`;
    }

    // 飞到案例位置
    if (sitingMap && sitingCaseCenter) {
        sitingMap.setView([sitingCaseCenter[1], sitingCaseCenter[0]], 16);
    }
}

// ── 评估 ────────────────────────────────────────────────────────────────────

async function runBikeSiting() {
    const caseName = document.getElementById('siting-case-select').value;
    if (!caseName) {
        showToast('请先选择一个 CFD 案例', 'error');
        return;
    }

    const windDir = document.getElementById('siting-wind').value;
    const inletSpeed = parseFloat(document.getElementById('siting-speed').value);
    if (!inletSpeed || inletSpeed <= 0) {
        showToast('请输入有效的风速', 'error');
        return;
    }

    const btn = document.getElementById('btn-siting-run');
    const oldText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ 评估中...';

    try {
        const resp = await fetch('/api/bike-siting', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                case_dir: String(rawCaseDirFromName(caseName)),
                wind_direction: windDir,
                inlet_speed: inletSpeed,
            }),
        });
        const data = await resp.json();
        if (!data.success) {
            showToast(data.detail || '评估失败', 'error');
            return;
        }

        sitingResult = data;
        renderSitingResults(data, caseName);
        showToast(`评估完成：${data.stats.total} 个停放点，高风险 ${data.stats.high} 个`, 'success');
    } catch (e) {
        showToast('评估失败: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = oldText;
    }
}

function rawCaseDirFromName(name) {
    // 与后端 CFD_CASES_DIR 一致：E:/UrbanWind/cfd_cases/<name>
    return `E:/UrbanWind/cfd_cases/${name}`;
}

// ── 结果渲染 ────────────────────────────────────────────────────────────────

function clearSitingResults() {
    if (sitingMarkers) sitingMarkers.clearLayers();
    document.getElementById('siting-empty').style.display = '';
    document.getElementById('siting-stats').style.display = 'none';
    document.getElementById('stat-high').textContent = '0';
    document.getElementById('stat-medium').textContent = '0';
    document.getElementById('stat-low').textContent = '0';
    document.getElementById('stat-calm').textContent = '0';
    document.getElementById('siting-meta').textContent = '';
    document.getElementById('siting-img').innerHTML = '';
    document.getElementById('recs-safest').innerHTML = '';
    document.getElementById('recs-riskiest').innerHTML = '';
}

function renderSitingResults(data, caseName) {
    // 统计
    const st = data.stats;
    document.getElementById('stat-high').textContent = st.high ?? 0;
    document.getElementById('stat-medium').textContent = st.medium ?? 0;
    document.getElementById('stat-low').textContent = st.low ?? 0;
    document.getElementById('stat-calm').textContent = st.calm ?? 0;
    document.getElementById('siting-meta').textContent =
        `倾覆阈值≈${(data.v_eff || 0).toFixed(1)} m/s · 建筑 ${data.n_buildings} · 树列 ${data.n_trees}` +
        ` · ${data.wind_direction}风 ${data.inlet_speed} m/s`;

    // 图片
    const imgBox = document.getElementById('siting-img');
    imgBox.innerHTML = '';
    const img = document.createElement('img');
    img.src = data.image_base64;
    img.alt = '风暴露评估';
    imgBox.appendChild(img);

    // 地图标记
    if (sitingMarkers) sitingMarkers.clearLayers();
    const avgLatLng = [];
    if (sitingMap && data.points && data.points.length) {
        // 需要把局部坐标（米）转回经纬度来显示。用 case center（geojson metadata）
        // —— siting 端点返回 grid_bounds 是局部米坐标，前端需案例中心点做换算。
        // 简化：从选中案例的 center（list-cases 已返回经纬度）换算。
        const center = sitingCaseCenter || null;
        if (center) {
            const [lon0, lat0] = center;
            for (const p of data.points) {
                const lng = lon0 + p.x / (111320 * Math.max(Math.cos(lat0 * Math.PI / 180), 0.3));
                const lat = lat0 + p.y / 111320;
                const lvl = p.risk_level;
                const circle = L.circleMarker([lat, lng], {
                    radius: 9,
                    color: RISK_COLORS[lvl] || RISK_COLORS.unknown,
                    fillColor: RISK_COLORS[lvl] || RISK_COLORS.unknown,
                    fillOpacity: 0.85,
                    weight: 2,
                    opacity: 0.9,
                });
                const spd = p.speed == null ? '—' : p.speed.toFixed(2);
                circle.bindPopup(
                    `<b>${RISK_LABELS[lvl] || '未知'}</b><br>` +
                    `风速: ${spd} m/s<br>` +
                    `<span style="font-size:11px;color:#94a3b8">${p.suggestion || ''}</span>`
                );
                circle.addTo(sitingMarkers);
                avgLatLng.push([lat, lng]);
            }
            sitingMap.fitBounds(L.latLngBounds(avgLatLng).pad(0.2));
        }
    }

    // 推荐列表
    const recs = data.recommendations || { safest: [], riskiest: [] };
    renderRecList('recs-safest', recs.safest, true);
    renderRecList('recs-riskiest', recs.riskiest, false);

    // 显示结果区
    document.getElementById('siting-empty').style.display = 'none';
    document.getElementById('siting-stats').style.display = '';
}

function renderRecList(containerId, items, isSafest) {
    const container = document.getElementById(containerId);
    container.innerHTML = '';
    if (!items || !items.length) {
        container.innerHTML = '<span style="font-size:12px;color:var(--text-muted)">无数据</span>';
        return;
    }
    items.forEach((p, i) => {
        const div = document.createElement('div');
        div.className = 'rec-item';
        const lvl = p.risk_level;
        const spd = p.speed == null ? '—' : p.speed.toFixed(2);
        div.innerHTML =
            `<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${RISK_COLORS[lvl] || '#94a3b8'};flex-shrink:0"></span>` +
            `<span class="rec-rank">#${i + 1}</span>` +
            `<span style="flex:1">${RISK_LABELS[lvl] || '未知'}</span>` +
            `<span class="rec-spd">${spd} m/s</span>`;
        // 点击飞到该点（用案例中心换算局部米→经纬度）
        div.onclick = () => {
            if (sitingMap && sitingCaseCenter) {
                const [lon0, lat0] = sitingCaseCenter;
                const lng = lon0 + p.x / (111320 * Math.max(Math.cos(lat0 * Math.PI / 180), 0.3));
                const lat = lat0 + p.y / 111320;
                sitingMap.setView([lat, lng], Math.max(sitingMap.getZoom(), 17));
                sitingMarkers.eachLayer(layer => {
                    if (layer instanceof L.CircleMarker) {
                        const ll = layer.getLatLng();
                        if (Math.abs(ll.lat - lat) < 1e-5 && Math.abs(ll.lng - lng) < 1e-5) {
                            layer.openPopup();
                        }
                    }
                });
            }
        };
        container.appendChild(div);
    });
}
