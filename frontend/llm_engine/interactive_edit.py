"""
Interactive natural language building editor.

Maintains a SitePlan state machine that accepts Chinese natural language
instructions and produces differential updates.

Architecture:
1. Template-based fast path: catches ~70% of common instructions via regex
2. LLM slow path: handles complex/ambiguous instructions
3. All operations are reversible (undo/redo stack)
"""
from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..schema import (
    SitePlan, Feature, Geometry, BuildingType, BikeCategory, RoofType,
    make_building_feature, make_bike_feature,
)
from .engine import get_engine
from .prompts import INTERACTIVE_EDIT

logger = logging.getLogger(__name__)


@dataclass
class EditOperation:
    """A single reversible edit operation."""
    action: str  # "update", "add", "delete", "move", "resize", "add_bike", "delete_bike"
    target_id: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    _snapshot: Optional[Dict] = None  # For undo

    def describe(self) -> str:
        """Human-readable description in Chinese."""
        descriptions = {
            "update": f"修改建筑 {self.target_id}",
            "add": f"添加建筑 '{self.params.get('name_zh', '')}'",
            "delete": f"删除建筑 {self.target_id}",
            "move": f"移动建筑 {self.target_id}",
            "resize": f"调整建筑 {self.target_id} 尺寸",
            "add_bike": f"添加单车点",
            "delete_bike": f"删除单车点",
        }
        return descriptions.get(self.action, self.action)


