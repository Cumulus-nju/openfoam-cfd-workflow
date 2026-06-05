"""
Generate OpenFOAM toy case for Phase 2 CFD-ML prototype.

Scenario: 100m x 100m urban block with 2 buildings and 5 bike candidate locations.
Wind: north wind (from -y), 6 m/s at reference height (10m).
Simulated at pedestrian height (1.5m).

Domain layout (top-down view, x-z plane):
    y=100  +---------------------+
           |                     |
           |   [Bldg1]   [Bldg2] |
           |     B1  B2    B3    |
           |     B4       B5     |
           |                     |
    y=0    +---------------------+
          x=0                   x=100

Buildings:
  - Bldg1: 20m(L) x 15m(W) x 30m(H), center at (35, 40)
  - Bldg2: 15m(L) x 10m(W) x 12m(H), center at (65, 65)

Bikes (simplified as cuboids, 5 candidate locations):
  - B1: center (25, 55) - upstream of Bldg1
  - B2: center (38, 55) - corner region of Bldg1
  - B3: center (55, 75) - between buildings
  - B4: center (25, 30) - open area, reference
  - B5: center (75, 35) - downstream of Bldg2

Wind: from north (negative y direction), inlet velocity 6 m/s at z=10m,
       power-law profile with exponent 0.25 (urban terrain).
"""

import os
import sys

# ── Configuration ────────────────────────────────────────────
CASE_DIR = "D:/Phase2_CFD_ML/cfd_cases/toy_case"

# Domain dimensions (meters)
DOMAIN_LX = 200.0   # x: cross-wind width (wider to avoid blockage effects)
DOMAIN_LY = 250.0   # y: along-wind length (longer for wake development)
DOMAIN_LZ = 60.0    # z: vertical height (~5x tallest building)

# Buildings: (name, cx, cy, lx, ly, h)
# cx,cy = footprint center; lx,ly = footprint dimensions (x,y); h = height
BUILDINGS = [
    ("building1", 35.0, 40.0, 20.0, 15.0, 30.0),
    ("building2", 65.0, 65.0, 15.0, 10.0, 12.0),
]

# Bikes: (name, cx, cy, lx, ly, h)
BIKES = [
    ("bike1", 25.0, 75.0, 1.7, 0.5, 1.0),
    ("bike2", 38.0, 70.0, 1.7, 0.5, 1.0),
    ("bike3", 55.0, 78.0, 1.7, 0.5, 1.0),
    ("bike4", 25.0, 55.0, 1.7, 0.5, 1.0),
    ("bike5", 75.0, 50.0, 1.7, 0.5, 1.0),
]

# Wind parameters
WIND_SPEED_REF = 6.0     # m/s at reference height
WIND_REF_HEIGHT = 10.0   # m
WIND_PROFILE_EXP = 0.25  # urban terrain
WIND_DIRECTION = "north"  # wind from -y

# Mesh
N_CELLS_X = 80
N_CELLS_Y = 100
N_CELLS_Z = 40
GRADING = 0.2  # expansion ratio towards domain boundaries

# ── Helper functions ─────────────────────────────────────────

