"""
Check that exported data means what the renderers assume.

Three independent checks:

1. Subduction polarity, against the resolved plate polygons. For each subduction segment,
   step a short distance onto the side named by gpml:subductionPolarity and ask which
   topology that point falls inside. The renderer draws triangles on that side and claims
   it is the overriding plate, so every segment must land inside the overriding plate and
   never inside the subducting one. Stepping is done on the sphere -- the normal is a
   cross product of unit vectors, not a lon/lat offset, which would be wrong by a factor
   of cos(latitude) and meaningless near the poles.

2. Velocity components, against gprm's own azimuths. The exported file carries east and
   north components; the renderer turns those back into a direction. An east/north swap
   or a sign slip would still produce a plausible-looking arrow field, so the check is
   that atan2(east, north) reproduces velocity_azimuth to within export rounding.

3. Visual, against PyGMT. gprm's plot_subduction_zones() is the reference implementation
   of the triangle decoration; it reverses 'Left' geometries and lets GMT's -Sf...+r+t
   draw them. Rendering the same snapshot on the same orthographic projection gives a
   figure directly comparable with a screenshot of the web page, with the velocity field
   drawn on top in blue.
"""

import json
import math
import os
import sys

import numpy as np
import pygplates

from .export import load_model
from .velocities import healpix_domain

# How far to step off a boundary when testing which plate is on the polarity side. Large
# enough to clear coordinate rounding, small enough to stay inside a narrow plate.
STEP_DEG = 0.75

# Below this speed the azimuth is dominated by export rounding, not by the plate motion.
MIN_SPEED = 5.0


def to_vec3(lon, lat):
    lo, la = math.radians(lon), math.radians(lat)
    return np.array([math.cos(la) * math.cos(lo),
                     math.cos(la) * math.sin(lo),
                     math.sin(la)])


def check_polarity(features, snapshot):
    """Returns (agree, disagree, indeterminate)."""
    polygons = {}
    for topology in snapshot.resolved_topologies:
        plate_id = topology.get_resolved_feature().get_reconstruction_plate_id()
        polygons.setdefault(plate_id, []).append(topology.get_resolved_boundary())

    agree = disagree = indeterminate = 0

    for feature in features:
        props = feature["properties"]
        if props["boundary_type"] != "subduction":
            continue
        if props["overriding_plate_id"] is None:
            indeterminate += 1
            continue

        coords = feature["geometry"]["coordinates"]
        i = len(coords) // 2
        a = to_vec3(*coords[i - 1])
        b = to_vec3(*coords[i])

        # Unit tangent at a: the component of b perpendicular to a.
        tangent = b - np.dot(a, b) * a
        norm = np.linalg.norm(tangent)
        if norm < 1e-12:
            indeterminate += 1
            continue
        tangent /= norm

        # a x tangent is left-of-travel on the sphere: at a point on the equator
        # heading north, it points west.
        left = np.cross(a, tangent)
        side = left if props["polarity"] == "Left" else -left

        step = math.radians(STEP_DEG)
        probe = a * math.cos(step) + side * math.sin(step)
        point = pygplates.PointOnSphere(
            math.degrees(math.asin(max(-1.0, min(1.0, probe[2])))),
            math.degrees(math.atan2(probe[1], probe[0])))

        in_overriding = any(p.is_point_in_polygon(point)
                            for p in polygons.get(props["overriding_plate_id"], []))
        in_subducting = any(p.is_point_in_polygon(point)
                            for p in polygons.get(props["subducting_plate_id"], []))

        if in_overriding and not in_subducting:
            agree += 1
        elif in_subducting and not in_overriding:
            disagree += 1
            print("  FLIPPED: {}".format(props["name"]), file=sys.stderr)
        else:
            # Probe landed in a third plate or outside every polygon -- happens where a
            # trench meets a triple junction. Not evidence either way.
            indeterminate += 1

    return agree, disagree, indeterminate


def check_velocities(snapshot, velocity_file, time):
    """Returns (n_checked, worst_azimuth_error_deg, worst_speed_error)."""
    with open(velocity_file) as fh:
        data = json.load(fh)

    key = str(int(time))
    if key not in data["frames"]:
        print("  velocities: no frame at {} Ma".format(time), file=sys.stderr)
        return 0, None, None
    east, north = data["frames"][key]

    domain = healpix_domain(data["healpix_n"])
    field = snapshot.velocity_field(
        velocity_domain_features=[domain.meshnode_feature],
        delta_time=data["delta_time"])

    east = np.asarray(east)
    north = np.asarray(north)
    reference = np.degrees(np.asarray(field.velocity_azimuth))
    speed = np.hypot(east, north)

    # Azimuth is clockwise from north, so it is atan2(east, north) -- not the
    # atan2(y, x) of ordinary maths. Getting that backwards mirrors the whole field.
    exported = np.degrees(np.arctan2(east, north)) % 360.0

    moving = speed > MIN_SPEED
    if not moving.any():
        return 0, None, None

    # Wrap into [-180, 180) so 359.9 vs 0.1 reads as 0.2, not 359.8.
    delta = (exported[moving] - reference[moving] + 180.0) % 360.0 - 180.0
    worst_azimuth = float(np.abs(delta).max())
    worst_speed = float(np.abs(
        speed[moving] - np.asarray(field.velocity_magnitude)[moving]).max())

    return int(moving.sum()), worst_azimuth, worst_speed


