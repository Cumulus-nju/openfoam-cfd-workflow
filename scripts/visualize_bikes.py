"""
Visualize urban_block CFD results.
- Bar chart: wind speed at 20 bike locations
- Top-down view: colored bike markers on building layout
- Wind field slice at z=1.5m (from full 3D U field)
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib import colormaps
import os
import struct
import re

# ── Configuration ────────────────────────────────────────────
PROBE_FILE = "D:/Phase2_CFD_ML/model_outputs/postProcessing/bikeProbes/0/U"
U_FIELD_DIR = "D:/Phase2_CFD_ML/cfd_cases/urban_block/1500"
POLYMESH_DIR = "D:/Phase2_CFD_ML/cfd_cases/urban_block/constant/polyMesh"
OUTPUT_DIR = "D:/Phase2_CFD_ML/model_outputs"

# Buildings (same as generate script)
BUILDINGS = [
    ("B1", 65.0, 65.0, 20.0, 16.0, 25.0),
    ("B2", 130.0, 60.0, 14.0, 20.0, 40.0),
    ("B3", 185.0, 70.0, 28.0, 12.0, 15.0),
    ("B4", 245.0, 65.0, 10.0, 10.0, 8.0),
    ("B5", 65.0, 150.0, 20.0, 16.0, 35.0),
    ("B6", 175.0, 145.0, 35.0, 20.0, 20.0),
    ("B7", 105.0, 220.0, 14.0, 14.0, 12.0),
    ("B8", 215.0, 210.0, 22.0, 16.0, 30.0),
]

BIKES = [
    ("bike1", 45.0, 50.0), ("bike2", 95.0, 55.0),
    ("bike3", 100.0, 98.0), ("bike4", 105.0, 145.0),
    ("bike5", 100.0, 185.0), ("bike6", 155.0, 80.0),
    ("bike7", 155.0, 118.0), ("bike8", 200.0, 85.0),
    ("bike9", 225.0, 100.0), ("bike10", 260.0, 80.0),
    ("bike11", 45.0, 125.0), ("bike12", 45.0, 180.0),
    ("bike13", 135.0, 195.0), ("bike14", 185.0, 185.0),
    ("bike15", 250.0, 185.0), ("bike16", 85.0, 42.0),
    ("bike17", 155.0, 40.0), ("bike18", 215.0, 42.0),
    ("bike19", 248.0, 40.0), ("bike20", 55.0, 240.0),
]

WIND_SPEED_REF = 5.0  # m/s
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── Parse probe data ─────────────────────────────────────────

def parse_probes(filepath):
    """Parse OpenFOAM probes U file."""
    with open(filepath, 'r') as f:
        lines = f.readlines()

    # Find the last data line
    data_lines = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        data_lines.append(line)

    if not data_lines:
        raise ValueError("No data in probe file")

    last = data_lines[-1]
    # Parse: "time (ux uy uz) (ux uy uz) ..."
    # Split by ") (" to get each vector
    parts = last.split()
    # First element is time
    time = float(parts[0])

    # Remaining is vector data; find all (ux uy uz) triplets
    # Join everything after time and parse with regex
    rest = ' '.join(parts[1:])
    vectors = re.findall(r'\(([^)]+)\)', rest)

    speeds = []
    ux_list, uy_list = [], []
    for v in vectors:
        vals = [float(x) for x in v.split()]
        ux, uy, uz = vals[0], vals[1], vals[2]
        speed = np.sqrt(ux**2 + uy**2 + uz**2)
        ux_list.append(ux)
        uy_list.append(uy)
        speeds.append(speed)

    return time, np.array(speeds), np.array(ux_list), np.array(uy_list)


# ── Read full 3D U field from polyMesh ────────────────────────

def read_polymesh_fields(polymesh_dir, u_dir):
    """Read OpenFOAM polyMesh and U field, return cell centers and velocity."""
    # Read points — parse properly: skip header, read count, then all coords
    with open(os.path.join(polymesh_dir, 'points'), 'r') as f:
        lines = f.readlines()
    # Find the count line (the one with just a large integer before '(')
    n_points = None
    points = []
    in_data = False
    for line in lines:
        line = line.strip()
        if not in_data:
            if line.isdigit() and int(line) > 1000:
                n_points = int(line)
                in_data = True
            continue
        if line == ')' or line.startswith('//'):
            break
        if line == '(':
            continue
        # Remove trailing/leading parens
        line = line.strip('(').strip(')').strip()
        if not line:
            continue
        vals = [float(x) for x in line.split()]
        if len(vals) == 3:
            points.append(vals)
    points = np.array(points)
    print(f"  Read {len(points)} points (expected {n_points})")

    # Read faces
    with open(os.path.join(polymesh_dir, 'faces'), 'r') as f:
        content = f.read()
    # Find the count and the main list
    match = re.search(r'(\d+)\s*\(\s*\n', content)
    if match:
        list_start = match.end()
    else:
        list_start = content.find('\n(\n') + 1

    # Extract everything between the opening ( and closing )
    start_idx = content.find('(', list_start - 5)
    end_idx = content.rfind(')')
    list_content = content[start_idx+1:end_idx]

    # Parse faces: each is N( i1 i2 ... iN )
    faces = []
    for face_match in re.finditer(r'(\d+)\(([^)]+)\)', list_content):
        n_verts = int(face_match.group(1))
        indices = [int(x) for x in face_match.group(2).split()]
        if len(indices) == n_verts:
            faces.append(indices)
    print(f"  Read {len(faces)} faces")

    # Read owner — similar approach
    with open(os.path.join(polymesh_dir, 'owner'), 'r') as f:
        content = f.read()
    start_idx = content.find('(\n') + 1
    end_idx = content.rfind(')')
    owner_content = content[start_idx:end_idx]
    owners = [int(x) for x in re.findall(r'(\d+)', owner_content)]

    # Read neighbour
    with open(os.path.join(polymesh_dir, 'neighbour'), 'r') as f:
        content = f.read()
    start_idx = content.find('(\n') + 1
    end_idx = content.rfind(')')
    neigh_content = content[start_idx:end_idx]
    neighbours = [int(x) for x in re.findall(r'(\d+)', neigh_content)]
    print(f"  Read {len(owners)} owners, {len(neighbours)} neighbours")

    # Determine number of cells
    n_cells = max(max(owners), max(neighbours)) + 1 if neighbours else max(owners) + 1

    # Compute cell centers (average of face centers per cell)
    cell_centers = np.zeros((n_cells, 3))
    face_centers = np.array([np.mean(points[f], axis=0) for f in faces])

    # Accumulate face centers per cell
    cell_face_sum = np.zeros((n_cells, 3))
    cell_face_count = np.zeros(n_cells, dtype=int)

    for i, owner in enumerate(owners):
        cell_face_sum[owner] += face_centers[i]
        cell_face_count[owner] += 1

    for i, neigh in enumerate(neighbours):
        cell_face_sum[neigh] += face_centers[i]
        cell_face_count[neigh] += 1

    for i in range(n_cells):
        if cell_face_count[i] > 0:
            cell_centers[i] = cell_face_sum[i] / cell_face_count[i]

    # Read U field
    u_file = os.path.join(u_dir, 'U')
    with open(u_file, 'r') as f:
        content = f.read()

    # Extract internal field
    match = re.search(r'internalField\s+uniform\s+\(([^)]+)\)', content)
    if match:
        # Uniform field
        vals = [float(x) for x in match.group(1).split()]
        u_field = np.tile(vals, (n_cells, 1))
    else:
        # Non-uniform field
        match = re.search(r'internalField\s+nonuniform\s+List<vector>\s*(\d+)\s*\(', content)
        if match:
            n_entries = int(match.group(1))
            start = match.end()
            vectors_str = content[start:content.find(')', start)]
            u_vecs = re.findall(r'\(([^)]+)\)', vectors_str)
            u_field = np.array([[float(x) for x in v.split()] for v in u_vecs[:n_cells]])
        else:
            raise ValueError("Cannot parse U field format")

    return cell_centers, u_field


# ── Plotting ──────────────────────────────────────────────────

# 1. BAR CHART: Wind speeds at bike locations
print("Creating bar chart...")
time, speeds, ux, uy = parse_probes(PROBE_FILE)

fig, ax = plt.subplots(figsize=(14, 6))
colors = ['#2ecc71' if s > 4.5 else '#f39c12' if s > 2.5 else '#e74c3c'
          for s in speeds]
bars = ax.bar(range(1, 21), speeds, color=colors, edgecolor='white', linewidth=0.5)
ax.axhline(y=WIND_SPEED_REF, color='#3498db', linestyle='--', linewidth=1.5,
           label=f'Free-stream ({WIND_SPEED_REF} m/s)')
ax.set_xlabel('Bike Station #', fontsize=12)
ax.set_ylabel('Wind Speed (m/s)', fontsize=12)
ax.set_title(f'Wind Speed at 20 Bike Stations (t={time:.0f}, z=1.5m)\n'
             'Urban Block CFD - k-ε RANS', fontsize=14, fontweight='bold')
ax.set_xticks(range(1, 21))
ax.set_xticklabels([b[0].replace('bike', 'B') for b in BIKES], rotation=45, ha='right', fontsize=8)
ax.legend(fontsize=10)
ax.grid(axis='y', alpha=0.3)

# Add speed labels on bars
for bar, speed in zip(bars, speeds):
    ax.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 0.05,
            f'{speed:.1f}', ha='center', va='bottom', fontsize=7)

# Stats annotation
stats_text = (f'Max: {speeds.max():.1f} m/s\n'
              f'Min: {speeds.min():.1f} m/s\n'
              f'Mean: {speeds.mean():.1f} m/s\n'
              f'Variation: {speeds.max()/max(speeds.min(), 0.01):.1f}x')
ax.text(0.98, 0.95, stats_text, transform=ax.transAxes, fontsize=9,
        verticalalignment='top', horizontalalignment='right',
        bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

plt.tight_layout()
bar_path = os.path.join(OUTPUT_DIR, 'urban_block_bike_speeds.png')
fig.savefig(bar_path, dpi=150, bbox_inches='tight')
print(f"  Saved: {bar_path}")
plt.close()

# 2. TOP-DOWN: Building layout with colored bike markers
print("Creating top-down map...")
fig, ax = plt.subplots(figsize=(14, 12))

# Draw buildings
for name, cx, cy, lx, ly, h in BUILDINGS:
    x0, y0 = cx - lx/2, cy - ly/2
    rect = mpatches.Rectangle((x0, y0), lx, ly, linewidth=1.5,
                              edgecolor='black', facecolor='#7f8c8d', alpha=0.7)
    ax.add_patch(rect)
    ax.text(cx, cy, f'{name}\n{h}m', ha='center', va='center', fontsize=7,
            color='white', fontweight='bold')

# Draw bike points colored by wind speed
norm = plt.Normalize(vmin=0, vmax=WIND_SPEED_REF + 1)
cmap = colormaps['RdYlGn']
sc = ax.scatter([b[1] for b in BIKES], [b[2] for b in BIKES],
                c=speeds, cmap=cmap, norm=norm, s=120, edgecolors='black',
                linewidth=1, zorder=5)

# Annotate bike points
for i, (name, cx, cy) in enumerate(BIKES):
    ax.annotate(f'{name.replace("bike","B")}\n{speeds[i]:.1f} m/s',
                (cx, cy), textcoords="offset points", xytext=(5, -15),
                fontsize=6, ha='left', color='darkblue')

# Wind arrow
ax.annotate('', xy=(150, 10), xytext=(150, 260),
            arrowprops=dict(arrowstyle='->', lw=3, color='#3498db'))
ax.text(155, 140, f'N Wind\n{WIND_SPEED_REF} m/s', fontsize=11, color='#3498db',
        fontweight='bold')

# Labels
ax.set_xlabel('x (m) - East-West', fontsize=12)
ax.set_ylabel('y (m) - North-South', fontsize=12)
ax.set_title(f'Urban Block CFD: Pedestrian-Level Wind Speed at Bike Stations\n'
             f'(z=1.5m, t={time:.0f}, 8 buildings, 20 stations)',
             fontsize=14, fontweight='bold')

cbar = plt.colorbar(sc, ax=ax, shrink=0.75)
cbar.set_label('Wind Speed (m/s)', fontsize=10)

ax.set_xlim(-25, 285)
ax.set_ylim(-35, 275)
ax.set_aspect('equal')
ax.grid(alpha=0.3)

plt.tight_layout()
map_path = os.path.join(OUTPUT_DIR, 'urban_block_topdown.png')
fig.savefig(map_path, dpi=150, bbox_inches='tight')
print(f"  Saved: {map_path}")
plt.close()

# 3. WIND FIELD EXTRACTION (if polyMesh is available)
print("Extracting wind field at z=1.5m...")
try:
    from scipy.interpolate import griddata

    cell_centers, u_field = read_polymesh_fields(POLYMESH_DIR, U_FIELD_DIR)
    print(f"  Read {len(cell_centers)} cells")

    # Select cells near z=1.5m (±1.0m)
    z_target = 1.5
    z_tol = 2.0
    mask = np.abs(cell_centers[:, 2] - z_target) < z_tol
    cells_xy = cell_centers[mask][:, :2]
    u_at_cells = u_field[mask]

    print(f"  Cells near z=1.5m: {mask.sum()}")

    if mask.sum() > 100:
        speeds_cells = np.sqrt(u_at_cells[:, 0]**2 + u_at_cells[:, 1]**2 + u_at_cells[:, 2]**2)

        # Interpolate to regular grid
        grid_x = np.linspace(-20, 280, 200)
        grid_y = np.linspace(-30, 270, 200)
        grid_X, grid_Y = np.meshgrid(grid_x, grid_y)

        grid_speed = griddata(cells_xy, speeds_cells, (grid_X, grid_Y),
                             method='linear', rescale=True)
        grid_Ux = griddata(cells_xy, u_at_cells[:, 0], (grid_X, grid_Y),
                          method='linear', rescale=True)
        grid_Uy = griddata(cells_xy, u_at_cells[:, 1], (grid_X, grid_Y),
                          method='linear', rescale=True)

        # Save numpy arrays
        np.save(os.path.join(OUTPUT_DIR, 'urban_block_Ux.npy'), grid_Ux)
        np.save(os.path.join(OUTPUT_DIR, 'urban_block_Uy.npy'), grid_Uy)
        np.save(os.path.join(OUTPUT_DIR, 'urban_block_speed.npy'), grid_speed)
        print(f"  Saved .npy arrays (200x200)")

        # Plot wind field colormap
        fig, ax = plt.subplots(figsize=(14, 12))

        im = ax.pcolormesh(grid_X, grid_Y, grid_speed, cmap='RdYlGn',
                          shading='auto', vmin=0, vmax=WIND_SPEED_REF + 1)

        # Overlay buildings
        for name, cx, cy, lx, ly, h in BUILDINGS:
            x0, y0 = cx - lx/2, cy - ly/2
            rect = mpatches.Rectangle((x0, y0), lx, ly, linewidth=1,
                                     edgecolor='black', facecolor='#2c3e50', alpha=0.8)
            ax.add_patch(rect)

        # Overlay bike points
        ax.scatter([b[1] for b in BIKES], [b[2] for b in BIKES],
                  c='white', s=30, edgecolors='black', linewidth=0.5, zorder=5)

        # Wind vectors (subsampled)
        step = 8
        Q = ax.quiver(grid_X[::step, ::step], grid_Y[::step, ::step],
                     grid_Ux[::step, ::step], grid_Uy[::step, ::step],
                     scale=50, width=0.002, alpha=0.5, color='darkblue')

        ax.set_xlim(-25, 285)
        ax.set_ylim(-35, 275)
        ax.set_aspect('equal')
        ax.set_xlabel('x (m)', fontsize=12)
        ax.set_ylabel('y (m)', fontsize=12)
        ax.set_title(f'Urban Block Wind Field at z=1.5m\n'
                    f'k-ε RANS, North Wind {WIND_SPEED_REF} m/s, t={time:.0f}',
                    fontsize=14, fontweight='bold')
        cbar = plt.colorbar(im, ax=ax, shrink=0.75)
        cbar.set_label('Wind Speed (m/s)', fontsize=10)

        plt.tight_layout()
        field_path = os.path.join(OUTPUT_DIR, 'urban_block_wind_field.png')
        fig.savefig(field_path, dpi=150, bbox_inches='tight')
        print(f"  Saved: {field_path}")
        plt.close()

    else:
        print("  WARNING: Not enough cells near z=1.5m for interpolation")

except FileNotFoundError as e:
    print(f"  WARNING: Cannot read polyMesh: {e}")
    print("  Skipping wind field extraction (bar chart + top-down map are ready)")
except Exception as e:
    print(f"  WARNING: Wind field extraction failed: {e}")
    import traceback
    traceback.print_exc()

# ── Summary ───────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Visualization complete!")
print(f"Output directory: {OUTPUT_DIR}")
print(f"Files created:")
for f in os.listdir(OUTPUT_DIR):
    if f.endswith('.png') or f.endswith('.npy'):
        size = os.path.getsize(os.path.join(OUTPUT_DIR, f))
        print(f"  {f} ({size/1024:.0f} KB)")

# Print bike speed summary
print(f"\nBike Wind Speed Summary (z=1.5m, t={time:.0f}):")
print(f"{'Station':<12} {'X(m)':<8} {'Y(m)':<8} {'Speed(m/s)':<12} {'Category'}")
print("-" * 55)
for i, (name, cx, cy) in enumerate(BIKES):
    if speeds[i] > 4.5:
        cat = "Open/Reference"
    elif speeds[i] > 2.5:
        cat = "Moderate"
    else:
        cat = "Wake/Sheltered"
    print(f"{name:<12} {cx:<8.0f} {cy:<8.0f} {speeds[i]:<12.2f} {cat}")

print(f"\nMax speed: {speeds.max():.2f} m/s (Bike {np.argmax(speeds)+1})")
print(f"Min speed: {speeds.min():.2f} m/s (Bike {np.argmin(speeds)+1})")
print(f"Speed ratio: {speeds.max()/max(speeds.min(), 0.01):.1f}x")
