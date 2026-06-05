"""
Visualize snappyHexMesh CFD results.
Reads unstructured polyMesh + U field, computes cell centers,
interpolates to regular grid at z=1.5m.
"""

import numpy as np
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from scipy.interpolate import griddata

CASE = Path("D:/Phase2_CFD_ML/cfd_cases/toy_case")
TIME = "200"
Z_TARGET = 1.5
Z_TOLERANCE = 2.0  # band around target height for cell selection

BUILDINGS = [
    ("Bldg A (30 m)", 35.0, 40.0, 20.0, 15.0),
    ("Bldg B (12 m)", 65.0, 65.0, 15.0, 10.0),
]
BIKES = [("B1", 25, 75), ("B2", 38, 70), ("B3", 55, 78), ("B4", 25, 55), ("B5", 75, 50)]

# ── Read polyMesh ────────────────────────────────────────────

def read_polymesh(basedir):
    """Read polyMesh: points, faces, owner, neighbour. Returns cell centers."""
    pm = basedir / "constant" / "polyMesh"

    # --- Read points ---
    with open(pm / "points", 'r') as f:
        txt = f.read()

    # Find number of points
    lines = [l.split('//')[0].strip() for l in txt.split('\n')]
    n_pts = None
    for l in lines:
        if l == '(': continue
        t = l.replace('(', '').replace(')', '').strip()
        if t and t.replace('-','').replace('.','').isdigit():
            n_pts = int(t)
            break
        # Might be on same line as (
        if '(' in l:
            t = l.split('(')[1].strip()
            if t.replace('-','').replace('.','').isdigit():
                n_pts = int(t)
                break

    # Read point coordinates
    pts = []
    in_data = False
    for l in lines:
        s = l.strip()
        if s.startswith('(') and not in_data:
            in_data = True
            continue
        if s.startswith(')'):
            break
        if in_data and s:
            parts = s.replace('(','').replace(')','').split()
            if len(parts) >= 3:
                try:
                    pts.append([float(parts[0]), float(parts[1]), float(parts[2])])
                except ValueError:
                    pass
    pts = np.array(pts[:n_pts], dtype=np.float64)
    print(f"  Points: {len(pts)}")

    # --- Read faces ---
    with open(pm / "faces", 'r') as f:
        ftxt = f.read()

    flines = [l.split('//')[0].strip() for l in ftxt.split('\n')]
    n_faces = None
    for l in flines:
        t = l.replace('(','').replace(')','').strip()
        if t and t.isdigit():
            n_faces = int(t)
            break

    faces = []
    in_data = False
    for l in flines:
        s = l.strip()
        # Skip header: look for "(" that starts the data section
        if s == '(':
            in_data = True
            continue
        if s == ')':
            break
        if not in_data:
            # This is the count line, or skip
            continue
        if in_data and s:
            # Format: "4(1 162 32281 32120)" or "3(5 10 15)"
            # Replace ) with space then split
            s_clean = s.replace(')', ' ').replace('(', ' ').strip()
            tokens = s_clean.split()
            i = 0
            while i < len(tokens):
                if tokens[i].isdigit():
                    nv = int(tokens[i])
                    i += 1
                    idxs = [int(tokens[j]) for j in range(i, i+nv)]
                    faces.append(idxs)
                    i += nv
                else:
                    i += 1
    faces = faces[:n_faces]
    print(f"  Faces: {len(faces)}")

    # --- Read owner ---
    with open(pm / "owner", 'r') as f:
        otxt = f.read()
    olines = [l.split('//')[0].strip() for l in otxt.split('\n')]
    n_owner = None
    for l in olines:
        t = l.replace('(','').replace(')','').strip()
        if t and t.isdigit():
            n_owner = int(t)
            break
    owner = []
    in_data = False
    for l in olines:
        s = l.strip()
        if s == '(': in_data = True; continue
        if s == ')': break
        if in_data and s:
            for tok in s.split():
                try: owner.append(int(tok))
                except ValueError: pass
    owner = np.array(owner[:n_owner], dtype=np.int32)
    n_cells_owner = int(owner.max()) + 1
    print(f"  Owner faces: {len(owner)}, max cell index: {n_cells_owner-1}")

    # Read neighbour for internal faces
    with open(pm / "neighbour", 'r') as f:
        ntxt = f.read()
    nlines = [l.split('//')[0].strip() for l in ntxt.split('\n')]
    n_neigh = None
    for l in nlines:
        t = l.replace('(','').replace(')','').strip()
        if t and t.isdigit():
            n_neigh = int(t)
            break
    neigh = []
    in_data = False
    for l in nlines:
        s = l.strip()
        if s == '(': in_data = True; continue
        if s == ')': break
        if in_data and s:
            for tok in s.split():
                try: neigh.append(int(tok))
                except ValueError: pass
    neigh = np.array(neigh[:n_neigh], dtype=np.int32)

    # Find number of cells
    n_cells = max(owner.max(), neigh.max() if len(neigh) > 0 else 0) + 1
    if n_pts > 0 and len(faces) > 0:
        # The actual nCells can also be inferred
        pass
    print(f"  Cells (from max index): {n_cells}")

    # --- Compute cell centers ---
    # For each cell, collect face centers of its owning faces
    # Face center = average of vertex positions
    fc = np.zeros((len(faces), 3))
    for i, f in enumerate(faces):
        fc[i] = pts[f].mean(axis=0)

    cc = np.zeros((n_cells, 3))
    cc_count = np.zeros(n_cells, dtype=np.int32)

    # Owner faces (all faces contribute to owner cell)
    for i in range(len(owner)):
        c = owner[i]
        if c < n_cells:
            cc[c] += fc[i]
            cc_count[c] += 1

    # Neighbour faces (only internal faces contribute to neighbour)
    for i in range(len(neigh)):
        c = neigh[i]
        if c < n_cells:
            cc[c] += fc[i]
            cc_count[c] += 1

    # Average
    valid = cc_count > 0
    cc[valid] /= cc_count[valid, np.newaxis]
    cc[~valid] = np.nan

    n_valid = valid.sum()
    print(f"  Cells with valid centers: {n_valid}")

    return cc, valid, n_cells


