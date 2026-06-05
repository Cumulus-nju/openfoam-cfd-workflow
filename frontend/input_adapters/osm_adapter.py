"""
OpenStreetMap / Overpass API adapter.

Downloads building footprints and metadata from OSM for a given bounding box,
converts them to the unified SitePlan format.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError

from .base import AbstractAdapter
from ..schema import (
    SitePlan, Feature, Geometry, BuildingType, SourceType,
    make_building_feature, BuildingProperties,
)
from ..config import OSM_OVERPASS_URL, OSM_TIMEOUT


# ── OSM tag → BuildingType mapping ──────────────────────────────────────────

_AMENITY_TYPE_MAP: Dict[str, BuildingType] = {
    "university": BuildingType.TEACHING,
    "college": BuildingType.TEACHING,
    "school": BuildingType.TEACHING,
    "library": BuildingType.LIBRARY,
    "canteen": BuildingType.CANTEEN,
    "restaurant": BuildingType.CANTEEN,
    "cafeteria": BuildingType.CANTEEN,
    "laboratory": BuildingType.LAB,
    "research_institute": BuildingType.LAB,
    "gym": BuildingType.GYMNASIUM,
    "sports_centre": BuildingType.GYMNASIUM,
    "office": BuildingType.OFFICE,
    "dormitory": BuildingType.DORMITORY,
}

_BUILDING_VALUE_MAP: Dict[str, BuildingType] = {
    "dormitory": BuildingType.DORMITORY,
    "apartments": BuildingType.DORMITORY,
    "residential": BuildingType.DORMITORY,
    "office": BuildingType.OFFICE,
    "commercial": BuildingType.OFFICE,
    "industrial": BuildingType.LAB,
    "school": BuildingType.TEACHING,
    "university": BuildingType.TEACHING,
    "college": BuildingType.TEACHING,
    "library": BuildingType.LIBRARY,
    "sports_centre": BuildingType.GYMNASIUM,
    "sports_hall": BuildingType.GYMNASIUM,
    "stadium": BuildingType.GYMNASIUM,
    "laboratory": BuildingType.LAB,
    "canteen": BuildingType.CANTEEN,
}


def _infer_building_type(osm_tags: Dict[str, str]) -> Tuple[BuildingType, float]:
    """
    Infer building type and confidence from OSM tags.

    Priority:
    1. amenity tag (strongest signal)
    2. building tag value
    3. building=yes → OTHER with low confidence
    """
    # Check amenity
    amenity = osm_tags.get("amenity", "").lower()
    if amenity in _AMENITY_TYPE_MAP:
        return _AMENITY_TYPE_MAP[amenity], 0.85

    # Check building value
    building_val = osm_tags.get("building", "").lower()
    if building_val in _BUILDING_VALUE_MAP:
        return _BUILDING_VALUE_MAP[building_val], 0.70
    elif building_val == "yes":
        return BuildingType.OTHER, 0.10
    else:
        return BuildingType.OTHER, 0.05


def _extract_levels(osm_tags: Dict[str, str]) -> Optional[int]:
    """Extract number of floors from OSM tags."""
    for key in ("building:levels", "height:levels", "levels"):
        val = osm_tags.get(key, "")
        if val:
            try:
                return int(float(val))
            except ValueError:
                pass
    return None


def _extract_height(osm_tags: Dict[str, str]) -> Optional[float]:
    """Extract building height in meters from OSM tags."""
    for key in ("height", "building:height"):
        val = osm_tags.get(key, "")
        if val:
            # Strip units
            val = val.strip().lower().replace("m", "").replace("metre", "").replace("meter", "")
            try:
                return float(val)
            except ValueError:
                pass
    return None


# ── Overpass query builder ───────────────────────────────────────────────────


def _build_overpass_query(bbox: Tuple[float, float, float, float]) -> str:
    """
    Build an Overpass QL query for building footprints.

    Args:
        bbox: (south, west, north, east) in WGS84 degrees

    Returns:
        Overpass QL query string
    """
    south, west, north, east = bbox
    return f"""