def land_snapshot(model, time):
    """Reconstructed land for context on the reference figure.

    Not every model ships coastlines -- Merdith2021 provides continent polygons instead
    -- and the figure only needs something to orient by, so take whichever exists.
    """
    if getattr(model, "coastlines", None):
        return model.polygon_snapshot("coastlines", time)
    if getattr(model, "continent_polygons", None):
        return model.polygon_snapshot("continents", time)
    return None


def draw_reference(model, snapshot, time, views, out_pattern, healpix_n=8):
    import pygmt

    land = land_snapshot(model, time)
    domain = healpix_domain(healpix_n)
    written = []

    for lon, lat in views:
        fig = pygmt.Figure()
        fig.basemap(region="g", projection="G{}/{}/15c".format(lon, lat), frame="g30")

        # Reconstructed land, not fig.coast() -- modern coastlines under a 100 Ma
        # reconstruction would put the trenches in absurd places and make the
        # comparison with the web page impossible to read.
        if land is not None:
            land.plot(fig, fill="gray30", pen="0.2p,gray45")

        snapshot.plot_other_boundaries(fig, pen="0.6p,gray60")
        snapshot.plot_mid_ocean_ridges(fig, pen="1p,red")
        snapshot.plot_subduction_zones(fig, fill="black", pen="1p,black",
                                       gap=12, size=5)
        snapshot.velocity_field(
            velocity_domain_features=[domain.meshnode_feature]
        ).plot(fig, scaling=400., pen="0.6p,blue", color="blue")

        path = out_pattern.format(lon=lon, lat=lat)
        fig.savefig(path, dpi=150)
        written.append(path)

    return written


def verify(data_dir="data", model_name="Merdith2021", time=100.0,
           views=((-60, 10), (100, 15)), figures=True, out_dir=None):
    geojson = os.path.join(
        data_dir, "frames", "boundaries_{:03d}Ma.geojson".format(int(time)))
    velocity_file = os.path.join(data_dir, "velocities.json")

    with open(geojson) as fh:
        data = json.load(fh)
    print("checking {} ({} features)".format(
        os.path.basename(geojson), len(data["features"])))

    model = load_model(model_name)
    snapshot = model.plate_snapshot(time)

    agree, disagree, indeterminate = check_polarity(data["features"], snapshot)
    print("\n  polarity side is the overriding plate : {}".format(agree))
    print("  polarity side is the subducting plate : {}".format(disagree))
    print("  indeterminate (triple junction, or no plate attribution) : {}".format(
        indeterminate))

    n, worst_azimuth, worst_speed = check_velocities(snapshot, velocity_file, time)
    if n:
        print("\n  velocity points checked : {}".format(n))
        print("  worst azimuth error     : {:.4f} deg".format(worst_azimuth))
        print("  worst speed error       : {:.4f} km/Myr".format(worst_speed))

    if figures:
        out_dir = out_dir or data_dir
        pattern = os.path.join(
            out_dir,
            "reference_pygmt_{:g}Ma".format(time) + "_{lon}_{lat}.png")
        for path in draw_reference(model, snapshot, time, views, pattern,
                                   healpix_n=json.load(open(velocity_file))["healpix_n"]):
            print("wrote {}".format(os.path.basename(path)))

    if disagree:
        print("\nFAIL: polarity convention is inverted somewhere", file=sys.stderr)
        return 1
    print("\nOK")
    return 0


def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(
        prog="petrify-verify",
        description="Check exported boundaries and velocities against pygplates.")
    p.add_argument("--data", default="data", help="directory holding the export")
    p.add_argument("--model", default="Merdith2021")
    p.add_argument("--time", type=float, default=100.0, help="frame to check, Ma")
    p.add_argument("--no-figures", action="store_true",
                   help="skip the PyGMT reference figures")
    p.add_argument("--out", default=None, help="where to write figures (default: --data)")
    args = p.parse_args(argv)

    raise SystemExit(verify(
        data_dir=args.data, model_name=args.model, time=args.time,
        figures=not args.no_figures, out_dir=args.out))


if __name__ == "__main__":
    main()
