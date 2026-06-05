"""
UrbanWind CFD — Unified Geometry Schema

All input adapters convert to this canonical GeoJSON-based format.
The schema is deliberately LLM-friendly: human-readable field names,
Chinese-compatible labels, and explicit confidence/uncertainty tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import json
import uuid


# ── Enums ────────────────────────────────────────────────────────────────────


class BuildingType(str, Enum):
    """Building category — used by LLM to infer height/geometry defaults."""
    TEACHING = "teaching"        # 教学楼
    DORMITORY = "dormitory"      # 宿舍楼
    CANTEEN = "canteen"          # 食堂
    LIBRARY = "library"          # 图书馆
    OFFICE = "office"            # 行政楼/办公楼
    LAB = "lab"                  # 实验楼
    GYMNASIUM = "gymnasium"      # 体育馆
    OTHER = "other"              # 其他建筑

    @classmethod
    def default_height(cls, btype: "BuildingType") -> float:
        """Return default height in meters for a building type (Chinese campus norms)."""
        defaults = {
            cls.TEACHING: 16.5,    # 5 floors × 3.3m
            cls.DORMITORY: 19.8,   # 6 floors × 3.3m
            cls.CANTEEN: 8.0,      # 2 floors × 4m (tall ceilings)
            cls.LIBRARY: 25.0,     # landmark building
            cls.OFFICE: 20.0,      # 6 floors × 3.3m
            cls.LAB: 15.0,         # 4-5 floors
            cls.GYMNASIUM: 10.0,   # large span, single-story feel
            cls.OTHER: 12.0,       # generic default
        }
        return defaults.get(btype, 12.0)

    @classmethod
    def default_floors(cls, btype: "BuildingType") -> int:
        """Return default floor count."""
        return round(cls.default_height(btype) / 3.3)


class RoofType(str, Enum):
    FLAT = "flat"
    PITCHED = "pitched"
    ARCHED = "arched"
    UNKNOWN = "unknown"


class SourceType(str, Enum):
    OSM = "osm"
    DXF = "dxf"
    MANUAL = "manual"
    LLM_INFERRED = "llm_inferred"


class BikeCategory(str, Enum):
    """Bike station placement category for research design."""
    OPEN = "open"            # Open/reference area — baseline wind
    WAKE = "wake"            # Building wake/sheltered zone
    CANYON = "canyon"        # Street canyon — venturi effect
    CORNER = "corner"        # Corner/shear zone


# ── Property Schemas ─────────────────────────────────────────────────────────


@dataclass
class BuildingProperties:
    """Properties for a building feature in the GeoJSON."""
    building_type: BuildingType = BuildingType.OTHER
    height: float = 12.0           # meters
    num_floors: int = 4
    roof_type: RoofType = RoofType.UNKNOWN
    name: str = ""                 # Human-readable name, e.g. "图书馆"
    name_zh: str = ""              # Chinese name
    confidence: float = 0.0        # LLM inference confidence (0-1)
    source: SourceType = SourceType.MANUAL
    osm_tags: Dict[str, str] = field(default_factory=dict)  # Raw OSM tags if applicable

    def to_dict(self) -> Dict[str, Any]:
        return {
            "building_type": self.building_type.value,
            "height": self.height,
            "num_floors": self.num_floors,
            "roof_type": self.roof_type.value,
            "name": self.name,
            "name_zh": self.name_zh,
            "confidence": self.confidence,
            "source": self.source.value,
            "osm_tags": self.osm_tags,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BuildingProperties":
        return cls(
            building_type=BuildingType(d.get("building_type", "other")),
            height=float(d.get("height", 12.0)),
            num_floors=int(d.get("num_floors", 4)),
            roof_type=RoofType(d.get("roof_type", "unknown")),
            name=str(d.get("name", "")),
            name_zh=str(d.get("name_zh", "")),
            confidence=float(d.get("confidence", 0.0)),
            source=SourceType(d.get("source", "manual")),
            osm_tags=dict(d.get("osm_tags", {})),
        )


@dataclass
class BikeProperties:
    """Properties for a bike station feature."""
    category: BikeCategory = BikeCategory.OPEN
    name: str = ""
    parking_capacity: int = 20    # Number of bikes
    confidence: float = 0.0
    source: SourceType = SourceType.MANUAL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "name": self.name,
            "parking_capacity": self.parking_capacity,
            "confidence": self.confidence,
            "source": self.source.value,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BikeProperties":
        return cls(
            category=BikeCategory(d.get("category", "open")),
            name=str(d.get("name", "")),
            parking_capacity=int(d.get("parking_capacity", 20)),
            confidence=float(d.get("confidence", 0.0)),
            source=SourceType(d.get("source", "manual")),
        )


# ── GeoJSON Schema ───────────────────────────────────────────────────────────


@dataclass
class Geometry:
    """Simple geometry wrapper."""
    type: str = "Polygon"
    coordinates: List[List[List[float]]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "coordinates": self.coordinates}


@dataclass
class Feature:
    """A single GeoJSON Feature (building, bike station, or boundary)."""
    type: str = "Feature"
    id: str = ""
    geometry: Geometry = field(default_factory=Geometry)
    properties: Dict[str, Any] = field(default_factory=dict)
    category: str = "building"  # "building", "bike_station", "boundary"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "geometry": self.geometry.to_dict(),
            "properties": self.properties,
            "category": self.category,
        }

    @property
    def centroid(self) -> Tuple[float, float]:
        """Compute approximate centroid from polygon coordinates."""
        coords = self.geometry.coordinates
        if not coords or not coords[0]:
            return (0.0, 0.0)
        ring = coords[0]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        """Return (min_x, min_y, max_x, max_y)."""
        coords = self.geometry.coordinates
        if not coords or not coords[0]:
            return (0.0, 0.0, 0.0, 0.0)
        ring = coords[0]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass
class SitePlan:
    """
    Top-level container for a complete site plan.

    This is the canonical representation that flows through the pipeline:
    Input → SitePlan → LLM enrichment → SitePlan → OpenFOAM case
    """
    type: str = "FeatureCollection"
    features: List[Feature] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ── Convenience accessors ──

    @property
    def buildings(self) -> List[Feature]:
        return [f for f in self.features if f.category == "building"]

    @property
    def bike_stations(self) -> List[Feature]:
        return [f for f in self.features if f.category == "bike_station"]

    @property
    def boundaries(self) -> List[Feature]:
        return [f for f in self.features if f.category == "boundary"]

    @property
    def overall_bbox(self) -> Tuple[float, float, float, float]:
        """Compute bounding box of all features."""
        if not self.features:
            return (0.0, 0.0, 100.0, 100.0)
        all_bboxes = [f.bbox for f in self.features if f.category in ("building", "boundary")]
        if not all_bboxes:
            return (0.0, 0.0, 100.0, 100.0)
        min_x = min(b[0] for b in all_bboxes)
        min_y = min(b[1] for b in all_bboxes)
        max_x = max(b[2] for b in all_bboxes)
        max_y = max(b[3] for b in all_bboxes)
        return (min_x, min_y, max_x, max_y)

    # ── I/O ──

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "features": [f.to_dict() for f in self.features],
            "metadata": self.metadata,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SitePlan":
        features = []
        for fd in d.get("features", []):
            feat = Feature(
                type=fd.get("type", "Feature"),
                id=fd.get("id", str(uuid.uuid4().hex[:8])),
                geometry=Geometry(**fd.get("geometry", {})),
                properties=fd.get("properties", {}),
                category=fd.get("category", "building"),
            )
            features.append(feat)
        return cls(
            type=d.get("type", "FeatureCollection"),
            features=features,
            metadata=d.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, text: str) -> "SitePlan":
        return cls.from_dict(json.loads(text))

    @classmethod
    def from_file(cls, path: str) -> "SitePlan":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_json(f.read())

    def to_file(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())


# ── Factory Helpers ──────────────────────────────────────────────────────────


def make_building_feature(
    coords: List[List[float]],
    height: float = 12.0,
    building_type: BuildingType = BuildingType.OTHER,
    name: str = "",
    name_zh: str = "",
    source: SourceType = SourceType.MANUAL,
    confidence: float = 0.0,
    fid: Optional[str] = None,
) -> Feature:
    """Create a building Feature with standard properties."""
    props = BuildingProperties(
        building_type=building_type,
        height=height,
        num_floors=round(height / 3.3),
        name=name or name_zh,
        name_zh=name_zh or name,
        source=source,
        confidence=confidence,
    )
    # Ensure closed polygon
    if coords and coords[0] != coords[-1]:
        coords = coords + [coords[0]]
    return Feature(
        id=fid or uuid.uuid4().hex[:8],
        category="building",
        geometry=Geometry(type="Polygon", coordinates=[coords]),
        properties=props.to_dict(),
    )


def make_bike_feature(
    cx: float,
    cy: float,
    category: BikeCategory = BikeCategory.OPEN,
    name: str = "",
    parking_capacity: int = 20,
) -> Feature:
    """Create a bike station Feature (represented as a small point/square)."""
    # Represent bike station as a 2m×4m rectangle (typical bike parking footprint)
    hw, hh = 2.0, 1.0  # half-width, half-height
    coords = [
        [cx - hw, cy - hh],
        [cx + hw, cy - hh],
        [cx + hw, cy + hh],
        [cx - hw, cy + hh],
        [cx - hw, cy - hh],
    ]
    props = BikeProperties(
        category=category,
        name=name or f"bike_{uuid.uuid4().hex[:4]}",
        parking_capacity=parking_capacity,
    )
    return Feature(
        id=f"bike_{uuid.uuid4().hex[:6]}",
        category="bike_station",
        geometry=Geometry(type="Polygon", coordinates=[coords]),
        properties=props.to_dict(),
    )


def make_boundary_feature(
    coords: List[List[float]],
    name: str = "domain",
) -> Feature:
    """Create a domain boundary feature."""
    if coords and coords[0] != coords[-1]:
        coords = coords + [coords[0]]
    return Feature(
        id=f"boundary_{name}",
        category="boundary",
        geometry=Geometry(type="Polygon", coordinates=[coords]),
        properties={"name": name},
    )


# ── Validation ───────────────────────────────────────────────────────────────


def validate_site_plan(plan: SitePlan) -> List[str]:
    """Validate a SitePlan and return list of issues (empty = valid)."""
    issues = []

    if plan.type != "FeatureCollection":
        issues.append("Top-level type must be 'FeatureCollection'")

    building_ids = set()
    for i, feat in enumerate(plan.features):
        if not feat.id:
            issues.append(f"Feature {i}: missing id")
        elif feat.id in building_ids:
            issues.append(f"Feature {i}: duplicate id '{feat.id}'")
        else:
            building_ids.add(feat.id)

        if feat.category == "building":
            if not feat.geometry.coordinates or not feat.geometry.coordinates[0]:
                issues.append(f"Building '{feat.id}': empty geometry")
            h = feat.properties.get("height", 0)
            if h <= 0:
                issues.append(f"Building '{feat.id}': height must be > 0, got {h}")

    return issues
