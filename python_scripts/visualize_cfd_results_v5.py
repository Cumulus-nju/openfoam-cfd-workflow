"""
CFD visualization v5 — clean, correct, publication-quality.
"""

import numpy as np
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

# ── Config ───────────────────────────────────────────────────
TIME_STEP = 700
NX, NY, NZ = 80, 100, 40
X0, Y0, Z0, LX, LY, LZ = -30.0, -50.0, 0.0, 200.0, 250.0, 60.0

BUILDINGS = [
    ("Bldg A\n(h=30 m)", 35.0, 40.0, 20.0, 15.0),
    ("Bldg B\n(h=12 m)", 65.0, 65.0, 15.0, 10.0),
]
BIKES = [("B1", 25.0, 75.0), ("B2", 38.0, 70.0), ("B3", 55.0, 78.0),
         ("B4", 25.0, 55.0), ("B5", 75.0, 50.0)]

# ── Load CFD ─────────────────────────────────────────────────

def load():
    uf = Path(f"D:/Phase2_CFD_ML/cfd_cases/toy_case/{TIME_STEP}/U")
    with open(uf) as f:
        content = f.read()
    lines = content.split('\n')
    n_cells = val_start = None
    for i, line in enumerate(lines):
        s = line.split('//')[0].strip()
        if s.startswith('internalField') and 'nonuniform' in s:
            parts = s.split()
            if len(parts) >= 3 and parts[-1].rstrip(';').isdigit():
                n_cells = int(parts[-1].rstrip(';')); val_start = i + 1
            else:
                n_cells = int(lines[i+1].split('//')[0].strip().rstrip(';')); val_start = i + 2
            break
    vals = []; fo = False
    for i in range(val_start, len(lines)):
        s = lines[i].split('//')[0].strip()
        if s == '(': fo = True; continue
        if s == ')' or s.startswith('boundaryField'): break
        if fo and s:
            for tok in s.replace('(', '').replace(')', '').split():
                vals.append(float(tok))
    arr = np.array(vals[:n_cells*3], dtype=np.float64).reshape(-1, 3)

    Ux = arr[:, 0].reshape((NX, NY, NZ))
    Uy = arr[:, 1].reshape((NX, NY, NZ))
    Uz = arr[:, 2].reshape((NX, NY, NZ))

    xi = np.linspace(X0 + LX/(2*NX), X0 + LX - LX/(2*NX), NX)
    yi = np.linspace(Y0 + LY/(2*NY), Y0 + LY - LY/(2*NY), NY)

    r = 0.2 ** (1.0 / (NZ-1))
    dz0 = LZ * (1-r) / (1 - r**NZ)
    ze = [0.0]
    for _ in range(NZ): ze.append(ze[-1] + dz0 * r**_)
    zi = np.array([(ze[i]+ze[i+1])/2 for i in range(NZ)])
    return xi, yi, zi, Ux, Uy, Uz

def ig(bx, by):
    """Physical → full-grid index (clamped)."""
    return max(0, min(NX-1, int((bx-X0)/LX*NX))), max(0, min(NY-1, int((by-Y0)/LY*NY)))

# ── Main ─────────────────────────────────────────────────────

