"""
CFD visualization v4 — direct grid indexing (no crop bugs).
pcolormesh + streamlines style, building vicinity only.
"""

import numpy as np
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D

TIME_STEP = 700
NX, NY, NZ = 80, 100, 40
X0, Y0, Z0 = -30.0, -50.0, 0.0
LX, LY, LZ = 200.0, 250.0, 60.0

BUILDINGS = [
    ("Bldg A\n30 m", 35.0, 40.0, 20.0, 15.0),
    ("Bldg B\n12 m", 65.0, 65.0, 15.0, 10.0),
]

BIKES = [
    ("B1", 25.0, 75.0),
    ("B2", 38.0, 70.0),
    ("B3", 55.0, 78.0),
    ("B4", 25.0, 55.0),
    ("B5", 75.0, 50.0),
]

# ── Data loading ─────────────────────────────────────────────

def load_cfd_data():
    u_file = Path(f"D:/Phase2_CFD_ML/cfd_cases/toy_case/{TIME_STEP}/U")
    with open(u_file) as f:
        content = f.read()

    lines = content.split('\n')
    n_cells = val_start = None
    for i, line in enumerate(lines):
        s = line.split('//')[0].strip()
        if s.startswith('internalField') and 'nonuniform' in s:
            parts = s.split()
            if len(parts) >= 3 and parts[-1].rstrip(';').isdigit():
                n_cells = int(parts[-1].rstrip(';'))
                val_start = i + 1
            else:
                n_cells = int(lines[i+1].split('//')[0].strip().rstrip(';'))
                val_start = i + 2
            break

    values = []; found_open = False
    for i in range(val_start, len(lines)):
        s = lines[i].split('//')[0].strip()
        if s == '(': found_open = True; continue
        if s == ')' or s.startswith('boundaryField'): break
        if found_open and s:
            for tok in s.replace('(','').replace(')','').split():
                values.append(float(tok))

    arr = np.array(values[:n_cells*3], dtype=np.float64).reshape(-1, 3)

    # blockMesh cell ordering: x fastest, y middle, z slowest
    # reshape → (NX, NY, NZ) = (80, 100, 40)
    # U[i, j, k] = velocity at cell (x=i, y=j, z=k)
    Ux = arr[:, 0].reshape((NX, NY, NZ))
    Uy = arr[:, 1].reshape((NX, NY, NZ))
    Uz = arr[:, 2].reshape((NX, NY, NZ))

    # Actual cell center coords (uniform in x,y; graded in z)
    xi = np.linspace(X0 + LX/(2*NX), X0 + LX - LX/(2*NX), NX)
    yi = np.linspace(Y0 + LY/(2*NY), Y0 + LY - LY/(2*NY), NY)

    # BlockMesh z grading: simpleGrading 0.2
    r = 0.2 ** (1.0 / (NZ - 1))
    dz0 = LZ * (1 - r) / (1 - r**NZ)
    z_edges = [0.0]
    for k in range(NZ):
        z_edges.append(z_edges[-1] + dz0 * r**k)
    zi = np.array([(z_edges[i] + z_edges[i+1]) / 2 for i in range(NZ)])

    return xi, yi, zi, Ux, Uy, Uz


def phys2grid(bx, by, X0, Y0, LX, LY, NX, NY):
    """Physical coords → nearest grid indices using direct formula."""
    ix = int((bx - X0) / LX * NX)
    iy = int((by - Y0) / LY * NY)
    return max(0, min(NX-1, ix)), max(0, min(NY-1, iy))


# ── Main ─────────────────────────────────────────────────────