def write_foam_header(f, obj_name, class_name):
    """Write OpenFOAM file header."""
    f.write(f"""/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  ubuntu                                |
|   \\\\  /    A nd           | Web:      www.OpenFOAM.org                      |
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

def calc_bounding_box(buildings, bikes):
    """Calculate overall bounding box margins."""
    all_obs = [(cx - lx/2, cx + lx/2, cy - ly/2, cy + ly/2, h)
               for _, cx, cy, lx, ly, h in buildings + bikes]
    x_min = min(o[0] for o in all_obs)
    x_max = max(o[1] for o in all_obs)
    y_min = min(o[2] for o in all_obs)
    y_max = max(o[3] for o in all_obs)
    return x_min, x_max, y_min, y_max

def obst_to_stl(obstacles, case_dir):
    """Generate STL files for each obstacle using surface feature vertices."""
    import struct
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
            (0, 3, 2), (0, 2, 1),  # bottom
            (4, 5, 6), (4, 6, 7),  # top
            (0, 1, 5), (0, 5, 4),  # front (y-)
            (2, 3, 7), (2, 7, 6),  # back  (y+)
            (0, 4, 7), (0, 7, 3),  # left  (x-)
            (1, 2, 6), (1, 6, 5),  # right (x+)
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
        print(f"  Created {path}")

# ── Generation ───────────────────────────────────────────────

def generate_case():
    os.makedirs(CASE_DIR, exist_ok=True)

    # Create system directory
    os.makedirs(os.path.join(CASE_DIR, "system"), exist_ok=True)
    os.makedirs(os.path.join(CASE_DIR, "constant"), exist_ok=True)
    os.makedirs(os.path.join(CASE_DIR, "0"), exist_ok=True)

    # Generate STL for obstacles
    print("Generating STL geometry files...")
    obst_to_stl(BUILDINGS + BIKES, CASE_DIR)

    # ── system/blockMeshDict ──
    # We'll use a blockMesh with geometry defined via vertices and blocks,
    # then use snappyHexMesh to snap to STL geometry.

    # Domain: from (-30, -50, 0) to (130, 150, 60)
    # This gives enough upstream/downstream distance
    dx, dy, dz = DOMAIN_LX, DOMAIN_LY, DOMAIN_LZ
    x0, y0, z0 = -30.0, -50.0, 0.0

    # blockMesh vertices (8 corners)
    v = [
        (x0,      y0,      z0),      # 0
        (x0 + dx, y0,      z0),      # 1
        (x0 + dx, y0 + dy, z0),      # 2
        (x0,      y0 + dy, z0),      # 3
        (x0,      y0,      z0 + dz), # 4
        (x0 + dx, y0,      z0 + dz), # 5
        (x0 + dx, y0 + dy, z0 + dz), # 6
        (x0,      y0 + dy, z0 + dz), # 7
    ]

    nx, ny, nz = N_CELLS_X, N_CELLS_Y, N_CELLS_Z

    # Write blockMeshDict with grading
    path = os.path.join(CASE_DIR, "system", "blockMeshDict")
    with open(path, "w") as f:
        write_foam_header(f, "blockMeshDict", "dictionary")
        f.write(f"""
scale   1;

vertices
(
    ({v[0][0]} {v[0][1]} {v[0][2]})
    ({v[1][0]} {v[1][1]} {v[1][2]})
    ({v[2][0]} {v[2][1]} {v[2][2]})
    ({v[3][0]} {v[3][1]} {v[3][2]})
    ({v[4][0]} {v[4][1]} {v[4][2]})
    ({v[5][0]} {v[5][1]} {v[5][2]})
    ({v[6][0]} {v[6][1]} {v[6][2]})
    ({v[7][0]} {v[7][1]} {v[7][2]})
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz})
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
            (0 4 7 3)
        );
    }}
    outlet
    {{
        type patch;
        faces
        (
            (2 6 5 1)
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
            (0 3 2 1)
        );
    }}
    sides
    {{
        type patch;
        faces
        (
            (0 1 5 4)
            (3 7 6 2)
        );
    }}
);

mergePatchPairs
(
);
""")
    print(f"  Created {path}")

    # ── system/snappyHexMeshDict ──
    path = os.path.join(CASE_DIR, "system", "snappyHexMeshDict")
    with open(path, "w") as f:
        write_foam_header(f, "snappyHexMeshDict", "dictionary")

        # List all STL files
        all_obs = BUILDINGS + BIKES
        geom_entries = []
        refinement_regions = []
        for name, _, _, _, _, _ in all_obs:
            geom_entries.append(f"""    {name}
    {{
        type triSurfaceMesh;
        name {name};
    }}""")
            refinement_regions.append(f"""        {name}
        {{
            mode inside;
            levels ((1E15 1));
        }}""")

        f.write(f"""
castellatedMesh true;
snap            true;
addLayers       false;

geometry
{{
{chr(10).join(geom_entries)}
}};

