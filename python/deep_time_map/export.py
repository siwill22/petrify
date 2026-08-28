"""
Export a boundary + velocity series for the web renderers.

Writes:
    <out>/frames/boundaries_<NNN>Ma.geojson    one per timestep
    <out>/boundaries.json                      manifest listing the frames
    <out>/velocities.json                      the whole velocity series, one file
"""

import json
import os
import sys

import pygplates

from gprm.datasets import Reconstructions

from .boundaries import build_frame
from .points import TRANSPORTS, build_points, points_from_dataframe
from .velocities import build_velocities, healpix_domain, velocity_payload

FRAME_NAME = "boundaries_{:03d}Ma.geojson"

SERIES_NOTE = (
    "One GeoJSON file per timestep. Resolved topologies change discontinuously "
    "between timesteps, so frames are shown with hard transitions rather than "
    "interpolated."
)


def load_model(model_name):
    """Fetch a reconstruction model by gprm name, e.g. 'Merdith2021'."""
    fetch = getattr(Reconstructions, "fetch_{}".format(model_name), None)
    if fetch is None:
        raise SystemExit(
            "unknown model {!r}. Available: {}".format(
                model_name,
                ", ".join(sorted(
                    n[len("fetch_"):] for n in dir(Reconstructions)
                    if n.startswith("fetch_")))))
    return fetch()


def export_series(model_name="Merdith2021", start=0, end=250, step=1,
                  anchor_plate=0, tessellate=0.5, decimals=3,
                  healpix_n=8, delta_time=1.0, out_dir="data", quiet=False):
    model = load_model(model_name)

    times = list(range(start, end + 1, step))
    frame_dir = os.path.join(out_dir, "frames")
    os.makedirs(frame_dir, exist_ok=True)

    domain = healpix_domain(healpix_n)
    if not quiet:
        print("building {} frames, {}-{} Ma every {} Myr".format(
            len(times), start, end, step))
        print("velocity domain: {} HEALPix points (N={})".format(
            len(domain.longitude), healpix_n))

    frames = []
    velocity_frames = {}
    total_bytes = 0
    unusable = 0

    for time in times:
        snapshot = model.plate_snapshot(float(time), anchor_plate_id=anchor_plate)

        payload, counts, polarities = build_frame(
            snapshot, float(time), model_name, anchor_plate,
            tessellate_degrees=tessellate, decimals=decimals)

        velocity_frames[str(time)] = build_velocities(
            snapshot, domain.meshnode_feature, delta_time=delta_time)

        name = FRAME_NAME.format(time)
        path = os.path.join(frame_dir, name)
        with open(path, "w") as fh:
            json.dump(payload, fh, separators=(",", ":"))

        total_bytes += os.path.getsize(path)

        # A subduction zone without a usable polarity cannot be decorated, and the whole
        # point of the export is lost silently if that slips through. Count them across
        # the series rather than failing on the first -- older times in any model are
        # patchier than the present day, and it is the trend that matters.
        unusable += sum(v for k, v in polarities.items()
                        if k not in ("Left", "Right"))

        frames.append({
            "time": time,
            "file": "frames/" + name,
            "features": len(payload["features"]),
            "subduction": counts.get("subduction", 0),
        })

        if not quiet and time % 25 == 0:
            print("  {:3d} Ma  {:4d} features  {:3d} subduction  {:5.0f} kB".format(
                time, len(payload["features"]), counts.get("subduction", 0),
                os.path.getsize(path) / 1e3))

    manifest = {
        "model": model_name,
        "anchor_plate_id": anchor_plate,
        "time_step": step,
        "tessellate_degrees": tessellate,
        "pygplates_version": str(pygplates.Version.get_imported_version()),
        "note": SERIES_NOTE,
        "frames": frames,
    }
    manifest_path = os.path.join(out_dir, "boundaries.json")
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, separators=(",", ":"))

    velocity_path = os.path.join(out_dir, "velocities.json")
    with open(velocity_path, "w") as fh:
        json.dump(velocity_payload(
            domain, velocity_frames, model_name, healpix_n, delta_time),
            fh, separators=(",", ":"))

    if not quiet:
        print("\nwrote {} frames to {}".format(len(frames), frame_dir))
        print("  manifest:   {}".format(manifest_path))
        print("  velocities: {} ({:.1f} MB)".format(
            velocity_path, os.path.getsize(velocity_path) / 1e6))
        print("  boundaries total {:.1f} MB, mean {:.0f} kB per frame".format(
            total_bytes / 1e6, total_bytes / len(frames) / 1e3))
        if unusable:
            print("  NOTE: {} subduction segments across the series have no usable "
                  "polarity and will be drawn undecorated".format(unusable),
                  file=sys.stderr)

    return manifest_path, velocity_path


