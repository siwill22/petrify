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

SUPPLIED_NOTE = (
    "Coordinates were supplied by the caller, one lon/lat pair per point per time, and "
    "are drawn as given -- no plate was assigned and nothing was rotated. null means the "
    "source had no position for that point at that time (it did not exist yet, or no "
    "longer does), so the point is not drawn then."
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
                          partition_lon_field=None, partition_lat_field=None):
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
    that silently does not fail: `pygplates` holds the oldest defined rotation pole fixed
    rather than erroring, so an over-old point just stops moving instead of reporting
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


# ---- tracks: positions already known at each time --------------------------------------

TRACK_NOTE = (
    "Coordinates were supplied by the caller, one lon/lat pair per track per time, already "
    "in the reconstruction frame -- nothing here was reconstructed on a plate. For things "
    "fixed to the frame rather than to any plate: a pole, a hotspot, a plume. null means "
    "the track has no position at that time, and the renderer draws nothing there."
)

TRAIL_NOTE = (
    "One fixed point per track per time, in input order. `segment` names an unbroken run "
    "of a track: it changes wherever the track has no position, so a line drawn through "
    "each segment breaks there instead of bridging the gap."
)


def time_key(time):
    """A time as the frame key the browser will look it up by.

    PointLayer reads `frames[String(t)]`, and JavaScript writes a whole number without a
    decimal point: Python's '5.0' would never be found.
    """
    time = float(time)
    return str(int(time)) if time.is_integer() else repr(time)


def _track_times(df, time_field):
    """The distinct times, ascending, checked to be evenly spaced.

    PointLayer finds the samples bracketing a time by arithmetic rather than search, so
    an uneven series would interpolate between the wrong samples without any error.
    """
    times = sorted(float(t) for t in df[time_field].dropna().unique())
    if len(times) > 2:
        steps = np.diff(times)
        if not np.allclose(steps, steps[0], rtol=1e-6):
            raise ValueError("track times must be evenly spaced, got steps {}".format(
                sorted(set(np.round(steps, 6)))))
    return times


def _number(time):
    time = float(time)
    return int(time) if time.is_integer() else time


def _position(row, lon_field, lat_field, decimals):
    lon, lat = row[lon_field], row[lat_field]
    if lon is None or lat is None or _is_nan(lon) or _is_nan(lat):
        return None, None
    return round(float(lon), decimals), round(float(lat), decimals)


def _is_nan(value):
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def build_tracks(df, track_field, time_field, lon_field="Longitude", lat_field="Latitude",
                 fields=(), categories=None, meta=None, decimals=3, model_name=None):
    """One moving point per track, as a 'trajectory' payload, from positions already known.

    @param df          one row per (track, time), positions in the reconstruction frame
    @param fields      [(payload key, column)] carried on each point, taken from the
                       track's first row -- so they should be properties of the track
    """
    times = _track_times(df, time_field)
    index = {t: k for k, t in enumerate(times)}
    tracks = list(dict.fromkeys(df[track_field]))
    slot = {track: i for i, track in enumerate(tracks)}

    lons = [[None] * len(tracks) for _ in times]
    lats = [[None] * len(tracks) for _ in times]
    for _, row in df.iterrows():
        lon, lat = _position(row, lon_field, lat_field, decimals)
        k, i = index[float(row[time_field])], slot[row[track_field]]
        lons[k][i], lats[k][i] = lon, lat

    points = []
    for track in tracks:
        rows = df[df[track_field] == track]
        first = rows.iloc[0]
        start = next((p for p in (_position(r, lon_field, lat_field, decimals)
                                  for _, r in rows.iterrows()) if p[0] is not None),
                     (0.0, 0.0))
        record = {key: _clean(first[column]) for key, column in fields}
        record.update({"lon": start[0], "lat": start[1]})
        points.append(record)

    return {
        "model": model_name or "unknown",
        "transport": "trajectory",
        "times": [_number(t) for t in times],
        "categories": categories or {},
        "points": points,
        "meta": meta or {},
        "note": TRACK_NOTE,
        "frames": {time_key(t): [lons[k], lats[k]] for k, t in enumerate(times)},
    }


