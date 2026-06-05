"""
Manual / natural language input adapter.

Handles free-form text descriptions of building layouts, e.g.:
  "校园500×500米，北侧一栋图书馆30米高50×30米，
   南侧两栋教学楼各20米高40×20米，中间广场放5个单车点"

Architecture:
1. Template-based pre-parser: catches common structured patterns
2. LLM fallback: hands complex descriptions to the inference engine
   for full semantic parsing
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .base import AbstractAdapter
from ..schema import (
    SitePlan, Feature, BuildingType, SourceType, BikeCategory,
    make_building_feature, make_bike_feature,
)


class ManualAdapter(AbstractAdapter):
    """
    Parse manual text descriptions of building layouts.

    Supports both structured parameter input and free-form Chinese text.
    For complex descriptions, delegates to LLM parsing (via ManualAdapter.parse_with_llm).
    """

    name = "manual"

    def validate_source(self, source: Any) -> bool:
        """Check if source is a text string or parameter dict."""
        return isinstance(source, (str, dict))

    def parse(
        self,
        source: Any,
        **kwargs,
    ) -> SitePlan:
        """
        Parse manual input.

        Args:
            source: Either a text description (str) or a structured dict like:
                    {
                        "domain": [x_min, y_min, x_max, y_max],
                        "buildings": [
                            {"name": "图书馆", "cx": 100, "cy": 200,
                             "width": 50, "depth": 30, "height": 30,
                             "type": "library"}
                        ],
                        "bikes": [
                            {"cx": 120, "cy": 180, "category": "open"}
                        ]
                    }

        Returns:
            SitePlan
        """
        if isinstance(source, dict):
            return self._parse_dict(source)
        elif isinstance(source, str):
            return self._parse_text(source)
        else:
            raise ValueError(f"ManualAdapter expects str or dict, got {type(source)}")

    def _parse_dict(self, data: Dict[str, Any]) -> SitePlan:
        """Parse structured dict input."""
        features: List[Feature] = []

        # Domain
        domain = data.get("domain", [0, 0, 300, 300])

        # Buildings
        for i, bld in enumerate(data.get("buildings", [])):
            cx = bld.get("cx", 0)
            cy = bld.get("cy", 0)
            width = bld.get("width", bld.get("lx", 20))
            depth = bld.get("depth", bld.get("ly", 15))
            height = bld.get("height", 12)
            btype_str = bld.get("type", "other")
            name = bld.get("name", "")
            name_zh = bld.get("name_zh", name)

            try:
                btype = BuildingType(btype_str)
            except ValueError:
                btype = BuildingType.OTHER

            hw, hh = width / 2, depth / 2
            coords = [
                [cx - hw, cy - hh],
                [cx + hw, cy - hh],
                [cx + hw, cy + hh],
                [cx - hw, cy + hh],
                [cx - hw, cy - hh],
            ]
            feature = make_building_feature(
                coords=coords,
                height=height,
                building_type=btype,
                name=name,
                name_zh=name_zh,
                source=SourceType.MANUAL,
                confidence=1.0 if btype != BuildingType.OTHER else 0.5,
            )
            features.append(feature)

        # Bike stations
        for i, bike in enumerate(data.get("bikes", [])):
            cx = bike.get("cx", 0)
            cy = bike.get("cy", 0)
            cat_str = bike.get("category", "open")
            name = bike.get("name", f"Bike-{i+1}")
            capacity = bike.get("capacity", 20)

            try:
                cat = BikeCategory(cat_str)
            except ValueError:
                cat = BikeCategory.OPEN

            feature = make_bike_feature(cx=cx, cy=cy, category=cat, name=name, parking_capacity=capacity)
            features.append(feature)

        plan = SitePlan(
            features=features,
            metadata={
                "source": "manual",
                "input_format": "dict",
                "domain": domain,
                "num_buildings": len(data.get("buildings", [])),
                "num_bikes": len(data.get("bikes", [])),
            },
        )
        return plan

    def _parse_text(self, text: str) -> SitePlan:
        """
        Parse free-form Chinese text.

        Uses regex-based template matching for common patterns.
        Falls back to a minimal SitePlan — the caller should use LLM
        enrichment to fill in the gaps.
        """
        features: List[Feature] = []
        domain_size = self._extract_domain(text)

        # Try to extract buildings with dimensions
        buildings = self._extract_buildings_from_text(text, domain_size)
        features.extend(buildings)

        # Try to extract bike stations
        bikes = self._extract_bikes_from_text(text, domain_size)
        features.extend(bikes)

        plan = SitePlan(
            features=features,
            metadata={
                "source": "manual",
                "input_format": "text",
                "raw_text": text,
                "domain": domain_size,
                "num_parsed": len(features),
                "needs_llm_enrichment": len(features) == 0,
            },
        )
        return plan

    def _extract_domain(self, text: str) -> List[float]:
        """Try to extract domain size from text patterns like '500×500米'."""
        pat = re.compile(r"(\d+)\s*[×xX]\s*(\d+)\s*米?")
        m = pat.search(text)
        if m:
            w = float(m.group(1))
            h = float(m.group(2))
            return [0, 0, w, h]
        return [0, 0, 300, 300]

    def _extract_buildings_from_text(
        self, text: str, domain: List[float]
    ) -> List[Feature]:
        """
        Try to parse building descriptions from text.

        Patterns matched:
        - "教学楼20米高40×20米"
        - "图书馆高30m"
        - etc.
        """
        features: List[Feature] = []

        # Pattern: <building_name> <height>高 <width>×<depth>米?
        pat = re.compile(
            r"([一-鿿\w]+?)\s*"
            r"(\d+)\s*[米mM]\s*高\s*"
            r"(\d+)\s*[×xX]\s*(\d+)\s*[米mM]?",
            re.UNICODE,
        )
        for i, m in enumerate(pat.finditer(text)):
            name = m.group(1)
            height = float(m.group(2))
            width = float(m.group(3))
            depth = float(m.group(4))

            btype, _ = self._name_to_type(name)

            # Approximate position: distribute buildings across domain
            idx = i
            cx = domain[0] + (idx + 1) * (domain[2] - domain[0]) / (len(list(pat.finditer(text))) + 1)
            cy = domain[1] + (domain[3] - domain[1]) / 2

            hw, hh = width / 2, depth / 2
            coords = [
                [cx - hw, cy - hh],
                [cx + hw, cy - hh],
                [cx + hw, cy + hh],
                [cx - hw, cy + hh],
                [cx - hw, cy - hh],
            ]
            feature = make_building_feature(
                coords=coords, height=height, building_type=btype,
                name=name, name_zh=name, source=SourceType.MANUAL, confidence=0.6,
            )
            features.append(feature)

        return features

    def _extract_bikes_from_text(
        self, text: str, domain: List[float]
    ) -> List[Feature]:
        """Try to extract bike station info from text."""
        features: List[Feature] = []

        # Count bikes mentioned
        pat = re.compile(r"(\d+)\s*个?\s*(?:共享)?单车(?:点|站|位)?")
        m = pat.search(text)
        count = int(m.group(1)) if m else 0

        if count > 0 and not self._extract_buildings_from_text(text, domain):
            # Place bikes in a grid across the domain
            cols = min(count, 5)
            rows = (count + cols - 1) // cols
            for i in range(count):
                col = i % cols
                row = i // cols
                cx = domain[0] + (col + 1) * (domain[2] - domain[0]) / (cols + 1)
                cy = domain[1] + (row + 1) * (domain[3] - domain[1]) / (rows + 1)
                features.append(make_bike_feature(cx=cx, cy=cy, name=f"Bike-{i+1}"))

        return features

    def _name_to_type(self, name: str) -> tuple:
        """Map Chinese building name to BuildingType."""
        type_map = {
            "教学": BuildingType.TEACHING, "教室": BuildingType.TEACHING,
            "宿舍": BuildingType.DORMITORY, "公寓": BuildingType.DORMITORY,
            "食堂": BuildingType.CANTEEN, "餐厅": BuildingType.CANTEEN,
            "图书馆": BuildingType.LIBRARY, "图书": BuildingType.LIBRARY,
            "行政": BuildingType.OFFICE, "办公": BuildingType.OFFICE,
            "实验": BuildingType.LAB, "科研": BuildingType.LAB,
            "体育": BuildingType.GYMNASIUM, "运动": BuildingType.GYMNASIUM, "馆": BuildingType.GYMNASIUM,
        }
        for key, bt in type_map.items():
            if key in name:
                return bt, 0.7
        return BuildingType.OTHER, 0.3

    def get_metadata(self) -> Dict[str, Any]:
        return {
            "adapter": self.name,
            "supported_formats": ["text", "dict"],
            "requires_network": False,
            "cost": "free",
            "recommendation": (
                "For best results with free-form text, follow this adapter "
                "with LLM enrichment via geometry_infer."
            ),
        }
