"""
OpenFOAM dictionary generator.

Generates all required configuration files for a CFD case from a SitePlan:
- system/: blockMeshDict, snappyHexMeshDict, controlDict, fvSchemes, fvSolution, meshQualityDict
- constant/: transportProperties, turbulenceProperties
- 0/: U, p, k, epsilon, nut

Based on the proven patterns from generate_urban_block.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..schema import SitePlan, Feature
from ..config import (
    DOMAIN_PADDING_UPSTREAM, DOMAIN_PADDING_DOWNSTREAM, DOMAIN_PADDING_SIDE,
    DOMAIN_PADDING_TOP_FACTOR, BACKGROUND_CELL_SIZE, SNAPPY_REFINEMENT_LEVELS,
    DEFAULT_WIND_SPEED, DEFAULT_REFERENCE_HEIGHT, END_TIME, WRITE_INTERVAL,
)


class DictGenerator:
    """
    Generate all OpenFOAM configuration dictionaries for a SitePlan.

    Usage:
        gen = DictGenerator(plan, case_dir)
        gen.generate_all()
    """

    def __init__(
        self,
        plan: SitePlan,
        case_dir: Path,
        wind_speed: float = DEFAULT_WIND_SPEED,
        wind_direction: str = "N",
        reference_height: float = DEFAULT_REFERENCE_HEIGHT,
    ):
        self.plan = plan
        self.case_dir = Path(case_dir)
        self.wind_speed = wind_speed
        self.wind_direction = wind_direction.upper()
        self.reference_height = reference_height

        # Computed domain parameters
        self._compute_domain()

    def generate_all(self):
        """Generate all OpenFOAM case files."""
        dirs = [
            self.case_dir / "system",
            self.case_dir / "constant",
            self.case_dir / "0",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        self.write_block_mesh_dict()
        self.write_snappy_hex_mesh_dict()
        self.write_control_dict()
        self.write_fv_schemes()
        self.write_fv_solution()
        self.write_mesh_quality_dict()
        self.write_transport_properties()
        self.write_turbulence_properties()
        self.write_boundary_conditions()
        self.write_case_summary()

    # ── Domain computation ───────────────────────────────────────────────────

    def _compute_domain(self):
        """Compute CFD domain dimensions from building layout."""
        bbox = self.plan.overall_bbox
        min_x, min_y, max_x, max_y = bbox

        # Expand domain
        if self.wind_direction == "N":
            # Wind from +y, flowing to -y
            self.x_min = min_x - DOMAIN_PADDING_SIDE
            self.x_max = max_x + DOMAIN_PADDING_SIDE
            self.y_min = min_y - DOMAIN_PADDING_DOWNSTREAM  # Downstream (south)
            self.y_max = max_y + DOMAIN_PADDING_UPSTREAM    # Upstream (north)
        elif self.wind_direction == "S":
            self.x_min = min_x - DOMAIN_PADDING_SIDE
            self.x_max = max_x + DOMAIN_PADDING_SIDE
            self.y_min = min_y - DOMAIN_PADDING_UPSTREAM
            self.y_max = max_y + DOMAIN_PADDING_DOWNSTREAM
        elif self.wind_direction == "E":
            self.x_min = min_x - DOMAIN_PADDING_DOWNSTREAM
            self.x_max = max_x + DOMAIN_PADDING_UPSTREAM
            self.y_min = min_y - DOMAIN_PADDING_SIDE
            self.y_max = max_y + DOMAIN_PADDING_SIDE
        else:  # W
            self.x_min = min_x - DOMAIN_PADDING_UPSTREAM
            self.x_max = max_x + DOMAIN_PADDING_DOWNSTREAM
            self.y_min = min_y - DOMAIN_PADDING_SIDE
            self.y_max = max_y + DOMAIN_PADDING_SIDE

        # Height
        max_height = max(
            (f.properties.get("height", 0) for f in self.plan.buildings),
            default=20.0,
        )
        self.z_min = 0.0
        self.z_max = max_height * DOMAIN_PADDING_TOP_FACTOR

        # Background mesh resolution
        self.nx = max(20, int((self.x_max - self.x_min) / BACKGROUND_CELL_SIZE))
        self.ny = max(20, int((self.y_max - self.y_min) / BACKGROUND_CELL_SIZE))
        self.nz = max(10, int((self.z_max - self.z_min) / BACKGROUND_CELL_SIZE))

    # ── System dictionaries ──────────────────────────────────────────────────

    def write_block_mesh_dict(self):
        """Generate blockMeshDict for background hex mesh."""
        content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      blockMeshDict;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

scale 1;

vertices
(
    ({self.x_min:.6f} {self.y_min:.6f} {self.z_min:.6f})  // 0
    ({self.x_max:.6f} {self.y_min:.6f} {self.z_min:.6f})  // 1
    ({self.x_max:.6f} {self.y_max:.6f} {self.z_min:.6f})  // 2
    ({self.x_min:.6f} {self.y_max:.6f} {self.z_min:.6f})  // 3
    ({self.x_min:.6f} {self.y_min:.6f} {self.z_max:.6f})  // 4
    ({self.x_max:.6f} {self.y_min:.6f} {self.z_max:.6f})  // 5
    ({self.x_max:.6f} {self.y_max:.6f} {self.z_max:.6f})  // 6
    ({self.x_min:.6f} {self.y_max:.6f} {self.z_max:.6f})  // 7
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({self.nx} {self.ny} {self.nz}) simpleGrading (1 1 1)
);

edges
(
);

boundary
(
    inlet
    {{
        type patch;
        faces
        (
            {self._inlet_face()}
        );
    }}
    outlet
    {{
        type patch;
        faces
        (
            {self._outlet_face()}
        );
    }}
    top
    {{
        type patch;
        faces
        (
            (4 5 6 7)
        );
    }}
    ground
    {{
        type wall;
        faces
        (
            (0 1 2 3)
        );
    }}
    sides
    {{
        type patch;
        faces
        (
            {self._side_faces()}
        );
    }}
);

mergePatchPairs
(
);

// ************************************************************************* //
"""
        (self.case_dir / "system" / "blockMeshDict").write_text(content)

    def write_snappy_hex_mesh_dict(self):
        """Generate snappyHexMeshDict for building geometry snapping."""
        buildings = self.plan.buildings
        bike_stations = self.plan.bike_stations

        # Geometry entries
        geom_lines = []
        refinement_lines = []
        for b in buildings:
            name = b.id
            geom_lines.append(f'    {name}\n    {{\n        type triSurfaceMesh;\n        name {name};\n    }}')
            # Refine around buildings
            level = SNAPPY_REFINEMENT_LEVELS
            refinement_lines.append(f'        {name}\n        {{\n            level ({level[0]} {level[1]});\n        }}')

        for bike in bike_stations:
            name = bike.id
            geom_lines.append(f'    {name}\n    {{\n        type triSurfaceMesh;\n        name {name};\n    }}')

        # Location in mesh (must be in fluid region)
        lx = (self.x_min + self.x_max) / 2
        ly = (self.y_min + self.y_max) / 2
        lz = self.z_max * 0.3  # High enough to avoid buildings

        content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      snappyHexMeshDict;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

