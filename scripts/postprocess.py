"""
UrbanWind CFD Post-Processor v2 — per-case output + ML-ready data export.
Usage: python postprocess_v2.py <case_dir> [time]

Output per case (under model_outputs/<case_name>/):
  wind_field_1.5m.npz    — gridded fields (GX, GY, Ux, Uy, Uz, speed) for ML
  cell_data_1.5m.csv     — raw cell-center data at z~1.5m
  buildings.json          — building footprints + properties
  case_info.json          — case metadata
  <case_name>_combined.png — 3-panel visualization
"""
import re, os, sys, math, json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
from scipy.interpolate import griddata

CASE = sys.argv[1] if len(sys.argv) > 1 else r"E:\UrbanWind\cfd_cases\my_campus"
TIME = sys.argv[2] if len(sys.argv) > 2 else "300"
CASE_NAME = os.path.basename(CASE)

# Per-case output folder
BASE_OUT = os.path.join(os.path.dirname(CASE), "..", "model_outputs")
CASE_OUT = os.path.join(BASE_OUT, CASE_NAME)
os.makedirs(CASE_OUT, exist_ok=True)

print(f"Case: {CASE}")
print(f"Time: {TIME}")
print(f"Output: {CASE_OUT}")

# ── Parse mesh ──
print("Parsing mesh...")
with open(os.path.join(CASE, "constant", "polyMesh", "points")) as f:
    lines = f.readlines()
points = []
in_data = False
for line in lines:
    line = line.strip()
    if not in_data:
        if line.isdigit() and int(line) > 100:
            in_data = True
        continue
    if line.startswith(')') or line.startswith('//'): break
    if line == '(': continue
    line = line.strip('(').strip(')')
    if not line: continue
    vals = line.split()
    if len(vals) == 3:
        points.append((float(vals[0]), float(vals[1]), float(vals[2])))
print(f"  Points: {len(points):,}")

def parse_int_list(fp):
    with open(fp) as f:
        c = f.read()
    s = c.find('(\n')
    if s < 0: s = c.find('(')
    e = c.rfind(')')
    return [int(x) for x in re.findall(r'\d+', c[s+1:e])]

owners = parse_int_list(os.path.join(CASE, "constant", "polyMesh", "owner"))
neighbours = parse_int_list(os.path.join(CASE, "constant", "polyMesh", "neighbour"))
n_cells = max(max(owners), max(neighbours)) + 1
print(f"  Cells: {n_cells:,}")

# ── Parse U field ──
print("Reading U field...")
with open(os.path.join(CASE, TIME, "U")) as f:
    content = f.read()
if_pos = content.find('internalField')
after_if = content[if_pos:]
U = []
if 'nonuniform' in after_if[:100]:
    m = re.search(r'(\d+)\s*\(', after_if)
    n = int(m.group(1))
    paren_pos = after_if.find('(')
    start = if_pos + paren_pos + 1
    depth = 1; end = start
    while end < len(content) and depth > 0:
        if content[end] == '(': depth += 1
        elif content[end] == ')': depth -= 1
        end += 1
    inner = content[start:end-1]
    vecs = re.findall(r'\(([^)]+)\)', inner)
    for v in vecs[:n]:
        vals = [float(x) for x in v.split()]
        if len(vals) == 3: U.append((vals[0], vals[1], vals[2]))
else:
    m = re.search(r'uniform\s*\(([^)]+)\)', after_if)
    vals = [float(x) for x in m.group(1).split()]
    U = [tuple(vals)] * n_cells
print(f"  U entries: {len(U):,}")

# ── Compute cell centers ──
print("Computing cell centers...")
with open(os.path.join(CASE, "constant", "polyMesh", "faces")) as f:
    fcontent = f.read()