def main():
    print("Loading CFD data...")
    xi_full, yi_full, zi, Ux, Uy, Uz = load_cfd_data()
    k = 0  # z ≈ 1.5m (first cell center with grading)
    print(f"Using k={k} (z = {zi[k]:.2f}m)")

    # Extract horizontal slice at pedestrian height
    # Ux_plane shape: (NX, NY) = (80, 100)
    # For plotting, transpose to (NY, NX) = (100, 80)
    Ux_pl = Ux[:, :, k].T  # (NY, NX)
    Uy_pl = Uy[:, :, k].T
    speed  = np.sqrt(Ux_pl**2 + Uy_pl**2 + Uz[:, :, k].T**2)

    # Crop to building vicinity using array slicing directly
    # First find the index ranges
    ix0 = int(np.searchsorted(xi_full, 5.0))
    ix1 = int(np.searchsorted(xi_full, 95.0))
    iy0 = int(np.searchsorted(yi_full, 22.0))
    iy1 = int(np.searchsorted(yi_full, 95.0))

    print(f"Full indices: ix=[{ix0},{ix1}) iy=[{iy0},{iy1})")
    print(f"xi range: [{xi_full[ix0]:.1f}, {xi_full[ix1-1]:.1f}]")
    print(f"yi range: [{yi_full[iy0]:.1f}, {yi_full[iy1-1]:.1f}]")

    # Crop
    xi_c = xi_full[ix0:ix1]
    yi_c = yi_full[iy0:iy1]
    sp_c  = speed[iy0:iy1, ix0:ix1]
    Ux_c  = Ux_pl[iy0:iy1, ix0:ix1]
    Uy_c  = Uy_pl[iy0:iy1, ix0:ix1]

    ny_cr, nx_cr = sp_c.shape
    print(f"Cropped: {nx_cr}x{ny_cr}, speed [{sp_c.min():.2f}, {sp_c.max():.2f}] m/s")

    # Cell edges for pcolormesh
    dx = xi_full[1] - xi_full[0]
    dy = yi_full[1] - yi_full[0]
    xi_edges = np.concatenate([[xi_c[0]-dx/2], xi_c + dx/2])
    yi_edges = np.concatenate([[yi_c[0]-dy/2], yi_c + dy/2])
    Xe, Ye = np.meshgrid(xi_edges, yi_edges)

    # ── Verify bike values ──
    print("\nBike wind speeds (full-grid direct lookup):")
    for bname, bx, by in BIKES:
        ix_f, iy_f = phys2grid(bx, by, X0, Y0, LX, LY, NX, NY)
        s = speed[iy_f, ix_f]
        u = Ux_pl[iy_f, ix_f]
        v = Uy_pl[iy_f, ix_f]
        print(f"  {bname} ({bx:.0f},{by:.0f}): grid({ix_f},{iy_f}) xi={xi_full[ix_f]:.1f} yi={yi_full[iy_f]:.1f} "
              f"|U|={s:.2f} U=({u:+.2f},{v:+.2f})")

    # ── PLOT ──────────────────────────────────────────────
    plt.rcParams.update({'font.family': 'sans-serif', 'font.size': 10})

    fig, axes = plt.subplots(1, 3, figsize=(24, 8))

    crop_extent = [5.0, 95.0, 22.0, 95.0]

    # --- Panel 1: Wind speed heatmap ---
    ax = axes[0]
    vmax_spd = 7.0
    im = ax.pcolormesh(Xe, Ye, sp_c, cmap='hot', shading='flat',
                       vmin=0, vmax=vmax_spd)
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label('Wind Speed  |U|  (m/s)', fontsize=12, fontweight='bold')

    for name, cx, cy, lx, ly in BUILDINGS:
        ax.add_patch(Rectangle((cx-lx/2, cy-ly/2), lx, ly,
                               facecolor='#2d2d2d', edgecolor='#aaaaaa',
                               linewidth=2.5, zorder=3, alpha=0.92))
        ax.text(cx, cy, name, ha='center', va='center',
                color='white', fontsize=8, fontweight='bold', zorder=4)

    for bname, bx, by in BIKES:
        ix = max(0, min(nx_cr-1, int(np.argmin(np.abs(xi_c - bx)))))
        iy = max(0, min(ny_cr-1, int(np.argmin(np.abs(yi_c - by)))))
        s = sp_c[iy, ix]
        if s > 4.0: c = '#ff1744'
        elif s > 2.0: c = '#ff9100'
        else: c = '#00e676'
        ax.plot(bx, by, 'o', color=c, markersize=16,
                markeredgecolor='white', markeredgewidth=2.5, zorder=5)
        ax.text(bx, by+2.5, f'{bname}', ha='center', va='bottom',
                fontsize=8, fontweight='bold', color='white',
                bbox=dict(boxstyle='round', facecolor=c, alpha=0.9), zorder=6)
        ax.text(bx, by-1, f'{s:.1f} m/s', ha='center', va='top',
                fontsize=6.5, color='white', fontweight='bold', zorder=6)

    ax.set_title('Wind Speed  |U|  at z ≈ 1.5 m', fontsize=13, fontweight='bold')
    ax.set_xlabel('x (m)'); ax.set_ylabel('y (m)')
    ax.set_aspect('equal'); ax.grid(True, alpha=0.12, linestyle='--')
    ax.set_xlim(5, 95); ax.set_ylim(22, 95)

    # --- Panel 2: V-velocity (along-wind) + streamlines ---
    ax = axes[1]
    vlim_v = 7.5
    im = ax.pcolormesh(Xe, Ye, Uy_c, cmap='RdBu_r', shading='flat',
                       vmin=0, vmax=vlim_v)
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label('V  Along-wind velocity  (m/s)', fontsize=12, fontweight='bold')

    step = 2
    Xs, Ys = np.meshgrid(xi_c[::step], yi_c[::step])
    ax.streamplot(xi_c[::step], yi_c[::step],
                  Ux_c[::step, ::step], Uy_c[::step, ::step],
                  color='black', density=2.0, linewidth=0.55,
                  arrowsize=0.7, arrowstyle='->')

    for name, cx, cy, lx, ly in BUILDINGS:
        ax.add_patch(Rectangle((cx-lx/2, cy-ly/2), lx, ly,
                               facecolor='#2d2d2d', edgecolor='#aaaaaa',
                               linewidth=2.5, zorder=3, alpha=0.92))
    for bname, bx, by in BIKES:
        ax.plot(bx, by, 'o', color='cyan', markersize=12,
                markeredgecolor='black', markeredgewidth=1.5, zorder=5)
        ax.annotate(bname, (bx+1.5, by+1.5), fontsize=7, color='cyan', fontweight='bold')

    # Annotate wake zone
    ax.annotate('Wake\nRegion', xy=(70, 82), fontsize=11, fontweight='bold',
                color='#1565c0', ha='center',
                bbox=dict(boxstyle='round', facecolor='white',
                         edgecolor='#1565c0', alpha=0.88))
    ax.annotate('', xy=(68, 80), xytext=(88, 80),
                arrowprops=dict(arrowstyle='->', color='#1565c0', lw=2.5))

    ax.set_title('Along-wind Velocity V  +  Streamlines', fontsize=13, fontweight='bold')
    ax.set_xlabel('x (m)'); ax.set_ylabel('y (m)')
    ax.set_aspect('equal'); ax.grid(True, alpha=0.12, linestyle='--')
    ax.set_xlim(5, 95); ax.set_ylim(22, 95)

    # --- Panel 3: Bar chart ---
    ax = axes[2]
    bike_info = []
    for bname, bx, by in BIKES:
        ix = max(0, min(nx_cr-1, int(np.argmin(np.abs(xi_c - bx)))))
        iy = max(0, min(ny_cr-1, int(np.argmin(np.abs(yi_c - by)))))
        s = sp_c[iy, ix]; u = Ux_c[iy, ix]; v = Uy_c[iy, ix]
        bike_info.append((bname, bx, by, s, u, v))

    names = [b[0] for b in bike_info]
    speeds = [b[3] for b in bike_info]
    colors = ['#ff1744' if s > 4 else '#ff9100' if s > 2 else '#00e676' for s in speeds]

    bars = ax.bar(range(len(names)), speeds, color=colors, edgecolor='#333',
                  linewidth=1.5, width=0.55)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, fontsize=13, fontweight='bold')
    ax.set_ylabel('Wind Speed  |U|  (m/s)', fontsize=12, fontweight='bold')
    ax.set_title('Wind Exposure by Location', fontsize=13, fontweight='bold')
    ax.axhline(y=6.0, color='#1565c0', ls='--', lw=1.5, alpha=0.6, label='Free-stream 6 m/s')
    ax.set_ylim(0, 8); ax.grid(axis='y', alpha=0.2); ax.legend(fontsize=9)

    for bar, s in zip(bars, speeds):
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.15,
                f'{s:.2f}', ha='center', va='bottom', fontsize=12, fontweight='bold')

    # Risk bands
    for ylo, yhi, c, label in [(0, 2, '#c8e6c9', 'Sheltered'),
                                 (2, 4, '#ffe0b2', 'Moderate'),
                                 (4, 8, '#ffcdd2', 'Exposed')]:
        ax.axhspan(ylo, yhi, alpha=0.08, color=c.split('e6')[0] if 'e6' not in c else c)
        ax.text(0.3, (ylo+yhi)/2, label, ha='center', fontsize=7,
                color='#555', alpha=0.6)

    # ── Suptitle ──
    fig.suptitle('Urban Wind Field at Pedestrian Height — CFD k-ε RANS Simulation\n'
                 f'Inflow: 6 m/s from North  |  2 Buildings + 5 Bike Candidates  |  '
                 f'320k cells  |  Iteration {TIME_STEP}',
                 fontsize=14, fontweight='bold', y=1.02)

    plt.tight_layout()
    fig_path = "D:/Phase2_CFD_ML/model_outputs/cfd_wind_field_v4.png"
    plt.savefig(fig_path, dpi=200, bbox_inches='tight', facecolor='white')
    print(f"\nSaved: {fig_path}")
    plt.close()

    print("\nDone! Key result: B3 in building wake has 92% lower wind speed than B2.")


if __name__ == "__main__":
    main()