castellatedMesh true;
snap            true;
addLayers       false;

geometry
{{
{chr(10).join(geom_lines)}
}};

castellatedMeshControls
{{
    maxLocalCells 1000000;
    maxGlobalCells 5000000;
    minRefinementCells 3;
    nCellsBetweenLevels 3;
    maxLoadUnbalance 0.10;

    features
    (
    );

    refinementSurfaces
    {{
{chr(10).join(refinement_lines)}
    }};

    resolveFeatureAngle 30;

    refinementRegions
    {{
    }};

    locationInMesh ({lx:.6f} {ly:.6f} {lz:.6f});
}}

snapControls
{{
    nSmoothPatch 3;
    tolerance 2.0;
    nSolveIter 30;
    nRelaxIter 5;
    nFeatureSnapIter 10;
    implicitFeatureSnap false;
    explicitFeatureSnap true;
    multiRegionFeatureSnap false;
}}

addLayersControls
{{
    relativeSizes true;
    layers
    {{
    }};
    expansionRatio 1.0;
    finalLayerThickness 0.3;
    minThickness 0.1;
    nGrow 0;
    featureAngle 60;
    nRelaxIter 3;
    nSmoothSurfaceNormals 1;
    nSmoothNormals 3;
    nSmoothThickness 10;
    maxFaceThicknessRatio 0.5;
    maxThicknessToMedialRatio 0.3;
    minMedianAxisAngle 90;
    nBufferCellsNoExtrude 0;
    nLayerIter 50;
}}