fstart = fcontent.find('(\n')
if fstart < 0: fstart = fcontent.find('(')
fend = fcontent.rfind(')')
finner = fcontent[fstart+1:fend]
cell_sum = np.zeros((n_cells, 3))
cell_cnt = np.zeros(n_cells, dtype=int)
face_idx = 0
for m in re.finditer(r'(\d+)\(([^)]+)\)', finner):
    indices = [int(x) for x in m.group(2).split()]
    if not indices: continue
    sx = sy = sz = 0.0
    for vi in indices:
        if vi < len(points):
            p = points[vi]; sx += p[0]; sy += p[1]; sz += p[2]
    nv = len(indices)
    fx, fy, fz = sx/nv, sy/nv, sz/nv
    if face_idx < len(owners):
        o = owners[face_idx]
        if o < n_cells: cell_sum[o] += (fx, fy, fz); cell_cnt[o] += 1
    if face_idx < len(neighbours):
        nb = neighbours[face_idx]
        if nb < n_cells: cell_sum[nb] += (fx, fy, fz); cell_cnt[nb] += 1
    face_idx += 1
    if face_idx % 1000000 == 0: print(f"  {face_idx//1000000}M faces...")
print(f"  {face_idx:,} faces processed")

# ── Filter z~1.5m ──
print("Extracting z~1.5m layer...")
csv_path = os.path.join(CASE_OUT, "cell_data_1.5m.csv")
data = []
with open(csv_path, 'w') as f:
    f.write("x,y,z,Ux,Uy,Uz,speed\n")
    for i in range(n_cells):
        if cell_cnt[i] == 0: continue
        cz = cell_sum[i][2] / cell_cnt[i]
        if abs(cz - 1.5) > 2.5: continue
        cx = cell_sum[i][0] / cell_cnt[i]
        cy = cell_sum[i][1] / cell_cnt[i]
        ux, uy, uz = U[i] if i < len(U) else (0, 0, 0)
        spd = math.sqrt(ux*ux + uy*uy + uz*uz)
        f.write(f"{cx:.6f},{cy:.6f},{cz:.6f},{ux:.6f},{uy:.6f},{uz:.6f},{spd:.6f}\n")
        data.append((cx, cy, cz, ux, uy, uz, spd))
print(f"  Written {len(data):,} points to {csv_path}")

if len(data) < 10:
    print("ERROR: Not enough points extracted!")
    sys.exit(1)

data = np.array(data, dtype=np.float32)

# ── Grid domain from CFD data only (buildings drawn on top) ──
data_x_min = float(data[:,0].min())
data_x_max = float(data[:,0].max())
data_y_min = float(data[:,1].min())
data_y_max = float(data[:,1].max())
margin = 15.0  # meters
gx_min = data_x_min - margin
gx_max = data_x_max + margin
gy_min = data_y_min - margin
gy_max = data_y_max + margin

