"""
Tests for geode.view.View.points() that need no pygplates/gprm -- construction
only (plate assignment/reconstruction happens later, in geode.build.Builder,
which is what actually touches the scientific stack).
"""

import pandas as pd

from geode.view import View


def _view():
    return View(reconstruction="Merdith2021", times=(0, 100, 1))


def test_lon_lat_rename_does_not_collide_with_a_same_named_source_column():
    """A source frame whose OWN columns are already literally named 'Longitude'/
    'Latitude' (e.g. a raw gprm loader's output), plotted via a DIFFERENT lon=/lat=
    pair (e.g. a caller-computed 'fixed_lon'/'fixed_lat'), must not end up with two
    columns sharing the rename target's name -- that makes every later
    `row[lon_field]` lookup return a Series instead of a scalar. Found for real
    wiring a kimberlite compilation (gprm's own 'Longitude'/'Latitude') through a
    second points() call using its own precomputed 'fixed_lon'/'fixed_lat'."""
    df = pd.DataFrame({
        "Longitude": [10.0, 20.0],
        "Latitude": [1.0, 2.0],
        "fixed_lon": [30.0, 40.0],
        "fixed_lat": [3.0, 4.0],
        "Age": [5.0, 6.0],
    })
    v = _view()
    v.points(df, age="Age", lon="fixed_lon", lat="fixed_lat")

    spec = v._layers[-1]
    frame = spec["_frame"]
    assert list(frame["Longitude"]) == [30.0, 40.0]
    assert list(frame["Latitude"]) == [3.0, 4.0]
    # Exactly one column under each name -- not two.
    assert (frame.columns == "Longitude").sum() == 1
    assert (frame.columns == "Latitude").sum() == 1


def test_age_rename_does_not_collide_with_a_same_named_source_column():
    df = pd.DataFrame({
        "Longitude": [10.0], "Latitude": [1.0],
        "Age": [5.0], "eruption_age": [9.0],
    })
    v = _view()
    v.points(df, age="eruption_age", lon="Longitude", lat="Latitude")

    frame = v._layers[-1]["_frame"]
    assert list(frame["Age"]) == [9.0]
    assert (frame.columns == "Age").sum() == 1


def test_no_collision_is_unaffected_the_usual_case():
    """The overwhelmingly common case -- lon=/lat=/age= already named 'Longitude'/
    'Latitude'/'Age', or names that do not collide at all -- must behave exactly as
    before: the rename is a no-op or a plain rename, never a drop."""
    df = pd.DataFrame({"long": [10.0], "lat": [1.0], "Age": [5.0]})
    v = _view()
    v.points(df, age="Age", lon="long", lat="lat")

    frame = v._layers[-1]["_frame"]
    assert list(frame["Longitude"]) == [10.0]
    assert list(frame["Latitude"]) == [1.0]
    assert list(frame["Age"]) == [5.0]


def test_lon_lat_per_model_dicts():
    """lon=/lat= as {model: column}: the primary model's columns become the
    frame's Longitude/Latitude, every model's pair is kept for Builder to swap in."""
    df = pd.DataFrame({"a_lon": [1.0], "a_lat": [2.0], "b_lon": [3.0], "b_lat": [4.0],
                       "Age": [5.0]})
    v = View(reconstruction=["A", "B"], times=(0, 100, 1))
    v.points(df, age="Age", lon={"A": "a_lon", "B": "b_lon"},
             lat={"A": "a_lat", "B": "b_lat"})
    spec = v._layers[-1]
    assert list(spec["_frame"]["Longitude"]) == [1.0]
    assert spec["_positions"] == {"A": ("a_lon", "a_lat"), "B": ("b_lon", "b_lat")}
    assert list(spec["_frame"]["b_lon"]) == [3.0]


def test_lon_lat_dict_must_name_every_model():
    import pytest
    df = pd.DataFrame({"a_lon": [1.0], "a_lat": [2.0], "Age": [5.0]})
    v = View(reconstruction=["A", "B"], times=(0, 100, 1))
    with pytest.raises(KeyError):
        v.points(df, age="Age", lon={"A": "a_lon"}, lat={"A": "a_lat"})