meshQualityControls
{{
    #include "meshQualityDict"
}}

mergeTolerance 1e-6;
allowFreeStandingZoneFaces true;

// ************************************************************************* //
"""
        (self.case_dir / "system" / "snappyHexMeshDict").write_text(content)

    def write_control_dict(self):
        """Generate controlDict with probe and cutting plane function objects."""
        bike_stations = self.plan.bike_stations
        buildings = self.plan.buildings

        # Probe locations at pedestrian height (z=1.5m)
        probe_points = []
        for bike in bike_stations:
            cx, cy = bike.centroid
            probe_points.append(f"        ({cx:.3f} {cy:.3f} 1.5)")

        # Generate all STL patch names for function objects
        stl_names = [b.id for b in buildings] + [b.id for b in bike_stations]
        patches_str = " ".join(f'"{n}"' for n in stl_names)

        content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      controlDict;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

application     simpleFoam;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {END_TIME};
deltaT          1;
writeControl    timeStep;
writeInterval   {WRITE_INTERVAL};
purgeWrite      0;
writeFormat     ascii;
writePrecision  8;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;

functions
{{
    bikeProbes
    {{
        type            probes;
        libs            (sampling);
        writeControl    timeStep;
        writeInterval   {WRITE_INTERVAL};
        fields          (U p);
        probeLocations
        (
{chr(10).join(probe_points) if probe_points else '        // No bike stations'}
        );
    }}

    pedestrianPlane
    {{
        type            surfaces;
        libs            (sampling);
        writeControl    timeStep;
        writeInterval   {WRITE_INTERVAL};
        surfaceFormat   raw;
        fields          (U);
        interpolationScheme cellPoint;
        surfaces
        (
            z_1.5
            {{
                type            cuttingPlane;
                planeType       pointAndNormal;
                pointAndNormalDict
                {{
                    point       ({(self.x_min + self.x_max)/2:.1f} {(self.y_min + self.y_max)/2:.1f} 1.5);
                    normal      (0 0 1);
                }};
                interpolate     true;
            }}
        );
    }}

    wallShearStress
    {{
        type            wallShearStress;
        libs            (fieldFunctionObjects);
        writeControl    timeStep;
        writeInterval   {WRITE_INTERVAL};
        patches         ({patches_str});
    }}
}}

// ************************************************************************* //
"""
        (self.case_dir / "system" / "controlDict").write_text(content)

    def write_fv_schemes(self):
        """Generate fvSchemes (discretization schemes)."""
        content = """/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      fvSchemes;
}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

ddtSchemes
{
    default         steadyState;
}

gradSchemes
{
    default         Gauss linear;
}

divSchemes
{
    default         none;
    div(phi,U)      bounded Gauss linearUpwind grad(U);
    div(phi,k)      bounded Gauss upwind;
    div(phi,epsilon) bounded Gauss upwind;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}

laplacianSchemes
{
    default         Gauss linear corrected;
}

interpolationSchemes
{
    default         linear;
}

snGradSchemes
{
    default         corrected;
}

wallDist
{
    method          meshWave;
}

// ************************************************************************* //
"""
        (self.case_dir / "system" / "fvSchemes").write_text(content)

    def write_fv_solution(self):
        """Generate fvSolution (solver settings)."""
        content = """/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      fvSolution;
}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

solvers
{
    p
    {
        solver          GAMG;
        smoother        GaussSeidel;
        tolerance       1e-7;
        relTol          0.01;
    }

    Phi
    {
        $p;
    }

    U
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-7;
        relTol          0.01;
        nSweeps         1;
    }

    k
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-7;
        relTol          0.1;
        nSweeps         1;
    }

    epsilon
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-7;
        relTol          0.1;
        nSweeps         1;
    }
}

SIMPLE
{
    nNonOrthogonalCorrectors 0;
    pRefCell        0;
    pRefValue       0;
    residualControl
    {
        p               1e-4;
        U               1e-4;
        k               1e-4;
        epsilon         1e-4;
    }
}

relaxationFactors
{
    fields
    {
        p               0.3;
    }
    equations
    {
        U               0.7;
        k               0.7;
        epsilon         0.7;
    }
}

// ************************************************************************* //
"""
        (self.case_dir / "system" / "fvSolution").write_text(content)

    def write_mesh_quality_dict(self):
        """Generate meshQualityDict."""
        content = """/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      meshQualityDict;
}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

#includeEtc "caseDicts/meshQualityDict"
nSmoothScale 4;
errorReduction 0.75;

// ************************************************************************* //
"""
        (self.case_dir / "system" / "meshQualityDict").write_text(content)

    # ── Constant dictionaries ─────────────────────────────────────────────────

    def write_transport_properties(self):
        """Generate transportProperties (air at 20°C)."""
        content = """/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      transportProperties;
}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

transportModel  Newtonian;
nu              1.5e-05;

// ************************************************************************* //
"""
        (self.case_dir / "constant" / "transportProperties").write_text(content)

    def write_turbulence_properties(self):
        """Generate turbulenceProperties (k-epsilon)."""
        content = """/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      turbulenceProperties;
}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

simulationType  RAS;

RAS
{
    model           kEpsilon;
    turbulence      on;
    printCoeffs     on;
}

// ************************************************************************* //
"""
        (self.case_dir / "constant" / "turbulenceProperties").write_text(content)

    # ── Boundary conditions ───────────────────────────────────────────────────

    def write_boundary_conditions(self):
        """Generate all 0/ boundary condition files."""
        zero_dir = self.case_dir / "0"

        buildings = self.plan.buildings
        bike_stations = self.plan.bike_stations
        all_stl = [b.id for b in buildings] + [b.id for b in bike_stations]

        # Inflow turbulence quantities
        u_in = self.wind_speed
        I_ref = 0.15  # Turbulence intensity
        k_in = 1.5 * (I_ref * u_in) ** 2
        Cmu = 0.09
        L = self.z_max * 0.1  # Turbulence length scale
        eps_in = Cmu ** 0.75 * k_in ** 1.5 / L
        nut_in = Cmu * k_in ** 2 / eps_in

        # Wind direction vector
        dir_map = {"N": (0, -1), "S": (0, 1), "E": (-1, 0), "W": (1, 0)}
        ux, uy = dir_map.get(self.wind_direction, (0, -1))

        # ── U ──
        u_content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       volVectorField;
    object      U;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

