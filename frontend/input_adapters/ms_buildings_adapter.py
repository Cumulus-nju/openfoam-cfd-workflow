"""
Microsoft Global ML Building Footprints adapter.

Uses Microsoft's publicly available building footprint dataset (free, no API key).
Data source: https://github.com/microsoft/GlobalMLBuildingFootprints

Tile-based GeoJSON access via QuadKey tiles at zoom level 9 (~150km² per tile).
"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

from .base import AbstractAdapter
from ..schema import (
    SitePlan, Feature, BuildingType, SourceType,
    make_building_feature,
)
from ..config import OSM_TIMEOUT

MS_BUILDINGS_URL = "https://mlbfpstorage.blob.core.windows.net/geojson/{quadkey}.geojson"
ZOOM = 9  # Microsoft uses zoom level 9

def _latlon_to_quadkey(lat: float, lon: float, zoom: int = ZOOM) -> str:
    """Convert WGS84 to Microsoft QuadKey tile."""
    n = 1 << zoom
    tx = int((lon + 180.0) / 360.0 * n) % n
    ty = int((1.0 - math.log(math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n)
    qk = []
    for i in range(zoom, 0, -1):
        d = 0
        mask = 1 << (i - 1)
        if tx & mask: d += 1
        if ty & mask: d += 2
        qk.append(str(d))
    return ''.join(qk)

def _bbox_to_quadkeys(south: float, west: float, north: float, east: float, zoom: int = ZOOM) -> List[str]:
    """Get all quadkeys covering a bounding box."""
    qks = set()
    # Sample points across the bbox
    steps = 4
    for i in range(steps + 1):
        for j in range(steps + 1):
            lat = south + (north - south) * i / steps
            lon = west + (east - west) * j / steps
            qks.add(_latlon_to_quadkey(lat, lon, zoom))
    return list(qks)

def _download_quadkey(qk: str, timeout: int = 30) -> Optional[Dict]:
    """Download building GeoJSON for a quadkey tile."""
    url = MS_BUILDINGS_URL.format(quadkey=qk)
    try:
        req = Request(url, headers={"User-Agent": "UrbanWind-CFD/0.1 (academic)"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

def _parse_features(geojson: Dict) -> List[Dict]:
    """Extract building polygons from Microsoft GeoJSON."""
    buildings = []
    for feat in geojson.get("features", []):
        geom = feat.get("geometry", {})
        props = feat.get("properties", {})
        if geom.get("type") == "Polygon":
            coords = geom.get("coordinates", [])
            if coords and coords[0]:
                buildings.append({
                    "coordinates": coords,
                    "height": props.get("height", None),
                    "levels": props.get("levels", None),
                })
    return buildings

def _bbox_filter(coords: List[List[float]], west: float, east: float, south: float, north: float) -> bool:
    """Check if polygon center is within bbox."""
    if not coords or not coords[0]: return False
    xs = [p[0] for p in coords[0]]
    ys = [p[1] for p in coords[0]]
    cx, cy = sum(xs)/len(xs), sum(ys)/len(ys)
    return west <= cx <= east and south <= cy <= north


class MSBuildingsAdapter(AbstractAdapter):
    """
    Downloads building footprints from Microsoft Global ML Building Footprints.

    Free, no API key required. Good coverage in China where OSM is sparse.

    Usage:
        adapter = MSBuildingsAdapter()
        plan = adapter.parse(bbox=(32.02, 118.78, 32.08, 118.86))
    """

    name = "ms_buildings"

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
        """Parse MS buildings for a bbox region."""
        resolved = bbox or source
        if not isinstance(resolved, tuple) or len(resolved) != 4:
            raise ValueError("bbox required: (south, west, north, east)")

        south, west, north, east = resolved
        center_lat = (south + north) / 2
        center_lon = (west + east) / 2

        # Get quadkeys
        qks = _bbox_to_quadkeys(south, west, north, east)
        print(f"  MS Buildings: {len(qks)} tile(s) for this area")

        # Download tiles
        all_buildings = []
        seen_geoms = set()
        for qk in qks:
            gj = _download_quadkey(qk)
            if not gj:
                continue
            buildings = _parse_features(gj)
            for b in buildings:
                if _bbox_filter(b["coordinates"], west, east, south, north):
                    # Deduplicate by centroid
                    xs = [p[0] for p in b["coordinates"][0]]
                    ys = [p[1] for p in b["coordinates"][0]]
                    key = (round(sum(xs)/len(xs), 6), round(sum(ys)/len(ys), 6))
                    if key not in seen_geoms:
                        seen_geoms.add(key)
                        all_buildings.append(b)

        print(f"  MS Buildings: {len(all_buildings)} buildings found")

        # Convert to SitePlan features
        features = []
        for i, b in enumerate(all_buildings):
            # MS data doesn't have building type - all default to OTHER
            btype = BuildingType.OTHER
            height = b.get("height")
            levels = b.get("levels")
            if height is None and levels is not None:
                height = levels * 3.3
            elif height is None:
                height = BuildingType.default_height(btype)

            coords = b["coordinates"]
            feature = make_building_feature(
                coords=coords,
                height=float(height) if height else 12.0,
                building_type=btype,
                name=f"Building_{i+1}",
                name_zh=f"建筑_{i+1}",
                source=SourceType.MANUAL,
                confidence=0.5,
                fid=f"ms_{i}",
            )
            features.append(feature)

        plan = SitePlan(
            features=features,
            metadata={
                "source": "ms_buildings",
                "bbox": [west, south, east, north],
                "center": [center_lon, center_lat],
                "center_lat": center_lat,
                "center_lon": center_lon,
                "num_buildings": len(features),
                "query_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "data_license": "Microsoft Global ML Building Footprints (ODbL)",
            },
        )
        return plan

    def get_metadata(self) -> Dict[str, Any]:
        return {
            "adapter": self.name,
            "supported_formats": ["bbox"],
            "requires_network": True,
            "cost": "free (Microsoft)",
            "coverage": "Global (including China)",
        }
