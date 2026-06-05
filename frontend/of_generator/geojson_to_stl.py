"""
GeoJSON building polygons → binary STL cuboids for OpenFOAM snappyHexMesh.

Each building is extruded from its footprint polygon to its specified height,
producing a watertight binary STL file with correct face normals.
"""
from __future__ import annotations

import struct
import math
from pathlib import Path
from typing import List, Tuple

from ..schema import SitePlan, Feature


def geojson_to_stl(
    plan: SitePlan,
    output_dir: Path,
    bike_size: Tuple[float, float, float] = (2.0, 0.6, 1.0),
) -> int:
    """
    Write all building and bike station STLs from a SitePlan.

    Args:
        plan: SitePlan with buildings and optionally bike stations
        output_dir: Directory to write STL files (typically constant/triSurface/)
        bike_size: (length, width, height) for bike parking STL

    Returns:
        Number of STL files written
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    for feature in plan.features:
        if feature.category == "building":
            height = float(feature.properties.get("height", 12.0))
            coords = feature.geometry.coordinates
            if coords and coords[0]:
                footprint = [(p[0], p[1]) for p in coords[0]]
                _write_stl_cuboid(
                    output_dir / f"{feature.id}.stl",
                    footprint, 0.0, height,
                )
                count += 1

        elif feature.category == "bike_station":
            cx, cy = feature.centroid
            lx, ly, h = bike_size
            # Simple rectangular prism
            hlx, hly = lx / 2, ly / 2
            footprint = [
                (cx - hlx, cy - hly),
                (cx + hlx, cy - hly),
                (cx + hlx, cy + hly),
                (cx - hlx, cy + hly),
            ]
            _write_stl_cuboid(
                output_dir / f"{feature.id}.stl",
                footprint, 0.0, h,
            )
            count += 1

    return count


def _write_stl_cuboid(
    path: Path,
    footprint: List[Tuple[float, float]],
    z_min: float,
    z_max: float,
):
    """
    Write a single building/bike as a binary STL cuboid.

    The cuboid is defined by extruding a 2D footprint polygon from z_min to z_max.
    Each building face is triangulated (2 triangles per face for quads).
    """
    # Ensure closed polygon
    if footprint and footprint[0] != footprint[-1]:
        footprint = footprint + [footprint[0]]

    n = len(footprint) - 1  # Number of unique vertices

    # Vertices (bottom + top)
    verts_bottom = [(x, y, z_min) for x, y in footprint[:-1]]
    verts_top = [(x, y, z_max) for x, y in footprint[:-1]]

    faces = []

    # Bottom face (normal pointing -z)
    for i in range(1, n - 1):
        faces.append(_make_face(
            verts_bottom[0], verts_bottom[i + 1], verts_bottom[i],
        ))

    # Top face (normal pointing +z)
    for i in range(1, n - 1):
        faces.append(_make_face(
            verts_top[0], verts_top[i], verts_top[i + 1],
        ))

    # Side faces
    for i in range(n):
        j = (i + 1) % n
        v0, v1 = verts_bottom[i], verts_bottom[j]
        v2, v3 = verts_top[i], verts_top[j]
        # Two triangles per quad
        faces.append((_face_normal(v0, v2, v1), (v0, v2, v1)))
        faces.append((_face_normal(v1, v2, v3), (v1, v2, v3)))

    # Write binary STL
    with open(path, "wb") as f:
        f.write(b"\x00" * 80)  # Header
        f.write(struct.pack("<I", len(faces)))  # Face count
        for normal, (v0, v1, v2) in faces:
            f.write(struct.pack("<3f", *normal))
            f.write(struct.pack("<3f", *v0))
            f.write(struct.pack("<3f", *v1))
            f.write(struct.pack("<3f", *v2))
            f.write(struct.pack("<H", 0))  # Attribute byte count


def _make_face(v0, v1, v2):
    """Create a face tuple (normal, vertices)."""
    return (_face_normal(v0, v1, v2), (v0, v1, v2))


def _face_normal(v0, v1, v2):
    """Compute unit normal from three vertices (right-hand rule)."""
    u = (v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2])
    v = (v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2])
    nx = u[1] * v[2] - u[2] * v[1]
    ny = u[2] * v[0] - u[0] * v[2]
    nz = u[0] * v[1] - u[1] * v[0]
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (nx / length, ny / length, nz / length)