def build_track_trail(df, track_field, time_field, lon_field="Longitude",
                      lat_field="Latitude", fields=(), categories=None, meta=None,
                      decimals=3, model_name=None):
    """Every (track, time) row as a fixed point, with `age` = its time and a `segment` key.

    Drawn with `connectLive` and `connectBy: 'segment'`, this is each track's whole path.
    Rows without a position are left out, and start a new segment.
    """
    points, lons, lats = [], [], []
    for track in dict.fromkeys(df[track_field]):
        rows = df[df[track_field] == track].sort_values(time_field)
        run = 0
        gap = False
        for _, row in rows.iterrows():
            lon, lat = _position(row, lon_field, lat_field, decimals)
            if lon is None:
                gap = True
                continue
            if gap:
                run += 1
                gap = False
            record = {key: _clean(row[column]) for key, column in fields}
            record.update({"lon": lon, "lat": lat, "age": _number(row[time_field]),
                           "segment": "{}#{}".format(_clean(track), run)})
            points.append(record)
            lons.append(lon)
            lats.append(lat)

    # Fixed points need no more than two identical frames: the layer clamps any time
    # outside them to the nearest, and slerping between equal positions is a no-op.
    times = _track_times(df, time_field)
    first, last = times[0], (times[-1] if len(times) > 1 else times[0] + 1)
    return {
        "model": model_name or "unknown",
        "transport": "trajectory",
        "times": [_number(first), _number(last)],
        "categories": categories or {},
        "points": points,
        "meta": meta or {},
        "note": TRAIL_NOTE,
        "frames": {time_key(first): [lons, lats], time_key(last): [list(lons), list(lats)]},
    }


def supplied_trajectory(frame, positions, key, times, age_field=None, fields=(),
                        categories=None, meta=None, model_name=None, decimals=2):
    """A 'trajectory' payload from positions the caller already has.

    For points whose motion was not a rigid plate rotation -- tracers advected through a
    model, say, or anything a simulation moved itself. Running those back through
    `points_from_dataframe` would assign each one a plate and rotate it, replacing the
    path the source computed with a different one, silently. So nothing is assigned
    here and nothing is rotated: the payload is the 'trajectory' shape the renderer
    already reads, filled straight from the caller's table.

    @param frame      one row per point, carrying `key` and the metadata columns
    @param positions  long table, one row per (point, time): `key`, `time`, `lon`, `lat`
    @param key        the column joining the two
    @param times      the view's times in Ma, ascending. A position whose time is not
                      one of these is ignored; a time with no row for a point is null.

    Records carry no `plate_id`, which the renderer reads as "no plate window to
    enforce" -- the positions themselves say when a point exists.
    """
    keys = list(frame[key])
    index = {k: i for i, k in enumerate(keys)}
    if len(index) != len(keys):
        raise ValueError("key column {!r} is not unique".format(key))

    pos = positions[positions[key].isin(index)]
    rows = pos[key].map(index).to_numpy()
    when = np.round(pos["time"].to_numpy(dtype=float), 6)
    lons = np.round(pos["lon"].to_numpy(dtype=float), decimals)
    lats = np.round(pos["lat"].to_numpy(dtype=float), decimals)

    n = len(keys)
    youngest = np.full(n, np.inf)
    home = np.full((n, 2), np.nan)
    frames = {}
    for time in times:
        sel = when == round(float(time), 6)
        lon_t = [None] * n
        lat_t = [None] * n
        for i, lo, la in zip(rows[sel], lons[sel], lats[sel]):
            lon_t[i] = float(lo)
            lat_t[i] = float(la)
            if time < youngest[i]:
                youngest[i] = time
                home[i] = (lo, la)
        frames[str(time)] = [lon_t, lat_t]

    records = []
    for i, (_, row) in enumerate(frame.iterrows()):
        # `lon`/`lat` is where the renderer starts from before the first setTime(); the
        # youngest position is the closest thing this data has to "present day".
        record = {"lon": _clean(home[i, 0]), "lat": _clean(home[i, 1]),
                  "age": _clean(row[age_field]) if age_field else None}
        for name, column in fields:
            record[name] = _clean(row[column])
        records.append(record)

    return {
        "model": model_name or "unknown",
        "transport": "trajectory",
        "anchor_plate_id": 0,
        "times": list(times),
        "categories": categories or {},
        "points": records,
        "meta": meta or {},
        "note": SUPPLIED_NOTE,
        "frames": frames,
    }
