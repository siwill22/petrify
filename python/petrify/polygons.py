"""
Reconstructed polygons -- continents, terranes, coastlines.

Unlike resolved plate boundaries, these are RIGID BODIES: the same block of crust at every
time, just somewhere else. So the geometry ships ONCE at its present-day position, with an
Euler pole and angle per plate per time, and the browser rotates it. That is the same
transport `points.py` offers, and for polygons it is not really a choice -- shipping
reconstructed geometry per frame would mean 74,000 vertices x 251 frames.

Verified against pygplates: applying a plate's finite rotation as a quaternion to the
present-day vertices reproduces `pygplates.reconstruct` to 0.0002 km across every vertex of
every polygon at 0, 100 and 250 Ma.

The format deliberately matches the coastline data the StoryMaps globe already reads
(`p`/`b`/`e`/`xy` per feature, plus a `rotations` block), so one renderer serves both.
"""

import math
from collections import defaultdict

import numpy as np
import pygplates

from .points import finite_rotation_to_pole_angle

# JSON has no -Infinity. pygplates uses distant future for "still exists", which comes back
# as -inf; the coastline data this format matches already used this sentinel.
DISTANT_FUTURE = -99999.0
DISTANT_PAST = 99999.0

GEOMETRY_NOTE = (
    "Geometry is present-day. To place a polygon at time t, slerp its plate's bracketing "
    "finite rotations and apply the result to each vertex. Draw a feature only while "
    "e <= t <= b. Rings are closed and are NOT split at the antimeridian -- the renderer "
    "works in 3-D unit vectors where the dateline is not special."
)


# Vertices are matched between rings at this precision to decide what is shared. Six
# decimal places of a degree is about 10 cm -- far below any real digitising difference,
# so this joins what was digitised as one boundary and nothing else.
_KEY_DECIMALS = 6


def _to_xyz(latlon):
    """(lat, lon) pairs in degrees to unit vectors."""
    a = np.asarray(latlon, dtype=float)
    lat = np.radians(a[:, 0])
    lon = np.radians(a[:, 1])
    return np.stack([np.cos(lat) * np.cos(lon),
                     np.cos(lat) * np.sin(lon),
                     np.sin(lat)], axis=-1)


def _simplify_arc(xyz, tol_rad):
    """Douglas-Peucker on the sphere. Returns indices of the vertices to keep.

    Deviation is the CROSS-TRACK ANGLE from the great circle through the two chord
    endpoints: with n = normalise(A x B), the angle of P off that plane is asin(|P.n|).
    All of it is 3-D vector maths on unit vectors, which is the only version that is right
    everywhere. Planar Douglas-Peucker in lon/lat would misjudge these polygons exactly
    where they spend most of their time -- a degree of longitude is 111 km at the equator
    and a few hundred metres in northern Greenland -- and would treat an arc crossing the
    antimeridian as a line most of the way round the world.
    """
    n = len(xyz)
    if n < 3:
        return list(range(n))

    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = xyz[i], xyz[j]
        seg = xyz[i + 1:j]
        normal = np.cross(a, b)
        length = np.linalg.norm(normal)
        if length < 1e-12:
            # Endpoints coincident or antipodal: no great circle is defined by them, so
            # fall back to angular distance from the first endpoint.
            dev = np.arccos(np.clip(seg @ a, -1.0, 1.0))
        else:
            dev = np.arcsin(np.abs(np.clip(seg @ (normal / length), -1.0, 1.0)))
        k = int(np.argmax(dev))
        if dev[k] > tol_rad:
            mid = i + 1 + k
            keep[mid] = True
            stack.append((i, mid))
            stack.append((mid, j))
    return list(np.nonzero(keep)[0])


def _arc_key(seq):
    """Direction-independent identity for an arc, plus whether seq runs backwards.

    A boundary shared by two blocks is traversed one way by one and the other way by the
    other. Both must resolve to the same stored arc or the point of the exercise is lost.
    """
    forward = (seq[0], seq[-1], len(seq))
    reverse = (seq[-1], seq[0], len(seq))
    return (forward, False) if forward <= reverse else (reverse, True)