# ── Load building outlines ──
geojson_path = os.path.join(CASE, "site_plan.geojson")
buildings = []
buildings_meta = []
if os.path.exists(geojson_path):
    with open(geojson_path, encoding='utf-8') as f:
        gj = json.load(f)
    raw_polys = []
    for feat in gj.get("features", []):
        cat = feat.get("category", feat.get("properties", {}).get("category", "unknown"))
        if cat != "building":
            continue
        coords = feat["geometry"]["coordinates"]
        if coords and coords[0]:
            poly = [(p[0], p[1]) for p in coords[0]]
            raw_polys.append(poly)
            props = feat.get("properties", {})
            buildings_meta.append({
                "category": cat,
                "height": props.get("height", props.get("building:height", None)),
                "levels": props.get("levels", props.get("building:levels", None)),
                "name": props.get("name", ""),
                "polygon_local": None
            })

    if raw_polys:
        sample_x = raw_polys[0][0][0]
        is_geo = abs(sample_x) > 100

        if is_geo:
            # Use SAME reference point as the case generator (center_lat/lon from metadata).
            # The case generator projects WGS84→meters using this ref point to place STL
            # files in the CFD domain. We MUST use the same ref so building polygons
            # align exactly with their STL positions in the CFD mesh.
            meta = gj.get("metadata", {})
            ref_lon = meta.get("center_lon")
            ref_lat = meta.get("center_lat")
            if not ref_lat or not ref_lon:
                # Fallback: no metadata (older cases) — use median
                all_lon = sorted([p[0] for poly in raw_polys for p in poly])
                all_lat = sorted([p[1] for poly in raw_polys for p in poly])
                ref_lon = all_lon[len(all_lon)//2]
                ref_lat = all_lat[len(all_lat)//2]
                print(f"  Warning: no center_lat/lon in metadata, using median fallback")

            lat_rad = math.radians(ref_lat)
            m_per_deg_lon = 111320.0 * math.cos(lat_rad)
            m_per_deg_lat = 111320.0

            # Convert to local meters — these are now CFD domain coordinates
            for poly in raw_polys:
                local_poly = [((p[0] - ref_lon) * m_per_deg_lon,
                               (p[1] - ref_lat) * m_per_deg_lat) for p in poly]
                buildings.append(local_poly)
                # Clone fresh for buildings_meta to avoid shared references
                if len(buildings) <= len(buildings_meta):
                    buildings_meta[len(buildings) - 1]["polygon_local"] = local_poly

            # Filter outliers (buildings far from CFD domain — cross-city contamination)
            data_cx = (data_x_min + data_x_max) / 2
            data_cy = (data_y_min + data_y_max) / 2
            data_extent = max(data_x_max - data_x_min, data_y_max - data_y_min)
            keep_idx = []
            for i, poly in enumerate(buildings):
                cx = sum(p[0] for p in poly) / len(poly)
                cy = sum(p[1] for p in poly) / len(poly)
                if math.sqrt((cx - data_cx)**2 + (cy - data_cy)**2) < data_extent * 1.5:
                    keep_idx.append(i)

            n_filtered = len(buildings) - len(keep_idx)
            buildings = [buildings[i] for i in keep_idx]
            buildings_meta = [buildings_meta[i] for i in keep_idx if i < len(buildings_meta)]
            if n_filtered > 0:
                print(f"  Filtered {n_filtered} outlier building(s) far from domain")
            print(f"  Loaded {len(buildings)} buildings (ref: {ref_lat:.4f},{ref_lon:.4f})")
        else:
            buildings = raw_polys
            for pi, poly in enumerate(raw_polys):
                if pi < len(buildings_meta):
                    buildings_meta[pi]["polygon_local"] = poly
            print(f"  Loaded {len(buildings)} building outlines")

# ── Grid interpolation ──
print("Interpolating...")
GRID_SIZE = 250
gx = np.linspace(gx_min, gx_max, GRID_SIZE)
gy = np.linspace(gy_min, gy_max, GRID_SIZE)
GX, GY = np.meshgrid(gx, gy)

Gspd = griddata(data[:,:2], data[:,6], (GX, GY), method='linear', fill_value=np.nan)
GUx  = griddata(data[:,:2], data[:,3], (GX, GY), method='linear', fill_value=np.nan)
GUy  = griddata(data[:,:2], data[:,4], (GX, GY), method='linear', fill_value=np.nan)
GUz  = griddata(data[:,:2], data[:,5], (GX, GY), method='linear', fill_value=np.nan)

# ── Save ML-ready data ──
print("Saving ML data...")
npz_path = os.path.join(CASE_OUT, "wind_field_1.5m.npz")
np.savez_compressed(npz_path,
    GX=GX, GY=GY,
    Ux=GUx, Uy=GUy, Uz=GUz,
    speed=Gspd,
    grid_x=gx, grid_y=gy,
    x_range=(gx_min, gx_max),
    y_range=(gy_min, gy_max),
    grid_size=GRID_SIZE,
    z_level=1.5)
print(f"  Saved: {npz_path}")

# ── Save building metadata ──
bld_json_path = os.path.join(CASE_OUT, "buildings.json")
for bi in buildings_meta:
    if bi["polygon_local"]:
        xs = [p[0] for p in bi["polygon_local"]]
        ys = [p[1] for p in bi["polygon_local"]]
        bi["bbox_local"] = {"x_min": min(xs), "x_max": max(xs),
                            "y_min": min(ys), "y_max": max(ys)}
        bi["area_m2"] = (max(xs) - min(xs)) * (max(ys) - min(ys))
        bi["centroid"] = [(min(xs) + max(xs))/2, (min(ys) + max(ys))/2]
with open(bld_json_path, 'w', encoding='utf-8') as f:
    json.dump({
        "n_buildings": len([b for b in buildings_meta if b["polygon_local"]]),
        "n_filtered": len(buildings_meta) - len([b for b in buildings_meta if b["polygon_local"]]),
        "buildings": buildings_meta
    }, f, ensure_ascii=False, indent=2)
print(f"  Saved: {bld_json_path}")

# ── Save case info ──
info_path = os.path.join(CASE_OUT, "case_info.json")
spd = data[:,6]
info = {
    "case_name": CASE_NAME,
    "time": TIME,
    "n_cells": n_cells,
    "n_points_mesh": len(points),
    "n_data_points_1_5m": len(data),
    "grid_size": GRID_SIZE,
    "z_level": 1.5,
    "wind_speed": {
        "min": float(spd.min()), "max": float(spd.max()),
        "mean": float(np.mean(spd)), "std": float(np.std(spd)),
        "median": float(np.median(spd)),
        "p5": float(np.percentile(spd, 5)),
        "p95": float(np.percentile(spd, 95)),
    },
    "domain": {
        "x_range": [float(data_x_min), float(data_x_max)],
        "y_range": [float(data_y_min), float(data_y_max)],
        "area_m2": float((data_x_max - data_x_min) * (data_y_max - data_y_min)),
    },
    "n_buildings": len(buildings),
}
with open(info_path, 'w', encoding='utf-8') as f:
    json.dump(info, f, ensure_ascii=False, indent=2)
print(f"  Saved: {info_path}")

# ── Save individual .npy files ──
npy_dir = os.path.join(CASE_OUT, "npy")
os.makedirs(npy_dir, exist_ok=True)
np.save(os.path.join(npy_dir, "GX.npy"), GX)
np.save(os.path.join(npy_dir, "GY.npy"), GY)
np.save(os.path.join(npy_dir, "Ux.npy"), GUx)
np.save(os.path.join(npy_dir, "Uy.npy"), GUy)
np.save(os.path.join(npy_dir, "Uz.npy"), GUz)
np.save(os.path.join(npy_dir, "speed.npy"), Gspd)
np.save(os.path.join(npy_dir, "grid_x.npy"), gx)
np.save(os.path.join(npy_dir, "grid_y.npy"), gy)
print(f"  Saved .npy files to {npy_dir}")

# ── Plot ──
print("Plotting...")
plt.rcParams.update({'font.family': 'sans-serif', 'font.sans-serif': ['Noto Sans SC', 'DejaVu Sans'], 'font.size': 9})
fig, axes = plt.subplots(1, 3, figsize=(26, 8.5))

dx_p, dy_p = gx[1]-gx[0], gy[1]-gy[0]
xe = np.concatenate([[gx[0]-dx_p/2], gx+dx_p/2])
ye = np.concatenate([[gy[0]-dy_p/2], gy+dy_p/2])
ext = [gx_min - dx_p, gx_max + dx_p, gy_min - dy_p, gy_max + dy_p]

# [0] Wind speed
ax = axes[0]
vmax_spd = max(np.nanmax(Gspd) if not np.all(np.isnan(Gspd)) else 1.5, 1.5)
im = ax.pcolormesh(xe, ye, Gspd, cmap='YlOrRd', shading='flat', vmin=0, vmax=vmax_spd)
plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04).set_label('|U| (m/s)', fontsize=12, fontweight='bold')
for poly in buildings[:200]:
    if len(poly) >= 3:
        ax.add_patch(Polygon(poly, fc='#1a1a2e', ec='#06B6D4', lw=0.8, zorder=5, alpha=0.9))
mean_spd = np.nanmean(Gspd) if not np.all(np.isnan(Gspd)) else 0
ax.set_title(f'Wind Speed at z ~ 1.5 m  |  Mean: {mean_spd:.1f} m/s', fontsize=13, fontweight='bold')
ax.set_aspect('equal'); ax.grid(alpha=0.1, ls='--'); ax.axis(ext)

# [1] V-velocity + streamlines
ax = axes[1]
vmax_v = max(abs(np.nanmin(GUy)) if not np.all(np.isnan(GUy)) else 6,
             abs(np.nanmax(GUy)) if not np.all(np.isnan(GUy)) else 6, 6)
im = ax.pcolormesh(xe, ye, GUy, cmap='RdBu_r', shading='flat', vmin=-vmax_v, vmax=vmax_v)
plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04).set_label('V (m/s)', fontsize=11, fontweight='bold')
st = max(1, int(GRID_SIZE/60))
# Streamplot only if we have valid data
if not np.all(np.isnan(GUx)):
    ax.streamplot(GX[::st,::st], GY[::st,::st], GUx[::st,::st], GUy[::st,::st],
                 color='black', density=1.5, linewidth=0.4, arrowsize=0.5)
