"""
Time series derived from an already-exported boundary-frame series.

Migrated from the StoryMaps `plate-boundaries` prototype's `build_timeseries.py` --
see docs/adr/0001: StoryMaps is a consumer, not this library's design home, and this
logic (arc length of boundary segments matching some grouping) satisfies the scope
rule as much as anything else here.

Reads the frames the exporter already wrote rather than re-resolving topologies, so
what gets measured is exactly what the renderer draws -- the chart and the map
cannot disagree.

Length is measured ON THE SPHERE -- pygplates gives the arc length of a polyline in
radians, which is scaled by the Earth's radius here. Summing lon/lat deltas instead
would be wrong by a factor that varies with latitude: a degree of longitude is 111 km
at the equator and a few hundred metres in northern Greenland, and these boundaries
run right up to the poles. Tessellation does not affect the answer, since subdividing
a great-circle arc leaves its total length unchanged.

Grouping is a function supplied by the caller, not fixed to boundary_type. Boundary
length by type (`by_boundary_type`, the CLI's default) is the common case, not the
only one -- a caller measuring, say, subduction length actually evidenced by nearby
zircon samples against subduction length that is merely drawn supplies its own
group_fn (feature -> 'evidenced' | 'unevidenced' | anything not in `groups` to
exclude the feature) and gets the same frame-reading, arc-length and CSV machinery.
That split -- the arc-length/grouping mechanics here, "what counts as evidenced" as
the caller's own business -- is the rule from docs/adr/0001 applied directly: this
module has no idea what a zircon is, the same way boundaries.py has no idea what a
deposit is.
"""

import csv
import glob
import json
import os
import re

import pygplates

EARTH_RADIUS_KM = 6371.0

# Column order in the CSV, and the default grouping.
DEFAULT_TYPES = ["subduction", "ridge", "transform", "other"]


def by_boundary_type(types=DEFAULT_TYPES):
    """The default grouping: a feature's own boundary_type, falling back to 'other'
    rather than being dropped -- every feature is counted somewhere."""
    def group_fn(feature):
        kind = feature["properties"]["boundary_type"]
        return kind if kind in types else "other"
    return group_fn


def arc_length_km(coordinates):
    """Arc length of one [lon, lat] coordinate list, in km, on the sphere."""
    if len(coordinates) < 2:
        return 0.0
    # GeoJSON is lon/lat; pygplates wants lat/lon.
    polyline = pygplates.PolylineOnSphere(
        [pygplates.PointOnSphere(lat, lon) for lon, lat in coordinates])
    return polyline.get_arc_length() * EARTH_RADIUS_KM


def frame_lengths(frame, group_fn, groups):
    """Arc length in km per group, for one already-loaded frame.

    @param frame     a parsed boundaries_<N>Ma.geojson (or anything sharing its
                     FeatureCollection-with-meta.reconstruction_time_ma shape)
    @param group_fn  feature -> group key
    @param groups    the group keys to report, in column order. A feature whose
                     group_fn result isn't one of these is dropped, not bucketed --
                     unlike `by_boundary_type`'s own 'other' fallback, which is a
                     property of that grouping, not of this function.
    @returns (time_ma, {group: length_km})
    """
    totals = {g: 0.0 for g in groups}
    for feature in frame["features"]:
        group = group_fn(feature)
        if group not in totals:
            continue
        totals[group] += arc_length_km(feature["geometry"]["coordinates"])
    return frame["meta"]["reconstruction_time_ma"], totals


def frame_paths(data_dir):
    """Every boundaries_<N>Ma.geojson in data_dir/frames/, sorted by time."""
    paths = glob.glob(os.path.join(data_dir, "frames", "boundaries_*Ma.geojson"))
    if not paths:
        raise SystemExit("no frames in {} -- run export first".format(data_dir))
    return sorted(paths, key=lambda p: int(re.search(r"(\d+)Ma", p).group(1)))


def length_series(data_dir, group_fn=None, groups=DEFAULT_TYPES):
    """Arc length per group, per frame, across an exported series.

    @returns [(time_ma, {group: length_km})], sorted by time
    """
    group_fn = group_fn or by_boundary_type(groups)
    rows = []
    for path in frame_paths(data_dir):
        with open(path) as fh:
            frame = json.load(fh)
        rows.append(frame_lengths(frame, group_fn, groups))
    return rows


def write_csv(rows, groups, out_path):
    """Write a length series as CSV: time_ma, <group>_km..., total_km.

    This is the shape js/timeseries.js's parseCsv/seriesFromCsv read -- see
    SCHEMA.md's Time series section. `total_km` is written for a consumer that wants
    it, but nothing requires a chart to show every column -- seriesFromCsv's `spec`
    picks a subset.
    """
    with open(out_path, "w", newline="") as fh:
        out = csv.writer(fh)
        out.writerow(["time_ma"] + ["{}_km".format(g) for g in groups] + ["total_km"])
        for time, totals in rows:
            values = [totals[g] for g in groups]
            out.writerow([round(time, 1)] + [round(v, 1) for v in values]
                        + [round(sum(values), 1)])


def boundary_length_series(data_dir, out_path=None, types=DEFAULT_TYPES, quiet=False):
    """Total boundary length by type, through time -- the common case, and the CLI's.

    Reads data_dir/frames/boundaries_*Ma.geojson, writes out_path (default
    data_dir/boundary_length.csv). For a different grouping, call
    length_series()/write_csv() directly with your own group_fn -- this function is a
    convenience wrapper around them, not the only way in.
    """
    out_path = out_path or os.path.join(data_dir, "boundary_length.csv")
    rows = length_series(data_dir, groups=types)
    write_csv(rows, types, out_path)

    if not quiet:
        print("{} frames -> {} ({:.1f} kB)".format(
            len(rows), out_path, os.path.getsize(out_path) / 1e3))
        for time, totals in (rows[0], rows[len(rows) // 2], rows[-1]):
            print("  {:>5.0f} Ma  ".format(time)
                  + "  ".join("{}={:,.0f}".format(t, totals[t]) for t in types))

    return out_path


def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(
        prog="petrify-timeseries",
        description="Total boundary length by type, through an exported frame series.")
    p.add_argument("--data", default="data", help="directory holding the export")
    p.add_argument("--out", default=None,
                   help="output CSV (default: <data>/boundary_length.csv)")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    boundary_length_series(args.data, out_path=args.out, quiet=args.quiet)


if __name__ == "__main__":
    main()