def _split_into_arcs(ring, membership):
    """Cut one ring into maximal runs of vertices with the same set of incident rings.

    This is the TopoJSON junction rule. A vertex where the membership changes is a
    junction -- the place where a shared boundary starts or stops being shared -- and the
    runs between junctions are the arcs.
    """
    n = len(ring)
    keys = [membership[p] for p in ring]
    cuts = [k for k in range(n)
            if keys[k] != keys[(k - 1) % n] or keys[k] != keys[(k + 1) % n]]

    if not cuts:
        # Nothing shared: an island. It still has to be cut somewhere, because a closed
        # loop handed to Douglas-Peucker with both endpoints on the same vertex can be
        # collapsed to nothing. Cut at the vertex furthest from the ring's centroid and
        # then at the vertex furthest from that, so the two anchors span the shape.
        xyz = _to_xyz(ring)
        centre = xyz.mean(axis=0)
        norm = np.linalg.norm(centre)
        first = int(np.argmin(xyz @ (centre / norm))) if norm > 1e-12 else 0
        second = int(np.argmin(xyz @ xyz[first]))
        cuts = sorted({first, second}) or [0]
        if len(cuts) < 2:
            cuts = [0, n // 2]

    arcs = []
    for x in range(len(cuts)):
        start = cuts[x]
        end = cuts[(x + 1) % len(cuts)]
        span = ((end - start) % n) + 1
        seq = [ring[(start + k) % n] for k in range(span)]
        if len(seq) > 1:
            arcs.append(seq)
    return arcs


def _simplify_topologically(rings, tolerance_deg):
    """Simplify a whole polygon set at once, keeping shared boundaries shared.

    These polygons tile against one another -- 76% of the edges in the Merdith2021
    continent set belong to more than one ring. Thinning each ring on its own gives two
    neighbours two DIFFERENT approximations of the boundary they share, which opens
    slivers of ocean between blocks that were touching. (Measured: independent decimation
    at 0.1 degrees took the shared-edge fraction from 76% down to 43%.)

    So: cut every ring into arcs at its junctions, simplify each distinct arc ONCE, and
    give every ring that uses it the identical vertex list. Neighbours then agree by
    construction rather than by luck.

    @param rings           list of rings, each a list of (lat, lon) tuples, not closed
    @param tolerance_deg   maximum deviation, degrees of arc; <= 0 disables simplification
    @returns (simplified_rings, stats)
    """
    if tolerance_deg <= 0:
        return rings, {"arcs": 0, "unique_arcs": 0}

    incident = defaultdict(set)
    for index, ring in enumerate(rings):
        for point in ring:
            incident[point].add(index)
    membership = {point: frozenset(owners) for point, owners in incident.items()}

    ring_arcs = [_split_into_arcs(ring, membership) for ring in rings]

    tol = math.radians(tolerance_deg)
    store = {}
    for arcs in ring_arcs:
        for seq in arcs:
            key, flipped = _arc_key(seq)
            if key in store:
                continue
            canonical = list(reversed(seq)) if flipped else seq
            kept = _simplify_arc(_to_xyz(canonical), tol)
            store[key] = [canonical[i] for i in kept]

    out = []
    for arcs in ring_arcs:
        ring = []
        for seq in arcs:
            key, flipped = _arc_key(seq)
            simplified = store[key]
            if flipped:
                simplified = list(reversed(simplified))
            # Drop the last vertex of each arc: it is the first of the next one, and the
            # ring is closed by the exporter, not stored closed.
            ring.extend(simplified[:-1])
        out.append(ring)

    stats = {
        "arcs": sum(len(a) for a in ring_arcs),
        "unique_arcs": len(store),
    }
    return out, stats


def _valid_time(feature):
    begin, end = feature.get_valid_time()
    if math.isinf(begin):
        begin = DISTANT_PAST
    if math.isinf(end):
        end = DISTANT_FUTURE
    return float(begin), float(end)


def build_polygons(features, rotation_model, times, anchor_plate=0,
                   tolerance=0.02, decimals=3):
    """Present-day rings plus a rotation series for every plate they sit on.

    Simplification happens across the WHOLE set at once, not ring by ring, so that blocks
    which share a boundary go on sharing it -- see `_simplify_topologically`. That is why
    this collects every ring first and only then emits.

    @param features   iterable of pygplates.Feature with polygon geometry
    @param times      list of times in Ma, ascending
    @param tolerance  maximum vertex deviation, degrees of arc
    @returns (features_payload, rotations_payload, stats)
    """
    rings = []
    attrs = []
    raw_verts = 0

    for feature in features:
        plate_id = int(feature.get_reconstruction_plate_id())
        begin, end = _valid_time(feature)
        name = feature.get_name() or ""

        for geometry in feature.get_geometries():
            ring = [tuple(round(c, _KEY_DECIMALS) for c in p.to_lat_lon())
                    for p in geometry.get_points()]
            if len(ring) < 3:
                continue
            raw_verts += len(ring)
            rings.append(ring)
            attrs.append((plate_id, begin, end, name))

    simplified, arc_stats = _simplify_topologically(rings, tolerance)

    out = []
    plates = set()
    kept_verts = 0

    for ring, (plate_id, begin, end, name) in zip(simplified, attrs):
        if len(set(ring)) < 3:
            continue                           # not a polygon any more
        kept_verts += len(ring)

        xy = []
        for lat, lon in ring:
            xy.append(round(lon, decimals))
            xy.append(round(lat, decimals))

        out.append({
            "p": plate_id,
            "b": begin,
            "e": end,
            "n": name,
            "xy": xy,
        })
        plates.add(plate_id)

    rotations = {}
    for plate_id in sorted(plates):
        rotations[str(plate_id)] = [
            finite_rotation_to_pole_angle(
                rotation_model.get_rotation(float(t), plate_id,
                                            anchor_plate_id=anchor_plate))
            for t in times
        ]

    stats = {
        "features": len(out),
        "plates": len(plates),
        "vertices": kept_verts,
        "vertices_before_simplification": raw_verts,
        "arcs": arc_stats["arcs"],
        "unique_arcs": arc_stats["unique_arcs"],
    }
    return out, rotations, stats


def polygon_payload(features, rotations, times, model_name, anchor_plate,
                    tolerance, source=None):
    return {
        "model": model_name,
        "anchor_plate_id": anchor_plate,
        "simplify_tolerance_degrees": tolerance,
        "pygplates_version": str(pygplates.Version.get_imported_version()),
        "note": GEOMETRY_NOTE,
        "source": source or "",
        "times": list(times),
        "rotations": rotations,
        "features": features,
    }
