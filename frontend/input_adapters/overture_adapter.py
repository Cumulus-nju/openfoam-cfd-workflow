"""
Overture Maps adapter — uses locally cached building data via overturemaps CLI.

First query downloads from S3 and caches; subsequent queries are instant.
Requires: pip install overturemaps
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import AbstractAdapter
from ..schema import (
    SitePlan, Feature, BuildingType, SourceType,
    make_building_feature,
)

CACHE_DIR = Path("/opt/overture_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def _bbox_to_cache_key(west: float, south: float, east: float, north: float) -> str:
    """Create a cache key from bbox."""
    return f"{west:.4f}_{south:.4f}_{east:.4f}_{north:.4f}.geojson"


def _download_overture(west: float, south: float, east: float, north: float) -> Optional[Dict]:
    """Download building data from Overture Maps S3."""
    bbox = f"{west},{south},{east},{north}"
    cache_key = _bbox_to_cache_key(west, south, east, north)
    cache_path = CACHE_DIR / cache_key

    # Return cached if exists (less than 7 days old)
    if cache_path.exists():
        age = time.time() - os.path.getmtime(cache_path)
        if age < 7 * 86400:
            with open(cache_path) as f:
                return json.load(f)

    # Download from Overture
    try:
        result = subprocess.run(
            ["overturemaps", "download", "--bbox", bbox, "-f", "geojson", "--type", "building"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            # Cache it
            with open(cache_path, "w") as f:
                json.dump(data, f)
            return data
    except Exception:
        pass

    # Fallback: return cached even if old
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)
    return None


def _infer_building_type(props: Dict) -> Tuple[BuildingType, float]:
    """Infer building type from Overture properties."""
    subtype = props.get("subtype", "") or ""
    class_name = props.get("class", "") or ""

    if subtype in ("university", "college", "school"):
        return BuildingType.TEACHING, 0.7
    if subtype in ("dormitory", "residential"):
        return BuildingType.DORMITORY, 0.7
    if subtype in ("office", "commercial"):
        return BuildingType.OFFICE, 0.7
    if subtype in ("canteen", "restaurant", "cafeteria"):
        return BuildingType.CANTEEN, 0.7
    if subtype in ("laboratory", "research"):
        return BuildingType.LAB, 0.7
    if subtype in ("library",):
        return BuildingType.LIBRARY, 0.8
    if subtype in ("sports_centre", "stadium", "gymnasium"):
        return BuildingType.GYMNASIUM, 0.8

    # Use class as hint
    if "education" in class_name:
        return BuildingType.TEACHING, 0.5
    if "residential" in class_name:
        return BuildingType.DORMITORY, 0.5

    return BuildingType.OTHER, 0.3


class OvertureAdapter(AbstractAdapter):
    """
    Downloads building data from Overture Maps (Microsoft + Meta + more).
    Precise building footprints with global coverage.
    Uses local cache for speed after first query.

    Usage:
        adapter = OvertureAdapter()
        plan = adapter.parse(bbox=(32.05, 118.78, 32.07, 118.80))
    """

    name = "overture"

    def validate_source(self, source: Any) -> bool:
        if isinstance(source, tuple) and len(source) == 4:
            return all(isinstance(v, (int, float)) for v in source)
        return False

    def parse(
        self,
        source: Any = None,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        **kwargs,
    ) -> SitePlan:
        """Parse Overture buildings for a bbox."""
        resolved = bbox or source
        if not isinstance(resolved, tuple) or len(resolved) != 4:
            raise ValueError("bbox required: (south, west, north, east)")

        south, west, north, east = resolved
        center_lat = (south + north) / 2
        center_lon = (west + east) / 2

        # Check cache first, then download
        print(f"  Overture: querying bbox ({west:.4f},{south:.4f})-({east:.4f},{north:.4f})")
        data = _download_overture(west, south, east, north)

        if not data:
            raise RuntimeError("Overture download failed")

        features_raw = data.get("features", [])
        print(f"  Overture: found {len(features_raw)} buildings")

        # Convert to SitePlan features
        features = []
        for i, feat in enumerate(features_raw):
            geom = feat.get("geometry", {})
            props = feat.get("properties", {})
            if geom.get("type") != "Polygon":
                continue

            coords = geom.get("coordinates", [])
            if not coords or not coords[0] or len(coords[0]) < 4:
                continue

            btype, confidence = _infer_building_type(props)
            height = props.get("height") or props.get("building_height")
            levels = props.get("num_floors") or props.get("building_levels")

            if height is None and levels is not None:
                try:
                    height = float(levels) * 3.3
                except (ValueError, TypeError):
                    height = BuildingType.default_height(btype)
            elif height is None:
                height = BuildingType.default_height(btype)
            else:
                try:
                    height = float(height)
                except (ValueError, TypeError):
                    height = BuildingType.default_height(btype)

            name = props.get("name", "") or f"Building_{i+1}"

            feature = make_building_feature(
                coords=coords,
                height=float(height),
                building_type=btype,
                name=name,
                name_zh=name,
                source=SourceType.OSM,  # Use OSM source type for consistency
                confidence=confidence,
                fid=f"ot_{props.get('id', i)}",
            )
            features.append(feature)

        plan = SitePlan(
            features=features,
            metadata={
                "source": "overture",
                "bbox": [west, south, east, north],
                "center": [center_lon, center_lat],
                "center_lat": center_lat,
                "center_lon": center_lon,
                "num_buildings": len(features),
                "query_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "data_license": "Overture Maps (Microsoft, Meta, Esri — ODbL)",
            },
        )
        return plan

    def get_metadata(self) -> Dict[str, Any]:
        return {
            "adapter": self.name,
            "supported_formats": ["bbox"],
            "requires_network": True,
            "cost": "free (Overture Maps Foundation)",
            "coverage": "Global (including China, excellent)",
            "note": "First query per area may be slow (download + cache). Subsequent queries are instant.",
        }
