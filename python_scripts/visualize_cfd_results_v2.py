"""
High-quality CFD visualization focused on building vicinity.
Clean design, clear wind speed differences, publication-ready style.
"""

import numpy as np
from pathlib import Path
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyBboxPatch, FancyArrowPatch
from matplotlib.colors import LinearSegmentedColormap
import matplotlib.ticker as ticker

# ── Configuration ────────────────────────────────────────────
TIME_STEP = 700
NX, NY, NZ = 80, 100, 40
X0, Y0, Z0 = -30.0, -50.0, 0.0
LX, LY, LZ = 200.0, 250.0, 60.0
TARGET_HEIGHT = 1.5

# Crop to building vicinity
CROP_X = (5.0, 95.0)   # x range for zoom
CROP_Y = (25.0, 95.0)   # y range for zoom

BUILDINGS = [
    ("Bldg A\n30m", 35.0, 40.0, 20.0, 15.0, 30.0),
    ("Bldg B\n12m", 65.0, 65.0, 15.0, 10.0, 12.0),
]

BIKES = [
    ("B1", 25.0, 75.0),
    ("B2", 38.0, 70.0),
    ("B3", 55.0, 78.0),
    ("B4", 25.0, 55.0),
    ("B5", 75.0, 50.0),
]

WIND_DIR = "N ↑"  # wind from north (+y direction)

# ── Data loading ─────────────────────────────────────────────

def load_cfd_data():
    """Load and reshape CFD velocity field."""
    u_file = Path(f"D:/Phase2_CFD_ML/cfd_cases/toy_case/{TIME_STEP}/U")
    with open(u_file, 'r') as f:
        content = f.read()

    lines = content.split('\n')

    # Parse header
    n_cells = None
    val_start = None
    for i, line in enumerate(lines):
        stripped = line.split('//')[0].strip()
        if stripped.startswith('internalField'):
            if 'nonuniform' in stripped:
                parts = stripped.split()
                if len(parts) >= 3 and parts[-1].rstrip(';').isdigit():
                    n_cells = int(parts[-1].rstrip(';'))
                    val_start = i + 1
                else:
                    n_cells = int(lines[i+1].split('//')[0].strip().rstrip(';'))
                    val_start = i + 2
            break

    # Read values
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
            clean = stripped.replace('(','').replace(')','').strip()
            for tok in clean.split():
                values.append(float(tok))

    n_expected = n_cells * 3
    values = values[:n_expected]
    arr = np.array(values, dtype=np.float64).reshape(-1, 3)

    # Reshape to structured grid (blockMesh ordering: x fastest, then y, then z)
    Ux = arr[:, 0].reshape((NX, NY, NZ))
    Uy = arr[:, 1].reshape((NX, NY, NZ))
    Uz = arr[:, 2].reshape((NX, NY, NZ))

    # Transpose: we want (ny, nx) for imshow
    Ux = Ux.T  # -> (NZ, NY, NX) ... wait
    Uy = Uy.T
    Uz = Uz.T

    # Actually, reshape gave (nx, ny, nz), transpose to (ny, nx, nz)
    Ux = np.transpose(Ux, (1, 0, 2))  # (ny, nx, nz)
    Uy = np.transpose(Uy, (1, 0, 2))
    Uz = np.transpose(Uz, (1, 0, 2))

    # Cell center coordinates
    xi = np.linspace(X0 + LX/(2*NX), X0 + LX - LX/(2*NX), NX)
    yi = np.linspace(Y0 + LY/(2*NY), Y0 + LY - LY/(2*NY), NY)
    zi = np.linspace(Z0 + LZ/(2*NZ), Z0 + LZ - LZ/(2*NZ), NZ)

    return xi, yi, zi, Ux, Uy, Uz


def crop_data(xi, yi, data_2d, crop_x, crop_y):
    """Crop 2D data to specified region."""
    ix0 = np.searchsorted(xi, crop_x[0])
    ix1 = np.searchsorted(xi, crop_x[1])
    iy0 = np.searchsorted(yi, crop_y[0])
    iy1 = np.searchsorted(yi, crop_y[1])
    return xi[ix0:ix1], yi[iy0:iy1], data_2d[iy0:iy1, ix0:ix1]


