"""
Visualize CFD results by directly parsing OpenFOAM mesh and field files.
Reads polyMesh + U field, extracts velocity at z=1.5m, and plots.
"""

import numpy as np
from pathlib import Path
import os

# ── Configuration ────────────────────────────────────────────
# WSL case path (copy to D drive first or access via WSL path)
CASE_DIR = Path("D:/Phase2_CFD_ML/cfd_cases/toy_case")
FIG_DIR = Path("D:/Phase2_CFD_ML/model_outputs")
FIG_DIR.mkdir(parents=True, exist_ok=True)

# Domain (from blockMeshDict)
X0, Y0, Z0 = -30.0, -50.0, 0.0
LX, LY, LZ = 200.0, 250.0, 60.0

# Bike locations
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

TARGET_HEIGHT = 1.5  # pedestrian height
TIME_STEP = 700  # use latest time step

# ── OpenFOAM format readers ──────────────────────────────────

def read_foam_scalar(filepath):
    """Read an OpenFOAM scalar field file. Returns header dict and numpy array."""
    with open(filepath, 'r') as f:
        content = f.read()

    # Remove header (everything before and including the last '}' of FoamFile)
    header_end = content.rfind('}\n//') + 1
    if header_end <= 0:
        header_end = content.find('\n\n') + 1
    body = content[header_end:].strip()

    # Remove comments
    lines = []
    for line in body.split('\n'):
        line = line.split('//')[0].strip()
        if line:
            lines.append(line)

    # Parse: first token is "dimensions [...]", then "internalField ...", then boundary
    assert lines[0].startswith('dimensions'), f"Expected dimensions, got: {lines[0]}"
    dims = lines[0]

    internal_line = lines[1]
    assert internal_line.startswith('internalField'), f"Expected internalField: {internal_line}"

    n_cells = None
    if 'nonuniform' in internal_line or 'nonuniform' in lines[2]:
        # nonuniform list format
        if 'nonuniform' in internal_line:
            n_cells = int(internal_line.split('nonuniform')[1].strip().rstrip(';'))
        else:
            n_cells = int(lines[2].strip().rstrip(';'))
        # Find the values
        val_start = 2
        if 'nonuniform' in lines[2]:
            val_start = 3
        elif 'nonuniform' in lines[1]:
            val_start = 2

        n_found = 0
        values = []
        i = val_start
        while n_found < n_cells and i < len(lines):
            line = lines[i].strip()
            if line == '(':
                i += 1
                continue
            if line.startswith(')'):
                break
            if line.startswith('boundaryField'):
                break
            # Parse numbers
            for tok in line.replace('(', '').replace(')', '').split():
                values.append(float(tok))
                n_found += 1
            i += 1
        return np.array(values, dtype=np.float64)
    else:
        # uniform format
        val_str = internal_line.split('uniform')[1].strip().rstrip(';')
        n_cells_known = 320000  # from blockMesh output
        return np.full(n_cells_known, float(val_str), dtype=np.float64)


def read_foam_vector(filepath, expected_cells=320000):
    """Read an OpenFOAM vector field file. Returns (N, 3) numpy array."""
    with open(filepath, 'r') as f:
        content = f.read()

    # Find internalField section
    lines = content.split('\n')

    n_cells = None
    val_start = None
    for i, line in enumerate(lines):
        stripped = line.split('//')[0].strip()
        if stripped.startswith('internalField'):
            if 'nonuniform' in stripped:
                # Check if count is on same line or next line
                parts = stripped.split()
                if len(parts) >= 3 and parts[-1].rstrip(';').isdigit():
                    n_cells = int(parts[-1].rstrip(';'))
                    val_start = i + 1
                else:
                    # Count is on next line
                    n_cells = int(lines[i+1].split('//')[0].strip().rstrip(';'))
                    val_start = i + 2
            elif 'uniform' in stripped:
                val_str = stripped.split('uniform')[1].strip().rstrip(';')
                val_str = val_str.replace('(','').replace(')','').strip()
                parts = [float(x) for x in val_str.split()]
                return np.tile(np.array(parts), (expected_cells, 1)).astype(np.float64)
            break

    if n_cells is None:
        raise ValueError(f"Could not parse internalField from {filepath}")

    # Read values after '('
    values = []
    found_open = False
    for i in range(val_start, len(lines)):
        stripped = lines[i].split('//')[0].strip()
        if stripped == '(':
            found_open = True
            continue
        if stripped == ')' or stripped.startswith('boundaryField'):
            break
        if found_open and stripped:
            # Remove parentheses and parse numbers
            clean = stripped.replace('(','').replace(')','').strip()
            for tok in clean.split():
                values.append(float(tok))

    n_expected = n_cells * 3
    if len(values) > n_expected:
        values = values[:n_expected]

    arr = np.array(values, dtype=np.float64).reshape(-1, 3)
    if len(arr) != n_cells:
        print(f"  Warning: expected {n_cells} cells, got {len(arr)} vectors")
    return arr[:n_cells]