for poly in buildings[:200]:
    if len(poly) >= 3:
        ax.add_patch(Polygon(poly, fc='#1a1a2e', ec='#06B6D4', lw=0.8, zorder=5, alpha=0.9))
ax.set_title('V-Velocity + Streamlines', fontsize=13, fontweight='bold')
ax.set_aspect('equal'); ax.grid(alpha=0.1, ls='--'); ax.axis(ext)

# [2] Histogram
ax = axes[2]
spd_clipped = np.clip(spd, 0, np.percentile(spd, 99))
ax.hist(spd_clipped, bins=40, color='#06B6D4', edgecolor='white', alpha=0.8)
ax.axvline(x=5.0, color='#ef4444', ls='--', lw=2, label='Free-stream 5 m/s')
ax.axvline(x=np.mean(spd), color='#f59e0b', ls='-', lw=1.5, label=f'Mean {np.mean(spd):.1f} m/s')
ax.axvline(x=np.median(spd), color='#10b981', ls=':', lw=1.5, label=f'Median {np.median(spd):.1f} m/s')
ax.set_xlabel('Wind Speed |U| (m/s)', fontsize=11)
ax.set_ylabel('Cell Count', fontsize=11)
ax.set_title(f'Speed Distribution at z~1.5m  |  std={np.std(spd):.2f}', fontsize=13, fontweight='bold')
ax.legend(fontsize=9); ax.grid(alpha=0.15, axis='y')

