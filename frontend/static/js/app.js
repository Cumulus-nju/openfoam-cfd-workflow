/**
 * UrbanWind CFD — Application Controller
 *
 * Handles:
 * - Session management
 * - Map (Leaflet) integration
 * - API communication
 * - Chat interface
 * - Building list & properties panel
 * - Import/Generate modals
 * - Toast notifications
 */
'use strict';

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

    // Init map
    initMap();

    // Check model status
    checkHealth();

    // Auto-collapse chat on small screens
    if (window.innerWidth < 1200) {
        toggleChat(true);
    }
}

function initMap() {
    map = L.map('map', {
        center: [32.06, 118.78],  // Default: Nanjing
        zoom: 15,
        zoomControl: false,
        attributionControl: true,
    });

    // Dark tile layer (CartoDB dark)
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
        subdomains: 'abcd',
        maxZoom: 20,
    }).addTo(map);

    // Zoom control (top-left)
    L.control.zoom({ position: 'topleft' }).addTo(map);

    // Scale control (bottom-left)
    L.control.scale({ position: 'bottomleft', metric: true, imperial: false }).addTo(map);

    // Invalidate size after layout changes
    setTimeout(() => map.invalidateSize(), 300);
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
    if (!plan || !plan.features) return;

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

    // Fit bounds
    const allLayers = [...Object.values(buildingLayers), ...Object.values(bikeLayers)];
    if (allLayers.length > 0) {
        const group = L.featureGroup(allLayers);
        map.fitBounds(group.getBounds(), { padding: [50, 50], maxZoom: 18 });
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

async function executeImport() {
    const btn = document.getElementById('btn-import-exec');
    const spinner = document.getElementById('import-spinner');
    btn.disabled = true;
    spinner.style.display = 'inline-block';

    try {
        if (currentImportTab === 'osm') {
            await importOSM();
        } else if (currentImportTab === 'dxf') {
            await importDXF();
        } else if (currentImportTab === 'manual') {
            await importManual();
        }
    } catch (e) {
        showToast(`导入失败: ${e.message}`, 'error');
    } finally {
        btn.disabled = false;
        spinner.style.display = 'none';
        hideImportModal();
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

function showGenerateModal() {
    if (!API.plan) return;
    document.getElementById('generate-modal').style.display = 'flex';
}

function hideGenerateModal() {
    document.getElementById('generate-modal').style.display = 'none';
}

async function executeGenerate() {
    const btn = document.getElementById('btn-generate-exec');
    btn.disabled = true;
    btn.textContent = '生成中...';

    const body = {
        case_name: document.getElementById('gen-name').value.trim() || 'my_campus',
        wind_speed: parseFloat(document.getElementById('gen-speed').value) || 5.0,
        wind_direction: document.getElementById('gen-direction').value,
        n_bikes: parseInt(document.getElementById('gen-bikes').value) || 20,
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
            showToast('案例生成成功！请在 WSL 中运行 CFD', 'success');
            addChatMessage('system', msg);
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
// Startup
// ═══════════════════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', init);
