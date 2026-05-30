"""
Generate OpenFOAM urban_block case for Phase 2 CFD-ML.
8 buildings + 20 bike candidates in a realistic small urban block.
Wind: north wind (from +y), 5 m/s uniform flow.
OpenFOAM v2312 (ESI) compatible.
"""

import os
import struct

# ── Configuration ────────────────────────────────────────────
CASE_DIR = "D:/Phase2_CFD_ML/cfd_cases/urban_block"

# Domain (meters): 300 x 300 x 80
DOMAIN_LX = 300.0
DOMAIN_LY = 300.0
DOMAIN_LZ = 80.0

# Origin offset (gives margins around buildings)
X0 = -20.0
Y0 = -30.0
Z0 = 0.0

# Buildings: (name, cx, cy, lx, ly, h)
# Layout: south row (y~60-80), north row (y~145-220)
BUILDINGS = [
    # South row
    ("building1", 65.0, 65.0, 20.0, 16.0, 25.0),
    ("building2", 130.0, 60.0, 14.0, 20.0, 40.0),   # Tallest
    ("building3", 185.0, 70.0, 28.0, 12.0, 15.0),
    ("building4", 245.0, 65.0, 10.0, 10.0, 8.0),    # Smallest
    # North row
    ("building5", 65.0, 150.0, 20.0, 16.0, 35.0),
    ("building6", 175.0, 145.0, 35.0, 20.0, 20.0),
    ("building7", 105.0, 220.0, 14.0, 14.0, 12.0),
    ("building8", 215.0, 210.0, 22.0, 16.0, 30.0),
]

# Bikes: (name, cx, cy, lx, ly, h)
# 0.6 x 0.4 x 1.0m cuboid = single bike parking spot
BIKES = [
    # Open/undisturbed reference
    ("bike1", 45.0, 50.0, 0.6, 0.4, 1.0),
    ("bike10", 260.0, 80.0, 0.6, 0.4, 1.0),
    ("bike15", 250.0, 185.0, 0.6, 0.4, 1.0),
    ("bike20", 55.0, 240.0, 0.6, 0.4, 1.0),
    # Wake regions
    ("bike16", 85.0, 42.0, 0.6, 0.4, 1.0),
    ("bike17", 155.0, 40.0, 0.6, 0.4, 1.0),
    ("bike18", 215.0, 42.0, 0.6, 0.4, 1.0),
    ("bike19", 248.0, 40.0, 0.6, 0.4, 1.0),
    # Street canyon
    ("bike2", 95.0, 55.0, 0.6, 0.4, 1.0),
    ("bike3", 100.0, 98.0, 0.6, 0.4, 1.0),
    ("bike4", 105.0, 145.0, 0.6, 0.4, 1.0),
    ("bike5", 100.0, 185.0, 0.6, 0.4, 1.0),
    # Corner/shear
    ("bike6", 155.0, 80.0, 0.6, 0.4, 1.0),
    ("bike7", 155.0, 118.0, 0.6, 0.4, 1.0),
    ("bike8", 200.0, 85.0, 0.6, 0.4, 1.0),
    ("bike9", 225.0, 100.0, 0.6, 0.4, 1.0),
    ("bike11", 45.0, 125.0, 0.6, 0.4, 1.0),
    ("bike12", 45.0, 180.0, 0.6, 0.4, 1.0),
    ("bike13", 135.0, 195.0, 0.6, 0.4, 1.0),
    ("bike14", 185.0, 185.0, 0.6, 0.4, 1.0),
]

# Wind
WIND_SPEED = 5.0       # m/s (uniform inlet)
TI = 0.10               # Turbulence intensity 10%
k_val = 1.5 * (WIND_SPEED * TI) ** 2
Cmu = 0.09
kappa = 0.41
L_mix = kappa * 10.0    # mixing length at 10m reference
eps_val = Cmu ** 0.75 * k_val ** 1.5 / L_mix

# Mesh: background grid
N_CELLS_X = 200
N_CELLS_Y = 200
N_CELLS_Z = 56
GRADING = 0.2  # z-direction grading (fine near ground)

# snappyHexMesh
BUILDING_LEVEL_MIN = 2
BUILDING_LEVEL_MAX = 3
BIKE_LEVEL_MIN = 2
BIKE_LEVEL_MAX = 2

ALL_OBS = BUILDINGS + BIKES


# ── Helpers ───────────────────────────────────────────────────

