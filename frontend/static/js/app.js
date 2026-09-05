/**
 * UrbanWind CFD — Application Controller
 */
'use strict';

// Debug: catch all errors
window.onerror = function(msg, src, line, col, err) {
    var el = document.getElementById('map');
    if (el) {
        el.innerHTML = '<div style=\"color:red;padding:20px;background:#111;\"><h3>JS Error</h3><pre>' +
            msg + '\nLine: ' + line + '\n' + (err ? err.stack : '') + '</pre></div>';
    }
    console.error('GLOBAL ERROR:', msg, 'line', line, err);
};

// ═══════════════════════════════════════════════════════════════════════════════
// Global state
// ═══════════════════════════════════════════════════════════════════════════════

const API = {
    base: '',
    sessionId: null,
    plan: null,
    selectedBuildingId: null,
};

let map = null;
let buildingLayers = {};
let bikeLayers = {};
let chatCollapsed = false;

// ═══════════════════════════════════════════════════════════════════════════════
// Initialization
// Dynamic update button (created by JS, no HTML artifact)
function getUpdateWindBtn() {
    var el = document.getElementById('btn-update-wind');
    if (!el) {
        el = document.createElement('button');
        el.id = 'btn-update-wind';
        el.className = 'btn btn-success';
        el.textContent = '更新风场';
        el.onclick = updateWindWithTrees;
        el.style.display = 'none';
        var container = document.getElementById('btn-update-wind-container');
        if (container) container.appendChild(el);
    }
    return el;
}

// ═══════════════════════════════════════════════════════════════════════════════

async function init() {
    // Create session
    try {
        const resp = await fetch('/api/session', { method: 'POST' });
        const data = await resp.json();
        API.sessionId = data.session_id;
        console.log('Session:', API.sessionId);
    } catch (e) {
        console.error('Failed to create session:', e);
        showToast('无法连接到服务器', 'error');
        return;
    }

    // 地图懒初始化：首次进入「城市风场模拟」模块时才创建（平台首页默认显示）
    getUpdateWindBtn();  // ensure button element exists

    // Check model status
    checkHealth();

    // 城市风场模拟是平台基底：默认进入（首次需初始化地图）
    switchScene('wind');

    // Auto-collapse chat on small screens
    if (window.innerWidth < 1200) {
        toggleChat(true);
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// 场景切换（城市风场模拟 = 平台基底；单车选址/无人机 = 可切入的应用场景）
// ═══════════════════════════════════════════════════════════════════════════════

let windMapReady = false;

function showView(name) {
    document.getElementById('wind-module').style.display = (name === 'wind') ? '' : 'none';
    document.getElementById('siting-module').style.display = (name === 'siting') ? '' : 'none';
    // 场景导航高亮
    var w = document.getElementById('scene-btn-wind');
    var s = document.getElementById('scene-btn-siting');
    if (w) w.classList.toggle('active', name === 'wind');
    if (s) s.classList.toggle('active', name === 'siting');
}

function switchScene(name) {
    if (name === 'wind') {
        showView('wind');
        ensureWindMap();
    } else if (name === 'siting') {
        showView('siting');
        if (window.ensureSitingMap) setTimeout(window.ensureSitingMap, 50);
    }
}

// 从应用场景返回风场基底
function goToWind() {
    switchScene('wind');
}

function ensureWindMap() {
    if (windMapReady) { map.invalidateSize(); return; }
    windMapReady = true;
    initMap();
    initTreeMode();   // 树木点击监听依赖 map，必须在 map 创建后绑定
}

function initMap() {
    map = L.map('map', {
        center: [32.06, 118.78],  // Default: Nanjing
        zoom: 15,
        zoomControl: false,
        attributionControl: true,
    });

    // Dark tile layer (Esri World Dark Gray — 免费无 key；CARTO cartocdn 2025 起提示 API key required，弃用)
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
        attribution: 'Tiles &copy; Esri — World Dark Gray Canvas',
        maxZoom: 20,
    }).addTo(map);

    // Zoom control (top-left)
    L.control.zoom({ position: 'topleft' }).addTo(map);

    // Scale control (bottom-left)
    L.control.scale({ position: 'bottomleft', metric: true, imperial: false }).addTo(map);

    // Invalidate size after layout changes
    setTimeout(() => map.invalidateSize(), 300);

    // Draw control for bbox selection
    const drawControl = new L.Control.Draw({
        draw: {
            polygon: false, polyline: false, circle: false, marker: false, circlemarker: false,
            rectangle: { shapeOptions: { color: '#06B6D4', weight: 2, fillOpacity: 0.1 } },
        },
        edit: false,
    });
    map.on(L.Draw.Event.CREATED, function(e) {
        const bounds = e.layer.getBounds();
        document.getElementById('osm-south').value = bounds.getSouth().toFixed(5);
        document.getElementById('osm-west').value = bounds.getWest().toFixed(5);
        document.getElementById('osm-north').value = bounds.getNorth().toFixed(5);
        document.getElementById('osm-east').value = bounds.getEast().toFixed(5);
        map.removeLayer(e.layer);
        map.removeControl(drawControl);
        document.getElementById('btn-draw-bbox').textContent = '✏️ 在地图上框选范围';
        document.getElementById('btn-draw-bbox').classList.remove('active');
        window._drawActive = false;
        showToast('范围已填入，点击「开始导入」', 'success');
    });
    window._drawControl = drawControl;
    window._drawActive = false;
}

async function flyToPlace() {
    const place = document.getElementById('osm-place').value.trim();
    if (!place) { showToast('请先输入地点名称', 'error'); return; }
    try {
        const resp = await fetch(`https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(place)}&format=json&limit=1`);
        const data = await resp.json();
        if (data.length > 0) {
            const lat = parseFloat(data[0].lat);
            const lon = parseFloat(data[0].lon);
            map.setView([lat, lon], 16);
            // Also fill bounding box if available
            if (data[0].boundingbox) {
                const bb = data[0].boundingbox;
                document.getElementById('osm-south').value = parseFloat(bb[0]).toFixed(5);
                document.getElementById('osm-north').value = parseFloat(bb[1]).toFixed(5);
                document.getElementById('osm-west').value = parseFloat(bb[2]).toFixed(5);
                document.getElementById('osm-east').value = parseFloat(bb[3]).toFixed(5);
            }
            showToast(`已定位: ${data[0].display_name.substring(0, 60)}`, 'success');
        } else {
            showToast('未找到该地点', 'error');
        }
    } catch(e) {
        showToast('定位失败: 网络错误', 'error');
    }
}

function toggleDrawMode() {
    if (window._drawActive) {
        map.removeControl(window._drawControl);
        document.getElementById('btn-draw-bbox').textContent = '✏️ 在地图上框选范围';
        document.getElementById('btn-draw-bbox').classList.remove('active');
        window._drawActive = false;
    } else {
        map.addControl(window._drawControl);
        new L.Draw.Rectangle(map, window._drawControl.options.draw.rectangle).enable();
        document.getElementById('btn-draw-bbox').textContent = '⏹ 停止框选';
        document.getElementById('btn-draw-bbox').classList.add('active');
        window._drawActive = true;
    }
}