# ── Read U field ─────────────────────────────────────────────

def read_U_field(filepath, n_cells):
    """Read U vector field. Returns (n_cells, 3) array."""
    with open(filepath, 'r') as f:
        content = f.read()

    lines = content.split('\n')
    n_cells_field = None
    val_start = None
    for i, line in enumerate(lines):
        s = line.split('//')[0].strip()
        if s.startswith('internalField'):
            if 'nonuniform' in s:
                parts = s.split()
                if len(parts) >= 3 and parts[-1].rstrip(';').isdigit():
                    n_cells_field = int(parts[-1].rstrip(';'))
                    val_start = i + 1
                else:
                    n_cells_field = int(lines[i+1].split('//')[0].strip().rstrip(';'))
                    val_start = i + 2
            break

    values = []
    found_open = False
    for i in range(val_start, len(lines)):
        s = lines[i].split('//')[0].strip()
        if s == '(': found_open = True; continue
        if s.startswith(')'):
            break
        if s.startswith('boundaryField'):
            break
        if found_open and s:
            clean = s.replace('(','').replace(')','').strip()
            for tok in clean.split():
                values.append(float(tok))

    print(f"  U field: {n_cells_field} cells, {len(values)} scalar values")

    if n_cells_field is not None:
        arr = np.array(values[:n_cells_field*3], dtype=np.float64).reshape(-1, 3)
    else:
        arr = np.array(values, dtype=np.float64).reshape(-1, 3)

    # Pad if fewer cells than mesh
    if len(arr) < n_cells:
        padding = np.zeros((n_cells - len(arr), 3))
        arr = np.vstack([arr, padding])

    return arr[:n_cells]


