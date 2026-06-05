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
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .config import SERVER_HOST, SERVER_PORT, STATIC_DIR, CFD_CASES_DIR, MODEL_FILE
from .schema import SitePlan, BuildingType, SourceType, validate_site_plan
from .input_adapters import OSMAdapter, DXFAdapter, ManualAdapter
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
    tmp_path = Path(f"D:/Phase2_CFD_ML/tmp_{uuid.uuid4().hex[:8]}.dxf")
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

    try:
        case_dir = assemble_case(
            plan, case_name,
            wind_speed=wind_speed,
            wind_direction=wind_direction,
            n_bikes=n_bikes,
        )

        return {
            "success": True,
            "case_dir": str(case_dir),
            "wsl_path": f"/mnt/d/Phase2_CFD_ML/cfd_cases/{case_name}",
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
    import webbrowser
    url = f"http://{SERVER_HOST}:{SERVER_PORT}"

    # Open browser after a short delay
    def _open():
        time.sleep(1.5)
        webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()

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