[out:json][timeout:{OSM_TIMEOUT}];
(
  way["building"]({south},{west},{north},{east});
  relation["building"]({south},{west},{north},{east});
);
out body;
>;
out skel qt;
"""


def _query_overpass(query: str) -> Optional[Dict[str, Any]]:
    """Execute an Overpass API query and return parsed JSON."""
    req = Request(
        OSM_OVERPASS_URL,
        data=query.encode("utf-8"),
        headers={
            "User-Agent": "UrbanWind-CFD/0.1 (academic research tool)",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urlopen(req, timeout=OSM_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except URLError as e:
        raise ConnectionError(f"Overpass API request failed: {e}")


# ── Geometry helpers ─────────────────────────────────────────────────────────


def _build_polygon(node_ids: List[int], node_map: Dict[int, Tuple[float, float]]) -> List[List[float]]:
    """
    Build polygon coordinates from a list of node IDs.

    Returns list of [lon, lat] pairs in WGS84 (will be projected later).
    """
    coords = []
    for nid in node_ids:
        if nid in node_map:
            lat, lon = node_map[nid]
            coords.append([lon, lat])
    return coords


# ── Adapter ──────────────────────────────────────────────────────────────────


class OSMAdapter(AbstractAdapter):
    """
    Downloads building footprints from OpenStreetMap via Overpass API.

    Usage:
        adapter = OSMAdapter()
        plan = adapter.parse(bbox=(32.02, 118.78, 32.08, 118.86))
        # Or by place name:
        plan = adapter.parse(place="Nanjing University Gulou Campus")
    """

    name = "osm"

    def validate_source(self, source: Any) -> bool:
        """Check if source is a valid bbox tuple or place name string."""
        if isinstance(source, tuple) and len(source) == 4:
            return all(isinstance(v, (int, float)) for v in source)
        if isinstance(source, str) and len(source) > 0:
            return True
        return False

    def parse(
        self,
        source: Any = None,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        place: Optional[str] = None,
        **kwargs,
    ) -> SitePlan:
        """
        Parse OSM data for a region.

        Args:
            source: Either a bbox tuple (south, west, north, east) or a place name string.
            bbox: Explicit bbox override.
            place: Explicit place name override.

        Returns:
            SitePlan with building features.
        """
        # Resolve input
        resolved_bbox: Optional[Tuple[float, float, float, float]] = None
        if bbox is not None:
            resolved_bbox = bbox
        elif isinstance(source, tuple) and len(source) == 4:
            resolved_bbox = source
        elif place is not None:
            resolved_bbox = self._geocode(place)
        elif isinstance(source, str):
            resolved_bbox = self._geocode(source)

        if resolved_bbox is None:
            raise ValueError("Could not resolve bounding box from input")

        south, west, north, east = resolved_bbox

        # Query Overpass API
        query = _build_overpass_query(resolved_bbox)
        data = _query_overpass(query)

        if data is None:
            raise RuntimeError("Overpass API returned no data")

        # Build node lookup
        node_map: Dict[int, Tuple[float, float]] = {}
        for element in data.get("elements", []):
            if element.get("type") == "node":
                node_map[element["id"]] = (element.get("lat", 0), element.get("lon", 0))

        # Process building ways
        features: List[Feature] = []
        relation_members: Dict[int, List[Dict]] = {}

        for element in data.get("elements", []):
            if element.get("type") == "way":
                tags = element.get("tags", {})
                node_ids = element.get("nodes", [])
                if not node_ids or len(node_ids) < 3:
                    continue

                # Build polygon
                coords = _build_polygon(node_ids, node_map)
                if len(coords) < 3 or coords[0] != coords[-1]:
                    if len(coords) >= 3:
                        coords.append(coords[0])
                    else:
                        continue

                # Infer properties
                btype, conf = _infer_building_type(tags)
                height = _extract_height(tags)
                levels = _extract_levels(tags)

                if height is None and levels is not None:
                    height = levels * 3.3  # Default floor height
                elif height is None:
                    height = BuildingType.default_height(btype)

                if levels is None:
                    levels = round(height / 3.3)

                name = tags.get("name", "")
                name_zh_parts = []
                for k in ("name:zh", "name:zh-CN", "name:zh-Hans"):
                    if k in tags:
                        name_zh_parts.append(tags[k])
                name_zh = name_zh_parts[0] if name_zh_parts else name

                feature = make_building_feature(
                    coords=coords,
                    height=height,
                    building_type=btype,
                    name=name or name_zh,
                    name_zh=name_zh or name,
                    source=SourceType.OSM,
                    confidence=conf,
                    fid=f"osm_w{element['id']}",
                )
                # Preserve raw OSM tags for LLM enrichment
                feature.properties["osm_tags"] = tags
                features.append(feature)

        # Process relations (multipolygon buildings)
        for element in data.get("elements", []):
            if element.get("type") == "relation":
                tags = element.get("tags", {})
                if tags.get("type") != "multipolygon":
                    continue
                # Collect outer way members
                outer_ways: List[List[int]] = []
                for member in element.get("members", []):
                    if member.get("role") == "outer" and member.get("type") == "way":
                        # Find the way's nodes from way elements
                        way_id = member.get("ref")
                        for we in data.get("elements", []):
                            if we.get("type") == "way" and we.get("id") == way_id:
                                outer_ways.append(we.get("nodes", []))

                if outer_ways:
                    # Use the largest outer ring
                    largest = max(outer_ways, key=len)
                    coords = _build_polygon(largest, node_map)
                    if len(coords) >= 3 and coords[0] != coords[-1]:
                        coords.append(coords[0])

                    btype, conf = _infer_building_type(tags)
                    height = _extract_height(tags)
                    levels = _extract_levels(tags)
                    if height is None and levels is not None:
                        height = levels * 3.3
                    elif height is None:
                        height = BuildingType.default_height(btype)
                    if levels is None:
                        levels = round(height / 3.3)

                    name = tags.get("name", "")
                    feature = make_building_feature(
                        coords=coords,
                        height=height,
                        building_type=btype,
                        name=name,
                        name_zh=name,
                        source=SourceType.OSM,
                        confidence=conf,
                        fid=f"osm_r{element['id']}",
                    )
                    feature.properties["osm_tags"] = tags
                    features.append(feature)

        # Build SitePlan
        center_lat = (south + north) / 2
        center_lon = (west + east) / 2

        plan = SitePlan(
            features=features,
            metadata={
                "source": "osm",
                "bbox": [west, south, east, north],
                "center": [center_lon, center_lat],
                "num_buildings": len(features),
                "query_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "data_license": "OpenStreetMap © OpenStreetMap contributors (ODbL)",
            },
        )
        return plan

    def _geocode(self, place: str) -> Tuple[float, float, float, float]:
        """
        Simple geocoding using Nominatim (OSM's geocoder).

        Returns: (south, west, north, east) bbox.
        """
        import urllib.parse
        encoded = urllib.parse.quote(place)
        url = (
            f"https://nominatim.openstreetmap.org/search"
            f"?q={encoded}&format=json&limit=1&polygon_geojson=0"
        )
        req = Request(url, headers={"User-Agent": "UrbanWind-CFD/0.1 (academic)"})
        try:
            with urlopen(req, timeout=15) as resp:
                results = json.loads(resp.read().decode("utf-8"))
                if results:
                    r = results[0]
                    bbox = r.get("boundingbox", [])
                    if len(bbox) == 4:
                        south, north, west, east = [float(v) for v in bbox]
                        return (south, west, north, east)
        except Exception:
            pass

        raise ValueError(
            f"Could not geocode '{place}'. Please provide explicit bbox coordinates: "
            f"(south_lat, west_lon, north_lat, east_lon)"
        )

    def get_metadata(self) -> Dict[str, Any]:
        return {
            "adapter": self.name,
            "supported_formats": ["bbox", "place_name"],
            "requires_network": True,
            "cost": "free (OpenStreetMap)",
            "rate_limit": "Please respect OSM servers — avoid bulk requests",
        }