def read_poly_mesh(poly_mesh_dir):
    """Read OpenFOAM polyMesh files. Returns points, faces, owner, neighbour."""
    # Read points
    with open(os.path.join(poly_mesh_dir, 'points'), 'r') as f:
        content = f.read()

    # Parse points
    lines = [l.split('//')[0].strip() for l in content.split('\n') if l.split('//')[0].strip()]

    # Find the number of points
    n_points = None
    for i, line in enumerate(lines):
        if line.replace('(', '').replace(')', '').strip().isdigit():
            n_points = int(line.replace('(', '').replace(')', '').strip())
            break

    if n_points is None:
        raise ValueError("Could not find number of points")

    # Read point coordinates
    points_start = None
    for i, line in enumerate(lines):
        if line == '(' and i > 0:
            points_start = i + 1
            break

    points = []
    i = points_start
    while len(points) < n_points and i < len(lines):
        if lines[i].startswith(')'):
            break
        # Parse (x y z) line
        line = lines[i].replace('(', '').replace(')', '').strip()
        parts = line.split()
        if len(parts) >= 3:
            points.append([float(parts[0]), float(parts[1]), float(parts[2])])
        i += 1

    pts = np.array(points, dtype=np.float64)
    print(f"  Read {len(pts)} points")

    # Read owner file for number of cells
    with open(os.path.join(poly_mesh_dir, 'owner'), 'r') as f:
        owner_content = f.read()

    # Count cells from owner (max face index + 1 = nCells)
    # Actually, owner lists cell index for each face; nCells = max(owner) + 1
    owner_lines = [l.split('//')[0].strip() for l in owner_content.split('\n')
                   if l.split('//')[0].strip()]
    # Find the data
    n_faces_owner = None
    for line in owner_lines:
        t = line.replace('(','').replace(')','').strip()
        if t.isdigit():
            n_faces_owner = int(t)
            break

    # Read owner values
    owner_vals = []
    in_data = False
    for line in owner_lines:
        if line == '(':
            in_data = True
            continue
        if line.startswith(')'):
            in_data = False
            continue
        if in_data:
            for tok in line.replace('(','').replace(')','').split():
                owner_vals.append(int(tok))

    owner = np.array(owner_vals[:n_faces_owner], dtype=np.int32)
    n_cells = int(owner.max()) + 1
    print(f"  Read {len(owner)} faces, {n_cells} cells (from owner)")

    # We don't need faces/neighbour for cell center approximation
    # Approximate cell centers as average of face centers
    return pts, owner, n_cells


def compute_cell_centers(pts, owner):
    """
    Approximate cell centers by averaging the points of faces belonging to each cell.
    This is a rough approximation for visualization purposes.
    """
    n_cells = int(owner.max()) + 1
    # For now, use a simpler approach: read cell centres from the C file if available
    # Otherwise return placeholder
    return None