def main():
    print("Loading...")
    xi, yi, zi, Ux, Uy, Uz = load()
    k = 0
    print(f"z = {zi[k]:.2f}m")

    # Full-resolution horizontal slice (NY × NX for plotting)
    Ux2d = Ux[:, :, k].T; Uy2d = Uy[:, :, k].T; Uz2d = Uz[:, :, k].T
    spd  = np.sqrt(Ux2d**2 + Uy2d**2 + Uz2d**2)

    # Crop indices
    xr = (5.0, 95.0); yr = (22.0, 95.0)
    ix0 = np.searchsorted(xi, xr[0]); ix1 = np.searchsorted(xi, xr[1])
    iy0 = np.searchsorted(yi, yr[0]); iy1 = np.searchsorted(yi, yr[1])

    xi_c = xi[ix0:ix1]; yi_c = yi[iy0:iy1]
    sp  = spd[iy0:iy1, ix0:ix1]
    ux  = Ux2d[iy0:iy1, ix0:ix1]
    uy  = Uy2d[iy0:iy1, ix0:ix1]

    dx = xi[1]-xi[0]; dy = yi[1]-yi[0]
    xe = np.concatenate([[xi_c[0]-dx/2], xi_c+dx/2])
    ye = np.concatenate([[yi_c[0]-dy/2], yi_c+dy/2])
    Xe, Ye = np.meshgrid(xe, ye)

    # Bike values (from full grid)
    bi = []
    for nm, bx, by in BIKES:
        ixf, iyf = ig(bx, by)
        s = spd[iyf, ixf]; u = Ux2d[iyf, ixf]; v = Uy2d[iyf, ixf]
        ix_c = ixf - ix0; iy_c = iyf - iy0
        bi.append((nm, bx, by, s, u, v, ix_c, iy_c))
        print(f"  {nm} ({bx:.0f},{by:.0f}) |U|={s:.2f}  U=({u:+.2f},{v:+.2f})")

    # ── Plot ──────────────────────────────────────────────
    plt.rcParams.update({'font.family': 'sans-serif', 'font.size': 10})
    fig, axes = plt.subplots(1, 3, figsize=(22, 7.5))
    ext = [xr[0], xr[1], yr[0], yr[1]]

    # --- [0] Wind speed ---
    ax = axes[0]
    im = ax.pcolormesh(Xe, Ye, sp, cmap='YlOrRd', shading='flat', vmin=0, vmax=7)
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04).set_label('|U| (m/s)', fontsize=12, fontweight='bold')

    for nm, cx, cy, lx, ly in BUILDINGS:
        ax.add_patch(Rectangle((cx-lx/2, cy-ly/2), lx, ly,
                               fc='#2d2d2d', ec='#999', lw=2.5, zorder=3, alpha=0.92))
        ax.text(cx, cy, nm, ha='center', va='center', color='white', fontsize=8, fontweight='bold', zorder=4)

    for nm, bx, by, s, u, v, ixc, iyc in bi:
        c = '#ff1744' if s > 4 else '#ff9100' if s > 2 else '#00e676'
        ax.plot(bx, by, 'o', color=c, ms=16, mec='white', mew=2.5, zorder=5)
        ax.text(bx, by-3, f'{nm}  {s:.1f}', ha='center', va='top',
                fontsize=7, fontweight='bold', color='white',
                bbox=dict(boxstyle='round', fc=c, alpha=0.9), zorder=6)

    ax.annotate('Wind 6 m/s ↓', xy=(12, 90), fontsize=10, color='white', fontweight='bold',
                bbox=dict(boxstyle='round', fc='#1565c0', alpha=0.85))
    ax.set_title('Wind Speed at z ≈ 1.5 m', fontsize=13, fontweight='bold')
    ax.set_aspect('equal'); ax.grid(alpha=0.12, ls='--'); ax.axis(ext)

    # --- [1] V-velocity + streamlines ---
    ax = axes[1]
    im = ax.pcolormesh(Xe, Ye, uy, cmap='RdBu_r', shading='flat', vmin=0, vmax=7.5)
    plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04).set_label('V (m/s)', fontsize=12, fontweight='bold')

    st = 2
    ax.streamplot(xi_c[::st], yi_c[::st], ux[::st,::st], uy[::st,::st],
                  color='black', density=2.0, linewidth=0.55, arrowsize=0.7, arrowstyle='->')

    for nm, cx, cy, lx, ly in BUILDINGS:
        ax.add_patch(Rectangle((cx-lx/2, cy-ly/2), lx, ly,
                               fc='#2d2d2d', ec='#999', lw=2.5, zorder=3, alpha=0.92))
    for nm, bx, by, s, u, v, ixc, iyc in bi:
        ax.plot(bx, by, 'o', color='cyan', ms=12, mec='black', mew=1.5, zorder=5)
        ax.annotate(f'{nm} {s:.1f}', (bx+2, by+2), fontsize=7, color='cyan', fontweight='bold')

    # Wake annotation
    ax.annotate('Wake', xy=(65, 88), fontsize=11, fontweight='bold', color='#1565c0', ha='center',
                bbox=dict(boxstyle='round', fc='white', ec='#1565c0', alpha=0.88))

    # Corner acceleration
    ax.annotate('Corner\nAccel.', xy=(48, 40), fontsize=9, fontweight='bold', color='#e65100', ha='center',
                bbox=dict(boxstyle='round', fc='white', ec='#e65100', alpha=0.88))

    ax.set_title('Along-wind Velocity V + Streamlines', fontsize=13, fontweight='bold')
    ax.set_aspect('equal'); ax.grid(alpha=0.12, ls='--'); ax.axis(ext)

    # --- [2] Bar chart ---
    ax = axes[2]
    nms = [b[0] for b in bi]; sps = [b[3] for b in bi]
    clr = ['#ff1744' if s>4 else '#ff9100' if s>2 else '#00e676' for s in sps]
    bars = ax.bar(range(len(nms)), sps, color=clr, edgecolor='#333', linewidth=1.5, width=0.55)
    ax.set_xticks(range(len(nms))); ax.set_xticklabels(nms, fontsize=13, fontweight='bold')
    ax.set_ylabel('Wind Speed |U| (m/s)', fontsize=12, fontweight='bold')
    ax.set_title('Wind Exposure by Location', fontsize=13, fontweight='bold')
    ax.axhline(y=6.0, color='#1565c0', ls='--', lw=1.5, alpha=0.6, label='Free-stream 6 m/s')
    ax.set_ylim(0, 8.5); ax.grid(axis='y', alpha=0.2); ax.legend(fontsize=9)
    for bar, s in zip(bars, sps):
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.15,
                f'{s:.2f}', ha='center', va='bottom', fontsize=13, fontweight='bold')
    ax.axhspan(0, 2, alpha=0.05, color='green'); ax.axhspan(2, 4, alpha=0.05, color='orange')
    ax.axhspan(4, 8, alpha=0.05, color='red')

    # ── Suptitle & save ──
    fig.suptitle('Urban Wind Field at Pedestrian Height (z ≈ 1.5 m) — CFD k-ε RANS\n'
                 f'Inflow: 6 m/s from North | 320k cells | Iteration {TIME_STEP}',
                 fontsize=14, fontweight='bold', y=1.02)
    plt.tight_layout()
    fp = "D:/Phase2_CFD_ML/model_outputs/cfd_wind_field_v5.png"
    plt.savefig(fp, dpi=150, bbox_inches='tight', facecolor='white')
    print(f"\nSaved: {fp}")
    plt.close()

if __name__ == "__main__":
    main()