dimensions      [0 1 -1 0 0 0 0];
internalField   uniform (0 0 0);

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform ({ux} {uy} 0);
    }}
    outlet
    {{
        type            zeroGradient;
    }}
    top
    {{
        type            symmetry;
    }}
    ground
    {{
        type            noSlip;
    }}
    sides
    {{
        type            symmetry;
    }}
"""
        for name in all_stl:
            u_content += f"""    {name}
    {{
        type            noSlip;
    }}
"""
        u_content += """}

// ************************************************************************* //
"""
        (zero_dir / "U").write_text(u_content)

        # ── p ──
        p_content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       volScalarField;
    object      p;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;

boundaryField
{{
    inlet
    {{
        type            zeroGradient;
    }}
    outlet
    {{
        type            fixedValue;
        value           uniform 0;
    }}
    top
    {{
        type            symmetry;
    }}
    ground
    {{
        type            zeroGradient;
    }}
    sides
    {{
        type            symmetry;
    }}
"""
        for name in all_stl:
            p_content += f"""    {name}
    {{
        type            zeroGradient;
    }}
"""
        p_content += """}

// ************************************************************************* //
"""
        (zero_dir / "p").write_text(p_content)

        # ── k ──
        k_content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       volScalarField;
    object      k;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

dimensions      [0 2 -2 0 0 0 0];
internalField   uniform {k_in:.6e};

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform {k_in:.6e};
    }}
    outlet
    {{
        type            zeroGradient;
    }}
    top
    {{
        type            symmetry;
    }}
    ground
    {{
        type            kqRWallFunction;
        value           uniform {k_in:.6e};
    }}
    sides
    {{
        type            symmetry;
    }}
"""
        for name in all_stl:
            k_content += f"""    {name}
    {{
        type            kqRWallFunction;
        value           uniform {k_in:.6e};
    }}
"""
        k_content += """}