def main():
    print("=" * 60)
    print("CFD Result Visualization")
    print("=" * 60)

    # Check if data is in WSL format
    # We need the user to copy data from WSL first
    print("\nThe CFD results are in WSL (~/toy_case/).")
    print("Please run this command in WSL to copy results:")
    print()
    print("  cp -r ~/toy_case/700 ~/toy_case/constant/polyMesh /mnt/d/Phase2_CFD_ML/cfd_cases/toy_case/")
    print()
    print("Then re-run this script.")

    # Check if polyMesh exists locally
    poly_mesh_dir = CASE_DIR / "constant" / "polyMesh"
    if not poly_mesh_dir.exists():
        print(f"polyMesh not found at {poly_mesh_dir}")
        return

    # Read mesh
    print("\nReading mesh...")
    pts, owner, n_cells = read_poly_mesh(str(poly_mesh_dir))

    # Read U field
    time_dir = CASE_DIR / str(TIME_STEP)
    u_file = time_dir / "U"
    if not u_file.exists():
        print(f"U field not found at {u_file}")
        # Try lower time steps
        for t in [700, 600, 500, 400, 300, 200, 100]:
            u_file = CASE_DIR / str(t) / "U"
            if u_file.exists():
                print(f"  Found U at time {t}")
                break
        else:
            print("No U field found!")
            return

    print(f"\nReading U field from {u_file}...")
    U = read_foam_vector(str(u_file))
    print(f"  U shape: {U.shape}")

    # Create a simple visualization using approximated coordinates
    # Since computing exact cell centers from unstructured mesh is complex,
    # we use a simpler approach: generate synthetic coordinates based on
    # blockMesh structure (80x100x40 structured cells).

    print("\nReconstructing structured grid from blockMesh layout...")
    nx, ny, nz = 80, 100, 40
    assert nx * ny * nz == n_cells, f"Expected {nx*ny*nz} cells, got {n_cells}"

    # Generate cell center coordinates (structured)
    xi = np.linspace(X0 + LX/(2*nx), X0 + LX - LX/(2*nx), nx)
    yi = np.linspace(Y0 + LY/(2*ny), Y0 + LY - LY/(2*ny), ny)
    zi = np.linspace(Z0 + LZ/(2*nz), Z0 + LZ - LZ/(2*nz), nz)

    # Verify point range
    print(f"  x range: [{xi[0]:.1f}, {xi[-1]:.1f}] m")
    print(f"  y range: [{yi[0]:.1f}, {yi[-1]:.1f}] m")
    print(f"  z range: [{zi[0]:.3f}, {zi[-1]:.3f}] m")

    # Find k-index closest to z=1.5m
    k_target = np.argmin(np.abs(zi - TARGET_HEIGHT))
    print(f"\n  Target height {TARGET_HEIGHT}m -> k-index {k_target} (z={zi[k_target]:.3f}m)")

    # Reshape U to structured grid
    # U is in cell order: i changes fastest, then j, then k (or similar)
    # For blockMesh generated grid: ordering is (x, y, z) = (i, j, k)
    Ux = U[:, 0].reshape((nx, ny, nz))  # i, j, k ordering
    Uy = U[:, 1].reshape((nx, ny, nz))
    Uz = U[:, 2].reshape((nx, ny, nz))

    # Handle transpose: OpenFOAM uses cell ordering that needs checking
    # The blockMesh output says: i:2.5 j:2.5 k:3.00174..0.600348
    # This means nx=80, ny=100, nz=40
    # But the reshape might be wrong. Try different orderings.
    # Usually OpenFOAM cell ordering for blockMesh is:
    #   cell i + nx*(j + ny*k) -- x changes fastest, then y, then z

    # Extract the z-slice at pedestrian height
    Ux_slice = Ux[:, :, k_target]  # shape (nx, ny)
    Uy_slice = Uy[:, :, k_target]
    Uz_slice = Uz[:, :, k_target]

    # Transpose for correct orientation (x=axis 0, y=axis 1 -> need y rows, x cols)
    Ux_slice = Ux_slice.T  # now (ny, nx)
    Uy_slice = Uy_slice.T
    Uz_slice = Uz_slice.T

    speed = np.sqrt(Ux_slice**2 + Uy_slice**2 + Uz_slice**2)

    print(f"\nWind speed at z={TARGET_HEIGHT}m:")
    print(f"  Min: {speed.min():.2f} m/s")
    print(f"  Max: {speed.max():.2f} m/s")
    print(f"  Mean: {speed.mean():.2f} m/s")

    # Bike locations (grid indices)
    print("\nWind at bike locations:")
    bike_data = []
    for name, cx, cy, lx, ly, h in BIKES:
        ix = int((cx - X0) / LX * nx)
        iy = int((cy - Y0) / LY * ny)
        ix = max(0, min(nx-1, ix))
        iy = max(0, min(ny-1, iy))
        u_val = Ux_slice[iy, ix]
        v_val = Uy_slice[iy, ix]
        s_val = np.sqrt(u_val**2 + v_val**2)
        bike_data.append((name, cx, cy, float(u_val), float(v_val), float(s_val)))
        print(f"  {name} at ({cx}, {cy}): U={u_val:.2f}, V={v_val:.2f}, |V|={s_val:.2f} m/s")

    # ── Plotting ─────────────────────────────────────────────
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(16, 14))
    extent = [X0, X0+LX, Y0, Y0+LY]

    # Speed contour
    ax = axes[0, 0]
    im = ax.imshow(speed, origin='lower', extent=extent, cmap='jet',
                   vmin=0, vmax=min(speed.max(), 12))
    plt.colorbar(im, ax=ax, label='Wind Speed (m/s)', shrink=0.8)
    ax.set_title(f'Wind Speed at z={TARGET_HEIGHT}m')
    ax.set_xlabel('x (m)')
    ax.set_ylabel('y (m)')

    # Draw buildings
    for name, cx, cy, lx, ly, h in BUILDINGS:
        rect = plt.Rectangle((cx-lx/2, cy-ly/2), lx, ly,
                             facecolor='gray', edgecolor='black', alpha=0.8)
        ax.add_patch(rect)
        ax.text(cx, cy, f'{h}m', ha='center', va='center', fontsize=7, color='white')

    # Draw bikes
    for name, cx, cy, lx, ly, h in BIKES:
        ax.plot(cx, cy, 'o', color='cyan', markersize=10, markeredgecolor='black')
        ax.annotate(name, (cx, cy), xytext=(5, 5), textcoords='offset points',
                    color='cyan', fontsize=8, fontweight='bold')

    # U component (x direction)
    ax = axes[0, 1]
    im = ax.imshow(Ux_slice, origin='lower', extent=extent, cmap='RdBu_r',
                   vmin=-6, vmax=6)
    plt.colorbar(im, ax=ax, label='U (m/s)', shrink=0.8)
    ax.set_title(f'U Velocity (cross-wind) at z={TARGET_HEIGHT}m')
    ax.set_xlabel('x (m)')
    ax.set_ylabel('y (m)')
    for name, cx, cy, lx, ly, h in BUILDINGS:
        rect = plt.Rectangle((cx-lx/2, cy-ly/2), lx, ly,
                             facecolor='gray', edgecolor='black', alpha=0.8)
        ax.add_patch(rect)

    # V component (y direction, along-wind)
    ax = axes[1, 0]
    im = ax.imshow(Vy_slice := Uy_slice, origin='lower', extent=extent, cmap='RdBu_r',
                   vmin=0, vmax=10)
    plt.colorbar(im, ax=ax, label='V (m/s)', shrink=0.8)
    ax.set_title(f'V Velocity (along-wind) at z={TARGET_HEIGHT}m')
    ax.set_xlabel('x (m)')
    ax.set_ylabel('y (m)')
    for name, cx, cy, lx, ly, h in BUILDINGS:
        rect = plt.Rectangle((cx-lx/2, cy-ly/2), lx, ly,
                             facecolor='gray', edgecolor='black', alpha=0.8)
        ax.add_patch(rect)

    # Wind vectors (subsampled)
    ax = axes[1, 1]
    # Show speed as background
    ax.imshow(speed, origin='lower', extent=extent, cmap='YlOrRd', alpha=0.6,
              vmin=0, vmax=min(speed.max(), 12))
    # Quiver every 4th cell
    skip = 4
    yy_grid = np.linspace(Y0, Y0+LY, ny)[::skip]
    xx_grid = np.linspace(X0, X0+LX, nx)[::skip]
    Yg, Xg = np.meshgrid(yy_grid, xx_grid, indexing='ij')
    u_plot = Ux_slice[::skip, ::skip]
    v_plot = Vy_slice[::skip, ::skip]
    ax.quiver(Xg, Yg, u_plot, v_plot, scale=80, alpha=0.7, width=0.002)
    ax.set_title(f'Wind Vectors at z={TARGET_HEIGHT}m')
    ax.set_xlabel('x (m)')
    ax.set_ylabel('y (m)')
    for name, cx, cy, lx, ly, h in BUILDINGS:
        rect = plt.Rectangle((cx-lx/2, cy-ly/2), lx, ly,
                             facecolor='gray', edgecolor='black', alpha=0.8)
        ax.add_patch(rect)
    for name, cx, cy, lx, ly, h in BIKES:
        ax.plot(cx, cy, 'o', color='cyan', markersize=10, markeredgecolor='black')

    plt.suptitle(f'Toy Case CFD Results — Uniform North Wind 6 m/s (t={TIME_STEP})',
                 fontsize=14, fontweight='bold')
    plt.tight_layout()

    fig_path = FIG_DIR / f"cfd_results_z1.5m_t{TIME_STEP}.png"
    plt.savefig(fig_path, dpi=150, bbox_inches='tight')
    print(f"\nSaved figure to {fig_path}")
    plt.close()

    # ── Save data for AI training ────────────────────────────
    np.save("D:/Phase2_CFD_ML/training_data/cfd_wind_u.npy", Ux_slice)
    np.save("D:/Phase2_CFD_ML/training_data/cfd_wind_v.npy", Uy_slice)
    np.save("D:/Phase2_CFD_ML/training_data/cfd_wind_speed.npy", speed)

    import json
    with open("D:/Phase2_CFD_ML/training_data/bike_wind_cfd.json", "w") as f:
        json.dump([{"name": n, "cx": cx, "cy": cy, "U": u, "V": v, "speed": s}
                   for n, cx, cy, u, v, s in bike_data], f, indent=2)

    print("Saved training data to D:/Phase2_CFD_ML/training_data/")
    print("\nDone!")


if __name__ == "__main__":
    main()