castellatedMeshControls
{{
    maxLocalCells 500000;
    maxGlobalCells 2000000;
    minRefinementCells 10;
    maxLoadUnbalance 0.10;
    nCellsBetweenLevels 3;

    features
    (
    );

    refinementSurfaces
    {{
{chr(10).join(refinement_regions)}
    }}

    resolveFeatureAngle 30;

    locationInMesh ({x0 + dx/2} {y0 + dy/2} {dz/2});
}}

snapControls
{{
    nSmoothPatch 3;
    tolerance 2.0;
    nSolveIter 30;
    nRelaxIter 5;
    nFeatureSnapIter 10;
}}

meshQualityControls
{{
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
}}

writeFlags
(
    scalarLevels
    layerSets
    layerFields
);
""")
    print(f"  Created {path}")

    # ── constant/transportProperties ──
    path = os.path.join(CASE_DIR, "constant", "transportProperties")
    with open(path, "w") as f:
        write_foam_header(f, "transportProperties", "dictionary")
        f.write("""
transportModel  Newtonian;

nu              [0 2 -1 0 0 0 0] 1.5e-05;
""")
    print(f"  Created {path}")

    # ── constant/turbulenceProperties ──
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

    # ── system/controlDict ──
    path = os.path.join(CASE_DIR, "system", "controlDict")
    with open(path, "w") as f:
        write_foam_header(f, "controlDict", "dictionary")
        f.write("""
application     simpleFoam;

startFrom       startTime;

startTime       0;

stopAt          endTime;

endTime         1000;

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
{
    // Probe wind speed at bike locations (z=1.5m)
    bikeProbes
    {
        type            probes;
        functionObjectLibs ("libsampling.so");
        enabled         true;
        writeControl    timeStep;
        writeInterval   100;
        probeLocations
        (
""")
        # Add bike probe locations
        for name, cx, cy, lx, ly, h in BIKES:
            f.write(f"            ({cx} {cy} 1.5)\n")

        f.write("""        );
        fields
        (
            U
            p
        );
    }

    // Sample entire plane at z=1.5m (pedestrian height)
    pedestrianPlane
    {
        type            cuttingPlane;
        functionObjectLibs ("libsampling.so");
        enabled         true;
        writeControl    writeTime;
        planeType       pointAndNormal;
        pointAndNormalDict
        {
            basePoint       (0 0 1.5);
            normalVector    (0 0 1);
        }
        interpolate     true;
        fields
        (
            U
        );
    }
}
""")
    print(f"  Created {path}")

    # ── system/fvSchemes ──
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
    path = os.path.join(CASE_DIR, "system", "fvSolution")
    with open(path, "w") as f:
        write_foam_header(f, "fvSolution", "dictionary")
        f.write("""
solvers
{
    p
    {
        solver          GAMG;
        tolerance       1e-06;
        relTol          0.1;
        smoother        GaussSeidel;
    }

    U
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-06;
        relTol          0.1;
    }

    k
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-06;
        relTol          0.1;
    }

    epsilon
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-06;
        relTol          0.1;
    }
}

SIMPLE
{
    nNonOrthogonalCorrectors 2;
    consistent      yes;

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
""")
    print(f"  Created {path}")

    # ── 0/ directory (boundary conditions) ──

    # Inlet wind profile
    z_ref = WIND_REF_HEIGHT
    u_ref = WIND_SPEED_REF
    alpha = WIND_PROFILE_EXP

    # 0/U
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
        type            atmBoundaryLayerInletVelocity;
        flowDir         (0 1 0);
        zDir            (0 0 1);
        Uref            {u_ref};
        Zref            {z_ref};
        z0              uniform 0.1;
        d               uniform 0;
    }}
    outlet
    {{
        type            zeroGradient;
    }}
    top
    {{
        type            symmetryPlane;
    }}
    ground
    {{
        type            noSlip;
    }}
    sides
    {{
        type            symmetryPlane;
    }}
}}
""")
    print(f"  Created {path}")

    # 0/p
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
        type            symmetryPlane;
    }}
    ground
    {{
        type            zeroGradient;
    }}
    sides
    {{
        type            symmetryPlane;
    }}
}}
""")
    print(f"  Created {path}")

    # 0/k
    path = os.path.join(CASE_DIR, "0", "k")
    with open(path, "w") as f:
        write_foam_header(f, "k", "volScalarField")
        # Estimate k from turbulence intensity (assume 10%)
        TI = 0.10
        k_val = 1.5 * (u_ref * TI) ** 2
        f.write(f"""
