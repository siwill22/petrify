"""
Symbolised point datasets, reconstructed through time.

Points differ from the other two layers in this library in a way that drives the whole
design: they are RIGID BODIES with correspondence across time. A given deposit is the
same deposit at 0 Ma and at 250 Ma, just somewhere else. Resolved topologies have no such
correspondence -- sub-segments appear and split at triple junctions -- which is why
boundary frames cut hard and points may legitimately be interpolated.

That leaves two honest ways to ship positions through time, and both are implemented:

  'rotations'   Present-day lon/lat per point, plus an Euler pole/angle per PLATE per
                time. The browser slerps the plate's finite rotation and applies it.
                Scales with plates x times.

  'trajectory'  Reconstructed lon/lat per point per time. Scales with points x times.

Which is smaller is a property of the dataset, not a matter of taste. The crossover is
roughly `effective_points > 2 * distinct_plates`, where "effective" discounts points that
do not exist for much of the range. Two real measurements:

    Whittaker LIPs         80 points,  23 plates, 33% populated -> trajectory wins 107 kB : 185 kB
    Hoggard base metals  1987 points, 174 plates, ~95% populated -> rotations  wins 1.4 MB : 6.0 MB

Exporting both is also the sharpest check available on either: reconstruct the same
points two independent ways and the answers must agree.
"""

import math

import numpy as np
import pygplates

TRANSPORTS = ("rotations", "trajectory")

ROTATION_NOTE = (
    "Geometry is present-day. To place a point at time t, slerp its plate's bracketing "
    "finite rotations and apply the result to the point's unit vector. A point is drawn "
    "only while from >= t >= to."
)

TRAJECTORY_NOTE = (
    "Coordinates are already reconstructed, one lon/lat pair per point per time. Points "
    "are reconstructed at EVERY time regardless of age -- the position of the crust a "
    "deposit sits on is well defined before the deposit formed, and a renderer showing "
    "points in a window around their age needs exactly that. null means no position "
    "could be computed (no rotation for the plate at that time), not 'did not exist'. "
    "Whether to draw a point at a given time is the renderer's decision, from `age`."
)


def finite_rotation_to_pole_angle(rotation, decimals=6):
    """(pole_lon, pole_lat, angle_deg) for a pygplates FiniteRotation.

    An identity rotation has no well-defined pole, so return an arbitrary one with zero
    angle. Six decimals rather than four: at four the reconstructed positions drift from
    pyGPlates by ~10 m, which is invisible on screen but would put a floor under the
    transport cross-check for no meaningful saving.

    Lifted from southern-ocean-gateways/build/build_vectors.py, which established this
    format for the coastline rotations.
    """
    if rotation.represents_identity_rotation():
        return (0.0, 90.0, 0.0)
    pole, angle_rad = rotation.get_euler_pole_and_angle()
    lat, lon = pole.to_lat_lon()
    return (round(lon, decimals), round(lat, decimals),
            round(math.degrees(angle_rad), decimals))