def export_points(gdf, model_name="Merdith2021", start=0, end=250, step=1,
                  anchor_plate=0, transport="rotations", fields=(), categories=None,
                  meta=None, decimals=2, out_dir="data", filename=None, model=None,
                  quiet=False):
    """Reconstruct a point dataset and write it as one JSON file.

    `transport` may also be 'both', which writes points.json (rotations) alongside
    points_trajectory.json. That is worth the extra build time: reconstructing the same
    points two independent ways and comparing is the sharpest check there is on either.
    """
    model = model or load_model(model_name)
    times = list(range(start, end + 1, step))
    os.makedirs(out_dir, exist_ok=True)

    records, unassigned = points_from_dataframe(gdf, model, fields=fields)
    if not quiet:
        print("{} points, {} distinct plates".format(
            len(records), len({p["plate_id"] for _, p in records})))

    # A point outside every static polygon gets plate 0, which reconstructs as "does not
    # move". On a globe that reads as a deposit sitting in the ocean while its continent
    # sails away -- plausible enough to miss, so say it loudly.
    if unassigned:
        print("  WARNING: {} points fell outside every static polygon and will be "
              "pinned at their present-day position".format(unassigned),
              file=sys.stderr)

    wanted = TRANSPORTS if transport == "both" else (transport,)
    written = []

    for mode in wanted:
        payload = build_points(
            model, records, times, transport=mode, anchor_plate=anchor_plate,
            decimals=decimals, categories=categories, meta=meta,
            model_name=model_name)

        name = filename or ("points.json" if mode == "rotations"
                            else "points_trajectory.json")
        path = os.path.join(out_dir, name)
        with open(path, "w") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        written.append(path)

        if not quiet:
            print("  {:<11s} {} ({:.1f} MB)".format(
                mode, path, os.path.getsize(path) / 1e6))

    return written


def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(
        prog="deep-time-map-export",
        description="Export plate boundaries and velocities as web-ready JSON.")
    p.add_argument("--model", default="Merdith2021",
                   help="gprm reconstruction model name (default: Merdith2021)")
    p.add_argument("--start", type=int, default=0, help="youngest time, Ma")
    p.add_argument("--end", type=int, default=250, help="oldest time, Ma")
    p.add_argument("--step", type=int, default=1, help="timestep, Myr")
    p.add_argument("--anchor-plate", type=int, default=0)
    p.add_argument("--tessellate", type=float, default=0.5,
                   help="arc spacing for densification, degrees (default: 0.5, about "
                        "1.5 screen px at typical zoom)")
    p.add_argument("--decimals", type=int, default=3,
                   help="coordinate rounding (default: 3, ~110 m)")
    p.add_argument("--healpix-n", type=int, default=8,
                   help="velocity domain density; 12*N^2 points (default: 8 -> 768)")
    p.add_argument("--delta-time", type=float, default=1.0,
                   help="stage interval for velocities, Myr")
    p.add_argument("--out", default="data", help="output directory")
    p.add_argument("--quiet", action="store_true")

    p.add_argument("--points", metavar="CSV",
                   help="also reconstruct a point dataset from this CSV. Needs "
                        "Longitude/Latitude columns and, for appearance through time, "
                        "an Age column in Ma.")
    p.add_argument("--point-fields", default="",
                   help="metadata columns to carry into the popup, as "
                        "label=Column,label=Column")
    p.add_argument("--transport", default="rotations",
                   choices=list(TRANSPORTS) + ["both"],
                   help="how point positions are shipped (default: rotations). "
                        "'rotations' scales with plates, 'trajectory' with points; "
                        "'both' writes each and lets you compare them.")
    p.add_argument("--points-only", action="store_true",
                   help="skip the boundary and velocity export")
    args = p.parse_args(argv)

    if not args.points_only:
        export_series(
            model_name=args.model, start=args.start, end=args.end, step=args.step,
            anchor_plate=args.anchor_plate, tessellate=args.tessellate,
            decimals=args.decimals, healpix_n=args.healpix_n,
            delta_time=args.delta_time, out_dir=args.out, quiet=args.quiet)

    if args.points:
        import pandas as pd

        fields = [tuple(part.split("=", 1))
                  for part in args.point_fields.split(",") if "=" in part]
        export_points(
            pd.read_csv(args.points), model_name=args.model, start=args.start,
            end=args.end, step=args.step, anchor_plate=args.anchor_plate,
            transport=args.transport, fields=fields, out_dir=args.out,
            quiet=args.quiet)


if __name__ == "__main__":
    main()