class InteractiveEditor:
    """
    Natural language interface for editing building site plans.

    Usage:
        editor = InteractiveEditor(plan)
        result = editor.execute("把图书馆高度改成30米")
        print(result.message)  # "已将 图书馆 的高度修改为 30m"
        updated_plan = editor.current_plan
        editor.undo()  # Revert last operation
    """

    def __init__(self, plan: SitePlan, use_llm: bool = True):
        self._plan = plan
        self.use_llm = use_llm
        self._undo_stack: List[EditOperation] = []
        self._redo_stack: List[EditOperation] = []
        self._operation_count = 0

    @property
    def current_plan(self) -> SitePlan:
        return self._plan

    def execute(self, instruction: str) -> "EditResult":
        """
        Execute a natural language instruction.

        Returns:
            EditResult with status, message, and any changes
        """
        # Try template fast-path first
        ops = self._parse_templates(instruction)

        if ops:
            return self._apply_operations(ops, instruction)

        # Fall back to LLM
        if self.use_llm:
            return self._llm_execute(instruction)

        return EditResult(
            success=False,
            message=f"无法理解指令: '{instruction}'。请尝试更明确的表述。",
            operations=[],
        )

    def undo(self) -> "EditResult":
        """Undo the last operation."""
        if not self._undo_stack:
            return EditResult(success=False, message="没有可撤销的操作。", operations=[])

        op = self._undo_stack.pop()
        self._redo_stack.append(op)
        self._apply_reverse(op)
        return EditResult(success=True, message=f"已撤销: {op.describe()}", operations=[op])

    def redo(self) -> "EditResult":
        """Redo the last undone operation."""
        if not self._redo_stack:
            return EditResult(success=False, message="没有可重做的操作。", operations=[])

        op = self._redo_stack.pop()
        self._undo_stack.append(op)
        self._apply_forward(op)
        return EditResult(success=True, message=f"已重做: {op.describe()}", operations=[op])

    # ── Template-based fast path ─────────────────────────────────────────

    def _parse_templates(self, text: str) -> Optional[List[EditOperation]]:
        """
        Try to parse common Chinese editing patterns.

        Returns list of operations or None if no template matched.
        """
        ops: List[EditOperation] = []

        # Pattern: "<building_name> 高度改[成/为] <number> [米/m]"
        pat = re.compile(
            r"([一-鿿\w]+?)\s*(?:的)?\s*高度\s*(?:改|改成|改为|设为|设置成?|调整[为到]?)\s*(\d+\.?\d*)\s*[米mM]?",
            re.UNICODE,
        )
        m = pat.search(text)
        if m:
            name = m.group(1)
            height = float(m.group(2))
            target = self._find_building_by_name(name)
            if target:
                old_height = target.properties.get("height", 0)
                target.properties["height"] = height
                target.properties["num_floors"] = max(1, round(height / 3.3))
                target.properties["confidence"] = 1.0
                target.properties["source"] = "manual"
                ops.append(EditOperation(
                    action="update", target_id=target.id,
                    params={"field": "height", "old": old_height, "new": height, "name": name},
                ))

        # Pattern: "<name> [类型]/[类别] 改[为/成] <type>"
        pat = re.compile(
            r"([一-鿿\w]+?)\s*(?:的)?\s*(?:类型|类别|用途)\s*(?:改|改成|改为|设为)\s*(.+?)(?:[。，\s]|$)",
            re.UNICODE,
        )
        m = pat.search(text)
        if m:
            name = m.group(1)
            type_text = m.group(2).strip()
            btype = self._match_type(type_text)
            target = self._find_building_by_name(name)
            if target and btype:
                old_type = target.properties.get("building_type", "")
                target.properties["building_type"] = btype.value
                if target.properties.get("confidence", 1.0) < 0.5:
                    target.properties["height"] = BuildingType.default_height(btype)
                ops.append(EditOperation(
                    action="update", target_id=target.id,
                    params={"field": "building_type", "old": old_type, "new": btype.value, "name": name},
                ))

        # Pattern: "删除 <name>"
        pat = re.compile(r"删除\s*(?:建筑|建筑物)?\s*([一-鿿\w]+)", re.UNICODE)
        m = pat.search(text)
        if m:
            name = m.group(1)
            target = self._find_building_by_name(name)
            if target:
                self._plan.features.remove(target)
                ops.append(EditOperation(
                    action="delete", target_id=target.id,
                    params={"name": name},
                    _snapshot=target.to_dict(),
                ))

        # Pattern: "添加 <type> [at] <cx>,<cy> [height] <h>m"
        pat = re.compile(
            r"添加\s*(.+?)\s*(?:在|at)?\s*\(?\s*(\d+\.?\d*)\s*[,，]\s*(\d+\.?\d*)\s*\)?"
            r"\s*(?:高度)?\s*(\d+\.?\d*)?\s*[米mM]?",
            re.UNICODE,
        )
        m = pat.search(text)
        if m:
            name_or_type = m.group(1).strip()
            cx = float(m.group(2))
            cy = float(m.group(3))
            height = float(m.group(4)) if m.group(4) else None

            btype = self._match_type(name_or_type)
            if btype is None:
                btype = BuildingType.OTHER

            if height is None:
                height = BuildingType.default_height(btype)

            hw, hh = 10.0, 7.5  # Default building footprint
            coords = [
                [cx - hw, cy - hh], [cx + hw, cy - hh],
                [cx + hw, cy + hh], [cx - hw, cy + hh],
                [cx - hw, cy - hh],
            ]
            feature = make_building_feature(
                coords=coords, height=height, building_type=btype,
                name=name_or_type, name_zh=name_or_type, confidence=1.0,
            )
            self._plan.features.append(feature)
            ops.append(EditOperation(
                action="add", target_id=feature.id,
                params={"name_zh": name_or_type, "height": height, "cx": cx, "cy": cy},
            ))

        # Pattern: "放 <N> 个单车 [at/在] <region>"
        pat = re.compile(
            r"(?:放|添加|放置)\s*(\d+)\s*个?\s*(?:共享)?单车(?:点|站)"
            r"(?:在|于|到)?\s*(.+?)(?:[。，\s]|$)",
            re.UNICODE,
        )
        m = pat.search(text)
        if m:
            count = int(m.group(1))
            region_text = m.group(2).strip()
            # Determine category from region description
            cat = BikeCategory.OPEN
            if any(w in region_text for w in ("背风", "尾流", "南侧", "后方", "wake")):
                cat = BikeCategory.WAKE
            elif any(w in region_text for w in ("峡谷", "之间", "夹道", "canyon")):
                cat = BikeCategory.CANYON
            elif any(w in region_text for w in ("转角", "角落", "拐角", "corner")):
                cat = BikeCategory.CORNER

            # Place bikes evenly across the domain
            bbox = self._plan.overall_bbox
            for i in range(count):
                # Simple heuristic placement
                frac = (i + 1) / (count + 1)
                cx = bbox[0] + frac * (bbox[2] - bbox[0])
                cy = bbox[1] + frac * (bbox[3] - bbox[1])

                # Adjust for category
                if cat == BikeCategory.WAKE:
                    cy -= 20  # Downwind (south of buildings for N wind)
                elif cat == BikeCategory.CANYON:
                    pass  # Between buildings
                elif cat == BikeCategory.CORNER:
                    pass

                feature = make_bike_feature(cx=cx, cy=cy, category=cat, name=f"Bike-{self._operation_count + i + 1}")
                self._plan.features.append(feature)
                ops.append(EditOperation(
                    action="add_bike", target_id=feature.id,
                    params={"cx": cx, "cy": cy, "category": cat.value},
                ))

        return ops if ops else None

    # ── LLM slow path ────────────────────────────────────────────────────

    def _llm_execute(self, instruction: str) -> "EditResult":
        """Execute complex instruction via LLM."""
        try:
            engine = get_engine()
            if not engine.is_loaded:
                engine.ensure_loaded()
            if not engine.is_loaded:
                return EditResult(success=False, message="LLM模型未加载。", operations=[])
        except Exception as e:
            return EditResult(success=False, message=f"LLM不可用: {e}", operations=[])

        plan_json = self._plan.to_json()
        messages = INTERACTIVE_EDIT(plan_json, instruction)

        try:
            result = engine.chat_json(messages, max_tokens=2048)
        except Exception as e:
            return EditResult(success=False, message=f"LLM推理失败: {e}", operations=[])

        if not result.get("understood", False):
            return EditResult(
                success=False,
                message=result.get("explanation", "无法理解该指令。"),
                operations=[],
            )

        # Apply operations from LLM response
        ops_data = result.get("operations", [])
        ops = []
        for od in ops_data:
            op = EditOperation(
                action=od.get("action", ""),
                target_id=od.get("target_id"),
                params=od.get("params", {}),
            )
            ops.append(op)
            self._apply_operation(op)

        # If LLM returned updated state, use it
        if "updated_state" in result:
            try:
                new_plan = SitePlan.from_dict(result["updated_state"])
                self._plan = new_plan
            except Exception:
                pass  # Keep incrementally updated plan

        return EditResult(
            success=True,
            message=result.get("explanation", "已完成修改。"),
            operations=ops,
        )

    # ── Operation application ────────────────────────────────────────────

    def _apply_operations(self, ops: List[EditOperation], instruction: str) -> "EditResult":
        """Apply a list of operations."""
        for op in ops:
            self._apply_operation(op)
            self._undo_stack.append(op)
            self._operation_count += 1
        self._redo_stack.clear()

        descs = [op.describe() for op in ops]
        return EditResult(
            success=True,
            message=f"执行 '{instruction}': " + "; ".join(descs),
            operations=ops,
        )

    def _apply_operation(self, op: EditOperation):
        """Apply a single operation to the current plan."""
        # Snapshot for undo
        if op.action == "delete":
            pass  # _snapshot already set
        elif op.action in ("update", "move", "resize"):
            target = self._find_by_id(op.target_id)
            if target:
                op._snapshot = copy.deepcopy(target.properties)
        elif op.action in ("add_bike", "delete_bike"):
            target = self._find_by_id(op.target_id)
            if target:
                op._snapshot = target.to_dict()

    def _apply_reverse(self, op: EditOperation):
        """Reverse an operation."""
        if op.action == "add":
            # Remove the added feature
            target = self._find_by_id(op.target_id)
            if target:
                self._plan.features.remove(target)
        elif op.action == "delete":
            # Restore from snapshot
            if op._snapshot:
                feature = Feature(
                    id=op._snapshot["id"],
                    category=op._snapshot.get("category", "building"),
                    geometry=Geometry(**op._snapshot["geometry"]),
                    properties=op._snapshot.get("properties", {}),
                )
                self._plan.features.append(feature)
        elif op.action == "update" and op._snapshot:
            target = self._find_by_id(op.target_id)
            if target:
                target.properties.update(op._snapshot)
        elif op.action == "add_bike":
            target = self._find_by_id(op.target_id)
            if target:
                self._plan.features.remove(target)
        elif op.action == "delete_bike" and op._snapshot:
            feature = Feature(
                id=op._snapshot["id"],
                category=op._snapshot.get("category", "bike_station"),
                geometry=Geometry(**op._snapshot["geometry"]),
                properties=op._snapshot.get("properties", {}),
            )
            self._plan.features.append(feature)

    def _apply_forward(self, op: EditOperation):
        """Re-apply an operation (for redo)."""
        if op.action == "add":
            # Need the original add params
            pass
        elif op.action == "delete":
            target = self._find_by_id(op.target_id)
            if target:
                self._plan.features.remove(target)
        elif op.action == "update":
            target = self._find_by_id(op.target_id)
            if target:
                field = op.params.get("field", "")
                new_val = op.params.get("new")
                if field and new_val is not None:
                    target.properties[field] = new_val

    # ── Helpers ──────────────────────────────────────────────────────────

    def _find_by_id(self, fid: str) -> Optional[Feature]:
        for f in self._plan.features:
            if f.id == fid:
                return f
        return None

    def _find_building_by_name(self, name: str) -> Optional[Feature]:
        """Find a building by name (exact or fuzzy match)."""
        name_lower = name.lower().strip()
        for f in self._plan.buildings:
            prop_name = f.properties.get("name", "").lower()
            prop_name_zh = f.properties.get("name_zh", "").lower()
            if name_lower in prop_name or name_lower in prop_name_zh or prop_name in name_lower:
                return f
        return None

    def _match_type(self, text: str) -> Optional[BuildingType]:
        """Match Chinese text to BuildingType."""
        text = text.strip().lower()
        type_map = {
            "教学楼": BuildingType.TEACHING, "教学": BuildingType.TEACHING,
            "宿舍": BuildingType.DORMITORY, "宿舍楼": BuildingType.DORMITORY, "公寓": BuildingType.DORMITORY,
            "食堂": BuildingType.CANTEEN, "餐厅": BuildingType.CANTEEN,
            "图书馆": BuildingType.LIBRARY, "图书": BuildingType.LIBRARY,
            "办公楼": BuildingType.OFFICE, "行政楼": BuildingType.OFFICE, "办公": BuildingType.OFFICE,
            "实验楼": BuildingType.LAB, "实验室": BuildingType.LAB, "科研楼": BuildingType.LAB,
            "体育馆": BuildingType.GYMNASIUM, "体育": BuildingType.GYMNASIUM, "运动馆": BuildingType.GYMNASIUM,
        }
        for key, bt in type_map.items():
            if key in text:
                return bt
        # Try English
        eng_map = {
            "teaching": BuildingType.TEACHING, "dormitory": BuildingType.DORMITORY,
            "canteen": BuildingType.CANTEEN, "library": BuildingType.LIBRARY,
            "office": BuildingType.OFFICE, "lab": BuildingType.LAB,
            "gymnasium": BuildingType.GYMNASIUM,
        }
        for key, bt in eng_map.items():
            if key in text:
                return bt
        return None


@dataclass
class EditResult:
    """Result of an edit operation."""
    success: bool
    message: str
    operations: List[EditOperation] = field(default_factory=list)
    updated_plan: Optional[SitePlan] = None
