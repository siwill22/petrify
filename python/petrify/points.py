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


def _partition_features(gdf, lon_field, lat_field):
    """One throwaway `pygplates.Feature` per row, geometry only, for partitioning.

    Tagged with the input row index via `set_name`, the same convention the caller uses
    to realign partitioning's arbitrary output order -- see `points_from_dataframe`.
    """
    features = []
    for index, (_, row) in enumerate(gdf.iterrows()):
        feature = pygplates.Feature()
        feature.set_geometry(
            pygplates.PointOnSphere(float(row[lat_field]), float(row[lon_field])))
        feature.set_name(str(index))
        feature.set_valid_time(pygplates.GeoTimeInstant.create_distant_past(),
                               pygplates.GeoTimeInstant.create_distant_future())
        features.append(feature)
    return features


def _partition_plate_ids(features, model, polygons_source):
    """Partition `features` and return {input index: (feature, plate_id, plate_begin_age)}.

    Shared by the primary partition pass and the optional `partition_lon_field`/
    `partition_lat_field` one -- both need exactly this, just against a different
    coordinate pair. The `feature` in the result is the one `partition_into_plates`
    hands back, not necessarily the same object passed in -- pygplates makes no promise
    the input list is mutated in place, so anything reading the properties this call just
    assigned (`reconstruction_plate_id`, `valid_time_begin`) has to read them from here.
    """
    partitioned = pygplates.partition_into_plates(
        polygons_source, model.rotation_model, features,
        properties_to_copy=[pygplates.PartitionProperty.reconstruction_plate_id,
                             pygplates.PartitionProperty.valid_time_begin])

    result = {}
    unassigned = 0
    for feature in partitioned:
        index = int(feature.get_name())
        plate_id = int(feature.get_reconstruction_plate_id())
        if plate_id == 0:
            unassigned += 1
        begin, _ = feature.get_valid_time()  # always plain floats, inf for distant past
        plate_begin_age = None if math.isinf(begin) else round(float(begin), 4)
        result[index] = (feature, plate_id, plate_begin_age)

    missing = [i for i in range(len(features)) if i not in result]
    if missing:
        raise RuntimeError(
            "{} points were lost during partitioning (first at index {})".format(
                len(missing), missing[0]))
    return result, unassigned


