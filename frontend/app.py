"""
UrbanWind CFD — FastAPI Application Server

Serves the web UI and provides REST + WebSocket APIs for the full pipeline:
multi-source input → LLM optimization → OpenFOAM case generation.

Run with:
    python -m frontend.app
    → Opens http://127.0.0.1:8765
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
import threading
import time
import uuid
import numpy as np
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .config import SERVER_HOST, SERVER_PORT, STATIC_DIR, CFD_CASES_DIR, MODEL_FILE
from .schema import SitePlan, BuildingType, SourceType, validate_site_plan
from .input_adapters import OSMAdapter, DXFAdapter, ManualAdapter, MSBuildingsAdapter, GaodeAdapter, OvertureAdapter
from .llm_engine import get_engine, GeometryInferrer, InteractiveEditor
from .of_generator import assemble_case

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("urbanwind")

# ── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="UrbanWind CFD",
    description="城市微风场智能建模前端",
    version="0.1.0",
)

# ── Session state ────────────────────────────────────────────────────────────

# In-memory session storage (simplified — for production, use file-based or Redis)
_sessions: Dict[str, Dict[str, Any]] = {}


def _get_session(session_id: str) -> Dict[str, Any]:
    if session_id not in _sessions:
        _sessions[session_id] = {
            "plan": None,      # SitePlan
            "editor": None,    # InteractiveEditor
            "created_at": time.time(),
            "messages": [],    # Chat history
        }
    return _sessions[session_id]


# ── API Routes ───────────────────────────────────────────────────────────────


@app.get("/api/health")
async def health():
    """Health check + model status."""
    engine = get_engine()
    return {
        "status": "ok",
        "model_available": engine.is_available,
        "model_loaded": engine.is_loaded,
        "model_path": str(MODEL_FILE),
        "sessions": len(_sessions),
    }


# ── Session management ───────────────────────────────────────────────────────


@app.post("/api/session")
async def create_session():
    """Create a new editing session."""
    sid = uuid.uuid4().hex[:12]
    _sessions[sid] = {
        "plan": None,
        "editor": None,
        "created_at": time.time(),
        "messages": [],
    }
    return {"session_id": sid}


@app.get("/api/session/{session_id}")
async def get_session_state(session_id: str):
    """Get current session state."""
    sess = _get_session(session_id)
    plan = sess.get("plan")
    return {
        "session_id": session_id,
        "has_plan": plan is not None,
        "plan": plan.to_dict() if plan else None,
        "num_buildings": len(plan.buildings) if plan else 0,
        "num_bikes": len(plan.bike_stations) if plan else 0,
        "messages": sess.get("messages", [])[-20:],  # Last 20 messages
    }


# ── Data import ──────────────────────────────────────────────────────────────


@app.post("/api/import/osm")
async def import_osm(session_id: str = Query(...), request: Dict[str, Any] = Body(...)):
    """
    Import building data from OpenStreetMap.

    Body: {"bbox": [south, west, north, east]} or {"place": "Nanjing University"}
    """
    sess = _get_session(session_id)
    adapter = OSMAdapter()

    try:
        bbox = request.get("bbox")
        place = request.get("place")

        if bbox and len(bbox) == 4:
            plan = adapter.parse(bbox=tuple(bbox))
        elif place:
            plan = adapter.parse(place=place)
        else:
            raise HTTPException(400, "Provide 'bbox' or 'place'")

        sess["plan"] = plan
        sess["editor"] = InteractiveEditor(plan)

        return {
            "success": True,
            "num_buildings": len(plan.buildings),
            "bbox": plan.overall_bbox,
            "metadata": plan.metadata,
            "plan": plan.to_dict(),
        }
    except Exception as e:
        logger.error(f"OSM import failed: {e}")
        raise HTTPException(500, str(e))


@app.post("/api/import/dxf")
async def import_dxf(
    session_id: str,
    file: UploadFile = File(...),
):
    """Import building data from uploaded DXF file."""
    sess = _get_session(session_id)

    # Save temp file
    import tempfile as _tf
    tmp_path = Path(_tf.gettempdir()) / f"urbanwind_dxf_{uuid.uuid4().hex[:8]}.dxf"
    try:
        with open(tmp_path, "wb") as f:
            content = await file.read()
            f.write(content)

        adapter = DXFAdapter()
        plan = adapter.parse(str(tmp_path))
        sess["plan"] = plan
        sess["editor"] = InteractiveEditor(plan)

        return {
            "success": True,
            "num_buildings": len(plan.buildings),
            "bbox": plan.overall_bbox,
            "metadata": plan.metadata,
            "plan": plan.to_dict(),
        }
    except Exception as e:
        logger.error(f"DXF import failed: {e}")
        raise HTTPException(500, str(e))
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


@app.post("/api/import/manual")
async def import_manual(session_id: str = Query(...), request: Dict[str, Any] = Body(...)):
    """
    Import from manual text description or structured dict.

    Body: {"text": "..."} or {"buildings": [...], "bikes": [...]}
    """
    sess = _get_session(session_id)
    adapter = ManualAdapter()

    try:
        plan = adapter.parse(request)
        sess["plan"] = plan
        sess["editor"] = InteractiveEditor(plan)

        return {
            "success": True,
            "num_buildings": len(plan.buildings),
            "needs_llm_enrichment": plan.metadata.get("needs_llm_enrichment", False),
            "plan": plan.to_dict(),
        }
    except Exception as e:
        logger.error(f"Manual import failed: {e}")
        raise HTTPException(500, str(e))


@app.post("/api/import/msbuildings")
async def import_ms_buildings(session_id: str = Query(...), request: Dict[str, Any] = Body(...)):
    """
    Import building data from Microsoft Global ML Building Footprints.
    Free, no API key. Good coverage in China.

    Body: {"bbox": [south, west, north, east]} or {"place": "..."}
    """
    sess = _get_session(session_id)
    adapter = MSBuildingsAdapter()

    try:
        bbox = request.get("bbox")
        place = request.get("place")

        if bbox and len(bbox) == 4:
            plan = adapter.parse(bbox=tuple(bbox))
        elif place:
            # Geocode place name using OSM Nominatim, then use MS data
            from .input_adapters.osm_adapter import OSMAdapter
            osm = OSMAdapter()
            latlon_bbox = osm._geocode(place)
            plan = adapter.parse(bbox=latlon_bbox)
        else:
            raise HTTPException(400, "Provide 'bbox' or 'place'")

        sess["plan"] = plan
        sess["editor"] = InteractiveEditor(plan)

        return {
            "success": True,
            "num_buildings": len(plan.buildings),
            "bbox": plan.overall_bbox,
            "metadata": plan.metadata,
            "plan": plan.to_dict(),
        }
    except Exception as e:
        logger.error(f"MS Buildings import failed: {e}")
        raise HTTPException(500, str(e))


@app.post("/api/import/gaode")
async def import_gaode(session_id: str = Query(...), request: Dict[str, Any] = Body(...)):
    """
    Import building data from Gaode (Amap) REST API.

    Body: {"place": "南京大学", "keywords": "学校"} or {"lat": 32.05, "lon": 118.78}
    """
    sess = _get_session(session_id)
    adapter = GaodeAdapter()

    try:
        plan = adapter.parse(source=request)
        sess["plan"] = plan
        sess["editor"] = InteractiveEditor(plan)

        return {
            "success": True,
            "num_buildings": len(plan.buildings),
            "metadata": plan.metadata,
            "plan": plan.to_dict(),
        }
    except Exception as e:
        logger.error(f"Gaode import failed: {e}")
        raise HTTPException(500, str(e))


@app.post("/api/import/overture")
async def import_overture(session_id: str = Query(...), request: Dict[str, Any] = Body(...)):
    """
    Import building data from Overture Maps (Microsoft + Meta + Esri).
    Precise building footprints, global coverage. Uses local cache.

    Body: {"bbox": [south, west, north, east]}
    """
    sess = _get_session(session_id)
    adapter = OvertureAdapter()

    try:
        bbox = request.get("bbox")
        if not bbox or len(bbox) != 4:
            raise HTTPException(400, "Provide 'bbox': [south, west, north, east]")

        plan = adapter.parse(bbox=tuple(bbox))
        sess["plan"] = plan
        sess["editor"] = InteractiveEditor(plan)

        return {
            "success": True,
            "num_buildings": len(plan.buildings),
            "metadata": plan.metadata,
            "plan": plan.to_dict(),
        }
    except Exception as e:
        logger.error(f"Overture import failed: {e}")
        raise HTTPException(500, str(e))


# ── LLM enrichment ───────────────────────────────────────────────────────────


@app.post("/api/llm/enrich")
async def llm_enrich(session_id: str = Query(...)):
    """Run LLM geometry inference on the current plan."""
    sess = _get_session(session_id)
    plan = sess.get("plan")
    if plan is None:
        raise HTTPException(400, "No plan loaded. Import data first.")

    inferrer = GeometryInferrer(use_llm=True)
    try:
        enriched = inferrer.enrich(plan)
        sess["plan"] = enriched
        sess["editor"] = InteractiveEditor(enriched)

        return {
            "success": True,
            "plan": enriched.to_dict(),
            "num_buildings": len(enriched.buildings),
        }
    except Exception as e:
        logger.error(f"LLM enrichment failed: {e}")
        # Fall back to rules-only
        enriched = inferrer.enrich_rules_only(plan)
        sess["plan"] = enriched
        sess["editor"] = InteractiveEditor(enriched)
        return {
            "success": True,
            "plan": enriched.to_dict(),
            "warning": f"LLM enrichment failed ({e}), used rules only.",
        }


# ── Interactive editing ──────────────────────────────────────────────────────


@app.post("/api/edit")
async def edit_buildings(session_id: str = Query(...), request: Dict[str, Any] = Body(...)):
    """
    Execute a natural language editing instruction.

    Body: {"instruction": "把图书馆高度改成30米"}
    """
    sess = _get_session(session_id)
    editor = sess.get("editor")
    if editor is None:
        raise HTTPException(400, "No plan loaded. Import data first.")

    instruction = request.get("instruction", "").strip()
    if not instruction:
        raise HTTPException(400, "Empty instruction.")

    # Add to chat history
    sess["messages"].append({"role": "user", "content": instruction})

    result = editor.execute(instruction)

    # Add response to chat history
    sess["messages"].append({"role": "assistant", "content": result.message})
    sess["plan"] = editor.current_plan

    return {
        "success": result.success,
        "message": result.message,
        "operations": [
            {"action": op.action, "target_id": op.target_id, "params": op.params}
            for op in result.operations
        ],
        "plan": editor.current_plan.to_dict(),
        "can_undo": len(editor._undo_stack) > 0,
        "can_redo": len(editor._redo_stack) > 0,
    }


@app.post("/api/edit/undo")
async def undo_edit(session_id: str = Query(...)):
    """Undo last edit operation."""
    sess = _get_session(session_id)
    editor = sess.get("editor")
    if editor is None:
        raise HTTPException(400, "No plan loaded.")

    result = editor.undo()
    sess["plan"] = editor.current_plan
    sess["messages"].append({"role": "system", "content": result.message})

    return {
        "success": result.success,
        "message": result.message,
        "plan": editor.current_plan.to_dict(),
        "can_undo": len(editor._undo_stack) > 0,
        "can_redo": len(editor._redo_stack) > 0,
    }


@app.post("/api/edit/redo")
async def redo_edit(session_id: str = Query(...)):
    """Redo last undone operation."""
    sess = _get_session(session_id)
    editor = sess.get("editor")
    if editor is None:
        raise HTTPException(400, "No plan loaded.")

    result = editor.redo()
    sess["plan"] = editor.current_plan
    sess["messages"].append({"role": "system", "content": result.message})

    return {
        "success": result.success,
        "message": result.message,
        "plan": editor.current_plan.to_dict(),
        "can_undo": len(editor._undo_stack) > 0,
        "can_redo": len(editor._redo_stack) > 0,
    }


# ── Direct plan manipulation ─────────────────────────────────────────────────


@app.put("/api/plan/building/{building_id}")
async def update_building(session_id: str, building_id: str, request: Dict[str, Any] = Body(...)):
    """Directly update a building's properties."""
    sess = _get_session(session_id)
    plan = sess.get("plan")
    if plan is None:
        raise HTTPException(400, "No plan loaded.")

    for feat in plan.features:
        if feat.id == building_id:
            for key, value in request.items():
                feat.properties[key] = value
            return {"success": True, "feature": feat.to_dict()}

    raise HTTPException(404, f"Building '{building_id}' not found")


