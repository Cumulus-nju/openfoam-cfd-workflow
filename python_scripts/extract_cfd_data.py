"""
Extract CFD results and format as ML training data.

Reads OpenFOAM output (U field at pedestrian height z=1.5m) and converts to:
1. Standardized numpy arrays for model training
2. Bike location wind speed tables
3. Visualization plots of the wind field
"""

import numpy as np
import os
import sys
import json

# ── Configuration ────────────────────────────────────────────
CFD_RESULTS_DIR = "D:/Phase2_CFD_ML/cfd_cases/toy_case"
TRAINING_DATA_DIR = "D:/Phase2_CFD_ML/training_data"
MODEL_OUTPUT_DIR = "D:/Phase2_CFD_ML/model_outputs"

# Physical domain parameters (matching generate_toy_case.py)
DOMAIN_LX = 200.0
DOMAIN_LY = 250.0
X0, Y0 = -30.0, -50.0  # domain origin offset

# Grid resolution for ML model
ML_NX = 64   # cross-wind
ML_NY = 80   # along-wind

# Bike locations (matching toy case)
BIKES = [
    ("bike1", 25.0, 75.0, 1.7, 0.5, 1.0),
    ("bike2", 38.0, 70.0, 1.7, 0.5, 1.0),
    ("bike3", 55.0, 78.0, 1.7, 0.5, 1.0),
    ("bike4", 25.0, 55.0, 1.7, 0.5, 1.0),
    ("bike5", 75.0, 50.0, 1.7, 0.5, 1.0),
]

BUILDINGS = [
    ("building1", 35.0, 40.0, 20.0, 15.0, 30.0),
    ("building2", 65.0, 65.0, 15.0, 10.0, 12.0),
]

# ── Helpers ─────────────────────────────────────────────────

def phys_to_grid(cx, cy, nx=ML_NX, ny=ML_NY):
    """Convert physical (x,y) to grid index."""
    gx = int((cx - X0) / DOMAIN_LX * nx)
    gy = int((cy - Y0) / DOMAIN_LY * ny)
    return max(0, min(nx - 1, gx)), max(0, min(ny - 1, gy))


def read_openfoam_scalar(directory, time_step, field_name):
    """
    Read an OpenFOAM scalar field from a given time directory.

    This is a simplified reader for structured OF ASCII format.
    For snappyHexMesh (unstructured), we'd need the full mesh reader.
    For now, this provides the structure to fill in when CFD results exist.
    """
    # Placeholder: parse OpenFOAM field files
    # Real implementation would:
    # 1. Read mesh/points and mesh/cells
    # 2. Read field values
    # 3. Interpolate to regular grid
    raise NotImplementedError("CFD data not yet available - use synthetic data first")


def read_openfoam_probes(probe_file):
    """
    Read OpenFOAM probe output file.

    OpenFOAM probe output format:
    # Time  (0 0 0)  (1 0 0) ...
    time    val1      val2    ...
    """
    if not os.path.exists(probe_file):
        return None

    data = {}
    with open(probe_file, 'r') as f:
        lines = f.readlines()

    header = lines[0]
    values = []
    for line in lines[1:]:
        parts = line.strip().split()
        if len(parts) > 1:
            values.append([float(x) for x in parts])

    return np.array(values) if values else None


def interpolate_to_grid(points, values, nx, ny):
    """
    Interpolate scattered CFD data to a regular grid.

    Uses linear interpolation via scipy.
    """
    from scipy.interpolate import griddata

    x = points[:, 0]
    y = points[:, 1]

    xi = np.linspace(X0, X0 + DOMAIN_LX, nx)
    yi = np.linspace(Y0, Y0 + DOMAIN_LY, ny)
    Xi, Yi = np.meshgrid(xi, yi)

    grid = griddata((x, y), values, (Xi, Yi), method='linear', fill_value=0.0)
    return grid


