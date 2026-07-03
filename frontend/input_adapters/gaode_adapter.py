"""
Gaode (Amap) POI adapter — uses Gaode's REST API to search for buildings.

Each POI is assigned a standard rectangular footprint based on its type,
since Gaode's REST API returns point locations, not polygon outlines.
"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen, quote

from .base import AbstractAdapter
from ..schema import (
    SitePlan, Feature, BuildingType, SourceType,
    make_building_feature,
)

GAODE_KEY = "2ec57c587e4fe5b8652faecea9847c60"
GAODE_SEARCH_URL = "https://restapi.amap.com/v3/place/text"
GAODE_AROUND_URL = "https://restapi.amap.com/v3/place/around"
GAODE_PAGE_SIZE = 25  # max per page

# ── Gaode type → BuildingType mapping ──────────────────────
_GAODE_TYPE_MAP: Dict[str, BuildingType] = {
    "高等院校": BuildingType.TEACHING,
    "中学": BuildingType.TEACHING,
    "小学": BuildingType.TEACHING,
    "职业技术学校": BuildingType.TEACHING,
    "科研机构": BuildingType.LAB,
    "图书馆": BuildingType.LIBRARY,
    "博物馆": BuildingType.LIBRARY,
    "餐饮": BuildingType.CANTEEN,
    "餐厅": BuildingType.CANTEEN,
    "食堂": BuildingType.CANTEEN,
    "宿舍": BuildingType.DORMITORY,
    "住宅小区": BuildingType.DORMITORY,
    "公司企业": BuildingType.OFFICE,
    "写字楼": BuildingType.OFFICE,
    "政府机关": BuildingType.OFFICE,
    "医疗": BuildingType.OTHER,
    "体育场馆": BuildingType.GYMNASIUM,
    "购物": BuildingType.OTHER,
}

# ── Building type → default footprint (width, depth) in meters ──
_STANDARD_SIZES: Dict[BuildingType, Tuple[float, float]] = {
    BuildingType.TEACHING: (50, 25),
    BuildingType.LIBRARY: (60, 35),
    BuildingType.LAB: (45, 30),
    BuildingType.CANTEEN: (40, 30),
    BuildingType.DORMITORY: (55, 15),
    BuildingType.OFFICE: (40, 30),
    BuildingType.GYMNASIUM: (50, 40),
    BuildingType.OTHER: (30, 20),
}


def _classify_gaode_type(poi_type: str, tags: str = "") -> BuildingType:
    """Map Gaode POI type string to our BuildingType."""
    t = poi_type or ""
    for key, btype in _GAODE_TYPE_MAP.items():
        if key in t:
            return btype
    # Check tags too
    tags_str = tags or ""
    for key, btype in _GAODE_TYPE_MAP.items():
        if key in tags_str:
            return btype
    return BuildingType.OTHER


def _search_gaode(keywords: str, city: str = "", page: int = 1,
                  lat: float = 0, lon: float = 0, radius: int = 2000) -> Optional[Dict]:
    """Query Gaode place search API."""
    params = f"key={GAODE_KEY}&keywords={quote(keywords)}&offset={GAODE_PAGE_SIZE}&page={page}&extensions=all"
    if city:
        params += f"&city={quote(city)}"
    if lat and lon:
        params += f"&location={lon},{lat}&radius={radius}"

    url = f"{GAODE_SEARCH_URL}?{params}"
    try:
        req = Request(url, headers={"User-Agent": "UrbanWind-CFD/0.1"})
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _search_around(lat: float, lon: float, keywords: str, radius: int = 1500,
                   page: int = 1) -> Optional[Dict]:
    """Search POIs strictly within radius of a point."""
    params = f"key={GAODE_KEY}&location={lon},{lat}&radius={radius}&keywords={quote(keywords)}&offset={GAODE_PAGE_SIZE}&page={page}&extensions=all"
    url = f"{GAODE_AROUND_URL}?{params}"
    try:
        req = Request(url, headers={"User-Agent": "UrbanWind-CFD/0.1"})
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

def _search_around_all(lat: float, lon: float, keywords: str, radius: int = 1500,
                       max_pages: int = 10) -> List[Dict]:
    """Fetch all pages of around-search results."""
    all_pois = []
    for page in range(1, max_pages + 1):
        data = _search_around(lat, lon, keywords, radius, page)
        if not data or data.get("status") != "1":
            break
        pois = data.get("pois", [])
        if not pois:
            break
        all_pois.extend(pois)
        count = int(data.get("count", 0))
        if page * GAODE_PAGE_SIZE >= count:
            break
        time.sleep(0.3)
    return all_pois

def _search_all_pages(keywords: str, city: str = "", lat: float = 0,
                      lon: float = 0, radius: int = 2000, max_pages: int = 10) -> List[Dict]:
    """Fetch all pages of results."""
    all_pois = []
    for page in range(1, max_pages + 1):
        data = _search_gaode(keywords, city, page, lat, lon, radius)
        if not data or data.get("status") != "1":
            break
        pois = data.get("pois", [])
        if not pois:
            break
        all_pois.extend(pois)
        count = int(data.get("count", 0))
        if page * GAODE_PAGE_SIZE >= count:
            break
        time.sleep(0.3)  # Rate limiting
    return all_pois


def _geocode_gaode(address: str, city: str = "") -> Optional[Tuple[float, float]]:
    """Geocode an address to coordinates using Gaode."""
    params = f"key={GAODE_KEY}&address={quote(address)}"
    if city:
        params += f"&city={quote(city)}"
    url = f"https://restapi.amap.com/v3/geocode/geo?{params}"
    try:
        req = Request(url, headers={"User-Agent": "UrbanWind-CFD/0.1"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "1" and data.get("geocodes"):
                loc = data["geocodes"][0]["location"]
                lon, lat = loc.split(",")
                return float(lat), float(lon)
    except Exception:
        pass
    return None


class GaodeAdapter(AbstractAdapter):
    """
    Downloads building data from Gaode (Amap) REST API.

    Returns rectangular building footprints based on POI type classification.

    Usage:
        adapter = GaodeAdapter()
        plan = adapter.parse(place="南京大学鼓楼校区")
        plan = adapter.parse(lat=32.05, lon=118.78, keywords="学校")
    """

    name = "gaode"

    def validate_source(self, source: Any) -> bool:
        return isinstance(source, dict)

    def parse(
        self,
        source: Any = None,
        place: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        keywords: str = "学校",
        city: str = "",
        radius: int = 2000,
        **kwargs,
    ) -> SitePlan:
        """Search building POIs via Gaode API."""
        # Resolve location
        center_lat, center_lon = 0.0, 0.0
        if place:
            # Pass first 4 chars of place as city hint for better geocoding
            city_hint = city or place[:4] if len(place) > 4 else city or place
            result = _geocode_gaode(place, city_hint)
            if result:
                center_lat, center_lon = result
                city = city or place
            else:
                raise ValueError(f"Could not geocode: {place}")
        elif lat and lon:
            center_lat, center_lon = lat, lon
        elif isinstance(source, dict):
            place = source.get("place", "")
            lat = source.get("lat", 0)
            lon = source.get("lon", 0)
            keywords = source.get("keywords", "学校")
            city = source.get("city", "")
            if place:
                result = _geocode_gaode(place)
                if result:
                    center_lat, center_lon = result
                    city = place
            elif lat and lon:
                center_lat, center_lon = lat, lon
        else:
            raise ValueError("Provide place name or lat/lon coordinates")

        if not center_lat or not center_lon:
            raise ValueError("Could not determine location")

        # Use strict radius-based around search (not text search which pulls from whole city)
        print(f"  Gaode: around search '{keywords}' at ({center_lat:.4f}, {center_lon:.4f}) r={radius}m")
        pois = _search_around_all(center_lat, center_lon, keywords, radius)

        if not pois:
            print(f"  Gaode: fallback text search with location bias")
            pois = _search_all_pages(keywords, city, center_lat, center_lon, radius)

        print(f"  Gaode: found {len(pois)} POIs")

        # Convert to features
        features = []
        seen = set()
        for poi in pois:
            loc = poi.get("location", "")
            if not loc:
                continue
            try:
                plon, plat = [float(x) for x in loc.split(",")]
            except ValueError:
                continue

            # Deduplicate by rounded coordinates
            key = (round(plat, 5), round(plon, 5))
            if key in seen:
                continue
            seen.add(key)

            name = poi.get("name", "")
            gaode_type = poi.get("type", "")
            tags = poi.get("tag", "")
            btype = _classify_gaode_type(gaode_type, tags)
            width, depth = _STANDARD_SIZES.get(btype, (30, 20))

            # POI may have floor info
            floor = poi.get("floor", "")
            try:
                levels = int(floor.replace("F", "").replace("f", "")) if floor else 0
            except ValueError:
                levels = 0
            height = poi.get("height", None)
            if height is None and levels > 0:
                height = levels * 3.3
            elif height is None:
                height = BuildingType.default_height(btype)
            else:
                height = float(height)

            # Create rectangular footprint centered on POI location
            hw, hd = width / 2, depth / 2
            footprint = [
                [plon - hw, plat - hd],
                [plon + hw, plat - hd],
                [plon + hw, plat + hd],
                [plon - hw, plat + hd],
                [plon - hw, plat - hd],
            ]

            feature = make_building_feature(
                coords=[footprint],
                height=float(height),
                building_type=btype,
                name=name,
                name_zh=name,
                source=SourceType.MANUAL,
                confidence=0.6,
                fid=f"gd_{poi.get('id', len(features))}",
            )
            feature.properties["gaode_type"] = gaode_type
            feature.properties["gaode_tags"] = tags
            features.append(feature)

        plan = SitePlan(
            features=features,
            metadata={
                "source": "gaode",
                "center_lat": center_lat,
                "center_lon": center_lon,
                "place": place or f"{lat},{lon}",
                "num_buildings": len(features),
                "query_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "data_license": "Gaode Maps API",
                "note": "Rectangular approximations based on building type",
            },
        )
        return plan

    def get_metadata(self) -> Dict[str, Any]:
        return {
            "adapter": self.name,
            "supported_formats": ["place", "lat/lon"],
            "requires_network": True,
            "cost": "Gaode Free Tier (5000 requests/day)",
            "coverage": "China",
        }