fig.suptitle(f'UrbanWind CFD - {CASE_NAME} | {n_cells:,} cells, k-e RANS\n'
             f'Inflow: 5 m/s North | {len(buildings)} Buildings | Grid: {GRID_SIZE}x{GRID_SIZE}',
            fontsize=15, fontweight='bold', y=1.03)
plt.tight_layout()
png_path = os.path.join(CASE_OUT, f"{CASE_NAME}_combined.png")
fig.savefig(png_path, dpi=150, bbox_inches='tight', facecolor='white')
plt.close()
print(f"\nSaved: {png_path}")
print(f"Wind range: {spd.min():.1f} - {spd.max():.1f} m/s, Mean: {np.mean(spd):.1f}, Median: {np.median(spd):.1f}")

# ── Summary ──
print(f"\n{'='*60}")
print(f"Case: {CASE_NAME}  ->  {CASE_OUT}")
print(f"  cell_data_1.5m.csv      - {len(data):,} raw data points")
print(f"  wind_field_1.5m.npz     - {GRID_SIZE}x{GRID_SIZE} gridded fields (ML-ready)")
print(f"  npy/*.npy               - individual field arrays")
print(f"  buildings.json           - {len(buildings)} building footprints + properties")
print(f"  case_info.json           - case metadata + statistics")
print(f"  {CASE_NAME}_combined.png - 3-panel visualization")
print(f"{'='*60}")
print("Done!")
