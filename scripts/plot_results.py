"""
Combined 3-panel figure for urban_block CFD results.
Uses probe data for accurate bike speeds.
"""
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
import os, re

OUTPUT_DIR = "D:/Phase2_CFD_ML/model_outputs"
WIND_REF = 5.0

# ── Load grid data ─────────────────────────────────────────────
GUx = np.load(os.path.join(OUTPUT_DIR, 'urban_block_Ux.npy'))
GUy = np.load(os.path.join(OUTPUT_DIR, 'urban_block_Uy.npy'))
Gspd = np.load(os.path.join(OUTPUT_DIR, 'urban_block_speed.npy'))
N = GUx.shape[0]
gx = np.linspace(-20, 280, N)
gy = np.linspace(-30, 270, N)
GX, GY = np.meshgrid(gx, gy)
dx = gx[1] - gx[0]; dy = gy[1] - gy[0]
xe = np.concatenate([[gx[0]-dx/2], gx+dx/2])
ye = np.concatenate([[gy[0]-dy/2], gy+dy/2])

# ── Parse probe data for accurate bike speeds ──────────────────
PROBE_FILE = os.path.join(OUTPUT_DIR, "postProcessing/bikeProbes/0/U")

def parse_probes(fp):
    with open(fp) as f:
        lines = f.readlines()
    last = None
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'): continue
        last = line
    parts = last.split()
    time = float(parts[0])
    rest = ' '.join(parts[1:])
    vecs = re.findall(r'\(([^)]+)\)', rest)
    speeds, ux_list, uy_list = [], [], []
    for v in vecs:
        vals = [float(x) for x in v.split()]
        ux, uy, uz = vals[0], vals[1], vals[2]
        speeds.append(np.sqrt(ux*ux + uy*uy + uz*uz))
        ux_list.append(ux); uy_list.append(uy)
    return time, np.array(speeds), np.array(ux_list), np.array(uy_list)

time, probe_spd, probe_ux, probe_uy = parse_probes(PROBE_FILE)
print(f"Probe time: {time:.0f}, {len(probe_spd)} stations")

# ── Config ─────────────────────────────────────────────────────
BUILDINGS = [
    ("B1\n25m", 65.0, 65.0, 20.0, 16.0),
    ("B2\n40m", 130.0, 60.0, 14.0, 20.0),
    ("B3\n15m", 185.0, 70.0, 28.0, 12.0),
    ("B4\n8m", 245.0, 65.0, 10.0, 10.0),
    ("B5\n35m", 65.0, 150.0, 20.0, 16.0),
    ("B6\n20m", 175.0, 145.0, 35.0, 20.0),
    ("B7\n12m", 105.0, 220.0, 14.0, 14.0),
    ("B8\n30m", 215.0, 210.0, 22.0, 16.0),
]

BIKES = [
    ("B1", 45.0, 50.0), ("B2", 95.0, 55.0), ("B3", 100.0, 98.0),
    ("B4", 105.0, 145.0), ("B5", 100.0, 185.0), ("B6", 155.0, 80.0),
    ("B7", 155.0, 118.0), ("B8", 200.0, 85.0), ("B9", 225.0, 100.0),
    ("B10", 260.0, 80.0), ("B11", 45.0, 125.0), ("B12", 45.0, 180.0),
    ("B13", 135.0, 195.0), ("B14", 185.0, 185.0), ("B15", 250.0, 185.0),
    ("B16", 85.0, 42.0), ("B17", 155.0, 40.0), ("B18", 215.0, 42.0),
    ("B19", 248.0, 40.0), ("B20", 55.0, 240.0),
]

# Build bike list with probe data
bi = []
for i, (nm, bx, by) in enumerate(BIKES):
    s = probe_spd[i]; u = probe_ux[i]; v = probe_uy[i]
    bi.append((nm, bx, by, s, u, v))
    print(f"  {nm} ({bx:.0f},{by:.0f}) |U|={s:.2f}  U=({u:+.2f},{v:+.2f})")

# ── Plot ───────────────────────────────────────────────────────
plt.rcParams.update({'font.family': 'sans-serif', 'font.size': 9})
fig, axes = plt.subplots(1, 3, figsize=(26, 8.5))
ext = [-15, 285, -35, 275]

# ═══════════════ [0] Wind Speed Colormap ═══════════════
ax = axes[0]
im = ax.pcolormesh(xe, ye, Gspd, cmap='YlOrRd', shading='flat', vmin=0, vmax=WIND_REF+2)
plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04).set_label('|U| (m/s)', fontsize=12,
                                                              fontweight='bold')

for nm, cx, cy, lx, ly in BUILDINGS:
    ax.add_patch(Rectangle((cx-lx/2, cy-ly/2), lx, ly,
                           fc='#2d2d2d', ec='#555', lw=2.5, zorder=3, alpha=0.92))
    ax.text(cx, cy, nm, ha='center', va='center', color='white', fontsize=7.5,
            fontweight='bold', zorder=4)

for nm, bx, by, s, u, v in bi:
    if s > 4:   c = '#ff1744'
    elif s > 2: c = '#ff9100'
    else:       c = '#00e676'
    ax.plot(bx, by, 'o', color=c, ms=16, mec='white', mew=2.5, zorder=5)
    ax.text(bx, by-6, f'{nm}\n{s:.1f}', ha='center', va='top',
           fontsize=6.5, fontweight='bold', color='white',
           bbox=dict(boxstyle='round', fc=c, alpha=0.9), zorder=6)