def write_foam_header(f, obj_name, class_name):
    """Write OpenFOAM dictionary file header (v2312 format)."""
    f.write(f"""/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2312                                |
|   \\\\  /    A nd           | Web:      www.openfoam.com                     |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       {class_name};
    object      {obj_name};
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //
""")


def obst_to_stl(obstacles, case_dir):
    """Generate binary STL files for box-shaped obstacles."""
    stl_dir = os.path.join(case_dir, "constant", "triSurface")
    os.makedirs(stl_dir, exist_ok=True)

    for name, cx, cy, lx, ly, h in obstacles:
        x0, x1 = cx - lx/2, cx + lx/2
        y0, y1 = cy - ly/2, cy + ly/2
        z0, z1 = 0.0, h

        verts = [
            (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
            (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
        ]
        faces = [
            (0, 3, 2), (0, 2, 1),   # bottom
            (4, 5, 6), (4, 6, 7),   # top
            (0, 1, 5), (0, 5, 4),   # front (y-)
            (2, 3, 7), (2, 7, 6),   # back  (y+)
            (0, 4, 7), (0, 7, 3),   # left  (x-)
            (1, 2, 6), (1, 6, 5),   # right (x+)
        ]

        path = os.path.join(stl_dir, f"{name}.stl")
        with open(path, "wb") as f:
            f.write(b"\x00" * 80)
            f.write(struct.pack("<I", len(faces)))
            for face in faces:
                v = [verts[i] for i in face]
                ux = (v[1][0]-v[0][0], v[1][1]-v[0][1], v[1][2]-v[0][2])
                uy = (v[2][0]-v[0][0], v[2][1]-v[0][1], v[2][2]-v[0][2])
                nx = ux[1]*uy[2] - ux[2]*uy[1]
                ny = ux[2]*uy[0] - ux[0]*uy[2]
                nz = ux[0]*uy[1] - ux[1]*uy[0]
                norm = (nx*nx + ny*ny + nz*nz) ** 0.5
                if norm > 0:
                    nx, ny, nz = nx/norm, ny/norm, nz/norm
                else:
                    nx, ny, nz = 0.0, 0.0, 1.0
                f.write(struct.pack("<3f", nx, ny, nz))
                for vi in v:
                    f.write(struct.pack("<3f", vi[0], vi[1], vi[2]))
                f.write(struct.pack("<H", 0))
        print(f"  STL: {path}")


# ── Main generation ───────────────────────────────────────────

def generate_case():
    os.makedirs(CASE_DIR, exist_ok=True)
    for sub in ["system", "constant", "0"]:
        os.makedirs(os.path.join(CASE_DIR, sub), exist_ok=True)

    # ── STL files ──
    print("Generating STL geometry...")
    obst_to_stl(BUILDINGS + BIKES, CASE_DIR)

    # Domain extents
    x_min, y_min = X0, Y0
    x_max, y_max = X0 + DOMAIN_LX, Y0 + DOMAIN_LY
    z_max = DOMAIN_LZ

    # ── system/blockMeshDict ──
    print("Writing blockMeshDict...")
    path = os.path.join(CASE_DIR, "system", "blockMeshDict")
    with open(path, "w") as f:
        write_foam_header(f, "blockMeshDict", "dictionary")
        f.write(f"""
scale   1;

vertices
(
    ({x_min} {y_min} {Z0})
    ({x_max} {y_min} {Z0})
    ({x_max} {y_max} {Z0})
    ({x_min} {y_max} {Z0})
    ({x_min} {y_min} {z_max})
    ({x_max} {y_min} {z_max})
    ({x_max} {y_max} {z_max})
    ({x_min} {y_max} {z_max})
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({N_CELLS_X} {N_CELLS_Y} {N_CELLS_Z})
    simpleGrading (1 1 {GRADING})
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
            (3 7 6 2)    // y=y_max (north), wind from +y
        );
    }}
    outlet
    {{
        type patch;
        faces
        (
            (1 5 4 0)    // y=y_min (south)
        );
    }}
    top
    {{
        type symmetry;
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
        type symmetry;
        faces
        (
            (0 4 7 3)    // x=x_min (west)
            (2 6 5 1)    // x=x_max (east)
        );
    }}
);

mergePatchPairs
(
);
""")
    print(f"  Created {path}")

    # ── system/snappyHexMeshDict ──
    print("Writing snappyHexMeshDict...")
    path = os.path.join(CASE_DIR, "system", "snappyHexMeshDict")
    with open(path, "w") as f:
        write_foam_header(f, "snappyHexMeshDict", "dictionary")

        # Geometry entries — use name as key (without .stl), file for actual file
        geom_lines = []
        for name, _, _, _, _, _ in ALL_OBS:
            geom_lines.append(f"""    {name}
    {{
        type triSurfaceMesh;
        file "{name}.stl";
    }}""")

        # Refinement entries for buildings (level 2-3)
        # Keys match geometry keys (without .stl)
        bldg_ref_lines = []
        for name, _, _, _, _, _ in BUILDINGS:
            bldg_ref_lines.append(f"""        {name}
        {{
            level ({BUILDING_LEVEL_MIN} {BUILDING_LEVEL_MAX});
        }}""")

        # Refinement entries for bikes (level 2-2)
        bike_ref_lines = []
        for name, _, _, _, _, _ in BIKES:
            bike_ref_lines.append(f"""        {name}
        {{
            level ({BIKE_LEVEL_MIN} {BIKE_LEVEL_MAX});
        }}""")

        all_ref_lines = bldg_ref_lines + bike_ref_lines

        # locationInMesh: center of domain, above tallest building
        loc_x = x_min + DOMAIN_LX / 2
        loc_y = y_min + DOMAIN_LY / 2
        loc_z = DOMAIN_LZ * 0.6  # well above all buildings

        f.write(f"""
castellatedMesh true;
snap            true;
addLayers       false;
mergeTolerance  1e-6;

geometry
{{
{chr(10).join(geom_lines)}
}};

castellatedMeshControls
{{
    maxLocalCells 5000000;
    maxGlobalCells 20000000;
    minRefinementCells 10;
    nCellsBetweenLevels 3;

    features
    (
    );

    refinementSurfaces
    {{
{chr(10).join(all_ref_lines)}
    }};

    refinementRegions
    {{
    }};

    resolveFeatureAngle 30;

    locationInMesh ({loc_x} {loc_y} {loc_z});

    allowFreeStandingZoneFaces true;
}}

snapControls
{{
    nSmoothPatch 3;
    tolerance 2.0;
    nSolveIter 100;
    nRelaxIter 5;
    nFeatureSnapIter 10;
    implicitFeatureSnap true;
    explicitFeatureSnap false;
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
    featureAngle 30;
    nRelaxIter 3;
    nSmoothSurfaceNormals 1;
    nSmoothNormals 3;
    nSmoothThickness 2;
    maxFaceThicknessRatio 0.5;
    minMedianAxisAngle 90;
    maxThicknessToMedialRatio 0.3;
}}

meshQualityControls
{{
    #include "meshQualityDict"
}}

writeFlags
(
    scalarLevels
    layerSets
    layerFields
);
""")
    print(f"  Created {path}")

    # ── system/meshQualityDict ──
    print("Writing meshQualityDict...")
    path = os.path.join(CASE_DIR, "system", "meshQualityDict")
    with open(path, "w") as f:
        write_foam_header(f, "meshQualityDict", "dictionary")
        f.write("""
maxNonOrtho 65;
maxBoundarySkewness 20;
maxInternalSkewness 4;
maxConcave 80;
minVol 1e-13;
minTetQuality 1e-9;
minArea -1;
minTwist 0.05;
minDeterminant 0.001;
minFaceWeight 0.05;
minVolRatio 0.01;
minTriangleTwist -1;
nSmoothScale 4;
errorReduction 0.75;
""")
    print(f"  Created {path}")

    # ── system/controlDict ──
    print("Writing controlDict...")
    path = os.path.join(CASE_DIR, "system", "controlDict")
    with open(path, "w") as f:
        write_foam_header(f, "controlDict", "dictionary")

        # Probe locations for all 20 bikes at z=1.5m
        probe_locs = []
        for name, cx, cy, _, _, _ in BIKES:
            probe_locs.append(f"        ({cx} {cy} 1.5)")

        f.write(f"""
application     simpleFoam;

startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         1500;
deltaT          1;

writeControl    timeStep;
writeInterval   100;
purgeWrite      0;
writeFormat     ascii;
writePrecision  6;
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
        enabled         true;
        writeControl    timeStep;
        writeInterval   100;
        probeLocations
        (
{chr(10).join(probe_locs)}
        );
        fields
        (
            U
            p
        );
    }}

    pedestrianPlane
    {{
        type            cuttingPlane;
        libs            (sampling);
        enabled         true;
        writeControl    writeTime;
        planeType       pointAndNormal;
        pointAndNormalDict
        {{
            basePoint       (0 0 1.5);
            normalVector    (0 0 1);
        }}
        interpolate     true;
        fields
        (
            U
        );
    }}
}}
""")
    print(f"  Created {path}")

    # ── system/fvSchemes ──
    print("Writing fvSchemes...")
    path = os.path.join(CASE_DIR, "system", "fvSchemes")
    with open(path, "w") as f:
        write_foam_header(f, "fvSchemes", "dictionary")
        f.write("""
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
    div(phi,U)      bounded Gauss upwind;
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
""")
    print(f"  Created {path}")

    # ── system/fvSolution ──
    print("Writing fvSolution...")
    path = os.path.join(CASE_DIR, "system", "fvSolution")
    with open(path, "w") as f:
        write_foam_header(f, "fvSolution", "dictionary")
        f.write(f"""
solvers
{{
    p
    {{
        solver          GAMG;
        tolerance       1e-06;
        relTol          0.1;
        smoother        GaussSeidel;
    }}

    U
    {{
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-06;
        relTol          0.1;
    }}

    k
    {{
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-06;
        relTol          0.1;
    }}

    epsilon
    {{
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-06;
        relTol          0.1;
    }}
}}

SIMPLE
{{
    nNonOrthogonalCorrectors 2;

    residualControl
    {{
        p               1e-4;
        U               1e-4;
        k               1e-4;
        epsilon         1e-4;
    }}
}}

relaxationFactors
{{
    fields
    {{
        p               0.3;
    }}
    equations
    {{
        U               0.7;
        k               0.7;
        epsilon         0.7;
    }}
}}
""")
    print(f"  Created {path}")

    # ── constant/transportProperties ──
    print("Writing transportProperties...")
    path = os.path.join(CASE_DIR, "constant", "transportProperties")
    with open(path, "w") as f:
        write_foam_header(f, "transportProperties", "dictionary")
        f.write("""
transportModel  Newtonian;
nu              [0 2 -1 0 0 0 0] 1.5e-05;
""")
    print(f"  Created {path}")

    # ── constant/turbulenceProperties ──
    print("Writing turbulenceProperties...")
    path = os.path.join(CASE_DIR, "constant", "turbulenceProperties")
    with open(path, "w") as f:
        write_foam_header(f, "turbulenceProperties", "dictionary")
        f.write("""
simulationType  RAS;

RAS
{
    RASModel        kEpsilon;
    turbulence      on;
    printCoeffs     on;
}
""")
    print(f"  Created {path}")

    # ── Helper: write BC patch entries for all STL objects ──
    def write_stl_bcs(f, patch_type, extra=""):
        """Write boundary entries for all building + bike STL patches."""
        for name, _, _, _, _, _ in ALL_OBS:
            f.write(f"""    {name}
    {{
        type            {patch_type};{extra}
    }}
""")

    # ── 0/U ──
    print("Writing 0/U...")
    path = os.path.join(CASE_DIR, "0", "U")
    with open(path, "w") as f:
        write_foam_header(f, "U", "volVectorField")
        f.write(f"""
dimensions      [0 1 -1 0 0 0 0];

internalField   uniform (0 0 0);

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform (0 -{WIND_SPEED} 0);
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
""")
        for name, _, _, _, _, _ in ALL_OBS:
            f.write(f"""    {name}
    {{
        type            noSlip;
    }}
""")
        f.write("}\n")
    print(f"  Created {path}")

    # ── 0/p ──
    print("Writing 0/p...")
    path = os.path.join(CASE_DIR, "0", "p")
    with open(path, "w") as f:
        write_foam_header(f, "p", "volScalarField")
        f.write(f"""
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
""")
        for name, _, _, _, _, _ in ALL_OBS:
            f.write(f"""    {name}
    {{
        type            zeroGradient;
    }}
""")
        f.write("}\n")
    print(f"  Created {path}")

    # ── 0/k ──
    print("Writing 0/k...")
    path = os.path.join(CASE_DIR, "0", "k")
    with open(path, "w") as f:
        write_foam_header(f, "k", "volScalarField")
        f.write(f"""
dimensions      [0 2 -2 0 0 0 0];

internalField   uniform {k_val:.4f};

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform {k_val:.4f};
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
        value           uniform {k_val:.4f};
    }}
    sides
    {{
        type            symmetry;
    }}
""")
        for name, _, _, _, _, _ in ALL_OBS:
            f.write(f"""    {name}
    {{
        type            kqRWallFunction;
        value           uniform {k_val:.4f};
    }}
""")
        f.write("}\n")
    print(f"  Created {path}")

    # ── 0/epsilon ──
    print("Writing 0/epsilon...")
    path = os.path.join(CASE_DIR, "0", "epsilon")
    with open(path, "w") as f:
        write_foam_header(f, "epsilon", "volScalarField")
        f.write(f"""
dimensions      [0 2 -3 0 0 0 0];

internalField   uniform {eps_val:.6f};

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform {eps_val:.6f};
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
        value           uniform {eps_val:.6f};
    }}
    sides
    {{
        type            symmetry;
    }}
""")
        for name, _, _, _, _, _ in ALL_OBS:
            f.write(f"""    {name}
    {{
        type            epsilonWallFunction;
        value           uniform {eps_val:.6f};
    }}
""")
        f.write("}\n")
    print(f"  Created {path}")

    # ── 0/nut ──
    print("Writing 0/nut...")
    path = os.path.join(CASE_DIR, "0", "nut")
    with open(path, "w") as f:
        write_foam_header(f, "nut", "volScalarField")
        f.write("""
dimensions      [0 2 -1 0 0 0 0];

internalField   uniform 0;

boundaryField
{
    inlet
    {
        type            calculated;
        value           uniform 0;
    }
    outlet
    {
        type            calculated;
        value           uniform 0;
    }
    top
    {
        type            symmetry;
    }
    ground
    {
        type            nutkWallFunction;
        value           uniform 0;
    }
    sides
    {
        type            symmetry;
    }
""")
        for name, _, _, _, _, _ in ALL_OBS:
            f.write(f"""    {name}
    {{
        type            nutkWallFunction;
        value           uniform 0;
    }}
""")
        f.write("}\n")
    print(f"  Created {path}")

    # ── CASE_SUMMARY.txt ──
    print("Writing CASE_SUMMARY.txt...")
    path = os.path.join(CASE_DIR, "CASE_SUMMARY.txt")
    with open(path, "w") as f:
        f.write("=" * 60 + "\n")
        f.write("Phase 2 Urban Block Case Summary\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Domain: {DOMAIN_LX} x {DOMAIN_LY} x {DOMAIN_LZ} m\n")
        f.write(f"Origin: ({X0}, {Y0}, {Z0})\n")
        f.write(f"Background mesh: {N_CELLS_X} x {N_CELLS_Y} x {N_CELLS_Z}"
                f" = {N_CELLS_X*N_CELLS_Y*N_CELLS_Z:,} cells\n")
        f.write(f"Z-grading: {GRADING} (fine near ground)\n")
        f.write(f"Wind: north (from +y), {WIND_SPEED} m/s uniform\n")
        f.write(f"Turbulence: k-epsilon RANS, TI={TI*100:.0f}%\n")
        f.write(f"k={k_val:.4f}, epsilon={eps_val:.6f}\n")
        f.write(f"snappyHexMesh: buildings level ({BUILDING_LEVEL_MIN}"
                f" {BUILDING_LEVEL_MAX}), bikes level ({BIKE_LEVEL_MIN}"
                f" {BIKE_LEVEL_MAX})\n\n")
        f.write(f"Buildings ({len(BUILDINGS)}):\n")
        for name, cx, cy, lx, ly, h in BUILDINGS:
            f.write(f"  {name}: center({cx},{cy}) dim({lx}x{ly}) h={h}m\n")
        f.write(f"\nBikes ({len(BIKES)}):\n")
        for name, cx, cy, lx, ly, h in BIKES:
            f.write(f"  {name}: center({cx},{cy}) z=1.5m\n")
        f.write("\nRun workflow:\n")
        f.write("  1. blockMesh\n")
        f.write("  2. snappyHexMesh -overwrite\n")
        f.write("  3. checkMesh\n")
        f.write("  4. simpleFoam\n")
        f.write("  5. postProcess -func bikeProbes\n")

    print(f"\n{'='*60}")
    print(f"Case generated at: {CASE_DIR}")
    print(f"STL files: {len(ALL_OBS)} ({len(BUILDINGS)} buildings + {len(BUILDINGS)} bikes)")
    print(f"Background mesh: {N_CELLS_X}×{N_CELLS_Y}×{N_CELLS_Z} = {N_CELLS_X*N_CELLS_Y*N_CELLS_Z:,} cells")
    print(f"Wind: {WIND_SPEED} m/s from north (uniform)")
    print(f"Turbulence: k-epsilon, k={k_val:.4f}, epsilon={eps_val:.6f}")
    print(f"Probes: {len(BIKES)} at z=1.5m")
    print(f"Cutting plane: z=1.5m pedestrian level")


if __name__ == "__main__":
    generate_case()