def _clean(value):
    """JSON-safe scalar. 'ND' and NaN both mean 'not determined', never zero."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return None if text in ("", "ND", "nd", "n/a", "N/A", "-") else text
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if math.isnan(float(value)) else round(float(value), 4)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def points_from_dataframe(gdf, model, lon_field="Longitude", lat_field="Latitude",
                          age_field="Age", fields=(), polygons="static"):
    """Build pygplates point features with plate IDs assigned by partitioning.

    The datasets this is aimed at -- ore deposit compilations, sample sites -- carry
    coordinates and an age but no plate ID, so one has to be assigned by asking which
    static polygon each point falls in. Points landing outside every polygon come back
    with plate 0, which reconstructs as "does not move"; that is silent and wrong-looking
    rather than loud, so the caller is told the count.

    `age_field` is the formation age in Ma. A point exists from then to the present.
    """
    features = []
    meta = []

    for index, (_, row) in enumerate(gdf.iterrows()):
        lon = float(row[lon_field])
        lat = float(row[lat_field])

        feature = pygplates.Feature()
        feature.set_geometry(pygplates.PointOnSphere(lat, lon))
        # Partitioning returns features grouped by partitioning polygon, not in input
        # order, and two deposits can share coordinates exactly -- so carry the input
        # index on the feature rather than trying to match positions back afterwards.
        feature.set_name(str(index))

        age = _clean(row.get(age_field)) if hasattr(row, "get") else None

        # Valid over ALL time, deliberately. It is tempting to set valid_time from the
        # age so pygplates filters the point out before it formed, and an earlier version
        # did -- but that bakes one display rule into the data. A consumer showing points
        # in a window AROUND their age needs positions from before formation, which those
        # nulls would have made impossible; and the rotations transport, which can place a
        # point at any time from its present-day position, would silently disagree.
        #
        # So the export carries WHERE, and the renderer decides WHEN. `age` travels as
        # ordinary metadata for the renderer to apply its own rule to.
        feature.set_valid_time(pygplates.GeoTimeInstant.create_distant_past(),
                               pygplates.GeoTimeInstant.create_distant_future())
        features.append(feature)

        record = {"lon": round(lon, 4), "lat": round(lat, 4), "age": age}
        for name, key in fields:
            record[name] = _clean(row.get(key))
        meta.append(record)

    partitioned = pygplates.partition_into_plates(
        model.static_polygons if polygons == "static" else model.continent_polygons,
        model.rotation_model, features)

    # Restore input order using the index carried on each feature, so the metadata list
    # and the geometry list stay aligned.
    ordered = [None] * len(meta)
    unassigned = 0
    for feature in partitioned:
        index = int(feature.get_name())
        plate_id = int(feature.get_reconstruction_plate_id())
        if plate_id == 0:
            unassigned += 1
        record = dict(meta[index])
        record["plate_id"] = plate_id
        ordered[index] = (feature, record)

    missing = [i for i, item in enumerate(ordered) if item is None]
    if missing:
        raise RuntimeError(
            "{} points were lost during partitioning (first at index {})".format(
                len(missing), missing[0]))

    return ordered, unassigned


def build_points(model, records, times, transport="rotations", anchor_plate=0,
                 decimals=2, categories=None, meta=None, model_name=None):
    """Assemble the single-file point payload.

    @param records     [(pygplates.Feature, metadata dict)] from points_from_dataframe
    @param times       list of times in Ma, ascending
    @param transport   'rotations' or 'trajectory'
    @param model_name  gprm short name, e.g. 'Merdith2021'. The model's own .name is a
                       citation string ('Merdith++2021_v1.0'), which is not what the
                       other payloads in this library record or what consumers key on.
    """
    if transport not in TRANSPORTS:
        raise ValueError("transport must be one of {}".format(TRANSPORTS))

    features = [f for f, _ in records]
    properties = [p for _, p in records]

    payload = {
        "model": model_name or getattr(model, "name", None) or "unknown",
        "transport": transport,
        "anchor_plate_id": anchor_plate,
        "times": list(times),
        "categories": categories or {},
        "points": properties,
        "meta": meta or {},
    }

    if transport == "rotations":
        payload["note"] = ROTATION_NOTE
        payload["rotations"] = _rotation_block(
            model, {p["plate_id"] for p in properties}, times, anchor_plate)
    else:
        payload["note"] = TRAJECTORY_NOTE
        payload["frames"] = _trajectory_block(
            model, features, properties, times, anchor_plate, decimals)

    return payload


def _rotation_block(model, plate_ids, times, anchor_plate):
    """[pole_lon, pole_lat, angle_deg] per plate per time.

    One entry per plate rather than per point: the whole reason this transport wins on
    large datasets is that a thousand deposits on one craton share one rotation.
    """
    rotation_model = model.rotation_model
    block = {}
    for plate_id in sorted(plate_ids):
        series = []
        for time in times:
            rotation = rotation_model.get_rotation(
                float(time), int(plate_id), anchor_plate_id=anchor_plate)
            series.append(finite_rotation_to_pole_angle(rotation))
        block[str(plate_id)] = series
    return block


def _trajectory_block(model, features, properties, times, anchor_plate, decimals):
    """[[lon...], [lat...]] per time, with null where the point does not yet exist.

    pygplates.reconstruct already honours valid time -- a deposit is simply not returned
    before it formed -- so the nulls fall out of the reconstruction rather than needing a
    separate age test. Results come back in arbitrary order, so each is matched to its
    input by feature identity.
    """
    frames = {}

    for time in times:
        reconstructed = []
        pygplates.reconstruct(features, model.rotation_model, reconstructed,
                              float(time), anchor_plate_id=anchor_plate)

        lons = [None] * len(features)
        lats = [None] * len(features)
        for item in reconstructed:
            # Same index tag set in points_from_dataframe.
            i = int(item.get_feature().get_name())
            lat, lon = item.get_reconstructed_geometry().to_lat_lon()
            lons[i] = round(lon, decimals)
            lats[i] = round(lat, decimals)

        frames[str(time)] = [lons, lats]

    return frames
