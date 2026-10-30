"""
Tests for petrify.aggregate: HealpixGrid alongside EqualAreaGrid, and build_density's
von Mises-Fisher KDE against build_aggregates' discrete counts.

Synthetic, offline, no gprm/pygplates needed -- a null rotation series (angle 0 at every
sample) exercises the reconstruction plumbing without a real plate model, the same choice
test_points.py makes for its own fixtures.
"""

import math

import numpy as np
import pytest

from petrify.aggregate import (
    EqualAreaGrid,
    HealpixGrid,
    build_aggregates,
    build_density,
    grid_geometry,
    verify_equal_area,
)


def _static_rotations(plate_id, times):
    """A rotation series that never actually rotates -- angle 0 at every sample, so a
    record's present-day lon/lat IS its position at every time in `times`."""
    return {str(plate_id): [[0.0, 90.0, 0.0] for _ in times]}


class TestHealpixGrid:
    def test_equal_area(self):
        grid = HealpixGrid(nside=16)
        expected, max_dev = verify_equal_area(grid)
        # verify_equal_area's own docstring bound (3/sqrt(expected)) is a per-cell
        # threshold; taking a MAX over ~3000 cells makes an occasional excursion past it
        # expected sampling noise, not a real deviation -- the same module's own
        # EqualAreaGrid() marginally exceeds 3/sqrt(expected) at this cell count too.
        # 6/sqrt(expected) accounts for that without weakening the check HEALPix cells
        # are actually meant to satisfy (exact equal area, by construction).
        assert max_dev < 6 / math.sqrt(expected)

    def test_rejects_non_power_of_two(self):
        with pytest.raises(ValueError):
            HealpixGrid(nside=12)

    def test_cell_of_matches_centres_roundtrip(self):
        grid = HealpixGrid(nside=8)
        centres = np.array(grid.centres())
        cells = grid.cell_of(centres[:, 0], centres[:, 1])
        # Every centre's own cell_of() must map back to itself, or a glyph drawn at "its"
        # centre would not be the cell that was actually binned into.
        assert np.array_equal(cells, np.arange(grid.n_cells))

    def test_bounds_are_four_corners_around_the_centre(self):
        grid = HealpixGrid(nside=8)
        bounds = grid.bounds()
        centres = grid.centres()
        assert len(bounds) == grid.n_cells
        for cell in (0, grid.n_cells // 2, grid.n_cells - 1):
            corners = bounds[cell]
            assert len(corners) == 4
            clon, clat = centres[cell]
            # Every corner should be within a couple of cell-widths of the centre --
            # a loose sanity bound, not a re-derivation of hp.boundaries' own maths.
            cell_width_deg = 90.0 / grid.nside
            for lon, lat in corners:
                assert abs(lat - clat) < 3 * cell_width_deg
                dlon = ((lon - clon + 180) % 360) - 180
                assert abs(dlon) < 6 * cell_width_deg or abs(clat) > 80

    def test_bounds_ship_in_aggregates_and_density_payloads(self):
        grid = HealpixGrid(nside=8)
        agg = build_aggregates(
            records=[], rotations={}, times=[0], grid=grid,
            groupings={"g": {"category_order": ["a"], "values": []}},
        )
        assert agg["cell_bounds"] is not None
        assert len(agg["cell_bounds"]) == grid.n_cells

        dens = build_density(
            [], {}, times=[0.0], grid=grid,
            groupings={"g": {"category_order": ["a"], "values": []}},
        )
        assert dens["cell_bounds"] is not None
        assert len(dens["cell_bounds"]) == grid.n_cells

    def test_equalarea_grid_has_no_bounds_in_payload(self):
        out = build_aggregates(
            records=[], rotations={}, times=[0], grid=EqualAreaGrid(rings=4),
            groupings={"g": {"category_order": ["a"], "values": []}},
        )
        assert out["cell_bounds"] is None

    def test_describe(self):
        grid = HealpixGrid(nside=32)
        assert grid.describe() == {"nside": 32, "order": "ring"}
        assert grid.n_cells == 12 * 32 * 32


class TestGeometryRef:
    """`build_density(..., geometry_ref=...)` -- a shared grid_geometry() file referenced
    by many datasets/bandwidths instead of each embedding its own cell_centres/
    cell_bounds copy (measured 2026-09-24: ~304MB of a 437MB nside=512 density export was
    exactly this, duplicated identically across every dataset at that resolution)."""

    def test_grid_geometry_matches_the_embedded_shape(self):
        grid = HealpixGrid(nside=8)
        geom = grid_geometry(grid)
        assert geom["kind"] == grid.kind
        assert geom["cells"] == grid.n_cells
        assert geom["cell_centres"] == grid.centres()
        assert geom["cell_bounds"] == grid.bounds()

    def test_geometry_ref_omits_coordinates_from_the_payload(self):
        grid = HealpixGrid(nside=8)
        dens = build_density(
            [], {}, times=[0.0], grid=grid,
            groupings={"g": {"category_order": ["a"], "values": []}},
            geometry_ref="../../_grid/healpix_n8.json.gz",
        )
        assert "cell_centres" not in dens
        assert "cell_bounds" not in dens
        assert dens["grid"]["geometry_ref"] == "../../_grid/healpix_n8.json.gz"

    def test_no_geometry_ref_keeps_the_old_self_contained_shape(self):
        grid = HealpixGrid(nside=8)
        dens = build_density(
            [], {}, times=[0.0], grid=grid,
            groupings={"g": {"category_order": ["a"], "values": []}},
        )
        assert dens["cell_centres"] == grid.centres()
        assert dens["cell_bounds"] == grid.bounds()
        assert "geometry_ref" not in dens["grid"]


def _unpack_frames_binary(blob):
    """Test-only mirror of js/aggregate.js's binary reader -- decodes
    _pack_frames_binary()'s layout back into (cells, density) numpy arrays per frame,
    in time order, so a test can compare it against the JSON shape directly."""
    import struct
    assert blob[:4] == b"DNSF"
    version, n_frames = struct.unpack_from("<II", blob, 4)
    assert version == 1
    offset = 12
    frames = []
    for _ in range(n_frames):
        (count,) = struct.unpack_from("<I", blob, offset)
        offset += 4
        cells = np.frombuffer(blob, dtype="<u4", count=count, offset=offset)
        offset += count * 4
        density = np.frombuffer(blob, dtype="<f4", count=count, offset=offset)
        offset += count * 4
        frames.append((cells, density))
    return frames


class TestFramesRef:
    """`build_density(..., frames_ref=...)` -- a grouping's per-time frames packed into
    a flat binary blob instead of embedded JSON arrays (measured 2026-09-24: extending
    one dataset's time range 3x undid the geometry_ref win alone -- 494MB from frame
    count growth, not duplicated geometry)."""

    def _dataset(self):
        rng = np.random.default_rng(0)
        n = 50
        lons = rng.uniform(-180, 180, n)
        lats = np.degrees(np.arcsin(rng.uniform(-1, 1, n)))
        records = [{"lon": float(lo), "lat": float(la), "plate_id": 1} for lo, la in zip(lons, lats)]
        rotations = _static_rotations(1, [0.0, 5.0])
        grouping = {"g": {"category_order": ["a"], "values": ["a"] * n}}
        return records, rotations, grouping

    def test_frames_ref_removes_frames_and_adds_frames_bin(self):
        records, rotations, grouping = self._dataset()
        grid = HealpixGrid(nside=8)
        dens = build_density(
            records, rotations, times=[0.0, 5.0], grid=grid, groupings=grouping,
            lifespan="always", frames_ref="density_bw1.bin.gz",
        )
        g = dens["groupings"]["g"]
        assert "frames" not in g
        assert isinstance(g["frames_bin"], bytes)
        assert g["frames_ref"] == "density_bw1.bin.gz"

    def test_no_frames_ref_keeps_the_old_embedded_json_shape(self):
        records, rotations, grouping = self._dataset()
        grid = HealpixGrid(nside=8)
        dens = build_density(
            records, rotations, times=[0.0, 5.0], grid=grid, groupings=grouping,
            lifespan="always",
        )
        g = dens["groupings"]["g"]
        assert "frames_bin" not in g
        assert "frames_ref" not in g
        assert isinstance(g["frames"], dict)

    def test_multiple_groupings_get_distinct_frames_ref_names(self):
        """panama declares THREE groupings (origin/basin/realm) sharing one
        build_density() call -- a single shared frames_ref name would have every
        grouping's JSON block point at whichever one's bytes got written last (a real
        bug, caught by running this against panama's actual config, not by a
        single-grouping fixture)."""
        records, rotations, _ = self._dataset()
        grid = HealpixGrid(nside=8)
        groupings = {
            "origin": {"category_order": ["a"], "values": ["a"] * len(records)},
            "realm": {"category_order": ["a"], "values": ["a"] * len(records)},
        }
        dens = build_density(
            # A fractional bandwidth (real filenames are "density_bw0.5.bin.gz") --
            # naively splitting on the first '.' would split INSIDE "0.5" itself,
            # exactly the bug this test caught against panama's real filenames.
            records, rotations, times=[0.0, 5.0], grid=grid, groupings=groupings,
            lifespan="always", frames_ref="density_bw0.5.bin.gz",
        )
        refs = {name: g["frames_ref"] for name, g in dens["groupings"].items()}
        assert refs["origin"] == "density_bw0.5.origin.bin.gz"
        assert refs["realm"] == "density_bw0.5.realm.bin.gz"
        assert refs["origin"] != refs["realm"]
        # And each grouping's OWN bytes decode back to what it was built from --
        # not just distinctly-NAMED, but actually independent.
        origin_packed = _unpack_frames_binary(dens["groupings"]["origin"]["frames_bin"])
        realm_packed = _unpack_frames_binary(dens["groupings"]["realm"]["frames_bin"])
        for (oc, od), (rc, rd) in zip(origin_packed, realm_packed):
            assert list(oc) == list(rc)
            assert np.allclose(od, rd)

    def test_frames_bin_round_trips_against_the_json_shape(self):
        records, rotations, grouping = self._dataset()
        grid = HealpixGrid(nside=8)
        json_form = build_density(
            records, rotations, times=[0.0, 5.0], grid=grid, groupings=grouping,
            lifespan="always",
        )
        bin_form = build_density(
            records, rotations, times=[0.0, 5.0], grid=grid, groupings=grouping,
            lifespan="always", frames_ref="x.bin.gz",
        )
        packed = _unpack_frames_binary(bin_form["groupings"]["g"]["frames_bin"])
        json_frames = list(json_form["groupings"]["g"]["frames"].values())
        assert len(packed) == len(json_frames) == 2
        for (cells, density), frame in zip(packed, json_frames):
            assert list(cells) == frame["cells"]
            assert np.allclose(density, frame["density"], atol=1e-4)


class TestGridMetaGeneralised:
    """build_aggregates()'s grid metadata must carry either grid's own construction
    params -- the bug this fixes let EqualAreaGrid's field names leak into the payload
    regardless of which grid was actually passed."""

    def _empty(self, grid):
        return build_aggregates(
            records=[], rotations={}, times=[0], grid=grid,
            groupings={"g": {"category_order": ["a"], "values": []}},
        )

    def test_equalarea_grid_block(self):
        out = self._empty(EqualAreaGrid(rings=10))
        assert out["grid"]["kind"] == "equal-area-rings"
        assert out["grid"]["rings"] == 10
        assert out["grid"]["lon_cells"] == 20
        assert "nside" not in out["grid"]

    def test_healpix_grid_block(self):
        out = self._empty(HealpixGrid(nside=8))
        assert out["grid"]["kind"] == "healpix-ring"
        assert out["grid"]["nside"] == 8
        assert out["grid"]["order"] == "ring"
        assert "rings" not in out["grid"]


class TestBuildDensity:
    def test_single_point_mass_conservation(self):
        """A single point's kernel must integrate to ~1 steradian's worth of density
        spread across the cells it reaches -- the KDE gives each point exactly one
        "point" of mass, wherever the kernel actually distributes it."""
        grid = HealpixGrid(nside=64)
        records = [{"lon": 40.0, "lat": -10.0, "plate_id": 1}]
        rotations = _static_rotations(1, [0.0])
        out = build_density(
            records, rotations, times=[0.0], grid=grid,
            groupings={"g": {"category_order": ["a"], "values": ["a"]}},
            bandwidth_deg=6.0, lifespan="always", eps=0.0,
        )
        frame = out["groupings"]["g"]["frames"]["0"]
        cell_area = out["grid"]["cell_area_sr"]
        mass = sum(d * cell_area for d in frame["density"])
        assert mass == pytest.approx(1.0, rel=0.03)

    def test_peak_is_near_the_point(self):
        grid = HealpixGrid(nside=32)
        lon0, lat0 = 40.0, -10.0
        records = [{"lon": lon0, "lat": lat0, "plate_id": 1}]
        rotations = _static_rotations(1, [0.0])
        out = build_density(
            records, rotations, times=[0.0], grid=grid,
            groupings={"g": {"category_order": ["a"], "values": ["a"]}},
            bandwidth_deg=6.0, lifespan="always",
        )
        frame = out["groupings"]["g"]["frames"]["0"]
        peak_cell = frame["cells"][int(np.argmax(frame["density"]))]
        peak_lon, peak_lat = out["cell_centres"][peak_cell]
        # nside=32 has ~1.8 degree cell spacing -- the peak cell should sit within about
        # one cell width of the point itself.
        assert abs(peak_lon - lon0) < 3.0
        assert abs(peak_lat - lat0) < 3.0

    def test_density_ref_is_series_max_not_per_frame(self):
        """One point live at t=0, a second coincident point joining at t=1 -- density_ref
        must be the larger of the two frames' own peaks, not recomputed per frame, or an
        animation's colour ramp would silently imply both times had equal peak abundance."""
        grid = HealpixGrid(nside=16)
        records = [
            {"lon": 0.0, "lat": 0.0, "plate_id": 1, "from": 1.0, "to": 0.0},
            {"lon": 0.0, "lat": 0.0, "plate_id": 1, "from": 1.0, "to": 1.0},
        ]
        rotations = _static_rotations(1, [0.0, 1.0])
        out = build_density(
            records, rotations, times=[0.0, 1.0], grid=grid,
            groupings={"g": {"category_order": ["a"], "values": ["a", "a"]}},
            bandwidth_deg=6.0, lifespan="range",
        )
        ref = out["groupings"]["g"]["density_ref"]
        frame0_max = max(out["groupings"]["g"]["frames"]["0"]["density"])
        frame1_max = max(out["groupings"]["g"]["frames"]["1"]["density"])
        assert ref == pytest.approx(frame1_max, rel=1e-6)
        assert frame0_max < frame1_max

    def test_eps_trims_the_tail(self):
        grid = HealpixGrid(nside=32)
        records = [{"lon": 0.0, "lat": 0.0, "plate_id": 1}]
        rotations = _static_rotations(1, [0.0])

        def _run(eps):
            out = build_density(
                records, rotations, times=[0.0], grid=grid,
                groupings={"g": {"category_order": ["a"], "values": ["a"]}},
                bandwidth_deg=6.0, lifespan="always", eps=eps,
            )
            return len(out["groupings"]["g"]["frames"]["0"]["cells"])

        assert _run(0.1) < _run(1e-6)

    def test_units_and_bandwidth_are_recorded(self):
        grid = HealpixGrid(nside=8)
        out = build_density(
            [], {}, times=[0.0], grid=grid,
            groupings={"g": {"category_order": ["a"], "values": []}},
            bandwidth_deg=4.5,
        )
        assert out["density_units"] == "points per steradian"
        assert out["bandwidth_deg"] == 4.5
