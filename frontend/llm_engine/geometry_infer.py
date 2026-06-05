"""
Building attribute inference engine.

Takes a SitePlan with incomplete building data (e.g., from OSM with only
footprints and partial tags) and enriches it using LLM reasoning.

Supports:
- Rule-based inference (fast, deterministic, no LLM needed)
- LLM-based inference (handles complex/ambiguous cases)
- Batch processing with caching by building type
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from ..schema import (
    SitePlan, Feature, BuildingType, RoofType, SourceType, BuildingProperties,
    make_building_feature, validate_site_plan,
)
from .engine import get_engine
from .prompts import INFER_BUILDING_ATTRS

logger = logging.getLogger(__name__)


class GeometryInferrer:
    """
    Enrich building features with inferred attributes.

    Usage:
        inferrer = GeometryInferrer()
        enriched_plan = inferrer.enrich(plan)
        # Or without LLM (rule-based only):
        enriched_plan = inferrer.enrich_rules_only(plan)
    """

    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm
        self._cache: Dict[str, Dict] = {}  # Cache by building type

    def enrich(self, plan: SitePlan) -> SitePlan:
        """
        Full enrichment: rules first, then LLM for low-confidence features.
        """
        # Step 1: Rule-based inference (fast, always applied)
        plan = self.enrich_rules_only(plan)

        # Step 2: Identify features that need LLM
        if self.use_llm:
            low_conf = [
                f for f in plan.buildings
                if f.properties.get("confidence", 0) < 0.5
            ]
            if low_conf:
                plan = self._llm_enrich(plan, low_conf)

        return plan

    def enrich_rules_only(self, plan: SitePlan) -> SitePlan:
        """Apply deterministic rules without LLM."""
        for feature in plan.features:
            if feature.category != "building":
                continue

            props = feature.properties

            # If height is missing or zero, infer from building type
            if props.get("height", 0) <= 0:
                btype_str = props.get("building_type", "other")
                try:
                    btype = BuildingType(btype_str)
                except ValueError:
                    btype = BuildingType.OTHER
                props["height"] = BuildingType.default_height(btype)
                props["confidence"] = min(props.get("confidence", 0.5), 0.5)

            # If num_floors missing, compute from height
            if props.get("num_floors", 0) <= 0:
                height = props.get("height", 12.0)
                # Use type-appropriate floor height
                btype_str = props.get("building_type", "other")
                floor_h = self._floor_height_for_type(btype_str)
                props["num_floors"] = max(1, round(height / floor_h))

            # Default roof type
            if not props.get("roof_type"):
                props["roof_type"] = "flat"

            # Default name
            if not props.get("name_zh"):
                btype_str = props.get("building_type", "other")
                props["name_zh"] = self._default_name(btype_str, feature.id)

            # Mark source if not set
            if not props.get("source"):
                props["source"] = "llm_inferred"

        return plan

    def _llm_enrich(self, plan: SitePlan, low_conf_features: List[Feature]) -> SitePlan:
        """
        Use LLM to infer attributes for features with low confidence.

        Sends batches to the LLM (max 10 buildings per batch to stay within context).
        """
        try:
            engine = get_engine()
            if not engine.is_loaded:
                engine.ensure_loaded()
            if not engine.is_loaded:
                logger.warning("LLM not available, skipping LLM enrichment")
                return plan
        except Exception as e:
            logger.warning(f"LLM not available: {e}")
            return plan

        # Process in batches
        batch_size = 10
        for i in range(0, len(low_conf_features), batch_size):
            batch = low_conf_features[i:i + batch_size]
            # Build simplified input for LLM
            buildings_input = []
            for f in batch:
                bld = {
                    "id": f.id,
                    "geometry": f.geometry.to_dict(),
                    "properties": {
                        k: v for k, v in f.properties.items()
                        if k in ("building_type", "height", "num_floors",
                                 "name", "name_zh", "osm_tags")
                    },
                }
                buildings_input.append(bld)

            try:
                messages = INFER_BUILDING_ATTRS(json.dumps(buildings_input, ensure_ascii=False))
                result = engine.chat_json(messages, max_tokens=2048)
                if "error" not in result and isinstance(result, list):
                    self._apply_inferences(plan, result)
            except Exception as e:
                logger.warning(f"LLM inference failed for batch {i}: {e}")
                continue

        return plan

    def _apply_inferences(self, plan: SitePlan, inferences: List[Dict]):
        """Apply LLM inferences back to the SitePlan."""
        feature_map = {f.id: f for f in plan.features}

        for inf in inferences:
            fid = inf.get("id", "")
            feature = feature_map.get(fid)
            if feature is None:
                continue

            # Update only low-confidence fields
            new_props = inf.get("properties", inf)  # Handle both formats
            for key in ("building_type", "height", "num_floors", "roof_type", "name_zh"):
                if key in new_props and new_props[key]:
                    feature.properties[key] = new_props[key]

            # Update confidence
            feature.properties["confidence"] = new_props.get("confidence", 0.7)
            feature.properties["source"] = "llm_inferred"

            # Store reasoning if provided
            if "reasoning" in new_props:
                feature.properties["inference_reasoning"] = new_props["reasoning"]

    # ── Rule helpers ──

    def _floor_height_for_type(self, btype_str: str) -> float:
        """Return typical floor-to-floor height for a building type."""
        heights = {
            "teaching": 3.3, "dormitory": 3.0, "canteen": 4.0,
            "library": 4.5, "office": 3.3, "lab": 3.6,
            "gymnasium": 6.0, "other": 3.3,
        }
        return heights.get(btype_str, 3.3)

    def _default_name(self, btype_str: str, fid: str) -> str:
        """Generate a default Chinese name."""
        names = {
            "teaching": "教学楼", "dormitory": "宿舍楼", "canteen": "食堂",
            "library": "图书馆", "office": "办公楼", "lab": "实验楼",
            "gymnasium": "体育馆", "other": f"建筑{fid[:4]}",
        }
        return names.get(btype_str, f"建筑{fid[:4]}")
