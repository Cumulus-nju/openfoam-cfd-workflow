"""
Smart bike station placement based on building layout.

Places bike stations in four strategic wind environment categories:
- open: upstream/open area, baseline wind speed reference
- wake: behind buildings, captures sheltering effect
- canyon: between parallel buildings, captures venturi acceleration
- corner: near building corners, captures separation/shear
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

from ..schema import SitePlan, Feature, BikeCategory, make_bike_feature


class BikePlacer:
    """
    Automatic bike station placement based on building geometry.

    Usage:
        placer = BikePlacer(plan)
        placer.place(n_total=20)  # Auto-distribute across categories
        enriched_plan = placer.plan
    """

    def __init__(self, plan: SitePlan, wind_direction: str = "N"):
        """
        Args:
            plan: SitePlan with buildings
            wind_direction: "N", "S", "E", "W"
        """
        self.plan = plan
        self.wind_direction = wind_direction.upper()
        self._buildings = plan.buildings
        self._bbox = plan.overall_bbox

    def place(
        self,
        n_total: int = 20,
        distribution: Optional[dict] = None,
        min_separation: float = 15.0,
    ) -> SitePlan:
        """
        Place bike stations.

        Args:
            n_total: Total number of bike stations
            distribution: Dict like {"open": 5, "wake": 5, "canyon": 5, "corner": 5}
            min_separation: Minimum distance between stations (meters)

        Returns:
            Updated SitePlan with bike features added
        """
        if distribution is None:
            distribution = {
                "open": max(2, n_total // 4),
                "wake": max(2, n_total // 4),
                "canyon": max(2, n_total // 4),
                "corner": n_total - 3 * (n_total // 4),
            }

        if not self._buildings:
            # No buildings — place evenly in domain
            return self._place_grid(n_total)

        # Collect candidate positions for each category
        candidates: dict = {
            "open": self._find_open_positions(),
            "wake": self._find_wake_positions(),
            "canyon": self._find_canyon_positions(),
            "corner": self._find_corner_positions(),
        }

        # Place bikes, avoiding overlaps
        placed: List[Tuple[float, float]] = []
        for cat_name, n in distribution.items():
            cat = BikeCategory(cat_name)
            positions = candidates.get(cat_name, [])
            # Sort by quality (open: upstream distance, wake: alignment, etc.)
            count = 0
            for cx, cy in positions:
                if count >= n:
                    break
                # Check separation
                if all(math.hypot(cx - px, cy - py) >= min_separation for px, py in placed):
                    feature = make_bike_feature(cx=cx, cy=cy, category=cat, name=f"Bike-{cat_name}-{count+1}")
                    self.plan.features.append(feature)
                    placed.append((cx, cy))
                    count += 1

        return self.plan

    # ── Position finders ─────────────────────────────────────────────────────

    def _find_open_positions(self) -> List[Tuple[float, float]]:
        """
        Find open/reference positions — upstream of all buildings.

        For N wind (flowing S), upstream is north of all buildings.
        """
        results = []
        min_x, min_y, max_x, max_y = self._bbox
        pad = 20.0  # Distance from building edge

        if self.wind_direction == "N":
            # Upstream = north side, higher y values
            cy = max_y + pad
            for i in range(5):
                cx = min_x + (i + 1) * (max_x - min_x) / 6
                results.append((cx, cy))
        elif self.wind_direction == "S":
            cy = min_y - pad
            for i in range(5):
                cx = min_x + (i + 1) * (max_x - min_x) / 6
                results.append((cx, cy))
        elif self.wind_direction == "E":
            cx = max_x + pad
            for i in range(5):
                cy = min_y + (i + 1) * (max_y - min_y) / 6
                results.append((cx, cy))
        else:  # W
            cx = min_x - pad
            for i in range(5):
                cy = min_y + (i + 1) * (max_y - min_y) / 6
                results.append((cx, cy))

        return results

    def _find_wake_positions(self) -> List[Tuple[float, float]]:
        """
        Find wake positions — directly downstream of buildings.

        For N wind, wake is on the south side of each building.
        """
        results = []
        pad = 15.0  # Distance from building face

        for bld in self._buildings:
            bbox = bld.bbox  # (min_x, min_y, max_x, max_y)
            cx = (bbox[0] + bbox[2]) / 2

            if self.wind_direction == "N":
                results.append((cx, bbox[1] - pad))  # South of building
            elif self.wind_direction == "S":
                results.append((cx, bbox[3] + pad))
            elif self.wind_direction == "E":
                results.append((bbox[0] - pad, (bbox[1] + bbox[3]) / 2))
            else:  # W
                results.append((bbox[2] + pad, (bbox[1] + bbox[3]) / 2))

        return results

    def _find_canyon_positions(self) -> List[Tuple[float, float]]:
        """
        Find street canyon positions — between closely-spaced parallel buildings.

        Looks for pairs of buildings with small separation and places station
        in the gap between them.
        """
        results = []
        buildings = self._buildings

        for i, b1 in enumerate(buildings):
            for j, b2 in enumerate(buildings):
                if i >= j:
                    continue
                bbox1 = b1.bbox
                bbox2 = b2.bbox
                gap_x = max(0, max(bbox1[0], bbox2[0]) - min(bbox1[2], bbox2[2]))
                gap_y = max(0, max(bbox1[1], bbox2[1]) - min(bbox1[3], bbox2[3]))

                # Check if buildings are aligned (parallel) with small gap
                if gap_x < 5 and 5 < gap_y < 60:
                    cx = (bbox1[0] + bbox1[2] + bbox2[0] + bbox2[2]) / 4
                    cy = (bbox1[1] + bbox1[3] + bbox2[1] + bbox2[3]) / 4
                    results.append((cx, cy))
                elif gap_y < 5 and 5 < gap_x < 60:
                    cx = (bbox1[0] + bbox1[2] + bbox2[0] + bbox2[2]) / 4
                    cy = (bbox1[1] + bbox1[3] + bbox2[1] + bbox2[3]) / 4
                    results.append((cx, cy))

        return results

    def _find_corner_positions(self) -> List[Tuple[float, float]]:
        """
        Find corner positions — near building corners (separation/shear zones).

        Places stations diagonally offset from building corners.
        """
        results = []
        offset = 10.0

        for bld in self._buildings:
            bbox = bld.bbox
            corners = [
                (bbox[0], bbox[1]),  # SW
                (bbox[2], bbox[1]),  # SE
                (bbox[0], bbox[3]),  # NW
                (bbox[2], bbox[3]),  # NE
            ]
            for cx, cy in corners:
                # Offset outward from each corner
                if self.wind_direction in ("N", "S"):
                    # Lateral offsets for crosswind corners
                    results.append((cx - offset, cy))
                    results.append((cx + offset, cy))
                else:
                    results.append((cx, cy - offset))
                    results.append((cx, cy + offset))

        return results

    def _place_grid(self, n: int) -> SitePlan:
        """Fallback: place bikes in a grid over the domain."""
        cols = min(n, 5)
        rows = (n + cols - 1) // cols
        min_x, min_y, max_x, max_y = self._bbox
        if max_x == min_x:
            max_x = min_x + 300
            max_y = min_y + 300

        for i in range(n):
            col = i % cols
            row = i // cols
            cx = min_x + (col + 1) * (max_x - min_x) / (cols + 1)
            cy = min_y + (row + 1) * (max_y - min_y) / (rows + 1)
            feature = make_bike_feature(cx=cx, cy=cy, name=f"Bike-{i+1}")
            self.plan.features.append(feature)

        return self.plan
