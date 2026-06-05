"""
OpenFOAM case assembler — orchestrates the full generation pipeline.

SitePlan → STL files + CFD dictionaries → Complete OpenFOAM case directory.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from ..schema import SitePlan
from ..config import CFD_CASES_DIR
from .geojson_to_stl import geojson_to_stl
from .bike_placer import BikePlacer
from .dict_generator import DictGenerator


class CaseAssembler:
    """
    Orchestrate complete OpenFOAM case generation from a SitePlan.

    Usage:
        plan = ...  # SitePlan from any adapter + LLM enrichment
        assembler = CaseAssembler(plan, "my_campus")
        case_dir = assembler.assemble()
        # → D:/Phase2_CFD_ML/cfd_cases/my_campus/
    """

    def __init__(
        self,
        plan: SitePlan,
        case_name: str,
        base_dir: Optional[Path] = None,
        wind_speed: float = 5.0,
        wind_direction: str = "N",
    ):
        self.plan = plan
        self.case_name = case_name
        self.base_dir = Path(base_dir) if base_dir else CFD_CASES_DIR
        self.case_dir = self.base_dir / case_name
        self.wind_speed = wind_speed
        self.wind_direction = wind_direction

    def assemble(
        self,
        auto_place_bikes: bool = True,
        n_bikes: int = 20,
        clean_existing: bool = False,
    ) -> Path:
        """
        Generate the complete OpenFOAM case.

        Steps:
        1. Auto-place bike stations if needed and none exist
        2. Write STL files to constant/triSurface/
        3. Generate all OpenFOAM dictionaries
        """
        # Clean if requested
        if clean_existing and self.case_dir.exists():
            shutil.rmtree(self.case_dir)

        self.case_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Auto-place bikes if requested
        if auto_place_bikes and not self.plan.bike_stations:
            placer = BikePlacer(self.plan, self.wind_direction)
            self.plan = placer.place(n_total=n_bikes)

        # Step 2: Write STL files
        tri_surface_dir = self.case_dir / "constant" / "triSurface"
        tri_surface_dir.mkdir(parents=True, exist_ok=True)

        n_stl = geojson_to_stl(self.plan, tri_surface_dir)
        print(f"  ✓ Wrote {n_stl} STL files to {tri_surface_dir}")

        # Step 3: Generate dictionaries
        generator = DictGenerator(
            self.plan,
            self.case_dir,
            wind_speed=self.wind_speed,
            wind_direction=self.wind_direction,
        )
        generator.generate_all()
        print(f"  ✓ Generated all OpenFOAM dictionaries")

        # Save the SitePlan alongside the case for reproducibility
        self.plan.to_file(self.case_dir / "site_plan.geojson")
        print(f"  ✓ Site plan saved to {self.case_dir / 'site_plan.geojson'}")

        print(f"\nCase assembled at: {self.case_dir}")
        print(f"Next steps:")
        print(f"  1. In WSL: cd {self._wsl_path()}")
        print(f"  2. blockMesh")
        print(f"  3. snappyHexMesh -overwrite")
        print(f"  4. simpleFoam")

        return self.case_dir

    def _wsl_path(self) -> str:
        """Convert Windows path to WSL path."""
        p = str(self.case_dir)
        # D:\Phase2_CFD_ML\... → /mnt/d/Phase2_CFD_ML/...
        drive = p[0].lower()
        rest = p[2:].replace("\\", "/")
        return f"/mnt/{drive}{rest}"


def assemble_case(
    plan: SitePlan,
    case_name: str,
    wind_speed: float = 5.0,
    wind_direction: str = "N",
    n_bikes: int = 20,
) -> Path:
    """
    Quick one-shot case assembly.

    Args:
        plan: SitePlan with buildings
        case_name: Name for the case directory
        wind_speed: Inlet wind speed (m/s)
        wind_direction: "N", "S", "E", "W"
        n_bikes: Number of bike stations

    Returns:
        Path to generated case directory
    """
    assembler = CaseAssembler(
        plan, case_name,
        wind_speed=wind_speed,
        wind_direction=wind_direction,
    )
    return assembler.assemble(n_bikes=n_bikes)