ax.annotate('Wind 5 m/s ↓', xy=(150, 250), fontsize=10, color='white',
           fontweight='bold', bbox=dict(boxstyle='round', fc='#1565c0', alpha=0.85))
ax.annotate('Canyon\nVenturi', xy=(105, 95), fontsize=8, fontweight='bold',
           color='#c0392b', ha='center',
           bbox=dict(boxstyle='round', fc='white', ec='#c0392b', alpha=0.85))
ax.annotate('Wake\n(40m bldg)', xy=(200, 55), fontsize=8, fontweight='bold',
           color='#1565c0', ha='center',
           bbox=dict(boxstyle='round', fc='white', ec='#1565c0', alpha=0.85))

ax.set_title('Wind Speed at z ≈ 1.5 m', fontsize=13, fontweight='bold')
ax.set_aspect('equal'); ax.grid(alpha=0.1, ls='--'); ax.axis(ext)

# ═══════════════ [1] V-velocity + Streamlines ═══════════════
ax = axes[1]
# V-velocity: use symmetric range around 0 to show recirculation clearly
vmax_abs = max(abs(np.nanmin(GUy)), abs(np.nanmax(GUy)), WIND_REF + 1)
im = ax.pcolormesh(xe, ye, GUy, cmap='RdBu_r', shading='flat', vmin=-vmax_abs, vmax=vmax_abs)
cb = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
cb.set_label('V (m/s, − = south, + = recirc.)', fontsize=11, fontweight='bold')

st = 6
ax.streamplot(GX[::st, ::st], GY[::st, ::st],
             GUx[::st, ::st], GUy[::st, ::st],
             color='black', density=2.5, linewidth=0.55, arrowsize=0.7, arrowstyle='->')

for nm, cx, cy, lx, ly in BUILDINGS:
    ax.add_patch(Rectangle((cx-lx/2, cy-ly/2), lx, ly,
                           fc='#2d2d2d', ec='#555', lw=2.5, zorder=3, alpha=0.92))

for nm, bx, by, s, u, v in bi:
    ax.plot(bx, by, 'o', color='cyan', ms=12, mec='black', mew=1.5, zorder=5)
    ax.annotate(f'{nm} {s:.1f}', (bx+3, by+3), fontsize=6.5, color='cyan',
               fontweight='bold')

ax.annotate('Wake\nRecirc.', xy=(195, 50), fontsize=9, fontweight='bold',
           color='#1565c0', ha='center',
           bbox=dict(boxstyle='round', fc='white', ec='#1565c0', alpha=0.88))
ax.annotate('Canyon\nJet', xy=(95, 110), fontsize=9, fontweight='bold',
           color='#e65100', ha='center',
           bbox=dict(boxstyle='round', fc='white', ec='#e65100', alpha=0.88))
ax.annotate('Corner\nAccel.', xy=(50, 60), fontsize=8, fontweight='bold',
           color='#c0392b', ha='center',
           bbox=dict(boxstyle='round', fc='white', ec='#c0392b', alpha=0.88))

ax.set_title('Along-wind Velocity V + Streamlines', fontsize=13, fontweight='bold')
ax.set_aspect('equal'); ax.grid(alpha=0.1, ls='--'); ax.axis(ext)

# ═══════════════ [2] Bar Chart ═══════════════
ax = axes[2]
nms = [b[0] for b in bi]
sps = [b[3] for b in bi]
clr = ['#ff1744' if s>4 else '#ff9100' if s>2 else '#00e676' for s in sps]

bars = ax.bar(range(len(nms)), sps, color=clr, edgecolor='#333', linewidth=1.2, width=0.55)
ax.set_xticks(range(len(nms)))
ax.set_xticklabels(nms, fontsize=12, fontweight='bold')
ax.set_ylabel('Wind Speed |U| (m/s)', fontsize=12, fontweight='bold')
ax.set_title('Wind Exposure by Station', fontsize=13, fontweight='bold')
ax.axhline(y=WIND_REF, color='#1565c0', ls='--', lw=2, alpha=0.7,
          label=f'Free-stream {WIND_REF} m/s')
ax.set_ylim(0, 7.5)
ax.grid(axis='y', alpha=0.15)
ax.legend(fontsize=9, loc='upper right')

for bar, s in zip(bars, sps):
    ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.1,
           f'{s:.1f}', ha='center', va='bottom', fontsize=11, fontweight='bold')

ax.axhspan(0, 2, alpha=0.06, color='green')
ax.axhspan(2, 4, alpha=0.06, color='orange')
ax.axhspan(4, 8, alpha=0.06, color='red')

mi, mx = min(sps), max(sps)
stats = f'Range: {mi:.1f}–{mx:.1f} m/s\nRatio: {mx/max(mi,0.01):.1f}×'
ax.text(0.02, 0.95, stats, transform=ax.transAxes, fontsize=9, va='top',
       bbox=dict(boxstyle='round', fc='wheat', alpha=0.7))

# ═══════════════ Suptitle & Save ═══════════════
fig.suptitle('Urban Block Wind Field at Pedestrian Height (z ≈ 1.5 m) — CFD k-ε RANS\n'
            f'Inflow: {WIND_REF} m/s from North | 8 Buildings | 2.4M cells | Iteration {time:.0f}',
            fontsize=15, fontweight='bold', y=1.03)

plt.tight_layout()
fp = os.path.join(OUTPUT_DIR, 'urban_block_combined.png')
fig.savefig(fp, dpi=150, bbox_inches='tight', facecolor='white')
print(f"\nSaved: {fp}")
plt.close()
print("Done!")