async function checkHealth() {
    try {
        const resp = await fetch('/api/health');
        const data = await resp.json();
        const el = document.getElementById('status-model');
        const dot = document.getElementById('status-dot');
        if (data.model_loaded) {
            el.textContent = '模型: 已加载';
            dot.className = 'status-dot';
        } else if (data.model_available) {
            el.textContent = '模型: 可用（未加载）';
            dot.className = 'status-dot warning';
        } else {
            el.textContent = '模型: 未找到（离线模式）';
            dot.className = 'status-dot warning';
        }
    } catch (e) {
        document.getElementById('status-model').textContent = '模型: 检查失败';
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Map rendering
// ═══════════════════════════════════════════════════════════════════════════════

function renderPlanOnMap(plan) {
    console.log('renderPlanOnMap called, plan:', plan ? 'has plan' : 'no plan', 'features:', plan?.features?.length);
    if (!plan || !plan.features) { console.log('renderPlanOnMap: no plan/features, returning'); return; }

    // Clear existing layers
    Object.values(buildingLayers).forEach(l => map.removeLayer(l));
    Object.values(bikeLayers).forEach(l => map.removeLayer(l));
    buildingLayers = {};
    bikeLayers = {};

    plan.features.forEach(feature => {
        if (feature.category === 'building') {
            renderBuilding(feature);
        } else if (feature.category === 'bike_station') {
            renderBikeStation(feature);
        }
    });

    // Fit bounds — use both methods for reliability
    const allLayers = [...Object.values(buildingLayers), ...Object.values(bikeLayers)];
    if (allLayers.length > 0) {
        const group = L.featureGroup(allLayers);
        const bounds = group.getBounds();
        if (bounds.isValid()) {
            map.fitBounds(bounds, { padding: [50, 50], maxZoom: 18 });
        }
    }
    // Backup: fly to first building if bounds failed
    if (allLayers.length > 0) {
        const firstLayer = allLayers[0];
        const center = firstLayer.getBounds ? firstLayer.getBounds().getCenter() : firstLayer.getLatLng();
        if (center) map.setView(center, 16);
    }

    // Update UI counters
    updateSidebar(plan);
    updateStatusBar(plan);
    updateButtons(plan);
}

function renderBuilding(feature) {
    const coords = feature.geometry.coordinates[0];
    if (!coords || coords.length < 3) return;

    // Convert [lon, lat] to [lat, lon] for Leaflet
    const latlngs = coords.map(c => [c[1], c[0]]);

    const props = feature.properties || {};
    const btype = props.building_type || 'other';
    const height = props.height || 12;
    const name = props.name_zh || props.name || feature.id;

    // Color by building type
    const typeColors = {
        teaching: '#3B82F6', dormitory: '#8B5CF6', canteen: '#F59E0B',
        library: '#EC4899', office: '#64748B', lab: '#10B981',
        gymnasium: '#06B6D4', other: '#94A3B8',
    };
    const color = typeColors[btype] || typeColors.other;

    // Opacity by confidence
    const conf = props.confidence || 0.5;
    const fillOpacity = 0.25 + conf * 0.35;

    const layer = L.polygon(latlngs, {
        color: color,
        weight: 2,
        fillColor: color,
        fillOpacity: fillOpacity,
        opacity: 0.8,
    }).addTo(map);

    // Tooltip
    layer.bindTooltip(
        `<strong>${name}</strong><br>` +
        `${btypeLabel(btype)} · ${height.toFixed(0)}m · ${(props.num_floors||0)}层`,
        { direction: 'top', offset: [0, -5] }
    );

    // Popup
    layer.bindPopup(
        `<div style="font-size:13px">` +
        `<strong>${name}</strong><br>` +
        `类型: ${btypeLabel(btype)}<br>` +
        `高度: ${height.toFixed(1)} m<br>` +
        `层数: ${props.num_floors || '?'} 层<br>` +
        `置信度: ${(conf*100).toFixed(0)}%` +
        `</div>`
    );

    // Click → select building
    layer.on('click', () => selectBuilding(feature.id));

    // Highlight on hover
    layer.on('mouseover', () => {
        layer.setStyle({ weight: 4, opacity: 1 });
        if (!(feature.id === API.selectedBuildingId)) {
            layer.bringToFront();
        }
    });
    layer.on('mouseout', () => {
        if (feature.id !== API.selectedBuildingId) {
            layer.setStyle({ weight: 2, opacity: 0.8 });
        }
    });

    buildingLayers[feature.id] = layer;
}

function renderBikeStation(feature) {
    const coords = feature.geometry.coordinates[0];
    if (!coords) return;

    // Center of bike footprint
    const xs = coords.map(c => c[0]);
    const ys = coords.map(c => c[1]);
    const cx = xs.reduce((a,b) => a+b, 0) / xs.length;
    const cy = ys.reduce((a,b) => a+b, 0) / ys.length;

    const props = feature.properties || {};
    const cat = props.category || 'open';

    const catColors = {
        open: '#10B981', wake: '#F59E0B', canyon: '#06B6D4', corner: '#EC4899',
    };
    const color = catColors[cat] || catColors.open;

    const marker = L.circleMarker([cy, cx], {
        radius: 6,
        fillColor: color,
        color: '#fff',
        weight: 1.5,
        fillOpacity: 0.85,
    }).addTo(map);

    marker.bindTooltip(
        `<strong>${props.name || feature.id}</strong><br>` +
        `${catLabel(cat)}`,
        { direction: 'top' }
    );

    bikeLayers[feature.id] = marker;
}

function selectBuilding(buildingId) {
    API.selectedBuildingId = buildingId;

    // Update highlight on map
    Object.entries(buildingLayers).forEach(([id, layer]) => {
        if (id === buildingId) {
            layer.setStyle({ weight: 5, opacity: 1, dashArray: null });
            layer.bringToFront();
        } else {
            layer.setStyle({ weight: 2, opacity: 0.8 });
        }
    });

    // Update sidebar
    updateSidebarSelection(buildingId);
    showPropertiesPanel(buildingId);
}

// ═══════════════════════════════════════════════════════════════════════════════
// Sidebar
// ═══════════════════════════════════════════════════════════════════════════════

function updateSidebar(plan) {
    const list = document.getElementById('building-list');
    const count = document.getElementById('building-count');

    const buildings = plan.features.filter(f => f.category === 'building');
    count.textContent = `${buildings.length} 栋`;

    if (buildings.length === 0) {
        list.innerHTML = `
            <div class="empty-state" style="height:200px">
                <div class="empty-icon">📋</div>
                <p style="font-size:12px">暂无建筑数据<br>点击「导入」开始</p>
            </div>`;
        return;
    }

    const typeIcons = {
        teaching: '🏫', dormitory: '🏠', canteen: '🍽️', library: '📚',
        office: '🏢', lab: '🔬', gymnasium: '🏟️', other: '🏗️',
    };

    list.innerHTML = buildings.map(b => {
        const props = b.properties || {};
        const btype = props.building_type || 'other';
        const icon = typeIcons[btype] || '🏗️';
        const name = props.name_zh || props.name || b.id;
        const height = props.height || '?';
        const activeClass = b.id === API.selectedBuildingId ? ' active' : '';

        return `
        <div class="building-list-item${activeClass}" data-id="${b.id}" onclick="selectBuilding('${b.id}')">
            <div class="bld-icon ${btype}">${icon}</div>
            <div class="bld-info">
                <div class="bld-name">${escHtml(name)}</div>
                <div class="bld-meta">
                    <span>${btypeLabel(btype)}</span>
                    <span>${height}m</span>
                </div>
            </div>
        </div>`;
    }).join('');
}

function updateSidebarSelection(buildingId) {
    document.querySelectorAll('.building-list-item').forEach(el => {
        el.classList.toggle('active', el.dataset.id === buildingId);
    });
}

function showPropertiesPanel(buildingId) {
    const plan = API.plan;
    if (!plan) return;

    const feature = plan.features.find(f => f.id === buildingId);
    if (!feature || feature.category !== 'building') {
        document.getElementById('properties-panel').style.display = 'none';
        document.getElementById('btn-delete-building').style.display = 'none';
        return;
    }

    const props = feature.properties || {};
    const panel = document.getElementById('properties-panel');
    const content = document.getElementById('props-content');

    content.innerHTML = `
        <div class="prop-row">
            <label>名称</label>
            <input type="text" value="${escHtml(props.name_zh || '')}" onchange="updateProp('${buildingId}','name_zh',this.value)">
        </div>
        <div class="prop-row">
            <label>类型</label>
            <select onchange="updateProp('${buildingId}','building_type',this.value)">
                ${['teaching','dormitory','canteen','library','office','lab','gymnasium','other']
                    .map(t => `<option value="${t}" ${props.building_type===t?'selected':''}>${btypeLabel(t)}</option>`)
                    .join('')}
            </select>
        </div>
        <div class="prop-row">
            <label>高度 (m)</label>
            <input type="number" value="${props.height||12}" step="0.5" min="1"
                   onchange="updateProp('${buildingId}','height',parseFloat(this.value))">
        </div>
        <div class="prop-row">
            <label>层数</label>
            <input type="number" value="${props.num_floors||4}" min="1" max="200"
                   onchange="updateProp('${buildingId}','num_floors',parseInt(this.value))">
        </div>
        <div class="prop-row">
            <label>屋顶类型</label>
            <select onchange="updateProp('${buildingId}','roof_type',this.value)">
                ${['flat','pitched','arched','unknown']
                    .map(t => `<option value="${t}" ${props.roof_type===t?'selected':''}>${t}</option>`)
                    .join('')}
            </select>
        </div>
        <div class="prop-row">
            <label>置信度</label>
            <span style="font-size:12px;color:var(--text-muted)">${((props.confidence||0)*100).toFixed(0)}%</span>
        </div>
        <div class="prop-row">
            <label>数据来源</label>
            <span style="font-size:12px;color:var(--text-muted)">${props.source||'?'}</span>
        </div>
    `;

    panel.style.display = 'block';
    document.getElementById('btn-delete-building').style.display = 'inline-flex';
}

async function updateProp(buildingId, key, value) {
    try {
        await fetch(`/api/plan/building/${buildingId}?session_id=${API.sessionId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ [key]: value }),
        });
        // Update local state
        const feature = API.plan.features.find(f => f.id === buildingId);
        if (feature) {
            feature.properties[key] = value;
            // Re-render map
            renderPlanOnMap(API.plan);
            selectBuilding(buildingId);
        }
    } catch (e) {
        showToast('更新失败', 'error');
    }
}

async function deleteSelectedBuilding() {
    if (!API.selectedBuildingId) return;
    if (!confirm('确定要删除这栋建筑吗？')) return;

    try {
        await fetch(
            `/api/plan/building/${API.selectedBuildingId}?session_id=${API.sessionId}`,
            { method: 'DELETE' }
        );
        // Update local state
        API.plan.features = API.plan.features.filter(f => f.id !== API.selectedBuildingId);
        API.selectedBuildingId = null;
        renderPlanOnMap(API.plan);
        document.getElementById('properties-panel').style.display = 'none';
        showToast('建筑已删除', 'success');
    } catch (e) {
        showToast('删除失败', 'error');
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Status bar & buttons
// ═══════════════════════════════════════════════════════════════════════════════

function updateStatusBar(plan) {
    const buildings = plan.features.filter(f => f.category === 'building');
    const bikes = plan.features.filter(f => f.category === 'bike_station');
    document.getElementById('status-buildings').textContent = `建筑: ${buildings.length}`;
    document.getElementById('status-bikes').textContent = `单车点: ${bikes.length}`;
    document.getElementById('status-text').textContent = '已加载';
}

function updateButtons(plan) {
    const hasBuildings = plan.features.some(f => f.category === 'building');
    document.getElementById('btn-enrich').disabled = !hasBuildings;
    document.getElementById('btn-generate').disabled = !hasBuildings;
    document.getElementById('btn-tree-mode').style.display = 'none';  // shown after prediction
}

// ═══════════════════════════════════════════════════════════════════════════════
// Chat
// ═══════════════════════════════════════════════════════════════════════════════

function toggleChat(forceCollapse) {
    const panel = document.getElementById('chat-panel');
    if (forceCollapse !== undefined) {
        chatCollapsed = forceCollapse;
    } else {
        chatCollapsed = !chatCollapsed;
    }
    panel.classList.toggle('collapsed', chatCollapsed);
}

async function sendChat() {
    const input = document.getElementById('chat-input');
    const instruction = input.value.trim();
    if (!instruction) return;

    // Add user message to chat
    addChatMessage('user', instruction);
    input.value = '';
    input.disabled = true;

    // Check if it's an import command
    if (isImportCommand(instruction)) {
        await handleImportCommand(instruction);
        input.disabled = false;
        input.focus();
        return;
    }

    // Send to API
    try {
        const resp = await fetch(`/api/edit?session_id=${API.sessionId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ instruction }),
        });
        const data = await resp.json();

        if (data.success) {
            addChatMessage('assistant', data.message);
            // Update plan
            API.plan = data.plan;
            renderPlanOnMap(data.plan);
        } else {
            addChatMessage('error', data.message);
        }
    } catch (e) {
        addChatMessage('error', '操作失败，请检查服务器连接');
    }

    input.disabled = false;
    input.focus();
}

