"""Points whose positions the caller supplied: `View.points(positions=, key=)`.

Nothing here touches gprm or pygplates -- the point of the path is that positions a
model computed are drawn as given, not reassigned to a plate and rotated.
"""

import json

import numpy as np
import pandas as pd
import pytest

import geode
from petrify.points import supplied_trajectory


@pytest.fixture
def seamounts():
    return pd.DataFrame({"sid": [7, 3], "born": [2.0, 1.0], "fertility": [0.4, 0.9]})


@pytest.fixture
def tracks():
    # 7 exists from 2 Ma to the present; 3 only at 1 Ma (consumed at a trench by 0).
    return pd.DataFrame({
        "sid":  [7, 7, 7, 3],
        "time": [2.0, 1.0, 0.0, 1.0],
        "lon":  [10.0, 11.0, 12.123, -170.0],
        "lat":  [5.0, 5.5, 6.0, -20.0],
    })


def test_positions_are_drawn_as_given_with_nulls_where_absent(seamounts, tracks):
    payload = supplied_trajectory(seamounts, tracks, "sid", times=[0, 1, 2])
    assert payload["transport"] == "trajectory"
    assert payload["frames"]["0"] == [[12.12, None], [6.0, None]]
    assert payload["frames"]["1"] == [[11.0, -170.0], [5.5, -20.0]]
    assert payload["frames"]["2"] == [[10.0, None], [5.0, None]]


def test_records_start_at_the_youngest_position_and_carry_no_plate(seamounts, tracks):
    payload = supplied_trajectory(seamounts, tracks, "sid", times=[0, 1, 2],
                                  age_field="born", fields=[("fertility", "fertility")])
    first, second = payload["points"]
    assert (first["lon"], first["lat"], first["age"]) == (12.12, 6.0, 2.0)
    assert (second["lon"], second["lat"]) == (-170.0, -20.0)
    assert second["fertility"] == 0.9
    # No plate id: the renderer then has no plate window to enforce, which is right --
    # the positions themselves say when the point exists.
    assert "plate_id" not in first


def test_times_off_the_view_grid_are_ignored(seamounts, tracks):
    payload = supplied_trajectory(seamounts, tracks.assign(time=tracks.time + 0.5),
                                  "sid", times=[0, 1, 2])
    assert all(v is None for frame in payload["frames"].values() for v in frame[0])


def test_duplicate_keys_are_refused(tracks):
    with pytest.raises(ValueError):
        supplied_trajectory(pd.DataFrame({"sid": [1, 1]}), tracks, "sid", times=[0])


def test_view_exports_supplied_points_and_a_raster(seamounts, tracks, tmp_path):
    lon = np.arange(-180, 180, 10.0)
    lat = np.arange(-90, 91, 10.0)
    fertility = {t: np.full((lat.size, lon.size), t) for t in (0.0, 1.0, 2.0)}

    v = geode.globe(times=(0, 2, 1))
    v.raster(fertility, "Fertility", lon=lon, lat=lat, width=36, range=(0, 2))
    v.points(seamounts, positions=tracks, key="sid", age="born",
             hover=["fertility"], lifespan="always")
    v.export(str(tmp_path), quiet=True)

    recipe = json.loads((tmp_path / "view.json").read_text())
    kinds = [layer["kind"] for layer in recipe["layers"]]
    assert kinds == ["raster", "points"]           # the ocean fill would hide the raster
    raster = recipe["layers"][0]
    assert raster["colourbar"]["min"] == 0 and raster["colourbar"]["max"] == 2
    manifest = json.loads((tmp_path / raster["url"]).read_text())
    assert [f["age"] for f in manifest["frames"]] == [0.0, 1.0, 2.0]

    points = json.loads((tmp_path / recipe["layers"][1]["url"]).read_text())
    assert points["frames"]["1"] == [[11.0, -170.0], [5.5, -20.0]]

    script = (tmp_path / "provenance" / "view_script.py").read_text()
    assert "v.raster(fertility, 'Fertility'" in script
    assert "positions=tracks" in script


def test_track_and_positions_together_are_refused(tracks):
    # Both supply positions; whichever won would silently discard the other's.
    with pytest.raises(ValueError, match="use one"):
        geode.globe().points(tracks, track="sid", time="time",
                             positions=tracks, key="sid")
