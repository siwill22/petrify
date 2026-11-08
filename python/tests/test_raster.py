"""Gridded fields -> RasterGlobe textures (petrify.raster), and the View verb on top.

The registration tests are the ones that matter: a texture half a cell off its grid
looks fine in a screenshot and is wrong everywhere.
"""

import json

import numpy as np
import pytest

from petrify.raster import (colourise, export_raster, resample, texture_coordinates,
                            write_png)


def smooth(lon, lat):
    """Periodic in longitude, so it is a fair test across the antimeridian."""
    lo, la = np.meshgrid(np.radians(lon), np.radians(lat))
    return np.cos(la) * np.cos(lo) + 0.5 * np.sin(la)


def test_texel_centres_match_the_shader_mapping():
    lon, lat = texture_coordinates(8)
    # u = lon/360 + 0.5 at the centre of texel i is (i + 0.5) / W
    assert np.allclose(lon / 360.0 + 0.5, (np.arange(8) + 0.5) / 8)
    # v = 0.5 - lat/180, row 0 at the north
    assert np.allclose(0.5 - lat / 180.0, (np.arange(4) + 0.5) / 4)


def test_gridline_registered_grid_resamples_onto_texel_centres():
    # Nodes on whole degrees with the seam repeated -- the GMT default.
    lon = np.arange(-180, 181, 1.0)
    lat = np.arange(-90, 91, 1.0)
    out = resample(smooth(lon, lat), lon, lat, width=360)
    tlon, tlat = texture_coordinates(360)
    assert np.max(np.abs(out - smooth(tlon, tlat))) < 2e-4


def test_longitude_convention_does_not_matter():
    lat = np.arange(-90, 91, 2.0)
    west = np.arange(-180, 180, 2.0)
    east = np.arange(0, 360, 2.0)
    a = resample(smooth(west, lat), west, lat, width=180)
    b = resample(smooth(east, lat), east, lat, width=180)
    assert np.allclose(a, b)


def test_latitude_order_does_not_matter():
    lon = np.arange(-180, 180, 2.0)
    lat = np.arange(-90, 91, 2.0)
    a = resample(smooth(lon, lat), lon, lat, width=180)
    b = resample(smooth(lon, lat[::-1]), lon, lat[::-1], width=180)
    assert np.allclose(a, b)


def test_missing_values_do_not_spread_beyond_their_cells():
    lon = np.arange(-180, 180, 1.0)
    lat = np.arange(-90, 91, 1.0)
    grid = np.ones((lat.size, lon.size))
    grid[:, lon < 0] = np.nan
    out = resample(grid, lon, lat, width=720)
    tlon, _ = texture_coordinates(720)
    east = (tlon > 0.5) & (tlon < 178)
    west = (tlon < -1) & (tlon > -179)
    assert np.allclose(out[:, east], 1.0)          # not dragged towards the gap
    assert np.isnan(out[:, west]).all()


def test_colourise_clamps_and_paints_missing():
    stops = ["#000000", "#ffffff"]
    rgb = colourise(np.array([[-1.0, 0.5, 2.0, np.nan]]), stops, 0, 1,
                    nan_colour="#ff0000")
    assert rgb[0, 0].tolist() == [0, 0, 0]
    assert rgb[0, 1].tolist() == [128, 128, 128]
    assert rgb[0, 2].tolist() == [255, 255, 255]
    assert rgb[0, 3].tolist() == [255, 0, 0]


def test_log_scale_takes_its_range_in_data_units():
    rgb = colourise(np.array([[1e-4, 1e-3, 1e-2, 0.0]]), ["#000000", "#ffffff"],
                    1e-4, 1e-2, scale="log", nan_colour="#ff0000")
    assert rgb[0, 1].tolist() == [128, 128, 128]
    assert rgb[0, 3].tolist() == [255, 0, 0]       # non-positive is missing on a log scale


def test_png_round_trips():
    Image = pytest.importorskip("PIL.Image")
    rgb = (np.random.default_rng(0).random((5, 10, 3)) * 255).astype(np.uint8)
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "x.png")
        write_png(path, rgb)
        back = np.asarray(Image.open(path).convert("RGB"))
    assert np.array_equal(back, rgb)


def test_export_writes_a_manifest_of_the_requested_times(tmp_path):
    lon = np.arange(-180, 180, 10.0)
    lat = np.arange(-90, 91, 10.0)
    frames = {t: smooth(lon, lat) * t for t in (0.0, 1.0, 2.0, 3.0)}
    path = export_raster(frames, lon, lat, str(tmp_path), ["#000000", "#ffffff"],
                         -3, 3, width=36, times=[1, 3], quiet=True)
    manifest = json.loads(open(path).read())
    assert [f["age"] for f in manifest["frames"]] == [1.0, 3.0]
    assert all((tmp_path / f["file"]).exists() for f in manifest["frames"])
    assert (manifest["width"], manifest["height"]) == (36, 18)