dimensions      [0 2 -2 0 0 0 0];

internalField   uniform {k_val:.4f};

boundaryField
{{
    inlet
    {{
        type            atmBoundaryLayerInletK;
        flowDir         (0 1 0);
        zDir            (0 0 1);
        Uref            {u_ref};
        Zref            {z_ref};
        z0              uniform 0.1;
        d               uniform 0;
    }}
    outlet
    {{
        type            zeroGradient;
    }}
    top
    {{
        type            symmetryPlane;
    }}
    ground
    {{
        type            kqRWallFunction;
        value           uniform {k_val:.4f};
    }}
    sides
    {{
        type            symmetryPlane;
    }}
}}
""")
    print(f"  Created {path}")

    # 0/epsilon
    path = os.path.join(CASE_DIR, "0", "epsilon")
    with open(path, "w") as f:
        write_foam_header(f, "epsilon", "volScalarField")
        # Estimate epsilon from k and mixing length
        Cmu = 0.09
        kappa = 0.41
        L = kappa * z_ref  # mixing length at reference height
        eps_val = Cmu ** 0.75 * k_val ** 1.5 / L
        f.write(f"""
dimensions      [0 2 -3 0 0 0 0];

internalField   uniform {eps_val:.6f};

boundaryField
{{
    inlet
    {{
        type            atmBoundaryLayerInletEpsilon;
        flowDir         (0 1 0);
        zDir            (0 0 1);
        Uref            {u_ref};
        Zref            {z_ref};
        z0              uniform 0.1;
        d               uniform 0;
    }}
    outlet
    {{
        type            zeroGradient;
    }}
    top
    {{
        type            symmetryPlane;
    }}
    ground
    {{
        type            epsilonWallFunction;
        value           uniform {eps_val:.6f};
    }}
    sides
    {{
        type            symmetryPlane;
    }}
}}
""")
    print(f"  Created {path}")

    # 0/nut
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
        type            symmetryPlane;
    }
    ground
    {
        type            nutkWallFunction;
        value           uniform 0;
    }
    sides
    {
        type            symmetryPlane;
    }
}
""")
    print(f"  Created {path}")

    # ── Case summary ──
    summary_path = os.path.join(CASE_DIR, "CASE_SUMMARY.txt")
    with open(summary_path, "w") as f:
        f.write("=" * 60 + "\n")
        f.write("Phase 2 Toy Case Summary\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Domain: {DOMAIN_LX} x {DOMAIN_LY} x {DOMAIN_LZ} m\n")
        f.write(f"Mesh: {N_CELLS_X} x {N_CELLS_Y} x {N_CELLS_Z} cells (background)\n")
        f.write(f"Wind: north (from -y), {WIND_SPEED_REF} m/s at {WIND_REF_HEIGHT} m\n")
        f.write(f"Turbulence model: k-epsilon RANS with atmospheric BL inlet\n\n")
        f.write("Buildings:\n")
        for name, cx, cy, lx, ly, h in BUILDINGS:
            f.write(f"  {name}: center({cx},{cy}) dim({lx}x{ly}) height={h}m\n")
        f.write("\nBike candidate locations:\n")
        for name, cx, cy, lx, ly, h in BIKES:
            f.write(f"  {name}: center({cx},{cy}) height={h}m\n")
        f.write("\nRun workflow:\n")
        f.write("  1. blockMesh\n")
        f.write("  2. snappyHexMesh -overwrite\n")
        f.write("  3. simpleFoam\n")

    print(f"\nCase generated at: {CASE_DIR}")
    print(f"Run 'cat {summary_path}' for the summary.")


if __name__ == "__main__":
    generate_case()