# ── Main ─────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Reading unstructured CFD data...")
    print("=" * 60)

    # Read mesh
    cc, cell_valid, n_cells = read_polymesh(CASE)

    # Read U
    U = read_U_field(CASE / TIME / "U", n_cells)

    # Use only valid cells and compute speed
    valid_mask = cell_valid & ~np.isnan(cc[:, 0])
    cc_v = cc[valid_mask]
    U_v = U[valid_mask]
    speed = np.sqrt((U_v ** 2).sum(axis=1))
    print(f"  Valid cells: {len(cc_v)}, speed range: [{speed.min():.2f}, {speed.max():.2f}] m/s")

    # Select cells near z=1.5m
    z_mask = np.abs(cc_v[:, 2] - Z_TARGET) < Z_TOLERANCE
    cc_z = cc_v[z_mask]
    U_z = U_v[z_mask]
    sp_z = np.sqrt((U_z ** 2).sum(axis=1))
    print(f"  Cells near z={Z_TARGET}m (±{Z_TOLERANCE}m): {len(cc_z)}")

    if len(cc_z) == 0:
        print("  No cells found near target height! Trying all z > 0...")
        z_mask = cc_v[:, 2] > 0
        cc_z = cc_v[z_mask]; U_z = U_v[z_mask]; sp_z = np.sqrt((U_z ** 2).sum(axis=1))

    # Interpolate to regular grid
    nx, ny = 200, 200  # high-res interpolation grid
    CROP_X = (5.0, 95.0); CROP_Y = (22.0, 95.0)
    xi = np.linspace(CROP_X[0], CROP_X[1], nx)
    yi = np.linspace(CROP_Y[0], CROP_Y[1], ny)
    Xi, Yi = np.meshgrid(xi, yi)
    print(f"  Interpolating to {nx}x{ny} grid via griddata...")

    sp_grid = griddata((cc_z[:, 0], cc_z[:, 1]), sp_z, (Xi, Yi),
                       method='linear', fill_value=np.nan)
    Ux_grid = griddata((cc_z[:, 0], cc_z[:, 1]), U_z[:, 0], (Xi, Yi),
                       method='linear', fill_value=np.nan)
    Uy_grid = griddata((cc_z[:, 0], cc_z[:, 1]), U_z[:, 1], (Xi, Yi),
                       method='linear', fill_value=np.nan)

    valid_grid = ~np.isnan(sp_grid)
    print(f"  Valid grid points: {valid_grid.sum()}/{nx*ny}")
    print(f"  Grid speed range: [{np.nanmin(sp_grid):.2f}, {np.nanmax(sp_grid):.2f}] m/s")

    # Fill NaN with nearest interpolation
    if valid_grid.sum() < nx*ny:
        from scipy.interpolate import NearestNDInterpolator
        nn_sp = NearestNDInterpolator(cc_z[:, :2], sp_z)
        sp_grid[np.isnan(sp_grid)] = nn_sp(Xi[np.isnan(sp_grid)], Yi[np.isnan(sp_grid)])
        nn_ux = NearestNDInterpolator(cc_z[:, :2], U_z[:, 0])
        Ux_grid[np.isnan(Ux_grid)] = nn_ux(Xi[np.isnan(Ux_grid)], Yi[np.isnan(Ux_grid)])
        nn_uy = NearestNDInterpolator(cc_z[:, :2], U_z[:, 1])
        Uy_grid[np.isnan(Uy_grid)] = nn_uy(Xi[np.isnan(Uy_grid)], Yi[np.isnan(Uy_grid)])

    # Cell edges for pcolormesh
    dx = xi[1]-xi[0]; dy = yi[1]-yi[0]
    xe = np.concatenate([[xi[0]-dx/2], xi+dx/2])
    ye = np.concatenate([[yi[0]-dy/2], yi+dy/2])
    Xe, Ye = np.meshgrid(xe, ye)

    # Bike values
    print("\n" + "="*60)
    print("Bike wind speeds:")
    print("-"*60)
    for nm, bx, by in BIKES:
        ix = np.argmin(np.abs(xi - bx)); iy = np.argmin(np.abs(yi - by))
        s = sp_grid[iy, ix]; u = Ux_grid[iy, ix]; v = Uy_grid[iy, ix]
        level = "HIGH" if s > 4 else "MED" if s > 2 else "LOW"
        print(f"  {nm} ({bx},{by}): |U|={s:.2f} U=({u:+.2f},{v:+.2f}) → {level}")

    # ── Plot ──────────────────────────────────────────────
    plt.rcParams.update({'font.family': 'sans-serif', 'font.size': 10})
    fig, axes = plt.subplots(1, 3, figsize=(22, 7.5))
    ext = [CROP_X[0], CROP_X[1], CROP_Y[0], CROP_Y[1]]

    # [0] Speed
    ax = axes[0]
    vmx = min(np.nanmax(sp_grid), 8.0)
    im = ax.pcolormesh(Xe, Ye, sp_grid, cmap='YlOrRd', shading='flat', vmin=0, vmax=vmx)
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04).set_label('|U| (m/s)', fontsize=12, fontweight='bold')

    for nm, cx, cy, lx, ly in BUILDINGS:
        ax.add_patch(Rectangle((cx-lx/2, cy-ly/2), lx, ly,
                               fc='#2d2d2d', ec='#999', lw=2.5, zorder=3, alpha=0.92))
        ax.text(cx, cy, nm, ha='center', va='center', color='white', fontsize=8, fontweight='bold', zorder=4)

    for nm, bx, by in BIKES:
        ix = np.argmin(np.abs(xi - bx)); iy = np.argmin(np.abs(yi - by))
        s = sp_grid[iy, ix]
        c = '#ff1744' if s > 4 else '#ff9100' if s > 2 else '#00e676'
        ax.plot(bx, by, 'o', color=c, ms=16, mec='white', mew=2.5, zorder=5)
        ax.text(bx, by-3, f'{nm} {s:.1f}', ha='center', va='top', fontsize=7,
                fontweight='bold', color='white', bbox=dict(boxstyle='round', fc=c, alpha=0.9), zorder=6)

    ax.set_title('Wind Speed |U| at z ~ 1.5 m', fontsize=13, fontweight='bold')
    ax.set_aspect('equal'); ax.grid(alpha=0.12, ls='--'); ax.axis(ext)

    # [1] V + streamlines
    ax = axes[1]
    im = ax.pcolormesh(Xe, Ye, Uy_grid, cmap='RdBu_r', shading='flat', vmin=-8, vmax=0)
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04).set_label('V (m/s)', fontsize=12, fontweight='bold')
    st = 4
    ax.streamplot(xi[::st], yi[::st], Ux_grid[::st,::st], Uy_grid[::st,::st],
                  color='black', density=2.0, linewidth=0.55, arrowsize=0.7, arrowstyle='->')
    for nm, cx, cy, lx, ly in BUILDINGS:
        ax.add_patch(Rectangle((cx-lx/2, cy-ly/2), lx, ly,
                               fc='#2d2d2d', ec='#999', lw=2.5, zorder=3, alpha=0.92))
    for nm, bx, by in BIKES:
        ax.plot(bx, by, 'o', color='cyan', ms=12, mec='black', mew=1.5, zorder=5)
    ax.set_title('V + Streamlines', fontsize=13, fontweight='bold')
    ax.set_aspect('equal'); ax.grid(alpha=0.12, ls='--'); ax.axis(ext)

    # [2] Bar chart
    ax = axes[2]
    bi_s = []; bi_n = []
    for nm, bx, by in BIKES:
        ix = np.argmin(np.abs(xi - bx)); iy = np.argmin(np.abs(yi - by))
        bi_s.append(sp_grid[iy, ix]); bi_n.append(nm)
    clr = ['#ff1744' if s>4 else '#ff9100' if s>2 else '#00e676' for s in bi_s]
    bars = ax.bar(range(len(bi_n)), bi_s, color=clr, edgecolor='#333', linewidth=1.5, width=0.55)
    ax.set_xticks(range(len(bi_n))); ax.set_xticklabels(bi_n, fontsize=13, fontweight='bold')
    ax.set_ylabel('|U| (m/s)', fontsize=12, fontweight='bold')
    ax.set_title('Wind Exposure', fontsize=13, fontweight='bold')
    ax.axhline(y=6.0, color='#1565c0', ls='--', lw=1.5, alpha=0.6, label='Inflow 6 m/s')
    ax.set_ylim(0, 8.5); ax.grid(axis='y', alpha=0.2); ax.legend(fontsize=9)
    for bar, s in zip(bars, bi_s):
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.15,
                f'{s:.2f}', ha='center', va='bottom', fontsize=13, fontweight='bold')

    fig.suptitle('Urban Wind Field at Pedestrian Height — CFD k-ε RANS (snappyHexMesh)\n'
                 f'Inflow: 6 m/s from North | 1.92M cells | t={TIME} | Buildings in mesh',
                 fontsize=14, fontweight='bold', y=1.02)
    plt.tight_layout()
    fp = "D:/Phase2_CFD_ML/model_outputs/cfd_snappy_v1.png"
    plt.savefig(fp, dpi=150, bbox_inches='tight', facecolor='white')
    print(f"\nSaved: {fp}")
    plt.close()


if __name__ == "__main__":
    main()
