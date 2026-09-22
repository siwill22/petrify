"""export_points() passes partition_lon_field/partition_lat_field straight through to
points_from_dataframe() -- this is the one integration point a real caller (a script
computing an actual points.json) would go through, so it is worth its own thin check
beyond points_from_dataframe()'s own tests.
"""

import json

import pandas as pd

from petrify.export import export_points

from test_points import PLATE_A, PLATE_B, model  # noqa: F401  (fixture)


def test_export_points_forwards_partition_fields(model, tmp_path):
    gdf = pd.DataFrame([{"Longitude": 25, "Latitude": 5, "Age": 1,
                        "SiteLon": 5, "SiteLat": 5}])

    written = export_points(
        gdf, model=model, start=0, end=100, step=100, transport="rotations",
        partition_lon_field="SiteLon", partition_lat_field="SiteLat",
        out_dir=str(tmp_path), quiet=True)

    payload = json.loads((tmp_path / "points.json").read_text())
    assert written == [str(tmp_path / "points.json")]
    assert payload["points"][0]["plate_id"] == PLATE_A
    assert payload["points"][0]["lon"] == 25.0
    assert payload["points"][0]["lat"] == 5.0