def generate_layout_input(nx, ny):
    """
    Generate the model input (layout image) for a given case.
    Returns (3, ny, nx) array: building mask, building height, bike mask.
    """
    layout = np.zeros((3, ny, nx), dtype=np.float32)
    max_h = max(h for _, _, _, _, _, h in BUILDINGS)

    for name, cx, cy, lx, ly, h in BUILDINGS:
        x0, y0 = phys_to_grid(cx - lx/2, cy - ly/2, nx, ny)
        x1, y1 = phys_to_grid(cx + lx/2, cy + ly/2, nx, ny)
        x0, x1 = max(0, min(x0, x1)), min(nx - 1, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(ny - 1, max(y0, y1))
        layout[0, y0:y1+1, x0:x1+1] = 1.0
        layout[1, y0:y1+1, x0:x1+1] = h / max_h

    for name, cx, cy, lx, ly, h in BIKES:
        bx, by = phys_to_grid(cx, cy, nx, ny)
        r = 1
        for dy in range(-r, r+1):
            for dx in range(-r, r+1):
                px, py = bx + dx, by + dy
                if 0 <= px < nx and 0 <= py < ny:
                    layout[2, py, px] = 1.0

    return layout


def create_training_dataset():
    """
    Create a training dataset from CFD results.

    For each CFD case:
      1. Read the velocity field at z=1.5m
      2. Read probe values at bike locations
      3. Generate corresponding layout input
      4. Save as (layout, wind_field, bike_speeds)
    """
    os.makedirs(TRAINING_DATA_DIR, exist_ok=True)

    # For now: use the single toy case
    layout = generate_layout_input(ML_NX, ML_NY)
    np.save(os.path.join(TRAINING_DATA_DIR, "toy_case_layout.npy"), layout)

    print("Layout image saved.")
    print(f"  Shape: {layout.shape} (channels, y, x)")
    print(f"  Buildings: {BUILDINGS}")
    print(f"  Bike locations: {[b[1:3] for b in BIKES]}")

    # When CFD results are available, also save:
    # - wind_field.npy: (2, ny, nx) array of (u, v)
    # - bike_speeds.json: list of {name, cx, cy, U, V, speed}

    return layout


# ── Visualization ────────────────────────────────────────────

def plot_wind_field(layout, wind_u, wind_v, save_path=None):
    """
    Plot the wind field overlaid on the layout.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))

    # Layout
    ax = axes[0]
    ax.imshow(layout[0], origin='lower', cmap='gray_r', alpha=0.7,
              extent=[X0, X0+DOMAIN_LX, Y0, Y0+DOMAIN_LY])
    ax.set_title('Urban Layout (Buildings)')
    ax.set_xlabel('x (m)')
    ax.set_ylabel('y (m)')

    # Wind speed
    ax = axes[1]
    speed = np.sqrt(wind_u**2 + wind_v**2)
    im = ax.imshow(speed, origin='lower', cmap='hot',
                   extent=[X0, X0+DOMAIN_LX, Y0, Y0+DOMAIN_LY])
    plt.colorbar(im, ax=ax, label='Wind Speed (m/s)')
    ax.set_title('Wind Speed at z=1.5m')

    # Layout + wind vectors
    ax = axes[2]
    ax.imshow(layout[0], origin='lower', cmap='gray_r', alpha=0.5,
              extent=[X0, X0+DOMAIN_LX, Y0, Y0+DOMAIN_LY])
    skip = 4
    yi, xi = np.mgrid[0:ML_NY:skip, 0:ML_NX:skip]
    x_phys = xi / ML_NX * DOMAIN_LX + X0
    y_phys = yi / ML_NY * DOMAIN_LY + Y0
    ax.quiver(x_phys, y_phys, wind_u[::skip, ::skip], wind_v[::skip, ::skip],
              scale=30, alpha=0.7)
    ax.set_title('Wind Vectors')

    # Mark bike locations
    for name, cx, cy, lx, ly, h in BIKES:
        for a in axes:
            a.plot(cx, cy, 'bo', markersize=8, markerfacecolor='cyan')
            a.annotate(name, (cx, cy), xytext=(3, 3), textcoords='offset points',
                       color='cyan', fontsize=8)

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f"Plot saved to {save_path}")
    else:
        plt.show()

    plt.close()


# ── Main ─────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("CFD Data Extraction & Training Data Preparation")
    print("=" * 60)

    # Step 1: Create training dataset
    print("\n[1] Creating training dataset from CFD...")
    layout = create_training_dataset()

    # Step 2: Check for CFD results
    probe_dir = os.path.join(CFD_RESULTS_DIR, "postProcessing", "bikeProbes")
    if os.path.exists(probe_dir):
        print(f"\n[2] CFD probe data found at {probe_dir}")
        # Parse probe files
        probe_files = sorted(os.listdir(probe_dir))
        print(f"  Files: {probe_files}")
    else:
        print(f"\n[2] No CFD results yet at {probe_dir}")
        print("  Run the OpenFOAM case first, then re-run this script.")
        print("  Using synthetic data for now (see train_surrogate_model.py).")

    # Step 3: Summary
    print("\n" + "=" * 60)
    print("Data preparation summary:")
    print(f"  Training data dir: {TRAINING_DATA_DIR}")
    print(f"  Grid resolution: {ML_NX} x {ML_NY}")
    print(f"  Number of bike candidates: {len(BIKES)}")
    print(f"  Number of buildings: {len(BUILDINGS)}")
    print("\nTo complete the pipeline:")
    print("  1. Install OpenFOAM in WSL2")
    print("  2. Copy case to WSL: cp -r /mnt/d/Phase2_CFD_ML/cfd_cases/toy_case ~/ ")
    print("  3. Run: cd ~/toy_case && blockMesh && snappyHexMesh -overwrite && simpleFoam")
    print("  4. Re-run this script to extract real CFD data")
    print("  5. Re-run train_surrogate_model.py with real data")
    print("=" * 60)


if __name__ == "__main__":
    main()