function addChatMessage(role, content) {
    const container = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = `chat-msg ${role}`;
    div.textContent = content;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function isImportCommand(text) {
    const patterns = [
        /导入\s*(?:OSM|地图)?\s*(.+)/,
        /下载\s*(.+?)的?\s*(?:建筑|地图)?/,
        /加载\s*(.+?)的?\s*(?:建筑|地图)?/,
    ];
    return patterns.some(p => p.test(text));
}

async function handleImportCommand(text) {
    let place = text;
    // Extract place name
    const m = text.match(/(?:导入|下载|加载)\s*(?:OSM|地图)?\s*(.+?)(?:的?\s*(?:建筑|地图))?$/);
    if (m) place = m[1].trim();

    if (!place) {
        addChatMessage('error', '请指定地点名称，例如：「导入南京大学鼓楼校区」');
        return;
    }

    addChatMessage('system', `正在从 OpenStreetMap 下载「${place}」的建筑数据...`);

    try {
        const resp = await fetch(`/api/import/osm?session_id=${API.sessionId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ place }),
        });
        const data = await resp.json();

        if (data.success) {
            API.plan = data.plan;
            renderPlanOnMap(data.plan);
            addChatMessage('assistant', `✅ 已导入「${place}」：${data.num_buildings} 栋建筑。需要我帮你推断建筑属性吗？`);
            showToast(`成功导入 ${data.num_buildings} 栋建筑`, 'success');
        } else {
            addChatMessage('error', `导入失败: ${data.detail || '未知错误'}`);
        }
    } catch (e) {
        addChatMessage('error', `导入失败: ${e.message}`);
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Import modal
// ═══════════════════════════════════════════════════════════════════════════════

let currentImportTab = 'osm';

function showImportModal() {
    document.getElementById('import-modal').style.display = 'flex';
    switchImportTab(currentImportTab);
}

function hideImportModal() {
    document.getElementById('import-modal').style.display = 'none';
}

function switchImportTab(tab) {
    currentImportTab = tab;
    document.querySelectorAll('.import-tab').forEach(b => {
        b.classList.toggle('active', false);
        b.style.background = '';
    });
    document.querySelector(`.import-tab[data-tab="${tab}"]`).style.background = 'var(--bg-tertiary)';
    document.querySelector(`.import-tab[data-tab="${tab}"]`).classList.add('active');

    document.querySelectorAll('.import-tab-content').forEach(c => c.style.display = 'none');
    document.getElementById(`tab-${tab}`).style.display = 'block';
}

function updateImportProgress(pct, text) {
    const bar = document.getElementById('import-progress-bar');
    const pctEl = document.getElementById('import-progress-pct');
    const textEl = document.getElementById('import-progress-text');
    const progress = document.getElementById('import-progress');
    progress.style.display = 'block';
    bar.style.width = pct + '%';
    pctEl.textContent = Math.round(pct) + '%';
    textEl.textContent = text;
}

async function executeImport() {
    const btn = document.getElementById('btn-import-exec');
    const cancelBtn = document.getElementById('btn-import-cancel');
    btn.disabled = true;
    cancelBtn.disabled = true;

    // Reset progress bar
    updateImportProgress(0, '准备导入...');

    try {
        // Simulate progress during API call
        const progressInterval = setInterval(() => {
            const bar = document.getElementById('import-progress-bar');
            const cur = parseFloat(bar.style.width) || 0;
            if (cur < 85) updateImportProgress(cur + 3, '正在获取数据...');
        }, 300);

        if (currentImportTab === 'osm') {
            updateImportProgress(10, '正在连接 OpenStreetMap...');
            await importOSM();
        } else if (currentImportTab === 'dxf') {
            updateImportProgress(10, '正在解析 DXF 文件...');
            await importDXF();
        } else if (currentImportTab === 'manual') {
            updateImportProgress(10, '正在解析建筑描述...');
            await importManual();
        } else if (currentImportTab === 'gaode') {
            updateImportProgress(10, '正在连接高德地图...');
            await importGaode();
        } else if (currentImportTab === 'overture') {
            updateImportProgress(10, '正在连接 Overture Maps...');
            await importOverture();
        } else if (currentImportTab === 'msbuildings') {
            updateImportProgress(10, '正在连接 Microsoft 建筑数据...');
            await importMSBuildings();
        }

        clearInterval(progressInterval);
        updateImportProgress(90, '正在渲染地图...');

        // Brief pause to show completion
        await new Promise(r => setTimeout(r, 400));
        updateImportProgress(100, '导入完成！');

        await new Promise(r => setTimeout(r, 300));
        hideImportModal();
        // Reset for next time
        setTimeout(() => {
            updateImportProgress(0, '准备导入...');
            document.getElementById('import-progress').style.display = 'none';
        }, 200);

    } catch (e) {
        clearInterval(progressInterval);
        updateImportProgress(0, '导入失败');
        document.getElementById('import-progress-bar').style.background = '#ef4444';
        showToast(`导入失败: ${e.message}`, 'error');
        // Reset after delay
        setTimeout(() => {
            document.getElementById('import-progress-bar').style.background = '';
            document.getElementById('import-progress').style.display = 'none';
        }, 2000);
    } finally {
        btn.disabled = false;
        cancelBtn.disabled = false;
    }
}

async function importOSM() {
    const place = document.getElementById('osm-place').value.trim();
    const south = document.getElementById('osm-south').value;
    const west = document.getElementById('osm-west').value;
    const north = document.getElementById('osm-north').value;
    const east = document.getElementById('osm-east').value;

    let body = {};
    if (place) {
        body.place = place;
    } else if (south && west && north && east) {
        body.bbox = [parseFloat(south), parseFloat(west), parseFloat(north), parseFloat(east)];
    } else {
        throw new Error('请输入地点名称或经纬度范围');
    }

    const resp = await fetch(`/api/import/osm?session_id=${API.sessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!data.success) throw new Error(data.detail);

    API.plan = data.plan;
    renderPlanOnMap(data.plan);
    showToast(`成功导入 ${data.num_buildings} 栋建筑`, 'success');
}

async function importDXF() {
    const fileInput = document.getElementById('dxf-file');
    const file = fileInput.files[0];
    if (!file) throw new Error('请选择 DXF 文件');

    const formData = new FormData();
    formData.append('file', file);

    const resp = await fetch(`/api/import/dxf?session_id=${API.sessionId}`, {
        method: 'POST',
        body: formData,
    });
    const data = await resp.json();
    if (!data.success) throw new Error(data.detail);

    API.plan = data.plan;
    renderPlanOnMap(data.plan);
    showToast(`成功导入 ${data.num_buildings} 栋建筑`, 'success');
}

async function importManual() {
    const text = document.getElementById('manual-text').value.trim();
    if (!text) throw new Error('请输入建筑描述');

    const resp = await fetch(`/api/import/manual?session_id=${API.sessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(text),  // Send as string in body directly
    });
    const data = await resp.json();
    if (!data.success) throw new Error(data.detail);

    API.plan = data.plan;
    renderPlanOnMap(data.plan);

    if (data.needs_llm_enrichment) {
        showToast('文本已导入，建议运行「智能推断」补全属性', 'success');
    } else {
        showToast(`成功导入 ${data.num_buildings} 栋建筑`, 'success');
    }
}

async function importMSBuildings() {
    const place = document.getElementById('ms-place').value.trim();
    const south = document.getElementById('ms-south').value;
    const west = document.getElementById('ms-west').value;
    const north = document.getElementById('ms-north').value;
    const east = document.getElementById('ms-east').value;

    let body = {};
    if (place) {
        body.place = place;
    } else if (south && west && north && east) {
        body.bbox = [parseFloat(south), parseFloat(west), parseFloat(north), parseFloat(east)];
    } else {
        throw new Error('请输入地点名称或经纬度范围');
    }

    const resp = await fetch(`/api/import/msbuildings?session_id=${API.sessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!data.success) throw new Error(data.detail);

    API.plan = data.plan;
    renderPlanOnMap(data.plan);
    showToast(`成功导入 ${data.num_buildings} 栋建筑 (Microsoft)`, 'success');
}

function fillOvertureFromOSM() {
    document.getElementById('ov-south').value = document.getElementById('osm-south').value;
    document.getElementById('ov-west').value = document.getElementById('osm-west').value;
    document.getElementById('ov-north').value = document.getElementById('osm-north').value;
    document.getElementById('ov-east').value = document.getElementById('osm-east').value;
    showToast('已复制 OSM 经纬度', 'success');
}

async function flyToPlaceOverture() {
    const place = document.getElementById('ov-place').value.trim();
    if (!place) { showToast('请先输入地点名称', 'error'); return; }
    try {
        const resp = await fetch(`https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(place)}&format=json&limit=1`);
        const data = await resp.json();
        if (data.length > 0) {
            const lat = parseFloat(data[0].lat);
            const lon = parseFloat(data[0].lon);
            map.setView([lat, lon], 16);
            if (data[0].boundingbox) {
                const bb = data[0].boundingbox;
                document.getElementById('ov-south').value = parseFloat(bb[0]).toFixed(5);
                document.getElementById('ov-north').value = parseFloat(bb[1]).toFixed(5);
                document.getElementById('ov-west').value = parseFloat(bb[2]).toFixed(5);
                document.getElementById('ov-east').value = parseFloat(bb[3]).toFixed(5);
            }
            showToast(`已定位: ${data[0].display_name.substring(0, 60)}`, 'success');
        } else { showToast('未找到该地点', 'error'); }
    } catch(e) { showToast('定位失败', 'error'); }
}

async function importOverture() {
    const place = document.getElementById('ov-place').value.trim();
    const south = document.getElementById('ov-south').value;
    const west = document.getElementById('ov-west').value;
    const north = document.getElementById('ov-north').value;
    const east = document.getElementById('ov-east').value;

    let body = {};
    if (place && !south) {
        // Geocode first, then use that bbox
        updateImportProgress(5, '正在定位地址...');
        const resp = await fetch(`https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(place)}&format=json&limit=1`);
        const data = await resp.json();
        if (data.length > 0 && data[0].boundingbox) {
            const bb = data[0].boundingbox;
            body.bbox = [parseFloat(bb[0]), parseFloat(bb[2]), parseFloat(bb[1]), parseFloat(bb[3])];
        } else {
            throw new Error('无法定位该地点，请手动输入经纬度');
        }
    } else if (south && west && north && east) {
        body.bbox = [parseFloat(south), parseFloat(west), parseFloat(north), parseFloat(east)];
    } else {
        throw new Error('请输入地点名称或经纬度范围');
    }
    updateImportProgress(30, '正在下载 Overture 建筑数据（首次较慢）...');
    const resp = await fetch(`/api/import/overture?session_id=${API.sessionId}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!data.success) throw new Error(data.detail);
    API.plan = data.plan;
    renderPlanOnMap(data.plan);
    const cached = data.metadata?.cached ? '(缓存)' : '(新下载)';
    showToast(`成功导入 ${data.num_buildings} 栋建筑 ${cached}`, 'success');
}

async function flyToPlaceGaode() {
    const place = document.getElementById('gaode-place').value.trim();
    if (!place) { showToast('请先输入地点名称', 'error'); return; }
    try {
        const resp = await fetch(`https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(place)}&format=json&limit=1`);
        const data = await resp.json();
        if (data.length > 0) {
            map.setView([parseFloat(data[0].lat), parseFloat(data[0].lon)], 16);
            showToast(`已定位: ${data[0].display_name.substring(0, 50)}`, 'success');
        } else { showToast('未找到', 'error'); }
    } catch(e) { showToast('定位失败', 'error'); }
}

async function importGaode() {
    const place = document.getElementById('gaode-place').value.trim();
    const keywords = document.getElementById('gaode-keywords').value.trim() || '学校';
    if (!place) throw new Error('请输入地点名称');
    const resp = await fetch(`/api/import/gaode?session_id=${API.sessionId}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ place, keywords }),
    });
    const data = await resp.json();
    if (!data.success) throw new Error(data.detail);
    API.plan = data.plan;
    renderPlanOnMap(data.plan);
    // Force zoom after short delay to ensure layers are added
    setTimeout(() => {
        const allLayers = [...Object.values(buildingLayers)];
        if (allLayers.length > 0) {
            const group = L.featureGroup(allLayers);
            map.fitBounds(group.getBounds(), { padding: [50, 50], maxZoom: 18 });
        }
    }, 500);
    showToast(`成功导入 ${data.num_buildings} 栋建筑 (高德)`, 'success');
}