@app.delete("/api/plan/building/{building_id}")
async def delete_building(session_id: str, building_id: str):
    """Delete a building by ID."""
    sess = _get_session(session_id)
    plan = sess.get("plan")
    if plan is None:
        raise HTTPException(400, "No plan loaded.")

    for i, feat in enumerate(plan.features):
        if feat.id == building_id:
            removed = plan.features.pop(i)
            sess["editor"] = InteractiveEditor(plan)
            return {"success": True, "removed": removed.to_dict()}

    raise HTTPException(404, f"Building '{building_id}' not found")


# ── CFD case generation ──────────────────────────────────────────────────────


@app.post("/api/generate")
async def generate_case(session_id: str = Query(...), request: Dict[str, Any] = Body(...)):
    """
    Generate a complete OpenFOAM case from the current plan.

    Body: {
        "case_name": "my_campus",
        "wind_speed": 5.0,
        "wind_direction": "N",
        "n_bikes": 20
    }
    """
    sess = _get_session(session_id)
    plan = sess.get("plan")
    if plan is None:
        raise HTTPException(400, "No plan loaded. Import data first.")

    # Validate
    issues = validate_site_plan(plan)
    if issues:
        raise HTTPException(400, f"Invalid plan: {'; '.join(issues)}")

    case_name = request.get("case_name", f"case_{uuid.uuid4().hex[:6]}")
    wind_speed = float(request.get("wind_speed", 5.0))
    wind_direction = str(request.get("wind_direction", "N")).upper()
    n_bikes = int(request.get("n_bikes", 20))
    output_dir = request.get("output_dir", None)
    cell_size = request.get("cell_size", None)
    if cell_size is not None:
        cell_size = float(cell_size)
    base_dir = Path(output_dir) if output_dir else None

    try:
        case_dir = assemble_case(
            plan, case_name,
            wind_speed=wind_speed,
            wind_direction=wind_direction,
            n_bikes=n_bikes,
            base_dir=base_dir,
            cell_size=cell_size,
        )

        wsl_drive = str(case_dir)[0].lower()
        wsl_path = f"/mnt/{wsl_drive}/" + str(case_dir)[2:].replace("\\", "/")
        return {
            "success": True,
            "case_dir": str(case_dir).replace("\\", "/"),
            "wsl_path": wsl_path,
            "num_buildings": len(plan.buildings),
            "num_bikes": len(plan.bike_stations),
            "next_steps": [
                f"wsl cd /mnt/d/Phase2_CFD_ML/cfd_cases/{case_name}",
                "blockMesh",
                "snappyHexMesh -overwrite",
                "simpleFoam",
            ],
        }
    except Exception as e:
        logger.error(f"Case generation failed: {e}")
        raise HTTPException(500, str(e))