def phys_to_grid(cx, cy, xi, yi):
    """Convert physical coords to nearest grid indices (clamped to valid range)."""
    ix = np.argmin(np.abs(xi - cx))
    iy = np.argmin(np.abs(yi - cy))
    ix = max(0, min(len(xi) - 1, ix))
    iy = max(0, min(len(yi) - 1, iy))
    return ix, iy


# ── Main visualization ───────────────────────────────────────

def main():
    print("Loading CFD data...")
    xi_full, yi_full, zi, Ux, Uy, Uz = load_cfd_data()

    # Find z-index closest to 1.5m
    k = np.argmin(np.abs(zi - TARGET_HEIGHT))
    print(f"Using z-index {k} (z = {zi[k]:.2f}m)")

    # Extract horizontal slice
    Ux_slice = Ux[:, :, k]
    Uy_slice = Uy[:, :, k]
    Uz_slice = Uz[:, :, k]
    speed = np.sqrt(Ux_slice**2 + Uy_slice**2 + Uz_slice**2)

    # Debug dimensions
    print(f"  speed slice shape: {speed.shape}  (expected ny={NY}, nx={NX})")
    print(f"  xi_full len={len(xi_full)}, yi_full len={len(yi_full)}")
    print(f"  xi_full[0]={xi_full[0]:.2f}, xi_full[-1]={xi_full[-1]:.2f}")
    print(f"  yi_full[0]={yi_full[0]:.2f}, yi_full[-1]={yi_full[-1]:.2f}")

    # Crop
    def crop_data_debug(xi, yi, data_2d, crop_x, crop_y):
        ix0 = np.searchsorted(xi, crop_x[0])
        ix1 = np.searchsorted(xi, crop_x[1])
        iy0 = np.searchsorted(yi, crop_y[0])
        iy1 = np.searchsorted(yi, crop_y[1])
        print(f"    crop: ix0={ix0} ix1={ix1} iy0={iy0} iy1={iy1}")
        print(f"    data shape before crop: {data_2d.shape}")
        result = data_2d[iy0:iy1, ix0:ix1]
        print(f"    data shape after crop: {result.shape}")
        return xi[ix0:ix1], yi[iy0:iy1], result

    xi_c, yi_c, speed_c = crop_data_debug(xi_full, yi_full, speed, CROP_X, CROP_Y)
    _, _, Ux_c = crop_data_debug(xi_full, yi_full, Ux_slice, CROP_X, CROP_Y)
    _, _, Uy_c = crop_data_debug(xi_full, yi_full, Uy_slice, CROP_X, CROP_Y)

    print(f"  Full grid: xi={len(xi_full)} yi={len(yi_full)}")
    print(f"  Cropped grid: xi_c={len(xi_c)} yi_c={len(yi_c)} (y rows, x cols)")
    print(f"  Cropped data shape: speed_c={speed_c.shape}")
    assert len(xi_c) == speed_c.shape[1], f"x mismatch: {len(xi_c)} vs {speed_c.shape[1]}"
    assert len(yi_c) == speed_c.shape[0], f"y mismatch: {len(yi_c)} vs {speed_c.shape[0]}"

    for bname, bx, by in BIKES:
        ix, iy = phys_to_grid(bx, by, xi_c, yi_c)
        print(f"  {bname} ({bx},{by}) -> grid ({ix},{iy}), xi_c[ix]={xi_c[ix]:.1f}, yi_c[iy]={yi_c[iy]:.1f}")

    # Check which bikes are in the cropped region
    safe_bikes = []
    for bname, bx, by in BIKES:
        if CROP_X[0] <= bx <= CROP_X[1] and CROP_Y[0] <= by <= CROP_Y[1]:
            safe_bikes.append((bname, bx, by))
        else:
            print(f"  WARNING: {bname} ({bx},{by}) is OUTSIDE crop region!")
    BIKES_CROPPED = safe_bikes

    # ── Create custom colormap ──
    # White-blue for low wind, yellow-orange-red for high wind
    colors = [
        (0.00, '#1a237e'),   # deep blue - very low
        (0.15, '#0d47a1'),   # blue
        (0.30, '#1565c0'),   # medium blue
        (0.45, '#42a5f5'),   # light blue
        (0.55, '#4fc3f7'),   # cyan
        (0.65, '#ffee58'),   # yellow
        (0.75, '#ff9800'),   # orange
        (0.85, '#e65100'),   # dark orange
        (0.95, '#b71c1c'),   # red
    ]
    cmap = LinearSegmentedColormap.from_list('wind', colors)

    # ── Figure setup ──
    plt.rcParams.update({
        'font.family': 'sans-serif',
        'font.size': 10,
        'axes.titlesize': 13,
        'axes.labelsize': 11,
        'figure.dpi': 200,
    })

    fig = plt.figure(figsize=(20, 10))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.2], wspace=0.15)

    # ── Panel 1: Wind speed heatmap ──────────────────────────
    ax1 = fig.add_subplot(gs[0, 0])

    extent_c = [CROP_X[0], CROP_X[1], CROP_Y[0], CROP_Y[1]]
    vmax = min(speed_c.max(), 7.0)
    im = ax1.imshow(speed_c, origin='lower', extent=extent_c, cmap=cmap,
                    vmin=0, vmax=vmax, aspect='equal', interpolation='bilinear')

    # Colorbar
    cbar = plt.colorbar(im, ax=ax1, fraction=0.046, pad=0.04)
    cbar.set_label('Wind Speed  |V|  (m/s)', fontsize=12, fontweight='bold')
    cbar.ax.tick_params(labelsize=10)

    # Draw buildings with 3D effect
    for name, cx, cy, lx, ly, h in BUILDINGS:
        x0, y0 = cx - lx/2, cy - ly/2
        # Shadow
        ax1.add_patch(Rectangle((x0-0.3, y0-0.3), lx+0.6, ly+0.6,
                                facecolor='#333333', edgecolor='none',
                                alpha=0.3, zorder=2))
        # Main building
        ax1.add_patch(Rectangle((x0, y0), lx, ly,
                                facecolor='#2d2d2d', edgecolor='#555555',
                                linewidth=2.0, zorder=3, alpha=0.92))
        # Label
        ax1.text(cx, cy, name, ha='center', va='center',
                color='white', fontsize=9, fontweight='bold', zorder=4)

    # Draw bike markers
    for bname, bx, by in BIKES:
        # Find speed at this bike
        ix, iy = phys_to_grid(bx, by, xi_c, yi_c)
        bike_speed = speed_c[iy, ix]

        # Color code: green=low risk, yellow=medium, red=high
        if bike_speed > 4.0:
            marker_color = '#ff5252'   # red - high wind risk
            risk_label = 'HIGH'
        elif bike_speed > 2.0:
            marker_color = '#ffab40'   # orange - medium
            risk_label = 'MED'
        else:
            marker_color = '#69f0ae'   # green - low wind (wake zone)
            risk_label = 'LOW'

        # Outer ring
        ax1.plot(bx, by, 'o', color=marker_color, markersize=18,
                markeredgecolor='white', markeredgewidth=2.5, zorder=5)
        # Speed text inside
        ax1.text(bx, by, f'{bike_speed:.1f}', ha='center', va='center',
                color='#1a1a1a' if bike_speed > 2.0 else '#1a1a1a',
                fontsize=7, fontweight='bold', zorder=6)
        # Label above
        ax1.annotate(f'{bname}\n{risk_label}',
                    (bx, by), xytext=(0, 16), textcoords='offset points',
                    ha='center', va='bottom',
                    fontsize=7, fontweight='bold', color=marker_color,
                    bbox=dict(boxstyle='round,pad=0.3', facecolor='white',
                             edgecolor=marker_color, alpha=0.85),
                    zorder=7)

    # Wind direction arrow (top right)
    ax1.annotate('', xy=(CROP_X[0] + 8, CROP_Y[1] - 6),
                xytext=(CROP_X[0] + 8, CROP_Y[1] - 22),
                arrowprops=dict(arrowstyle='->', color='white', lw=3,
                               mutation_scale=25))
    ax1.text(CROP_X[0] + 8, CROP_Y[1] - 2, f'Wind\n{WIND_DIR} 6 m/s',
            ha='center', va='bottom', color='white', fontsize=10,
            fontweight='bold')

    ax1.set_title('Pedestrian-Level Wind Speed at z ≈ 1.5 m', fontsize=15,
                  fontweight='bold', pad=12)
    ax1.set_xlabel('x (m)', fontsize=12)
    ax1.set_ylabel('y (m)', fontsize=12)
    ax1.grid(True, alpha=0.15, linestyle='--')
    ax1.set_xlim(CROP_X[0], CROP_X[1])
    ax1.set_ylim(CROP_Y[0], CROP_Y[1])

    # ── Panel 2: Streamlines + speed ─────────────────────────
    ax2 = fig.add_subplot(gs[0, 1])

    # Speed background
    im2 = ax2.imshow(speed_c, origin='lower', extent=extent_c, cmap=cmap,
                     vmin=0, vmax=vmax, aspect='equal', interpolation='bilinear',
                     alpha=0.85)

    # Streamlines
    Xg, Yg = np.meshgrid(xi_c, yi_c)
    strm = ax2.streamplot(Xg, Yg, Ux_c, Uy_c,
                          density=1.8, linewidth=1.2,
                          color=speed_c.ravel() if speed_c.size == Xg.size else speed_c,
                          cmap='gray', arrowsize=1.2,
                          arrowstyle='->', integration_direction='forward',
                          broken_streamlines=False)

    # Buildings
    for name, cx, cy, lx, ly, h in BUILDINGS:
        x0, y0 = cx - lx/2, cy - ly/2
        ax2.add_patch(Rectangle((x0-0.3, y0-0.3), lx+0.6, ly+0.6,
                                facecolor='#333333', edgecolor='none',
                                alpha=0.3, zorder=2))
        ax2.add_patch(Rectangle((x0, y0), lx, ly,
                                facecolor='#2d2d2d', edgecolor='#555555',
                                linewidth=2.0, zorder=3, alpha=0.92))
        ax2.text(cx, cy, name, ha='center', va='center',
                color='white', fontsize=9, fontweight='bold', zorder=4)

    # Annotate flow features
    # Wake zone behind Bldg B
    ax2.annotate('Wake\nZone', xy=(70, 78), fontsize=10, fontweight='bold',
                color='#1565c0', ha='center', va='center',
                bbox=dict(boxstyle='round', facecolor='white',
                         edgecolor='#1565c0', alpha=0.85))
    ax2.annotate('', xy=(68, 78), xytext=(88, 78),
                arrowprops=dict(arrowstyle='->', color='#1565c0', lw=2))

    # Corner acceleration near Bldg A
    ax2.annotate('Corner\nAccel.', xy=(50, 55), fontsize=9, fontweight='bold',
                color='#e65100', ha='center', va='center',
                bbox=dict(boxstyle='round', facecolor='white',
                         edgecolor='#e65100', alpha=0.85))

    # Bike markers on streamlines
    for bname, bx, by in BIKES:
        ix, iy = phys_to_grid(bx, by, xi_c, yi_c)
        bike_speed = speed_c[iy, ix]

        if bike_speed > 4.0:
            marker_color = '#ff5252'
        elif bike_speed > 2.0:
            marker_color = '#ffab40'
        else:
            marker_color = '#69f0ae'

        ax2.plot(bx, by, 'o', color=marker_color, markersize=16,
                markeredgecolor='white', markeredgewidth=2.5, zorder=5)
        ax2.annotate(f'{bname}\n{bike_speed:.1f} m/s',
                    (bx, by), xytext=(8, 8), textcoords='offset points',
                    fontsize=7, fontweight='bold', color='#1a1a1a',
                    bbox=dict(boxstyle='round,pad=0.3', facecolor='white',
                             edgecolor=marker_color, alpha=0.85),
                    zorder=7)

    ax2.set_title('Wind Streamlines at z ≈ 1.5 m', fontsize=15,
                  fontweight='bold', pad=12)
    ax2.set_xlabel('x (m)', fontsize=12)
    ax2.set_ylabel('y (m)', fontsize=12)
    ax2.grid(True, alpha=0.15, linestyle='--')
    ax2.set_xlim(CROP_X[0], CROP_X[1])
    ax2.set_ylim(CROP_Y[0], CROP_Y[1])

    # ── Suptitle ──
    fig.suptitle('Urban Wind Field at Pedestrian Height — CFD k-ε RANS Simulation\n'
                 f'Inflow: 6 m/s from North  |  Domain: 100 m × 100 m  |  '
                 f'Mesh: 320k cells  |  t = {TIME_STEP} iterations',
                 fontsize=14, fontweight='bold', y=0.99)

    # ── Legend for risk levels ──
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], marker='o', color='w', markerfacecolor='#ff5252',
               markersize=14, label='High Risk  |V| > 4 m/s'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='#ffab40',
               markersize=14, label='Medium Risk 2 < |V| < 4 m/s'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='#69f0ae',
               markersize=14, label='Low Risk  |V| < 2 m/s  (Wake Sheltered)'),
    ]
    fig.legend(handles=legend_elements, loc='lower center', ncol=3,
              fontsize=10, framealpha=0.9)

    # ── Save ──
    fig_path = f"D:/Phase2_CFD_ML/model_outputs/cfd_wind_field_v2.png"
    plt.savefig(fig_path, dpi=200, bbox_inches='tight', facecolor='white')
    print(f"\nSaved to {fig_path}")
    plt.close()

    # ── Also create a bike risk comparison bar chart ─────────
    fig2, ax = plt.subplots(figsize=(10, 5))
    bike_names = []
    bike_speeds = []
    bike_colors = []
    for bname, bx, by in BIKES:
        ix, iy = phys_to_grid(bx, by, xi_c, yi_c)
        s = speed_c[iy, ix]
        bike_names.append(bname)
        bike_speeds.append(s)
        if s > 4.0:
            bike_colors.append('#ff5252')
        elif s > 2.0:
            bike_colors.append('#ffab40')
        else:
            bike_colors.append('#69f0ae')

    bars = ax.bar(range(len(bike_names)), bike_speeds, color=bike_colors,
                  edgecolor='#333333', linewidth=1.5, width=0.55)
    ax.set_xticks(range(len(bike_names)))
    ax.set_xticklabels(bike_names, fontsize=13, fontweight='bold')
    ax.set_ylabel('Wind Speed  |V|  (m/s)', fontsize=13, fontweight='bold')
    ax.set_title('Wind Speed at Candidate Bike Parking Locations', fontsize=15,
                 fontweight='bold')
    ax.axhline(y=6.0, color='#1565c0', linestyle='--', linewidth=1.5, alpha=0.7)
    ax.text(len(bike_names)-0.3, 6.1, 'Free-stream (6 m/s)', fontsize=9,
            color='#1565c0', ha='right', va='bottom')
    ax.set_ylim(0, 8)
    ax.grid(axis='y', alpha=0.2)

    # Value labels on bars
    for bar, speed_val in zip(bars, bike_speeds):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
                f'{speed_val:.2f}', ha='center', va='bottom',
                fontsize=12, fontweight='bold')

    fig2.tight_layout()
    bar_path = f"D:/Phase2_CFD_ML/model_outputs/bike_wind_comparison.png"
    plt.savefig(bar_path, dpi=200, bbox_inches='tight', facecolor='white')
    print(f"Saved to {bar_path}")
    plt.close()

    # ── Print summary ──
    print("\n" + "="*60)
    print("Bike Location Wind Risk Assessment")
    print("="*60)
    for bname, bx, by in BIKES:
        ix, iy = phys_to_grid(bx, by, xi_c, yi_c)
        s = speed_c[iy, ix]
        u = Ux_c[iy, ix]
        v = Uy_c[iy, ix]
        if s > 4.0:
            risk = "HIGH - likely toppling risk"
        elif s > 2.0:
            risk = "MEDIUM - moderate risk"
        else:
            risk = "LOW - sheltered by buildings"
        print(f"  {bname:6s} ({bx:4.0f},{by:4.0f}): "
              f"U={u:+5.2f}  V={v:+5.2f}  |V|={s:5.2f} m/s  → {risk}")

    print(f"\nKey finding: B3 in building wake has {bike_speeds[2]/bike_speeds[1]*100:.0f}% "
          f"lower wind speed than B2 at building corner.")
    print("This demonstrates the core concept: building geometry creates "
          "significant wind speed variations.\nAI surrogate model needs to "
          "learn this geometry→wind mapping.")


if __name__ == "__main__":
    main()