// ═══════════════════════════════════════════════════════════════════════════════
// LLM Enrichment
// ═══════════════════════════════════════════════════════════════════════════════

async function runLLMEnrich() {
    if (!API.plan) return;
    showToast('正在运行 LLM 智能推断...', 'success');
    showMapLoading(true);

    try {
        const resp = await fetch(`/api/llm/enrich?session_id=${API.sessionId}`, {
            method: 'POST',
        });
        const data = await resp.json();

        if (data.success) {
            API.plan = data.plan;
            renderPlanOnMap(data.plan);
            const msg = data.warning
                ? `部分推断完成（${data.warning}）`
                : `LLM 智能推断完成！已补全 ${data.num_buildings} 栋建筑的属性`;
            showToast(msg, 'success');
        }
    } catch (e) {
        showToast(`推断失败: ${e.message}`, 'error');
    } finally {
        showMapLoading(false);
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Generate modal
// ═══════════════════════════════════════════════════════════════════════════════

// ── Output directory persistence ──────────────────────────────────
const DEFAULT_OUTPUT = 'E:\\\\UrbanWind\\\\cfd_cases';

function getOutputDir() {
    return localStorage.getItem('urbanwind_output_dir') || DEFAULT_OUTPUT;
}

function saveOutputDir(dir) {
    if (dir) localStorage.setItem('urbanwind_output_dir', dir);
    else localStorage.removeItem('urbanwind_output_dir');
}

function resetOutputDir() {
    localStorage.removeItem('urbanwind_output_dir');
    document.getElementById('gen-output-dir').value = DEFAULT_OUTPUT;
    updateWslPathPreview();
}

function updateWslPathPreview() {
    const dir = document.getElementById('gen-output-dir').value.trim() || DEFAULT_OUTPUT;
    const name = document.getElementById('gen-name').value.trim() || 'my_campus';
    const drive = dir.charAt(0).toLowerCase();
    const rest = dir.replace(/\\/g, '/').replace(/^.:/, '');
    document.getElementById('gen-wsl-path').textContent = `cd /mnt/${drive}${rest}/${name}`;
}

function showGenerateModal() {
    if (!API.plan) return;
    document.getElementById('gen-output-dir').value = getOutputDir();
    const savedCellSize = localStorage.getItem('urbanwind_cell_size') || '2';
    document.getElementById('gen-cell-size').value = savedCellSize;
    document.getElementById('gen-cell-size-val').textContent = savedCellSize + 'm';
    document.getElementById('generate-modal').style.display = 'flex';
    updateWslPathPreview();
    // Live preview as user types
    document.getElementById('gen-output-dir').oninput = () => { saveOutputDir(document.getElementById('gen-output-dir').value); updateWslPathPreview(); };
    document.getElementById('gen-name').oninput = updateWslPathPreview;
}

function hideGenerateModal() {
    document.getElementById('generate-modal').style.display = 'none';
}

async function executeGenerate() {
    const btn = document.getElementById('btn-generate-exec');
    btn.disabled = true;
    btn.textContent = '生成中...';

    const outputDir = document.getElementById('gen-output-dir').value.trim() || DEFAULT_OUTPUT;
    saveOutputDir(outputDir);

    const cellSize = parseFloat(document.getElementById('gen-cell-size').value) || 2.0;
    localStorage.setItem('urbanwind_cell_size', cellSize);

    const body = {
        case_name: document.getElementById('gen-name').value.trim() || 'my_campus',
        wind_speed: parseFloat(document.getElementById('gen-speed').value) || 5.0,
        wind_direction: document.getElementById('gen-direction').value,
        n_bikes: parseInt(document.getElementById('gen-bikes').value) || 20,
        output_dir: outputDir,
        cell_size: cellSize,
    };

    try {
        const resp = await fetch(`/api/generate?session_id=${API.sessionId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();

        if (data.success) {
            hideGenerateModal();
            const msg = `✅ CFD 案例已生成！\n` +
                `目录: ${data.case_dir}\n` +
                `WSL 路径: ${data.wsl_path}\n` +
                `建筑: ${data.num_buildings} | 单车点: ${data.num_bikes}`;
            showToast('案例生成成功！', 'success');
            addChatMessage('system', msg);

            // 保存 case_dir 用于预测
            API.lastCaseDir = data.case_dir;
            API.windDirection = body.wind_direction;
            API.inletSpeed = body.wind_speed;
            // 显示预测按钮
            document.getElementById('btn-predict-case').style.display = 'inline-block';
        } else {
            showToast(`生成失败: ${data.detail}`, 'error');
        }
    } catch (e) {
        showToast(`生成失败: ${e.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '生成案例';
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Toast notifications
// ═══════════════════════════════════════════════════════════════════════════════

function showToast(message, type) {
    type = type || '';
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    // Auto-remove
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-12px)';
        toast.style.transition = 'all 250ms ease-in';
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

// ═══════════════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════════════

function showMapLoading(show) {
    document.getElementById('map-loading').style.display = show ? 'flex' : 'none';
}

function btypeLabel(type) {
    const labels = {
        teaching: '教学楼', dormitory: '宿舍', canteen: '食堂',
        library: '图书馆', office: '办公楼', lab: '实验楼',
        gymnasium: '体育馆', other: '其他',
    };
    return labels[type] || type;
}

function catLabel(cat) {
    const labels = {
        open: '开敞区', wake: '尾流区', canyon: '街道峡谷', corner: '转角区',
    };
    return labels[cat] || cat;
}

function escHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ═══════════════════════════════════════════════════════════════════════════════
// Keyboard shortcuts
// ═══════════════════════════════════════════════════════════════════════════════

document.addEventListener('keydown', (e) => {
    // Ctrl+K → focus chat
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        document.getElementById('chat-input').focus();
    }
    // Ctrl+Z → undo
    if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey && document.activeElement === document.body) {
        e.preventDefault();
        undoEdit();
    }
    // Escape → close modals, deselect
    if (e.key === 'Escape') {
        hideImportModal();
        hideGenerateModal();
        if (API.selectedBuildingId) {
            API.selectedBuildingId = null;
            Object.values(buildingLayers).forEach(l => l.setStyle({ weight: 2, opacity: 0.8 }));
            document.getElementById('properties-panel').style.display = 'none';
            updateSidebarSelection(null);
        }
    }
});

async function undoEdit() {
    try {
        const resp = await fetch(`/api/edit/undo?session_id=${API.sessionId}`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            API.plan = data.plan;
            renderPlanOnMap(data.plan);
            showToast(data.message, 'success');
        }
    } catch (e) {
        // Ignore
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Click-outside to close modals
// ═══════════════════════════════════════════════════════════════════════════════

document.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal-overlay')) {
        e.target.style.display = 'none';
    }
});

// ═══════════════════════════════════════════════════════════════════════════════
// Wind Field Prediction + Tree Placement
// ═══════════════════════════════════════════════════════════════════════════════

let windOverlay = null;       // Leaflet canvas overlay
let windData = null;          // last prediction result
let treeMode = false;
let pendingTree = null;       // tree being placed
let placedTrees = [];         // [{cx, cy, length, angle_deg}]
let treeMarkers = [];         // Leaflet markers for placed trees

// Step-guided flow: 导入 → 智能推断/生成CFD → 风场预测 → 放置树木

// ── 风场预测参数弹窗（通用）──────────────────────────────────────────────

let windPredictSource = null;   // 'fromCase' | 'fromSession'

function showWindParamsModal(source) {
    windPredictSource = source;
    // 预填上次参数
    var dirSel = document.getElementById('wp-direction');
    if (API.windDirection) dirSel.value = API.windDirection;
    var spdIn = document.getElementById('wp-speed');
    if (API.inletSpeed) spdIn.value = API.inletSpeed;
    document.getElementById('wind-params-modal').style.display = 'flex';
}

function hideWindParamsModal() {
    document.getElementById('wind-params-modal').style.display = 'none';
}

async function runWindPrediction() {
    var dir = document.getElementById('wp-direction').value;
    var spd = parseFloat(document.getElementById('wp-speed').value);
    if (!spd || spd <= 0) { showToast('请输入有效的入口风速', 'error'); return; }
    API.windDirection = dir;
    API.inletSpeed = spd;
    hideWindParamsModal();
    if (windPredictSource === 'fromCase') {
        await predictFromCase();
    } else if (windPredictSource === 'fromSession') {
        await predictWindField();
    }
}

async function predictFromCase() {
    if (!API.lastCaseDir) { showToast('请先生成 CFD 案例', 'warning'); return; }

    // Show progress
    var progBar = document.getElementById('predict-progress');
    var progFill = document.getElementById('predict-progress-fill');
    progBar.style.display = 'block';
    progFill.style.width = '0%';
    var progress = 0;
    var timer = setInterval(function() {
        progress = Math.min(progress + Math.random() * 15, 90);
        progFill.style.width = progress + '%';
    }, 500);

    try {
        const resp = await fetch('/api/predict-from-case', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                case_dir: API.lastCaseDir,
                wind_direction: API.windDirection || 'N',
                inlet_speed: API.inletSpeed || 5.0
            })
        });
        clearInterval(timer);
        progFill.style.width = '100%';

        const data = await resp.json();
        if (!data.success) { showToast(data.detail || '预测失败', 'error'); progBar.style.display = 'none'; return; }

        windData = data;
        // Show server-rendered image
        var panel = document.getElementById('wind-panel');
        var imgContainer = document.getElementById('wind-img-container');
        imgContainer.innerHTML = `<img src="${data.image_base64}" style="width:100%;max-width:500px;border-radius:8px;">`;
        panel.style.display = 'block';
        document.getElementById('wind-speed-range').textContent =
            `${data.speed_min.toFixed(1)} - ${data.speed_max.toFixed(1)} m/s`;

        document.getElementById('btn-tree-mode').disabled = false;
        document.getElementById('btn-tree-mode').style.display = 'inline-block';
        showToast(`风场预测完成! 风速: ${data.speed_min.toFixed(1)}-${data.speed_max.toFixed(1)} m/s`, 'success');
        setTimeout(function() { progBar.style.display = 'none'; }, 500);
    } catch (e) {
        clearInterval(timer);
        progBar.style.display = 'none';
        showToast('预测失败: ' + e.message, 'error');
    }
}