def points_from_dataframe(gdf, model, lon_field="Longitude", lat_field="Latitude",
                          age_field="Age", fields=(), polygons="static",
                          partition_lon_field=None, partition_lat_field=None,
                          plate_id_field=None):
    """Build pygplates point features with plate IDs assigned by partitioning.

    The datasets this is aimed at -- ore deposit compilations, sample sites -- carry
    coordinates and an age but no plate ID, so one has to be assigned by asking which
    static polygon each point falls in. Points landing outside every polygon come back
    with plate 0, which reconstructs as "does not move"; that is silent and wrong-looking
    rather than loud, so the caller is told the count.

    `age_field` is the formation age in Ma. A point exists from then to the present.

    Each record also carries `plate_begin_age`: the assigned static polygon's own begin
    age (`pygplates.PartitionProperty.valid_time_begin`), i.e. how far back that specific
    piece of crust is a geologically meaningful assignment at all -- a static polygon is
    digitized in present-day space and rotated backward, so it is always crust that
    survives to today, but that same construction means the polygon (and the plate id it
    hands out) has no meaning before its own begin age. Reconstructing a point older than
    that silently does not fail: `pygplates` still returns a rotation (the plate's own,
    while its rotation sequence lasts; the identity beyond it -- see `_rotation_block`),
    so an over-old point is drawn somewhere plausible-looking instead of reporting
    anything wrong. This is a mechanism, not a policy, the same "export carries WHERE, the
    renderer decides WHEN" split as `age` above -- whether to exclude such a point, flag
    it, or ignore the field entirely is left to the caller. `None` (not a numeric age)
    when no polygon was assigned at all (plate 0): the point's own valid time is left
    untouched in that case (see below), so its begin age is technically "distant past",
    which would serialise as Infinity, not valid JSON -- reported as `None` instead, the
    same "not recorded" convention `_clean()` already uses for NaN.

    `partition_lon_field`/`partition_lat_field` split the plate ASSIGNMENT from the
    drawn POSITION -- e.g. a paleomagnetic pole, where the pole's own coordinates are
    what gets drawn but the plate it moves with is decided by where its sample site sits,
    not by the pole position. Default `None` means "same as `lon_field`/`lat_field`",
    today's behaviour, unchanged. When given, a second, throwaway partition runs against
    those fields purely to recover `plate_id`/`plate_begin_age` for the record -- the
    features built from `lon_field`/`lat_field` (and their own `reconstruction_plate_id`,
    which is what `build_points`'s 'trajectory' transport actually reconstructs from) are
    completely untouched by this. That is deliberate, not an oversight: repointing the
    same feature list's geometry instead would make 'trajectory' silently reconstruct the
    partition anchor rather than the drawn position, for any caller who combines that
    transport with a split -- two independent feature lists close that off rather than
    relying on every future caller to remember the trap. This module stays ignorant of
    what a "site" or a "pole" is; the names are deliberately generic (see ADR-0001).

    `plate_id_field` trusts an existing plate-id column instead of the partitioned
    result, for rows where it is not null (NaN). Some compilations (LIPs, ore
    deposits with expert QA) ship a plate id that reflects real domain knowledge
    point-in-polygon testing cannot recover -- especially for oceanic crust a
    model's static polygons may not cover at all, which is exactly when partitioning
    silently falls back to plate 0 (pinned, never moves). Partitioning still runs
    for every row regardless, both to fill in rows this column leaves null and
    because it is what sets the drawn feature's own `reconstruction_plate_id` (see
    above) -- this only overrides the RECORD read back from it, same as the
    `partition_lon_field`/`partition_lat_field` split. An overridden row's
    `plate_begin_age` is left `None` (always valid) rather than keeping whatever
    partitioning happened to find, since that age describes a different polygon's
    own begin age, not the supplied plate's.

    An explicit `0` is a real override, not "no override" -- a caller who wants a
    point held at its literal, present-day position at every time (a fixed marker
    in the mantle/absolute frame, say) asks for the anchor plate the same way any
    other override is asked for. That is a different claim than partitioning
    falling through to plate 0 on its own, which means "not assigned, don't trust
    this outside the present" -- so an override, even to 0, also sets
    `plate_forced=True` on the record, the signal a renderer's own plate-0
    suspicion (age-gating it to the present only) needs to stand down for.
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

    polygons_source = model.static_polygons if polygons == "static" else model.continent_polygons

    # Always partition `features` itself (lon_field/lat_field) first: this is what sets
    # each returned feature's own `reconstruction_plate_id`, the property 'trajectory'
    # transport's `pygplates.reconstruct()` actually reconstructs from later, in
    # `_trajectory_block`. That must reflect the DRAWN position regardless of any split
    # below, so these are the feature objects that go into `ordered` no matter what --
    # only the plate_id/plate_begin_age used for the RECORD can be overridden.
    display_info, unassigned = _partition_plate_ids(features, model, polygons_source)
    plate_ids = display_info

    if partition_lon_field is not None or partition_lat_field is not None:
        # A second, throwaway partition against the split fields, purely to recover the
        # record's plate_id/plate_begin_age -- its own feature objects are discarded once
        # read, never mixed into `ordered`. See the docstring for why.
        partition_features = _partition_features(
            gdf, partition_lon_field or lon_field, partition_lat_field or lat_field)
        plate_ids, unassigned = _partition_plate_ids(
            partition_features, model, polygons_source)

    # Restore input order using the index carried on each feature, so the metadata list
    # and the geometry list stay aligned.
    ordered = [None] * len(meta)
    for index, (feature, _, _) in display_info.items():
        _, plate_id, plate_begin_age = plate_ids[index]
        record = dict(meta[index])
        record["plate_id"] = plate_id
        record["plate_begin_age"] = plate_begin_age
        ordered[index] = (feature, record)

    if plate_id_field is not None:
        for index, (_, row) in enumerate(gdf.iterrows()):
            given = row.get(plate_id_field)
            if given is None or (isinstance(given, float) and math.isnan(given)):
                continue
            given = int(given)
            feature, record = ordered[index]
            was_unassigned = record["plate_id"] == 0
            record = dict(record)
            record["plate_id"] = given
            record["plate_begin_age"] = None
            record["plate_forced"] = True
            ordered[index] = (feature, record)
            if was_unassigned and given != 0:
                unassigned -= 1
            elif not was_unassigned and given == 0:
                unassigned += 1

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


def rotation_block(model, plate_ids, times, anchor_plate=0):
    """Public name for the per-plate rotation series `build_points` embeds.

    Exposed because a consumer summarising the SAME points a different way (binning
    them into cells, say) must reconstruct them with the identical rotations, not an
    independent `pygplates.reconstruct` call -- otherwise the summary describes points
    that are not where the map draws them, with nothing on screen to show it.
    """
    return _rotation_block(model, plate_ids, times, anchor_plate)


def _rotation_block(model, plate_ids, times, anchor_plate):
    """[pole_lon, pole_lat, angle_deg] per plate per time.

    One entry per plate rather than per point: the whole reason this transport wins on
    large datasets is that a thousand deposits on one craton share one rotation.

    Past the oldest time a plate's rotation sequence covers, `get_rotation` returns
    the IDENTITY, not the oldest pole -- a point shown there snaps back to its
    present-day position (Torsvik & Cocks 2017's Pacific plate 901 ends at 150 Ma:
    Shatsky Rise jumped ~60 deg mid-eruption at 151 Ma). So a trailing run of
    identities after real rotations is filled with the plate's oldest defined
    rotation: held where the model last put it. Needs ascending `times`.
    """
    rotation_model = model.rotation_model
    ascending = all(a < b for a, b in zip(times, list(times)[1:]))
    block = {}
    for plate_id in sorted(plate_ids):
        series = []
        for time in times:
            rotation = rotation_model.get_rotation(
                float(time), int(plate_id), anchor_plate_id=anchor_plate)
            series.append(finite_rotation_to_pole_angle(rotation))
        if ascending and int(plate_id) != int(anchor_plate):
            defined = [i for i, r in enumerate(series) if r[2] != 0.0]
            if defined and defined[-1] < len(series) - 1:
                last = defined[-1]
                series[last + 1:] = [series[last]] * (len(series) - last - 1)
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