@app.get("/api/download/{case_name}")
async def download_case(case_name: str):
    """Download a generated case as ZIP."""
    import zipfile
    import io

    case_dir = CFD_CASES_DIR / case_name
    if not case_dir.exists():
        raise HTTPException(404, f"Case '{case_name}' not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in case_dir.rglob("*"):
            if f.is_file() and "polyMesh" not in str(f):  # Skip huge mesh files
                zf.write(f, f.relative_to(case_dir))

    buf.seek(0)
    return FileResponse(
        buf,
        media_type="application/zip",
        filename=f"{case_name}.zip",
    )


# ── WebSocket for streaming chat ─────────────────────────────────────────────


@app.websocket("/ws/chat/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for streaming LLM chat."""
    await websocket.accept()
    sess = _get_session(session_id)

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            instruction = msg.get("instruction", "").strip()
            if not instruction:
                continue

            sess["messages"].append({"role": "user", "content": instruction})

            editor = sess.get("editor")
            if editor is None:
                await websocket.send_json({
                    "type": "error",
                    "message": "No plan loaded. Import data first.",
                })
                continue

            # Execute edit
            result = editor.execute(instruction)
            sess["plan"] = editor.current_plan

            await websocket.send_json({
                "type": "result",
                "success": result.success,
                "message": result.message,
                "plan": editor.current_plan.to_dict(),
                "can_undo": len(editor._undo_stack) > 0,
                "can_redo": len(editor._redo_stack) > 0,
            })

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {session_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


# ── Static files & Frontend ──────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main web UI."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return HTMLResponse("<h1>UrbanWind CFD</h1><p>Frontend not built yet.</p>")


# Mount static files (CSS, JS)
if (STATIC_DIR / "css").exists():
    app.mount("/static/css", StaticFiles(directory=str(STATIC_DIR / "css")), name="css")
if (STATIC_DIR / "js").exists():
    app.mount("/static/js", StaticFiles(directory=str(STATIC_DIR / "js")), name="js")


# ── GNN 风场预测 ────────────────────────────────────────────────────────────


@app.post("/api/predict-from-case")
async def predict_from_case(request: Dict[str, Any] = Body(...)):
    """
    从已生成的 CFD case 直接预测风场。
    Body: {case_dir: "E:/UrbanWind/cfd_cases/my_case"}
    """
    import traceback as _tb
    try:
        case_dir_raw = str(request.get("case_dir", ""))
        logger.info(f"predict-from-case: case_dir={case_dir_raw}")
        case_dir = Path(case_dir_raw.replace("\\", "/"))
        if not case_dir.exists():
            raise HTTPException(400, f"案例目录不存在: {case_dir}")

        geojson_path = case_dir / "site_plan.geojson"
        if not geojson_path.exists():
            raise HTTPException(400, f"案例中没有 site_plan.geojson，目录内容: {list(case_dir.iterdir())}")

        with open(geojson_path, encoding='utf-8') as f:
            geojson = json.load(f)

        buildings_local = []
        for feat in geojson.get("features", []):
            if feat.get("category") != "building":
                continue
            coords = feat.get("geometry", {}).get("coordinates", [[]])[0]
            if not coords: continue
            props = feat.get("properties", {})
            h = props.get("height") or props.get("inferred_height") or 10.0
            buildings_local.append({"polygon_local": [[p[0], p[1]] for p in coords], "height": float(h)})

        if not buildings_local:
            raise HTTPException(400, "案例中没有建筑数据")
        logger.info(f"predict-from-case: {len(buildings_local)} buildings, predicting...")

        wind_dir = request.get("wind_direction", "N")
        inlet_speed = float(request.get("inlet_speed", 5.0))

        all_x = [p[0] for b in buildings_local for p in b["polygon_local"]]
        all_y = [p[1] for b in buildings_local for p in b["polygon_local"]]

        # Auto-detect: if mean abs coord > 10, it's lat/lng (e.g. 116, 40)
        x_mean = (max(all_x) + min(all_x)) / 2
        if abs(x_mean) > 10:  # lat/lng like 116.3
            lon0 = (min(all_x) + max(all_x)) / 2
            lat0 = (min(all_y) + max(all_y)) / 2
            cos_lat = np.cos(np.radians(lat0))
            m_per_deg_lng = 111320.0 * max(cos_lat, 0.3)
            m_per_deg_lat = 111320.0
            # Convert to local meters
            buildings_local = [{
                "polygon_local": [((p[0]-lon0)*m_per_deg_lng, (p[1]-lat0)*m_per_deg_lat) for p in b["polygon_local"]],
                "height": b["height"]
            } for b in buildings_local]
            all_x = [p[0] for b in buildings_local for p in b["polygon_local"]]
            all_y = [p[1] for b in buildings_local for p in b["polygon_local"]]

        margin = max(40, (max(all_x)-min(all_x)) * 0.3)
        grid_size = 250
        grid_x = np.linspace(min(all_x)-margin, max(all_x)+margin, grid_size)
        grid_y = np.linspace(max(all_y)+margin, min(all_y)-margin, grid_size)

        predictor = _get_predictor()
        if predictor is None:
            raise HTTPException(503, "GNN 模型未就绪")

        Ux, Uy, speed = predictor.predict(buildings_local, wind_dir, inlet_speed, grid_x, grid_y)

        def nan_to_none(arr):
            return [[None if np.isnan(v) else float(v) for v in row] for row in arr]

        logger.info(f"predict-from-case: OK, speed {np.nanmin(speed):.1f}-{np.nanmax(speed):.1f}")

        # 服务端渲染为 PNG，返回 base64
        import base64, io as _io
        from matplotlib.figure import Figure
        fig = Figure(figsize=(5, 5), dpi=80)
        ax = fig.add_subplot(111)
        vmin, vmax = float(np.nanmin(speed)), float(np.nanmax(speed))
        speed_masked = np.where(np.isnan(speed), np.nan, speed)
        im = ax.imshow(speed_masked, cmap='turbo', vmin=vmin, vmax=vmax, origin='upper',
                        extent=[0, grid_size, grid_size, 0])
        ax.set_xticks([]); ax.set_yticks([])
        fig.colorbar(im, ax=ax, label='Wind Speed (m/s)', shrink=0.8)
        buf = _io.BytesIO()
        fig.savefig(buf, format='png', dpi=80, bbox_inches='tight', pad_inches=0.1)
        buf.seek(0)
        img_b64 = base64.b64encode(buf.read()).decode('utf-8')
        import matplotlib.pyplot as _plt; _plt.close(fig)

        return {
            "success": True,
            "case_dir": str(case_dir),
            "speed_min": vmin,
            "speed_max": vmax,
            "image_base64": f"data:image/png;base64,{img_b64}",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"predict-from-case CRASH: {e}\n{_tb.format_exc()}")
        raise HTTPException(500, f"预测失败: {e}")


# ── 树列保存 ─────────────────────────────────────────────────────────────────


@app.post("/api/save-trees")
async def save_trees(request: Dict[str, Any] = Body(...)):
    """保存树列位置到 case/trees.json。"""
    case_dir = Path(str(request.get("case_dir", "")).replace("\\", "/"))
    if not case_dir.exists():
        raise HTTPException(400, f"案例不存在: {case_dir}")
    trees_data = request.get("trees", [])
    trees_path = case_dir / "trees.json"
    with open(trees_path, 'w', encoding='utf-8') as f:
        json.dump({"trees": trees_data, "wind_direction": request.get("wind_direction", "N"),
                    "inlet_speed": request.get("inlet_speed", 5.0)}, f, ensure_ascii=False, indent=2)
    logger.info(f"save-trees: {len(trees_data)} trees -> {trees_path}")
    return {"success": True, "num_trees": len(trees_data)}


# ── 树列修正（从 case 读 trees.json）─────────────────────────────────────────


@app.post("/api/correct-from-case")
async def correct_from_case(request: Dict[str, Any] = Body(...)):
    """
    从 case + 树列参数重新预测并修正风场。
    Body: {case_dir, wind_direction, inlet_speed, trees: [{cx, cy, length, angle_deg}]}
    """
    case_dir = Path(str(request.get("case_dir", "")).replace("\\", "/"))
    if not case_dir.exists():
        raise HTTPException(400, f"案例不存在: {case_dir}")

    wind_dir = request.get("wind_direction", "N")
    inlet_speed = float(request.get("inlet_speed", 5.0))
    wd_map = {"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)}
    wdx, wdy = wd_map.get(wind_dir, (0, 1))

    # 从 trees.json 读取（由 /api/save-trees 写入）
    trees_path = case_dir / "trees.json"
    trees_data = []
    if trees_path.exists():
        with open(trees_path, encoding='utf-8') as f:
            trees_data = json.load(f).get("trees", [])

    # 从 case 读取建筑 + 预测 + 修正（和 predict-from-case 相同流程）
    geojson_path = case_dir / "site_plan.geojson"
    if not geojson_path.exists():
        raise HTTPException(400, "案例中没有 site_plan.geojson")

    with open(geojson_path, encoding='utf-8') as f:
        geojson = json.load(f)

    buildings_local = []
    for feat in geojson.get("features", []):
        if feat.get("category") != "building": continue
        coords = feat.get("geometry", {}).get("coordinates", [[]])[0]
        if not coords: continue
        h = feat.get("properties", {}).get("height") or 10.0
        buildings_local.append({"polygon_local": [[p[0], p[1]] for p in coords], "height": float(h)})

    # Coordinate conversion params (used for buildings AND trees)
    _lon0, _lat0, _m_lng, _m_lat = 0.0, 0.0, 1.0, 1.0
    all_x = [p[0] for b in buildings_local for p in b["polygon_local"]]
    all_y = [p[1] for b in buildings_local for p in b["polygon_local"]]
    x_mean = (max(all_x) + min(all_x)) / 2
    if abs(x_mean) > 10:
        _lon0 = (min(all_x) + max(all_x)) / 2; _lat0 = (min(all_y) + max(all_y)) / 2
        cos_lat = np.cos(np.radians(_lat0))
        _m_lng = 111320.0 * max(cos_lat, 0.3); _m_lat = 111320.0
        buildings_local = [{"polygon_local": [((p[0]-_lon0)*_m_lng, (p[1]-_lat0)*_m_lat) for p in b["polygon_local"]], "height": b["height"]} for b in buildings_local]
        all_x = [p[0] for b in buildings_local for p in b["polygon_local"]]
        all_y = [p[1] for b in buildings_local for p in b["polygon_local"]]

    margin = max(40, (max(all_x)-min(all_x))*0.3)
    grid_size = 250
    grid_x = np.linspace(min(all_x)-margin, max(all_x)+margin, grid_size)
    grid_y = np.linspace(max(all_y)+margin, min(all_y)-margin, grid_size)

    predictor = _get_predictor()
    if predictor is None: raise HTTPException(503, "GNN 模型未就绪")

    Ux, Uy, speed = predictor.predict(buildings_local, wind_dir, inlet_speed, grid_x, grid_y)

    # 参数化树列修正（树坐标用和建筑相同的转换）
    from .param_correction import TreeRow, apply_tree_correction
    trees = [TreeRow(
        cx=(t["cx"] - _lon0) * _m_lng,
        cy=(t["cy"] - _lat0) * _m_lat,
        length=t.get("length", 20),
        angle_deg=t.get("angle_deg", 0)
    ) for t in trees_data]
    if trees:
        Ux_c, Uy_c, speed_c = apply_tree_correction(
            Ux, Uy, speed, grid_x, grid_y, trees, (wdx, wdy), inlet_speed)
    else:
        speed_c = speed

    # 渲染图片
    import base64, io as _io
    from matplotlib.figure import Figure
    fig = Figure(figsize=(5, 5), dpi=80)
    ax = fig.add_subplot(111)
    vmin, vmax = float(np.nanmin(speed_c)), float(np.nanmax(speed_c))
    speed_c_masked = np.where(np.isnan(speed_c), np.nan, speed_c)
    im = ax.imshow(speed_c_masked, cmap='turbo', vmin=vmin, vmax=vmax, origin='upper',
                    extent=[0, grid_size, grid_size, 0])
    ax.set_xticks([]); ax.set_yticks([])
    fig.colorbar(im, ax=ax, label='Wind Speed (m/s)', shrink=0.8)
    buf = _io.BytesIO()
    fig.savefig(buf, format='png', dpi=80, bbox_inches='tight', pad_inches=0.1)
    buf.seek(0)
    img_b64 = base64.b64encode(buf.read()).decode('utf-8')
    import matplotlib.pyplot as _plt; _plt.close(fig)

    return {
        "success": True,
        "case_dir": str(case_dir),
        "speed_min": vmin,
        "speed_max": vmax,
        "image_base64": f"data:image/png;base64,{img_b64}",
        "num_trees": len(trees),
    }


# ── 单车选址板块 ─────────────────────────────────────────────────────────────


def _case_buildings_from_geojson(geojson) -> list:
    """从 site_plan.geojson 提取建筑 polygon_local + height（含经纬度→米转换）。"""
    buildings_local = []
    for feat in geojson.get("features", []):
        if feat.get("category") != "building":
            continue
        coords = feat.get("geometry", {}).get("coordinates", [[]])[0]
        if not coords:
            continue
        props = feat.get("properties", {})
        h = props.get("height") or props.get("inferred_height") or 10.0
        buildings_local.append({"polygon_local": [[p[0], p[1]] for p in coords], "height": float(h)})

    if not buildings_local:
        return buildings_local

    all_x = [p[0] for b in buildings_local for p in b["polygon_local"]]
    x_mean = (max(all_x) + min(all_x)) / 2
    if abs(x_mean) > 10:  # WGS84 经纬度 → 局部米
        all_y = [p[1] for b in buildings_local for p in b["polygon_local"]]
        lon0 = (min(all_x) + max(all_x)) / 2
        lat0 = (min(all_y) + max(all_y)) / 2
        cos_lat = np.cos(np.radians(lat0))
        m_lng = 111320.0 * max(cos_lat, 0.3)
        m_lat = 111320.0
        buildings_local = [{
            "polygon_local": [((p[0] - lon0) * m_lng, (p[1] - lat0) * m_lat) for p in b["polygon_local"]],
            "height": b["height"],
        } for b in buildings_local]
    return buildings_local


@app.get("/api/list-cases")
async def list_cases():
    """列出 CFD 案例目录中的案例（板块选择用）。"""
    cases = []
    if CFD_CASES_DIR.exists():
        for d in sorted(CFD_CASES_DIR.iterdir()):
            if not d.is_dir():
                continue
            gj_path = d / "site_plan.geojson"
            info = {
                "name": d.name,
                "has_plan": gj_path.exists(),
                "n_buildings": 0,
                "n_bikes": 0,
                "center": None,
                "modified": None,
            }
            try:
                import datetime as _dt
                info["modified"] = _dt.datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
            if gj_path.exists():
                try:
                    with open(gj_path, encoding="utf-8") as f:
                        gj = json.load(f)
                    nb = nbk = 0
                    for feat in gj.get("features", []):
                        if feat.get("category") == "building":
                            nb += 1
                        elif feat.get("category") == "bike_station":
                            nbk += 1
                    info["n_buildings"] = nb
                    info["n_bikes"] = nbk
                    meta = gj.get("metadata", {})
                    if meta.get("center_lat") and meta.get("center_lon"):
                        info["center"] = [meta["center_lon"], meta["center_lat"]]
                    else:
                        # fallback：从建筑坐标计算中心
                        xs, ys = [], []
                        for feat in gj.get("features", []):
                            if feat.get("category") != "building":
                                continue
                            coords = feat.get("geometry", {}).get("coordinates", [[]])[0]
                            xs += [p[0] for p in coords]
                            ys += [p[1] for p in coords]
                        if xs and ys:
                            cx = (min(xs) + max(xs)) / 2
                            cy = (min(ys) + max(ys)) / 2
                            # 只有经纬度坐标（绝对值>10）才能用于地图定位；局部米坐标返回 None
                            if abs(cx) > 10 and abs(cy) < 90 and abs(cx) < 180:
                                info["center"] = [cx, cy]
                            else:
                                info["center"] = None
                except Exception as e:
                    logger.warning(f"list-cases: 解析 {d.name} 失败: {e}")
            cases.append(info)
    return {"success": True, "cases": cases}


@app.post("/api/bike-siting")
async def bike_siting(request: Dict[str, Any] = Body(...)):
    """
    单车选址评估：GNN 预测（+树列修正）→ 候选单车点风暴露评分/风险分级/选址建议。

    Body: {
        case_dir: "E:/UrbanWind/cfd_cases/my_case",
        wind_direction: "N", inlet_speed: 5.0,
        v_crit: 11.7,            # 单车倾覆临界风速 (m/s)，来源 bike_wind_overturning_model.tex
        gust_factor: 0.67,       # 阵风修正 (7.8/11.7)
        high_factor: 0.8,        # 高风险阈值系数 (相对阵风阈值)
        medium_factor: 0.5,      # 中等风险阈值系数
        calm_speed: 1.5,         # 静风区判据 (m/s)
    }
    """
    import base64, io as _io
    import traceback as _tb
    from matplotlib.figure import Figure
    from matplotlib.patches import Polygon as _MplPoly

    try:
        case_dir = Path(str(request.get("case_dir", "")).replace("\\", "/"))
        if not case_dir.exists():
            raise HTTPException(400, f"案例不存在: {case_dir}")

        geojson_path = case_dir / "site_plan.geojson"
        if not geojson_path.exists():
            raise HTTPException(400, f"案例中没有 site_plan.geojson")

        with open(geojson_path, encoding="utf-8") as f:
            geojson = json.load(f)

        wind_dir = str(request.get("wind_direction", "N")).upper()
        inlet_speed = float(request.get("inlet_speed", 5.0))
        v_crit = float(request.get("v_crit", 11.7))
        gust_factor = float(request.get("gust_factor", 0.67))
        high_factor = float(request.get("high_factor", 0.8))
        medium_factor = float(request.get("medium_factor", 0.5))
        calm_speed = float(request.get("calm_speed", 1.5))
        v_eff = v_crit * gust_factor  # 阵风修正后的倾覆阈值

        buildings = _case_buildings_from_geojson(geojson)
        if not buildings:
            raise HTTPException(400, "案例中没有建筑数据")

        # 提取单车点（bike_station，矩形多边形或点 → 质心）
        bike_stations = []
        for feat in geojson.get("features", []):
            if feat.get("category") != "bike_station":
                continue
            geom = feat.get("geometry", {})
            gtype = geom.get("type")
            if gtype == "Point":
                px, py = geom.get("coordinates", [0, 0])[:2]
            else:
                coords = geom.get("coordinates", [[]])[0]
                if not coords:
                    continue
                px = sum(p[0] for p in coords) / len(coords)
                py = sum(p[1] for p in coords) / len(coords)
            bike_stations.append({"x": float(px), "y": float(py)})
        logger.info(f"bike-siting: {case_dir}, {len(buildings)} buildings, {len(bike_stations)} bike stations")

        # 建筑可能是经纬度 → 单车点同步转换（与 _case_buildings_from_geojson 相同参考点）
        all_x = [p[0] for b in buildings for p in b["polygon_local"]]
        x_mean = (max(all_x) + min(all_x)) / 2
        if abs(x_mean) > 10:
            all_y = [p[1] for b in buildings for p in b["polygon_local"]]
            lon0 = (min(all_x) + max(all_x)) / 2
            lat0 = (min(all_y) + max(all_y)) / 2
            cos_lat = np.cos(np.radians(lat0))
            m_lng = 111320.0 * max(cos_lat, 0.3)
            m_lat = 111320.0
            for bs in bike_stations:
                bs["x"], bs["y"] = (bs["x"] - lon0) * m_lng, (bs["y"] - lat0) * m_lat

        # 域网格（与 predict-from-case 相同规则）
        all_x = [p[0] for b in buildings for p in b["polygon_local"]]
        all_y = [p[1] for b in buildings for p in b["polygon_local"]]
        margin = max(40, (max(all_x) - min(all_x)) * 0.3)
        grid_size = 250
        grid_x = np.linspace(min(all_x) - margin, max(all_x) + margin, grid_size)
        grid_y = np.linspace(max(all_y) + margin, min(all_y) - margin, grid_size)

        predictor = _get_predictor()
        if predictor is None:
            raise HTTPException(503, "GNN 模型未就绪")

        Ux, Uy, speed = predictor.predict(buildings, wind_dir, inlet_speed, grid_x, grid_y)

        # 树列修正（若 case 有 trees.json，自动叠加）
        trees_path = case_dir / "trees.json"
        n_trees = 0
        if trees_path.exists():
            from .param_correction import TreeRow, apply_tree_correction
            wd_map = {"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)}
            wd = wd_map.get(wind_dir, (0, 1))
            try:
                with open(trees_path, encoding="utf-8") as f:
                    trees_data = json.load(f).get("trees", [])
                trees = [TreeRow(cx=t["cx"], cy=t["cy"], length=t.get("length", 20),
                                 angle_deg=t.get("angle_deg", 0)) for t in trees_data]
                n_trees = len(trees)
                if trees:
                    _, _, speed = apply_tree_correction(Ux, Uy, speed, grid_x, grid_y, trees, wd, inlet_speed)
            except Exception as e:
                logger.warning(f"bike-siting: 树列修正跳过: {e}")

        # 对每个单车点采样（最近邻，网格分辨率 1-2m 足够）
        dx = grid_x[1] - grid_x[0]
        dy = grid_y[1] - grid_y[0]
        H, W = speed.shape

        def _sample(px, py):
            ix = int(round((px - grid_x[0]) / dx))
            iy = int(round((py - grid_y[0]) / dy))  # grid_y 递减，取最近邻即可
            ix = max(0, min(W - 1, ix))
            iy = max(0, min(H - 1, iy))
            v = speed[iy, ix]
            return float(v) if v == v else None  # NaN → None

        def _risk_level(sp):
            if sp is None:
                return "unknown"
            if sp >= high_factor * v_eff:
                return "high"
            if sp >= medium_factor * v_eff:
                return "medium"
            if sp >= calm_speed:
                return "low"
            return "calm"

        _sug = {
            "high": "倾覆风险高：阵风下易倒伏，建议迁移或加装防风围挡",
            "medium": "存在风致风险：大风天建议调度清空，或加装挡风设施",
            "low": "风暴露正常：适合停放，日常通风良好",
            "calm": "静风区：通风较差，注意雨雪后潮湿积渍",
            "unknown": "超出预测范围：数据不完整，建议人工复核",
        }

        points = []
        for bs in bike_stations:
            sp = _sample(bs["x"], bs["y"])
            lvl = _risk_level(sp)
            points.append({
                "x": bs["x"], "y": bs["y"],
                "speed": sp,
                "risk_level": lvl,
                "suggestion": _sug[lvl],
                "v_eff": round(v_eff, 2),
            })

        stats = {"total": len(points), "high": 0, "medium": 0, "low": 0, "calm": 0, "unknown": 0}
        for p in points:
            stats[p["risk_level"]] = stats.get(p["risk_level"], 0) + 1
        stats.pop("unknown", None)
        if points:
            # unknown 计入其他（前端展示用原始统计即可）
            pass

        ranked = sorted([p for p in points if p["speed"] is not None], key=lambda p: p["speed"])
        recommendations = {
            "safest": [{"x": p["x"], "y": p["y"], "speed": p["speed"], "risk_level": p["risk_level"]} for p in ranked[:5]],
            "riskiest": [{"x": p["x"], "y": p["y"], "speed": p["speed"], "risk_level": p["risk_level"]} for p in ranked[-5:][::-1]],
        }

        # 渲染：风场 + 单车点标记
        vmin = float(np.nanmin(speed)) if not np.all(np.isnan(speed)) else 0.0
        vmax = float(np.nanmax(speed)) if not np.all(np.isnan(speed)) else 5.0
        fig = Figure(figsize=(6.4, 5.4), dpi=110)
        ax = fig.add_subplot(111)
        sp_masked = np.where(np.isnan(speed), np.nan, speed)
        im = ax.imshow(sp_masked, cmap="turbo", vmin=vmin, vmax=vmax, origin="upper",
                       extent=[grid_x[0], grid_x[-1], grid_y[-1], grid_y[0]])
        for b in buildings[:200]:
            poly = b["polygon_local"]
            if len(poly) >= 3:
                ax.add_patch(_MplPoly(poly, fc="none", ec="black", lw=0.6, alpha=0.7))
        _color_map = {"high": "#ef4444", "medium": "#f59e0b", "low": "#10b981", "calm": "#38bdf8"}
        for p in points:
            ax.plot(p["x"], p["y"], "o", ms=7, mec="white", mew=1.2,
                    color=_color_map.get(p["risk_level"], "#94a3b8"), alpha=0.9)
        ax.set_title(f"单车风暴露评估 — {case_dir.name} | {wind_dir}风 {inlet_speed} m/s\n"
                     f"倾覆阈值≈{v_eff:.1f} m/s (V_crit={v_crit} × 阵风修正{gust_factor})", fontsize=10)
        ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
        fig.colorbar(im, ax=ax, label="Wind Speed (m/s)", shrink=0.8)
        buf = _io.BytesIO()
        fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", pad_inches=0.1)
        buf.seek(0)
        img_b64 = base64.b64encode(buf.read()).decode("utf-8")

        return {
            "success": True,
            "case_dir": str(case_dir),
            "wind_direction": wind_dir,
            "inlet_speed": inlet_speed,
            "v_crit": v_crit,
            "gust_factor": gust_factor,
            "v_eff": v_eff,
            "n_trees": n_trees,
            "n_buildings": len(buildings),
            "points": points,
            "stats": stats,
            "recommendations": recommendations,
            "grid_bounds": [grid_x[0], grid_y[-1], grid_x[-1], grid_y[0]],
            "image_base64": f"data:image/png;base64,{img_b64}",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"bike-siting CRASH: {e}\n{_tb.format_exc()}")
        raise HTTPException(500, f"单车选址评估失败: {e}")


# ── 旧 predict 端点（保留兼容） ─────────────────────────────────────────────

# Lazy-loaded GNN predictor (loaded on first use to save RAM)
_gnn_predictor = None
_gnn_checkpoint = Path(r"E:\UrbanWind\gnn\checkpoints\stage1_best.pt")


def _get_predictor():
    """延迟加载 GNN 模型（首次调用时加载，节省内存）。"""
    global _gnn_predictor
    if _gnn_predictor is None:
        from .gnn_predictor import GNNSurrogate
        if _gnn_checkpoint.exists():
            _gnn_predictor = GNNSurrogate(_gnn_checkpoint)
            logger.info("GNN predictor loaded")
        else:
            logger.warning(f"GNN checkpoint not found: {_gnn_checkpoint}")
            return None
    return _gnn_predictor


@app.post("/api/predict-wind")
async def predict_wind(session_id: str = Query(...), request: Dict[str, Any] = Body(...)):
    """
    GNN 快速风场预测。
    Body: {wind_direction: "N", inlet_speed: 5.0}
    """
    sess = _get_session(session_id)
    plan = sess.get("plan")
    logger.info(f"predict-wind: session={session_id[:8]}..., plan={'OK' if plan else 'NONE'}")

    if plan is None:
        raise HTTPException(400, "请先导入建筑数据")

    predictor = _get_predictor()
    if predictor is None:
        raise HTTPException(503, "GNN 模型未就绪，请等待训练完成")

    wind_dir = request.get("wind_direction", "N")
    inlet_speed = float(request.get("inlet_speed", 5.0))

    # Write debug info to file
    import datetime as _dt
    _debug_log = Path("D:/Phase2_CFD_ML/predict_debug.log")
    try:
        _debug_log.write_text(
            f"[{_dt.datetime.now()}] session={session_id} wind={wind_dir} speed={inlet_speed}\n"
            f"  plan type: {type(plan).__name__}, buildings: {len(plan.buildings)}\n"
            f"  first bld coords: {plan.buildings[0].geometry.coordinates[0][:2] if plan.buildings else 'N/A'}\n",
            encoding='utf-8')
    except Exception as _e:
        _debug_log.write_text(f"[{_dt.datetime.now()}] DEBUG WRITE ERROR: {_e}\n", encoding='utf-8')

    # 保存 plan 为 geojson，从中提取建筑（确保坐标格式一致）
    import tempfile, uuid as _uuid, traceback as _tb
    tmp_dir = Path(tempfile.gettempdir()) / "urbanwind_predict"
    tmp_dir.mkdir(exist_ok=True)
    geojson_path = tmp_dir / f"plan_{_uuid.uuid4().hex[:8]}.geojson"
    try:
        plan.to_file(geojson_path)
    except Exception as e:
        logger.error(f"to_file failed: {e}\n{_tb.format_exc()}")
        raise HTTPException(500, f"导出geojson失败: {e}")

    with open(geojson_path) as f:
        geojson = json.load(f)
    try:
        geojson_path.unlink()  # 临时文件用完即删，避免累积
    except Exception:
        pass

    # 从 geojson 提取建筑 footprint 和 domain
    buildings_local = []
    for feat in geojson.get("features", []):
        if feat.get("category") != "building":
            continue
        coords = feat.get("geometry", {}).get("coordinates", [[]])[0]
        if not coords: continue
        props = feat.get("properties", {})
        h = props.get("height") or props.get("inferred_height") or 10.0
        buildings_local.append({"polygon_local": [[p[0], p[1]] for p in coords], "height": float(h)})

    if not buildings_local:
        raise HTTPException(400, "没有有效的建筑数据")

    # 从 buildings 计算网格
    all_x = [p[0] for b in buildings_local for p in b["polygon_local"]]
    all_y = [p[1] for b in buildings_local for p in b["polygon_local"]]
    margin = max(40, (max(all_x)-min(all_x)) * 0.3)
    x_min = min(all_x) - margin
    x_max = max(all_x) + margin
    y_min = min(all_y) - margin
    y_max = max(all_y) + margin

    grid_size = 250
    grid_x = np.linspace(x_min, x_max, grid_size)
    grid_y = np.linspace(y_max, y_min, grid_size)

    # GNN 推理
    Ux, Uy, speed = predictor.predict(buildings_local, wind_dir, inlet_speed, grid_x, grid_y)

    # 用于地图叠图的 bounds（同坐标系）
    latlng_bounds = [x_min, y_min, x_max, y_max]

    def nan_to_none(arr):
        return [[None if np.isnan(v) else float(v) for v in row] for row in arr]

    return {
        "success": True,
        "grid_bounds": [x_min, y_min, x_max, y_max],
        "grid_bounds_latlng": latlng_bounds,
        "grid_size": grid_size,
        "speed_min": float(np.nanmin(speed)),
        "speed_max": float(np.nanmax(speed)),
        "speed_grid": nan_to_none(speed),
        "Ux_grid": nan_to_none(Ux),
        "Uy_grid": nan_to_none(Uy),
    }


@app.post("/api/correct-wind")
async def correct_wind(session_id: str = Query(...), request: Dict[str, Any] = Body(...)):
    """
    在 GNN 风场上叠加树列参数化修正。
    Body: {
        wind_direction: "N", inlet_speed: 5.0,
        trees: [{cx, cy, length, angle_deg}, ...],
        base_prediction: {...}  // 上次 /predict-wind 的返回值
    }
    """
    from .param_correction import TreeRow, apply_tree_correction

    base = request.get("base_prediction")
    if base is None:
        raise HTTPException(400, "需要 base_prediction（先调用 /api/predict-wind）")

    wind_dir = request.get("wind_direction", "N")
    inlet_speed = float(request.get("inlet_speed", 5.0))

    wd_map = {"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)}
    wd = wd_map.get(wind_dir, (0, 1))

    bounds = base["grid_bounds"]
    grid_size = base["grid_size"]
    grid_x = np.linspace(bounds[0], bounds[2], grid_size)
    grid_y = np.linspace(bounds[3], bounds[1], grid_size)

    Ux_base = np.array(base["Ux_grid"], dtype=np.float32)
    Uy_base = np.array(base["Uy_grid"], dtype=np.float32)
    speed_base = np.array(base["speed_grid"], dtype=np.float32)

    trees = [TreeRow(
        cx=t["cx"], cy=t["cy"],
        length=t.get("length", 20),
        angle_deg=t.get("angle_deg", 0),
    ) for t in request.get("trees", [])]

    Ux_c, Uy_c, speed_c = apply_tree_correction(
        Ux_base, Uy_base, speed_base, grid_x, grid_y,
        trees, wd, inlet_speed)

    return {
        "success": True,
        "grid_bounds": bounds,
        "grid_size": grid_size,
        "speed_min": float(np.nanmin(speed_c)),
        "speed_max": float(np.nanmax(speed_c)),
        "speed_grid": speed_c.tolist(),
        "Ux_grid": Ux_c.tolist(),
        "Uy_grid": Uy_c.tolist(),
        "num_trees": len(trees),
    }


# ── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    logger.info("UrbanWind CFD Server starting...")
    logger.info(f"Model path: {MODEL_FILE}")
    logger.info(f"Model exists: {MODEL_FILE.exists()}")
    logger.info(f"Static dir: {STATIC_DIR}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    """Entry point: start the server and open the browser."""
    import os as _os
    url = f"http://{SERVER_HOST}:{SERVER_PORT}"

    # Open browser after server is ready
    def _open_browser():
        time.sleep(3)  # Wait for uvicorn to fully start
        # Use os.startfile on Windows (more reliable than webbrowser)
        if _os.name == "nt":
            _os.startfile(url)
        else:
            import webbrowser
            webbrowser.open(url)

    threading.Thread(target=_open_browser, daemon=True).start()

    logger.info(f"Starting UrbanWind CFD at {url}")
    uvicorn.run(
        "frontend.app:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