async function predictWindField() {
    // Ask for wind params first
    const dir = prompt('风向 (N/S/E/W):', API.windDirection || 'N');
    if (!dir) return;
    const spd = parseFloat(prompt('入口风速 (m/s):', API.inletSpeed || '5.0'));
    if (!spd || spd <= 0) return;
    API.windDirection = dir.toUpperCase();
    API.inletSpeed = spd;

    showToast(`正在 GNN 预测 (${API.windDirection}风 ${API.inletSpeed} m/s)...`, 'info');
    try {
        const resp = await fetch(`/api/predict-wind?session_id=${API.sessionId}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                wind_direction: API.windDirection,
                inlet_speed: API.inletSpeed
            })
        });
        const data = await resp.json();
        if (!data.success) { showToast(data.detail || '预测失败', 'error'); return; }
        windData = data;
        renderWindOverlay(data);
        document.getElementById('btn-update-wind').style.display = 'inline-block';
        showToast(`风场预测完成! 风速范围: ${data.speed_min.toFixed(1)} - ${data.speed_max.toFixed(1)} m/s`, 'success');
    } catch (e) {
        showToast('预测失败: ' + e.message, 'error');
    }
}

function renderWindOverlay(data) {
    if (windOverlay) { map.removeLayer(windOverlay); }
    if (window._windCanvas) { window._windCanvas.remove(); }

    const grid = data.speed_grid;
    const H = grid.length, W = grid[0].length;
    const vmin = data.speed_min, vmax = data.speed_max, vrange = vmax - vmin || 1;

    // Render to canvas
    const canvas = document.createElement('canvas');
    canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext('2d');
    const imgData = ctx.createImageData(W, H);

    for (let y = 0; y < H; y++) {
        for (let x = 0; x < W; x++) {
            const v = grid[y][x];
            const idx = (y * W + x) * 4;
            if (v === null || v === undefined || isNaN(v)) {
                imgData.data[idx+3] = 0;
            } else {
                const t = Math.max(0, Math.min(1, (v - vmin) / vrange));
                let r, g, b;
                if (t < 0.33) { const s=t/0.33; r=30; g=100+Math.round(155*s); b=200+Math.round(55*s); }
                else if (t < 0.66) { const s=(t-0.33)/0.33; r=30+Math.round(225*s); g=255-Math.round(55*s); b=255-Math.round(200*s); }
                else { const s=(t-0.66)/0.34; r=255; g=200-Math.round(150*s); b=55-Math.round(55*s); }
                imgData.data[idx+0] = r; imgData.data[idx+1] = g; imgData.data[idx+2] = b;
                imgData.data[idx+3] = 140;
            }
        }
    }
    ctx.putImageData(imgData, 0, 0);

    // Use API lat/lng bounds for geo-referencing (same coords as buildings)
    let bounds;
    if (data.grid_bounds_latlng) {
        const b = data.grid_bounds_latlng; // [min_lng, min_lat, max_lng, max_lat]
        bounds = [[b[1], b[0]], [b[3], b[2]]]; // [[south, west], [north, east]]
    } else {
        // Fallback: use map center + local grid size
        const c = map.getCenter();
        const b = data.grid_bounds; // [x_min, y_min, x_max, y_max] in local meters
        const dx = (b[2] - b[0]) / 111320 / Math.cos(c.lat * Math.PI/180);
        const dy = (b[3] - b[1]) / 111320;
        bounds = [[c.lat - dy/2, c.lng - dx/2], [c.lat + dy/2, c.lng + dx/2]];
    }
    // Show in sidebar panel (no map alignment needed)
    var panel = document.getElementById('wind-panel');
    var imgContainer = document.getElementById('wind-img-container');
    imgContainer.innerHTML = '';
    imgContainer.appendChild(canvas);
    panel.style.display = 'block';
    document.getElementById('wind-speed-range').textContent =
        (data.speed_min || 0).toFixed(1) + ' - ' + (data.speed_max || 0).toFixed(1) + ' m/s';
    // Also add as map overlay for reference
    if (data.grid_bounds_latlng) {
        var lb = data.grid_bounds_latlng;
        windOverlay = L.imageOverlay(canvas.toDataURL(), [[lb[1], lb[0]], [lb[3], lb[2]]], {opacity: 0.4}).addTo(map);
    }

    // Show colorbar
    document.getElementById('wind-colorbar').style.display = 'flex';
    document.getElementById('colorbar-min').textContent = vmin.toFixed(1);
    document.getElementById('colorbar-max').textContent = vmax.toFixed(1);
    drawColorbar(vmin, vmax);
}

function drawColorbar(vmin, vmax) {
    const c = document.getElementById('colorbar-canvas');
    const ctx = c.getContext('2d');
    for (let i = 0; i < 200; i++) {
        const t = 1 - i / 199;
        let r, g, b;
        if (t < 0.33) { const s=t/0.33; r=30; g=100+Math.round(155*s); b=200+Math.round(55*s); }
        else if (t < 0.66) { const s=(t-0.33)/0.33; r=30+Math.round(225*s); g=255-Math.round(55*s); b=255-Math.round(200*s); }
        else { const s=(t-0.66)/0.34; r=255; g=200-Math.round(150*s); b=55-Math.round(55*s); }
        ctx.fillStyle = `rgb(${r},${g},${b})`;
        ctx.fillRect(0, i, 20, 1);
    }
}

// ── Tree placement ───────────────────────────────────────────────────────

function toggleTreeMode() {
    treeMode = !treeMode;
    const btn = document.getElementById('btn-tree-mode');
    btn.classList.toggle('active', treeMode);
    map.getContainer().style.cursor = treeMode ? 'crosshair' : '';
    if (!treeMode) { cancelTree(); }
}

function initTreeMode() {
if (!map) return;
map.on('click', function(e) {
    if (!treeMode) return;
    const popup = document.getElementById('tree-popup');
    popup.style.display = 'block';
    // Position near click, clamped to viewport
    const x = Math.min(e.originalEvent.clientX, window.innerWidth - 250);
    const y = Math.min(e.originalEvent.clientY, window.innerHeight - 200);
    popup.style.left = x + 'px';
    popup.style.top = y + 'px';
    pendingTree = { cx: e.latlng.lng, cy: e.latlng.lat };
    document.getElementById('tree-length').value = 20;
    document.getElementById('tree-angle').value = 0;
    document.getElementById('tree-length-val').textContent = '20m';
    document.getElementById('tree-angle-val').textContent = '0°';
});

document.getElementById('tree-length').addEventListener('input', function() {
    document.getElementById('tree-length-val').textContent = this.value + 'm';
});
document.getElementById('tree-angle').addEventListener('input', function() {
    document.getElementById('tree-angle-val').textContent = this.value + '°';
});

// Tree popup buttons (bound inside initTreeMode for scope access)
var btnConfirm = document.getElementById('btn-confirm-tree');
var btnCancel = document.getElementById('btn-cancel-tree');
if (btnConfirm) btnConfirm.addEventListener('click', function() {
    if (!pendingTree) return;
    var len = parseFloat(document.getElementById('tree-length').value);
    var theta = parseFloat(document.getElementById('tree-angle').value);
    placedTrees.push({ cx: pendingTree.cx, cy: pendingTree.cy, length: len, angle_deg: theta });
    drawPlacedTrees();
    document.getElementById('tree-popup').style.display = 'none';
    document.getElementById('btn-save-trees').style.display = 'inline-block';
    document.getElementById('btn-update-wind').style.display = 'none';
    pendingTree = null;
    showToast('Placed tree: ' + len + 'm, ' + theta + '°', 'info');
});
if (btnCancel) btnCancel.addEventListener('click', function() {
    document.getElementById('tree-popup').style.display = 'none';
    pendingTree = null;
});
}  // end initTreeMode


function confirmTree() {
    if (!pendingTree) return;
    const len = parseFloat(document.getElementById('tree-length').value);
    const theta = parseFloat(document.getElementById('tree-angle').value);
    placedTrees.push({ cx: pendingTree.cx, cy: pendingTree.cy, length: len, angle_deg: theta });
    drawPlacedTrees();
    document.getElementById('tree-popup').style.display = 'none';
    pendingTree = null;
    document.getElementById('btn-save-trees').style.display = 'inline-block';
    showToast(`放置了 ${len}m 行道树 (θ=${theta}°)，点击"保存树木"`, 'info');
}

function cancelTree() {
    document.getElementById('tree-popup').style.display = 'none';
    pendingTree = null;
}

function drawPlacedTrees() {
    treeMarkers.forEach(m => map.removeLayer(m));
    treeMarkers = [];
    placedTrees.forEach((t, i) => {
        const rad = t.angle_deg * Math.PI / 180;
        const halfLen = t.length / 2;
        // Convert meters to lat/lng
        const latlng = L.latLng(t.cy, t.cx);
        // Approximate: 1m ≈ 1/111320 deg lat, 1/111320*cos(lat) deg lng
        const cosLat = Math.cos(t.cy * Math.PI / 180);
        const dLat = halfLen * Math.cos(rad) / 111320;
        const dLng = halfLen * Math.sin(rad) / (111320 * cosLat);
        const p1 = L.latLng(t.cy - dLat, t.cx - dLng);
        const p2 = L.latLng(t.cy + dLat, t.cx + dLng);
        const line = L.polyline([p1, p2], {
            color: '#22c55e', weight: 4, opacity: 0.8,
            dashArray: '10 5'
        }).addTo(map);
        line.bindTooltip(`🌳 ${t.length}m / θ=${t.angle_deg}°`, {permanent: false});
        treeMarkers.push(line);
    });
}

async function saveTreesToCase() {
    if (!API.lastCaseDir) { showToast('请先生成 CFD 案例', 'warning'); return; }
    if (placedTrees.length === 0) { showToast('请先在图上放置树木', 'warning'); return; }
    try {
        const resp = await fetch('/api/save-trees', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                case_dir: API.lastCaseDir,
                wind_direction: API.windDirection || 'N',
                inlet_speed: API.inletSpeed || 5.0,
                trees: placedTrees,
            })
        });
        const data = await resp.json();
        if (!data.success) { showToast(data.detail || '保存失败', 'error'); return; }
        document.getElementById('btn-update-wind').style.display = 'inline-block';
        showToast(`树木已保存 (${data.num_trees} 排)，点击"更新风场"`, 'success');
    } catch (e) { showToast('保存失败: ' + e.message, 'error'); }
}

async function updateWindWithTrees() {
    if (!API.lastCaseDir) { showToast('请先生成 CFD 案例', 'warning'); return; }
    showToast('正在计算参数化修正...', 'info');
    try {
        const resp = await fetch('/api/correct-from-case', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                case_dir: API.lastCaseDir,
                wind_direction: API.windDirection || 'N',
                inlet_speed: API.inletSpeed || 5.0,
            })
        });
        const data = await resp.json();
        if (!data.success) { showToast(data.detail || '修正失败', 'error'); return; }
        windData = data;
        var imgContainer = document.getElementById('wind-img-container');
        imgContainer.innerHTML = `<img src="${data.image_base64}" style="width:100%;max-width:500px;border-radius:8px;">`;
        document.getElementById('wind-speed-range').textContent =
            `${data.speed_min.toFixed(1)} - ${data.speed_max.toFixed(1)} m/s`;
        showToast(`修正完成! ${data.num_trees} 排树 | 风速: ${data.speed_min.toFixed(1)}-${data.speed_max.toFixed(1)} m/s`, 'success');
    } catch (e) { showToast('修正失败: ' + e.message, 'error'); }
}


// ═══════════════════════════════════════════════════════════════════════════════
// Startup
// ═══════════════════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', init);
