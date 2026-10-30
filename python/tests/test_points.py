"""
Tests for petrify.points, focused on points_from_dataframe()'s plate assignment and
build_points()'s two transports.

No real gprm reconstruction model is used -- everything here is a minimal, deterministic
stand-in built directly with pygplates, so the tests run offline and fast. Two square
static polygons on different plates give partitioning something real to decide between,
and a two-plate rotation model with genuinely different motions is what makes it possible
to tell "reconstructed via plate 101" apart from "reconstructed via plate 202" by the
numbers alone, not just by which id got stamped on a record.
"""

import math

import pandas as pd
import pygplates
import pytest

from petrify.points import build_points, points_from_dataframe

PLATE_A = 101   # lon 0-10, lat 0-10
PLATE_B = 202   # lon 20-30, lat 0-10


def _square(plate_id, lon0, lon1, lat0, lat1, begin):
    poly = pygplates.PolygonOnSphere([
        (lat0, lon0), (lat0, lon1), (lat1, lon1), (lat1, lon0)])
    feature = pygplates.Feature()
    feature.set_geometry(poly)
    feature.set_reconstruction_plate_id(plate_id)
    feature.set_valid_time(begin, 0.0)
    return feature


def _rotation_feature(moving_plate_id, samples):
    """`samples`: [(time, pole_lat, pole_lon, angle_deg), ...]."""
    gpml_samples = [
        pygplates.GpmlTimeSample(
            pygplates.GpmlFiniteRotation(
                pygplates.FiniteRotation(
                    pygplates.PointOnSphere(pole_lat, pole_lon), math.radians(angle_deg))),
            time)
        for time, pole_lat, pole_lon, angle_deg in samples
    ]
    return pygplates.Feature.create_total_reconstruction_sequence(
        0, moving_plate_id, pygplates.GpmlIrregularSampling(gpml_samples))


class FakeModel:
    """The two attributes points_from_dataframe()/build_points() actually touch."""

    name = "FakeModel_v0"

    def __init__(self, static_polygons, rotation_features):
        self.static_polygons = static_polygons
        self.continent_polygons = static_polygons
        self.rotation_model = pygplates.RotationModel(rotation_features)


@pytest.fixture
def model():
    """Plate A never moves; plate B rotates 30 deg about the north pole by 100 Ma --
    different enough that reconstructing through one plate vs. the other at 100 Ma gives
    clearly different coordinates, not just a different id in a record."""
    polygons = [
        _square(PLATE_A, 0, 10, 0, 10, begin=500.0),
        _square(PLATE_B, 20, 30, 0, 10, begin=300.0),
    ]
    rotations = [
        _rotation_feature(PLATE_A, [(0, 0, 0, 0), (100, 90, 0, 0)]),
        _rotation_feature(PLATE_B, [(0, 0, 0, 0), (100, 0, 0, 30)]),
    ]
    return FakeModel(polygons, rotations)


def df(rows):
    return pd.DataFrame(rows)


# ---- backward compatibility: no split configured -----------------------------------

def test_default_partition_assigns_by_lon_lat_field(model):
    gdf = df([
        {"Longitude": 5, "Latitude": 5, "Age": 10},     # inside A
        {"Longitude": 25, "Latitude": 5, "Age": 20},    # inside B
    ])
    records, unassigned = points_from_dataframe(gdf, model)
    assert unassigned == 0

    by_index = {int(f.get_name()): r for f, r in records}
    assert by_index[0]["plate_id"] == PLATE_A
    assert by_index[0]["plate_begin_age"] == 500.0
    assert by_index[1]["plate_id"] == PLATE_B
    assert by_index[1]["plate_begin_age"] == 300.0


def test_point_outside_every_polygon_gets_plate_zero(model):
    gdf = df([{"Longitude": 80, "Latitude": 80, "Age": 1}])
    records, unassigned = points_from_dataframe(gdf, model)
    assert unassigned == 1
    _, record = records[0]
    assert record["plate_id"] == 0
    # "distant past" would serialise as Infinity -- must come back None, not inf/nan.
    assert record["plate_begin_age"] is None


def test_feature_geometry_matches_lon_lat_field_regardless_of_split(model):
    """The geometry list is built from lon_field/lat_field -- this must stay true whether
    or not a partition split is requested."""
    gdf = df([{"Longitude": 25, "Latitude": 5, "Age": 1,
               "SiteLon": 5, "SiteLat": 5}])

    for kwargs in ({}, {"partition_lon_field": "SiteLon", "partition_lat_field": "SiteLat"}):
        records, _ = points_from_dataframe(gdf, model, **kwargs)
        feature, _ = records[0]
        lat, lon = feature.get_geometry().to_lat_lon()
        assert (round(lon), round(lat)) == (25, 5)


# ---- partition_lon_field / partition_lat_field --------------------------------------

