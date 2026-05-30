#!/usr/bin/env python3
"""Extract raw point cloud at z~1.5m. Pure Python, no numpy needed."""
import re, os, sys, math

CASE_DIR = "/home/yunhang/urban_block"
TIME = "1500"
BATCH = 20000  # Process in batches to save memory

def parse_points(filepath):
    with open(filepath) as f:
        lines = f.readlines()
    points = []
    in_data = False
    for line in lines:
        line = line.strip()
        if not in_data:
            if line.isdigit() and int(line) > 1000:
                in_data = True
            continue
        if line.startswith(')') or line.startswith('//'):
            break
        if line == '(': continue
        line = line.strip('(').strip(')')
        if not line: continue
        vals = line.split()
        if len(vals) == 3:
            points.append((float(vals[0]), float(vals[1]), float(vals[2])))
    return points

def parse_int_list(filepath):
    with open(filepath) as f:
        content = f.read()
    start = content.find('(\n')
    if start < 0: start = content.find('(')
    end = content.rfind(')')
    inner = content[start+1:end]
    return [int(x) for x in re.findall(r'\d+', inner)]

def parse_U(filepath, expected):
    with open(filepath) as f:
        content = f.read()

    # Find internalField section: search for 'internalField' keyword
    if_pos = content.find('internalField')
    if if_pos < 0:
        return [(0,0,0)] * expected

    # Get the line + following text
    after_if = content[if_pos:]

    # Check if uniform or nonuniform
    if 'nonuniform' in after_if[:100]:
        # Find the count (the large integer before the opening paren)
        m = re.search(r'(\d+)\s*\(', after_if)
        if not m:
            return [(0,0,0)] * expected
        n = int(m.group(1))

        # Find the opening paren
        paren_pos = after_if.find('(')
        start = if_pos + paren_pos + 1

        # Find matching closing paren
        depth = 1
        end = start
        while end < len(content) and depth > 0:
            if content[end] == '(':
                depth += 1
            elif content[end] == ')':
                depth -= 1
            end += 1
        inner = content[start:end-1]

        vecs = re.findall(r'\(([^)]+)\)', inner)
        u = []
        for v in vecs[:n]:
            vals = [float(x) for x in v.split()]
            if len(vals) == 3:
                u.append((vals[0], vals[1], vals[2]))
        return u

    else:
        # uniform
        m = re.search(r'uniform\s*\(([^)]+)\)', after_if)
        vals = [float(x) for x in m.group(1).split()]
        return [tuple(vals)] * expected

print("Parsing points...", file=sys.stderr, flush=True)
points = parse_points(os.path.join(CASE_DIR, "constant/polyMesh/points"))
print(f"Points: {len(points):,}", file=sys.stderr, flush=True)

# Build face->cell mapping (owner file gives cell for each internal face)
owners = parse_int_list(os.path.join(CASE_DIR, "constant/polyMesh/owner"))
neighbours = parse_int_list(os.path.join(CASE_DIR, "constant/polyMesh/neighbour"))
n_cells = max(max(owners), max(neighbours)) + 1
print(f"Cells: {n_cells:,}", file=sys.stderr, flush=True)

# Read U field
print("Reading U...", file=sys.stderr, flush=True)
U = parse_U(os.path.join(CASE_DIR, TIME, "U"), n_cells)
print(f"U entries: {len(U):,}", file=sys.stderr, flush=True)

# Parse faces and compute cell centers on-the-fly
# Strategy: for each face, compute its center, add to owner cell's accumulator
print("Processing faces...", file=sys.stderr, flush=True)

with open(os.path.join(CASE_DIR, "constant/polyMesh/faces")) as f:
    fcontent = f.read()
start = fcontent.find('(\n')
if start < 0: start = fcontent.find('(')
end = fcontent.rfind(')')
inner = fcontent[start+1:end]

cell_sum_x = [0.0] * n_cells
cell_sum_y = [0.0] * n_cells
cell_sum_z = [0.0] * n_cells
cell_cnt = [0] * n_cells

face_idx = 0
for m in re.finditer(r'(\d+)\(([^)]+)\)', inner):
    idx_str = m.group(2)
    indices = [int(x) for x in idx_str.split()]

    # Compute face center
    sx, sy, sz = 0.0, 0.0, 0.0
    for vi in indices:
        if vi < len(points):
            p = points[vi]
            sx += p[0]; sy += p[1]; sz += p[2]
    fx = sx / len(indices) if indices else 0
    fy = sy / len(indices) if indices else 0
    fz = sz / len(indices) if indices else 0

    # Add to owner cell
    if face_idx < len(owners):
        o = owners[face_idx]
        if o < n_cells:
            cell_sum_x[o] += fx
            cell_sum_y[o] += fy
            cell_sum_z[o] += fz
            cell_cnt[o] += 1

    # Add to neighbour cell (internal faces only)
    if face_idx < len(neighbours):
        n = neighbours[face_idx]
        if n < n_cells:
            cell_sum_x[n] += fx
            cell_sum_y[n] += fy
            cell_sum_z[n] += fz
            cell_cnt[n] += 1

    face_idx += 1
    if face_idx % 500000 == 0:
        print(f"  {face_idx:,} faces...", file=sys.stderr, flush=True)

print(f"Processed {face_idx:,} faces", file=sys.stderr, flush=True)

# Output cells near z=1.5m
out_path = "/mnt/d/Phase2_CFD_ML/model_outputs/urban_block_slice.csv"
written = 0
with open(out_path, 'w') as f:
    f.write("x,y,z,Ux,Uy,Uz,speed\n")
    for i in range(n_cells):
        if cell_cnt[i] == 0:
            continue
        cz = cell_sum_z[i] / cell_cnt[i]
        if abs(cz - 1.5) > 2.5:
            continue
        cx = cell_sum_x[i] / cell_cnt[i]
        cy = cell_sum_y[i] / cell_cnt[i]
        # Domain filter
        if cx <= -25 or cx >= 285 or cy <= -35 or cy >= 275:
            continue
        if i < len(U):
            ux, uy, uz = U[i]
        else:
            ux, uy, uz = 0, 0, 0
        spd = math.sqrt(ux*ux + uy*uy + uz*uz)
        f.write(f"{cx:.6f},{cy:.6f},{cz:.6f},{ux:.6f},{uy:.6f},{uz:.6f},{spd:.6f}\n")
        written += 1

print(f"Written {written:,} points to {out_path}", file=sys.stderr, flush=True)
print("Done!", file=sys.stderr, flush=True)