// ************************************************************************* //
"""
        (zero_dir / "k").write_text(k_content)

        # ── epsilon ──
        eps_content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       volScalarField;
    object      epsilon;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

dimensions      [0 2 -3 0 0 0 0];
internalField   uniform {eps_in:.6e};

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform {eps_in:.6e};
    }}
    outlet
    {{
        type            zeroGradient;
    }}
    top
    {{
        type            symmetry;
    }}
    ground
    {{
        type            epsilonWallFunction;
        value           uniform {eps_in:.6e};
    }}
    sides
    {{
        type            symmetry;
    }}
"""
        for name in all_stl:
            eps_content += f"""    {name}
    {{
        type            epsilonWallFunction;
        value           uniform {eps_in:.6e};
    }}
"""
        eps_content += """}

// ************************************************************************* //
"""
        (zero_dir / "epsilon").write_text(eps_content)

        # ── nut ──
        nut_content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       volScalarField;
    object      nut;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

dimensions      [0 2 -1 0 0 0 0];
internalField   uniform 0;

boundaryField
{{
    inlet
    {{
        type            calculated;
        value           uniform {nut_in:.6e};
    }}
    outlet
    {{
        type            calculated;
        value           uniform 0;
    }}
    top
    {{
        type            symmetry;
    }}
    ground
    {{
        type            nutkWallFunction;
        value           uniform 0;
    }}
    sides
    {{
        type            symmetry;
    }}
"""
        for name in all_stl:
            nut_content += f"""    {name}
    {{
        type            nutkWallFunction;
        value           uniform 0;
    }}
"""
        nut_content += """}

// ************************************************************************* //
"""
        (zero_dir / "nut").write_text(nut_content)

    # ── Case summary ──────────────────────────────────────────────────────────

    def write_case_summary(self):
        """Write a human-readable case summary."""
        buildings = self.plan.buildings
        bikes = self.plan.bike_stations

        lines = [
            "=" * 70,
            "UrbanWind CFD Case Summary",
            "=" * 70,
            f"Generated: Auto-generated from SitePlan",
            f"",
            f"Domain: {self.x_max - self.x_min:.1f} × {self.y_max - self.y_min:.1f} × {self.z_max:.1f} m",
            f"  x: [{self.x_min:.1f}, {self.x_max:.1f}]",
            f"  y: [{self.y_min:.1f}, {self.y_max:.1f}]",
            f"  z: [{self.z_min:.1f}, {self.z_max:.1f}]",
            f"",
            f"Wind: {self.wind_speed} m/s, direction: {self.wind_direction}",
            f"Solver: simpleFoam (steady RANS, k-ε turbulence)",
            f"End time: {END_TIME} iterations, write every {WRITE_INTERVAL}",
            f"",
            f"Buildings ({len(buildings)}):",
        ]
        for b in buildings:
            bbox = b.bbox
            h = b.properties.get("height", "?")
            name = b.properties.get("name_zh", b.properties.get("name", b.id))
            lines.append(f"  {name}: {bbox[2]-bbox[0]:.1f}×{bbox[3]-bbox[1]:.1f}m, h={h}m")

        lines.append(f"")
        lines.append(f"Bike Stations ({len(bikes)}):")
        for bk in bikes:
            cx, cy = bk.centroid
            cat = bk.properties.get("category", "?")
            lines.append(f"  {bk.id}: ({cx:.1f}, {cy:.1f}) [{cat}]")

        lines.append(f"")
        lines.append(f"Mesh: {self.nx}×{self.ny}×{self.nz} = {self.nx*self.ny*self.nz:,} background cells")
        lines.append("=" * 70)

        (self.case_dir / "CASE_SUMMARY.txt").write_text("\n".join(lines), encoding="utf-8")

    # ── Face helpers ──────────────────────────────────────────────────────────

    def _inlet_face(self) -> str:
        if self.wind_direction == "N":
            return "(3 7 6 2)"
        elif self.wind_direction == "S":
            return "(0 1 5 4)"
        elif self.wind_direction == "E":
            return "(0 4 7 3)"
        else:  # W
            return "(1 2 6 5)"

    def _outlet_face(self) -> str:
        if self.wind_direction == "N":
            return "(0 1 5 4)"
        elif self.wind_direction == "S":
            return "(3 7 6 2)"
        elif self.wind_direction == "E":
            return "(1 2 6 5)"
        else:  # W
            return "(0 4 7 3)"

    def _side_faces(self) -> str:
        if self.wind_direction in ("N", "S"):
            return "(0 3 7 4)\n            (1 5 6 2)"
        else:
            return "(3 2 6 7)\n            (0 4 5 1)"