def test_partition_split_assigns_plate_from_partition_fields_not_drawn_position(model):
    # Drawn at a point inside plate B's polygon, but the "site" sits inside plate A's.
    gdf = df([{"Longitude": 25, "Latitude": 5, "Age": 1,
               "SiteLon": 5, "SiteLat": 5}])

    records, unassigned = points_from_dataframe(
        gdf, model, partition_lon_field="SiteLon", partition_lat_field="SiteLat")
    assert unassigned == 0
    _, record = records[0]
    assert record["plate_id"] == PLATE_A
    assert record["plate_begin_age"] == 500.0


def test_partition_split_leaves_the_display_feature_untouched(model):
    """The crux guarantee: even though the RECORD's plate_id comes from the split fields,
    the geometry FEATURE's own reconstruction_plate_id -- what 'trajectory' transport's
    pygplates.reconstruct() actually uses -- must still be the one lon_field/lat_field
    would have produced on their own. Otherwise 'trajectory' would silently reconstruct
    the partition anchor rather than the drawn position."""
    gdf = df([{"Longitude": 25, "Latitude": 5, "Age": 1,
               "SiteLon": 5, "SiteLat": 5}])

    records, _ = points_from_dataframe(
        gdf, model, partition_lon_field="SiteLon", partition_lat_field="SiteLat")
    feature, record = records[0]

    assert record["plate_id"] == PLATE_A                       # from the split (site)
    assert feature.get_reconstruction_plate_id() == PLATE_B    # from the drawn position


def test_partition_split_one_field_only_defaults_the_other_to_the_display_field(model):
    # Only latitude differs between "drawn" and "site" here; longitude is shared, and
    # partition_lon_field is left None -- it should fall back to lon_field, not to 0/None.
    gdf = df([{"Longitude": 5, "Latitude": 5, "Age": 1, "SiteLat": 25}])

    records, _ = points_from_dataframe(gdf, model, partition_lat_field="SiteLat")
    _, record = records[0]
    # partition point is (lon=5 [from lon_field], lat=25) -- outside both squares.
    assert record["plate_id"] == 0


def test_no_split_is_pixel_for_pixel_identical_to_before(model):
    """Same records, whether the two new kwargs are omitted or explicitly passed their
    'same as lon_field/lat_field' default -- the documented backward-compatible case."""
    gdf = df([
        {"Longitude": 5, "Latitude": 5, "Age": 10},
        {"Longitude": 25, "Latitude": 5, "Age": 20},
        {"Longitude": 80, "Latitude": 80, "Age": 1},
    ])
    baseline, baseline_unassigned = points_from_dataframe(gdf, model)
    explicit, explicit_unassigned = points_from_dataframe(
        gdf, model, partition_lon_field=None, partition_lat_field=None)

    assert baseline_unassigned == explicit_unassigned
    for (fb, rb), (fe, re) in zip(baseline, explicit):
        assert rb == re
        assert fb.get_reconstruction_plate_id() == fe.get_reconstruction_plate_id()


# ---- end to end: build_points()'s two transports stay honest with a split -----------

def test_trajectory_transport_reconstructs_the_drawn_point_not_the_partition_anchor(model):
    """A point drawn inside plate B, whose site sits in (non-moving) plate A. If
    'trajectory' reconstructed through the split's plate (A), the point would not have
    moved at all by 100 Ma. It must move as plate B does instead, because that -- not the
    partition anchor -- is the plate the DRAWN geometry belongs to."""
    gdf = df([{"Longitude": 25, "Latitude": 5, "Age": 1,
               "SiteLon": 5, "SiteLat": 5}])

    records, _ = points_from_dataframe(
        gdf, model, partition_lon_field="SiteLon", partition_lat_field="SiteLat")

    payload = build_points(model, records, times=[0, 100], transport="trajectory")
    lon0, lat0 = payload["frames"]["0"][0][0], payload["frames"]["0"][1][0]
    lon100, lat100 = payload["frames"]["100"][0][0], payload["frames"]["100"][1][0]

    assert (lon0, lat0) == (25.0, 5.0)
    # Plate B's own 30 degree rotation moved it; plate A (the site's plate) would not have.
    assert (lon100, lat100) != (25.0, 5.0)


# ---- duplicate positions: partition once per position, broadcast the result --------

