"""Tracks: things whose position at each time is supplied, not reconstructed.

First consumer: PlateNet's net-characteristics viewer, whose poles are fixed to the
reconstruction frame rather than to any plate. Nothing here needs a plate model.
"""

import json

import numpy as np
import pandas as pd
import pytest

from petrify.points import build_track_trail, build_tracks, time_key


def poles():
    """Two tracks over 0-10 Ma every 5 Myr; track 'b' has no position at 5 Ma."""
    return pd.DataFrame({
        "pole": ["a", "a", "a", "b", "b", "b"],
        "Age": [0.0, 5.0, 10.0, 0.0, 5.0, 10.0],
        "Longitude": [0.0, 10.0, 20.0, 100.0, np.nan, 120.0],
        "Latitude": [0.0, 1.0, 2.0, -5.0, np.nan, -7.0],
        "kind": ["dipole"] * 3 + ["rotation"] * 3,
        "amplitude": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    })


FIELDS = [("pole", "pole"), ("type", "kind"), ("amplitude", "amplitude")]


def test_time_key_matches_javascript_number_to_string():
    assert time_key(5.0) == "5"
    assert time_key(0) == "0"
    assert time_key(2.5) == "2.5"


def test_tracks_payload_has_one_moving_point_per_track():
    payload = build_tracks(poles(), "pole", "Age", fields=FIELDS)

    assert payload["transport"] == "trajectory"
    assert payload["times"] == [0, 5, 10]
    assert set(payload["frames"]) == {"0", "5", "10"}
    assert [p["pole"] for p in payload["points"]] == ["a", "b"]
    # Fields come from each track's first row.
    assert payload["points"][1]["type"] == "rotation"
    assert payload["points"][1]["amplitude"] == 4.0
    lons, lats = payload["frames"]["5"]
    assert lons == [10.0, None] and lats == [1.0, None]
    json.dumps(payload, allow_nan=False)        # NaN must never reach the JSON


def test_tracks_reject_unevenly_spaced_times():
    df = poles()
    df.loc[2, "Age"] = 12.0
    with pytest.raises(ValueError, match="evenly spaced"):
        build_tracks(df, "pole", "Age", fields=FIELDS)


def test_trail_breaks_a_track_where_it_has_no_position():
    payload = build_track_trail(poles(), "pole", "Age", fields=FIELDS)

    segments = [p["segment"] for p in payload["points"]]
    assert segments == ["a#0", "a#0", "a#0", "b#0", "b#1"]
    # Every vertex carries its own time and its own row's fields.
    assert [p["age"] for p in payload["points"]] == [0, 5, 10, 0, 10]
    assert [p["amplitude"] for p in payload["points"]] == [1.0, 2.0, 3.0, 4.0, 6.0]
    # Fixed points: two identical frames spanning the series.
    assert payload["times"] == [0, 10]
    assert payload["frames"]["0"] == payload["frames"]["10"]


def test_geode_view_exports_a_track_layer_and_its_trail(tmp_path):
    import geode
    from geode.cache import Cache

    v = geode.globe(reconstruction="Muller2022", times=(0, 10, 5))
    v.points(poles(), track="pole", time="Age", group="kind", trail=True, caption="Poles.",
             lon="Longitude", lat="Latitude", hover=["amplitude"])
    v.export(str(tmp_path / "site"), quiet=True, cache=Cache(str(tmp_path / "cache")))

    recipe = json.loads((tmp_path / "site" / "view.json").read_text())
    kinds = [(layer["id"], layer.get("connectBy"), layer.get("legend") is False)
             for layer in recipe["layers"] if layer["kind"] == "points"]
    # The trail is drawn first, so the moving symbols sit on top of it, and has no
    # legend rows of its own.
    assert kinds == [("pointsTrail", "segment", True), ("points", None, False)]
    moving = json.loads((tmp_path / "site" / "data" / "points.json").read_text())
    assert [p["type"] for p in moving["points"]] == ["dipole", "rotation"]
    trail = json.loads((tmp_path / "site" / "data" / "pointsTrail.json").read_text())
    assert len(trail["points"]) == 5
    assert "caption" not in trail["meta"]


def test_geode_track_needs_time_and_refuses_age():
    import geode

    v = geode.globe(reconstruction="Muller2022", times=(0, 10, 5))
    with pytest.raises(ValueError, match="needs `time`"):
        v.points(poles(), track="pole", lon="Longitude", lat="Latitude")
    with pytest.raises(ValueError, match="not `age`"):
        v.points(poles(), track="pole", time="Age", age="Age",
                 lon="Longitude", lat="Latitude")
    with pytest.raises(ValueError, match="need `track`"):
        v.points(poles(), trail=True, lon="Longitude", lat="Latitude")
