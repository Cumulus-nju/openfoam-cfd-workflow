"""
DXF/DWG CAD drawing adapter.

Parses architectural CAD drawings (DXF format) to extract building footprints
and annotation text, then converts to unified SitePlan format.

Key design decisions:
- Uses ezdxf for pure-Python DXF parsing (no AutoCAD dependency)
- Extracts closed polylines as building footprints
- Parses text/mtext near footprints to infer building name, floors, height
- Handles common Chinese campus drawing conventions (floor annotations like "5F", "6层")
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import AbstractAdapter
from ..schema import (
    SitePlan, Feature, BuildingType, SourceType,
    make_building_feature,
)


# ── Text annotation parsers ──────────────────────────────────────────────────

_FLOOR_PATTERNS = [
    re.compile(r"(\d+)\s*[Ff][lL]?[oO]?[oO]?[rR]?"),    # "5F", "5Floors"
    re.compile(r"(\d+)\s*层"),                              # "6层", "6 层"
    re.compile(r"(\d+)\s*樓"),                              # "6樓" (traditional)
    re.compile(r"[Ff](\d+)"),                               # "F5", "f6"
    re.compile(r"层数[:：]\s*(\d+)"),                       # "层数: 6"
    re.compile(r"高度[:：]\s*(\d+\.?\d*)\s*[mM米]"),       # "高度: 20m"
]

_BUILDING_TYPE_PATTERNS = [
    (re.compile(r"教学|教室|teach|classroom", re.I), BuildingType.TEACHING),
    (re.compile(r"宿舍|公寓|dorm|apartment", re.I), BuildingType.DORMITORY),
    (re.compile(r"食堂|餐厅|canteen|cafeteria|dining", re.I), BuildingType.CANTEEN),
    (re.compile(r"图书馆|图书|library", re.I), BuildingType.LIBRARY),
    (re.compile(r"行政|办公|office|admin", re.I), BuildingType.OFFICE),
    (re.compile(r"实验|lab|实验|科研|研究", re.I), BuildingType.LAB),
    (re.compile(r"体育|运动|gym|sport|健身|体育馆", re.I), BuildingType.GYMNASIUM),
]


def _parse_floors_from_text(text: str) -> Optional[int]:
    """Try to extract floor count from annotation text."""
    for pat in _FLOOR_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                floors = int(m.group(1))
                if 1 <= floors <= 200:
                    return floors
            except ValueError:
                pass
    return None


def _parse_height_from_text(text: str) -> Optional[float]:
    """Try to extract height from annotation text."""
    pat = re.compile(r"高度[:：]\s*(\d+\.?\d*)\s*[mM米]")
    m = pat.search(text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _infer_type_from_text(text: str) -> Tuple[BuildingType, float]:
    """Infer building type from annotation text."""
    for pat, btype in _BUILDING_TYPE_PATTERNS:
        if pat.search(text):
            return btype, 0.75
    return BuildingType.OTHER, 0.05


# ── Geometry helpers ─────────────────────────────────────────────────────────


def _polyline_is_closed(points: List[Tuple[float, float]], tolerance: float = 0.01) -> bool:
    """Check if a polyline is approximately closed."""
    if len(points) < 3:
        return False
    dx = points[0][0] - points[-1][0]
    dy = points[0][1] - points[-1][1]
    return (dx * dx + dy * dy) < tolerance * tolerance


def _polyline_to_coords(points) -> List[List[float]]:
    """Convert DXF points to [[x, y], ...] list."""
    coords = [[float(p[0]), float(p[1])] for p in points]
    if coords and coords[0] != coords[-1]:
        coords.append(coords[0])
    return coords


# ── Adapter ──────────────────────────────────────────────────────────────────


class DXFAdapter(AbstractAdapter):
    """
    Parse DXF CAD files to extract building footprints and annotations.

    Handles:
    - LWPOLYLINE and POLYLINE as building footprints (closed or nearly-closed)
    - TEXT and MTEXT as building annotations
    - Proximity-based matching of text to footprints
    """

    name = "dxf"

    def validate_source(self, source: Any) -> bool:
        """Check if source is a .dxf file path."""
        if isinstance(source, (str, Path)):
            p = Path(source)
            return p.exists() and p.suffix.lower() in (".dxf", ".dwg")
        return False

    def parse(
        self,
        source: Any,
        floor_height: float = 3.3,
        default_height: float = 12.0,
        **kwargs,
    ) -> SitePlan:
        """
        Parse a DXF file.

        Args:
            source: Path to .dxf file
            floor_height: Default floor-to-floor height in meters
            default_height: Fallback height when no annotation found

        Returns:
            SitePlan with building features
        """
        try:
            import ezdxf
        except ImportError:
            raise ImportError(
                "ezdxf is required for DXF parsing. Install with: pip install ezdxf"
            )

        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"DXF file not found: {source}")

        doc = ezdxf.readfile(str(path))
        msp = doc.modelspace()

        # ── Extract footprints (closed polylines) ──
        footprints: List[Dict[str, Any]] = []
        poly_types = {"LWPOLYLINE", "POLYLINE"}
        for entity in msp:
            if entity.dxftype() not in poly_types:
                continue
            try:
                points = list(entity.vertices())
                # Filter to only include valid 2D/3D points
                pts = [(float(p.dxf.location[0]), float(p.dxf.location[1])) for p in points]
            except Exception:
                continue

            if len(pts) < 3:
                continue

            if _polyline_is_closed(pts):
                coords = _polyline_to_coords(pts)
                # Compute centroid
                cx = sum(p[0] for p in pts[:-1]) / (len(pts) - 1)
                cy = sum(p[1] for p in pts[:-1]) / (len(pts) - 1)
                footprints.append({
                    "coords": coords,
                    "centroid": (cx, cy),
                    "entity": entity,
                })

        # ── Extract text annotations ──
        annotations: List[Dict[str, Any]] = []
        text_types = {"TEXT", "MTEXT"}
        for entity in msp:
            if entity.dxftype() not in text_types:
                continue
            try:
                if entity.dxftype() == "TEXT":
                    text = entity.dxf.text
                    pos = (float(entity.dxf.insert[0]), float(entity.dxf.insert[1]))
                else:  # MTEXT
                    text = entity.text
                    pos = (float(entity.dxf.insert[0]), float(entity.dxf.insert[1]))
                annotations.append({"text": text, "pos": pos})
            except Exception:
                continue

        # ── Match annotations to footprints by proximity ──
        features: List[Feature] = []
        used_annotations = set()

        for fp in footprints:
            cx, cy = fp["centroid"]

            # Find closest annotation within 50 units (meters if scaled correctly)
            best_dist = 50.0
            best_ann = None
            for i, ann in enumerate(annotations):
                if i in used_annotations:
                    continue
                dx = ann["pos"][0] - cx
                dy = ann["pos"][1] - cy
                dist = (dx * dx + dy * dy) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best_ann = (i, ann)

            # Parse attributes from annotation
            name = ""
            name_zh = ""
            floors = None
            height = None
            btype = BuildingType.OTHER
            conf = 0.0

            if best_ann is not None:
                i, ann = best_ann
                used_annotations.add(i)
                text = ann["text"].strip()

                btype, conf = _infer_type_from_text(text)
                floors = _parse_floors_from_text(text)
                height = _parse_height_from_text(text)
                name_zh = text.split("\n")[0].strip()  # First line as name
                name = name_zh

            # Compute height
            if height is None and floors is not None:
                height = floors * floor_height
            elif height is None:
                height = BuildingType.default_height(btype)

            if floors is None:
                floors = round(height / floor_height)

            feature = make_building_feature(
                coords=fp["coords"],
                height=height,
                building_type=btype,
                name=name,
                name_zh=name_zh,
                source=SourceType.DXF,
                confidence=conf,
            )
            features.append(feature)

        # ── Orphan annotations as small buildings with inferred properties ──
        for i, ann in enumerate(annotations):
            if i in used_annotations:
                continue
            text = ann["text"].strip()
            btype, conf = _infer_type_from_text(text)
            height = _parse_height_from_text(text) or BuildingType.default_height(btype)
            floors = _parse_floors_from_text(text) or round(height / floor_height)

            # Create a default rectangular footprint (20m × 15m) centered on text
            x, y = ann["pos"]
            w, h = 20.0, 15.0
            coords = [
                [x - w / 2, y - h / 2],
                [x + w / 2, y - h / 2],
                [x + w / 2, y + h / 2],
                [x - w / 2, y + h / 2],
                [x - w / 2, y - h / 2],
            ]

            feature = make_building_feature(
                coords=coords,
                height=height,
                building_type=btype,
                name=text,
                name_zh=text,
                source=SourceType.DXF,
                confidence=conf,
            )
            features.append(feature)

        plan = SitePlan(
            features=features,
            metadata={
                "source": "dxf",
                "file": str(path.name),
                "num_buildings": len(features),
                "num_annotations_used": len(used_annotations),
                "num_orphan_annotations": len(annotations) - len(used_annotations),
            },
        )
        return plan

    def get_metadata(self) -> Dict[str, Any]:
        return {
            "adapter": self.name,
            "supported_formats": [".dxf"],
            "requires_network": False,
            "cost": "free",
            "limitations": [
                "DWG format not directly supported (convert to DXF first)",
                "3D DXF entities ignored (only 2D footprints)",
                "Layer names not currently used for type inference",
                "Proximity matching assumes annotations are near their building",
            ],
        }