class TestDuplicatePositionsPartitionOnce:
    """`_partition_plate_ids` partitions one representative per DISTINCT (lon, lat) and
    broadcasts its result to every feature sharing that position, instead of
    partitioning every feature individually (measured 2026-09-24: partition_into_plates()
    was the dominant cost of a real 2M-record PBDB run, and real point data clusters
    heavily at identical coordinates). Must be provably EXACT, not just faster."""

    def test_duplicate_positions_get_identical_plate_assignment(self, model):
        gdf = df([
            {"Longitude": 5, "Latitude": 5, "Age": 10},   # inside A
            {"Longitude": 5, "Latitude": 5, "Age": 20},   # same position, different age
            {"Longitude": 5, "Latitude": 5, "Age": 30},   # same position again
        ])
        records, unassigned = points_from_dataframe(gdf, model)
        assert unassigned == 0
        by_index = {int(f.get_name()): (f, r) for f, r in records}
        for i in range(3):
            feature, record = by_index[i]
            assert record["plate_id"] == PLATE_A
            assert record["plate_begin_age"] == 500.0
            # 'trajectory' transport reads this off the feature directly, not the
            # returned metadata -- see _partition_plate_ids' own docstring.
            assert feature.get_reconstruction_plate_id() == PLATE_A

    def test_partition_into_plates_is_called_once_per_distinct_position(self, model, monkeypatch):
        """The actual point of the optimisation: assert the CALL, not just the result,
        so a future change that accidentally re-partitions every duplicate individually
        fails loudly here instead of only showing up as a slow prep run."""
        seen_sizes = []
        real = pygplates.partition_into_plates

        def spy(*args, **kwargs):
            # partition_into_plates(polygons, rotation_model, features, ...)
            seen_sizes.append(len(args[2]))
            return real(*args, **kwargs)

        monkeypatch.setattr(pygplates, "partition_into_plates", spy)

        gdf = df([
            {"Longitude": 5, "Latitude": 5, "Age": 10},    # inside A, position 1
            {"Longitude": 5, "Latitude": 5, "Age": 20},    # inside A, position 1 (dup)
            {"Longitude": 25, "Latitude": 5, "Age": 30},   # inside B, position 2
        ])
        points_from_dataframe(gdf, model)
        assert seen_sizes == [2]  # 2 distinct positions, not 3 records

    def test_duplicate_positions_match_a_fully_distinct_baseline(self, model):
        """Same three positions, once with a duplicate and once all-distinct (a fourth,
        harmless point standing in for what the duplicate would have gotten on its own)
        -- the duplicate's own record must be pixel-for-pixel what individual
        partitioning would have produced."""
        with_dup = df([
            {"Longitude": 5, "Latitude": 5, "Age": 10},
            {"Longitude": 5, "Latitude": 5, "Age": 20},
        ])
        all_distinct = df([
            {"Longitude": 5, "Latitude": 5, "Age": 10},
        ])
        dup_records, dup_unassigned = points_from_dataframe(with_dup, model)
        distinct_records, distinct_unassigned = points_from_dataframe(all_distinct, model)

        assert dup_unassigned == distinct_unassigned == 0
        _, dup_record_0 = dup_records[0]
        _, dup_record_1 = dup_records[1]
        _, distinct_record = distinct_records[0]
        # Both duplicates match the independently-computed baseline (Age differs by
        # construction, so compare everything else).
        for record in (dup_record_0, dup_record_1):
            assert record["plate_id"] == distinct_record["plate_id"]
            assert record["plate_begin_age"] == distinct_record["plate_begin_age"]

    def test_trajectory_transport_reconstructs_every_duplicate_correctly(self, model):
        """The broadcast writes reconstruction_plate_id/valid_time onto each duplicate
        feature object directly -- confirm 'trajectory' transport, which reads those
        properties straight off the feature (not off this module's metadata), still
        reconstructs every duplicate through the right plate."""
        gdf = df([
            {"Longitude": 25, "Latitude": 5, "Age": 1},   # inside B, position 1
            {"Longitude": 25, "Latitude": 5, "Age": 2},   # same position (dup)
        ])
        records, _ = points_from_dataframe(gdf, model)
        payload = build_points(model, records, times=[0, 100], transport="trajectory")

        for i in (0, 1):
            lon0, lat0 = payload["frames"]["0"][0][i], payload["frames"]["0"][1][i]
            lon100, lat100 = payload["frames"]["100"][0][i], payload["frames"]["100"][1][i]
            assert (lon0, lat0) == (25.0, 5.0)
            # Plate B's 30 degree rotation must have moved BOTH duplicates identically.
            assert (lon100, lat100) != (25.0, 5.0)


def test_rotations_transport_groups_points_by_the_splits_plate_id(model):
    """'rotations' transport ships one rotation series per plate keyed off record
    plate_id -- which, with a split, is the partition field's plate. The payload's
    rotation block must be keyed the same way, or a consumer would look up a series that
    was never written."""
    gdf = df([{"Longitude": 25, "Latitude": 5, "Age": 1,
               "SiteLon": 5, "SiteLat": 5}])

    records, _ = points_from_dataframe(
        gdf, model, partition_lon_field="SiteLon", partition_lat_field="SiteLat")

    payload = build_points(model, records, times=[0, 100], transport="rotations")
    assert payload["points"][0]["plate_id"] == PLATE_A
    assert str(PLATE_A) in payload["rotations"]
