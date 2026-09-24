"""
geode -- put a DataFrame on a reconstructed globe, from a notebook.

    import geode

    v = geode.globe(reconstruction="Merdith2021", times=(0, 1000, 1))
    v.continents()
    v.boundaries()
    v.velocities()
    v.points(samples, age="age", group="rock_type", highlight=geode.age_window(5))
    v.theme("abyssal")
    v.export("my_viewer/")

The audience is someone who works in a notebook with pandas, matplotlib and PyGMT,
has a DataFrame of results, and wants it on a globe -- with a time slider, a legend,
hover, and no JavaScript.

Two things this deliberately does NOT try to be.

It is not a plotting library. The wrangling and the science stay in the notebook,
in the author's own code, calling whatever they already call. Nothing here reduces
that, and it should not: that half is the work.

It is not a narrative tool. What it builds is an *Explorer* -- a globe with standard
layers and controls. Scroll choreography, authored camera moves and prose
interleaved with the map are a different thing, and pages that want them stay
hand-written. See Geode's docs/adr/0046.

The output of `export()` is a directory of static files that works offline, with the
recipe (`view.json`) readable and editable beside it, and a provenance drawer
carrying the generated View Script.
"""

from .view import View, age_window, constant, payload_key
from .cache import Cache, cache_root

__version__ = "0.1.0"

__all__ = [
    "Cache",
    "View",
    "age_window",
    "cache_root",
    "constant",
    "globe",
    "payload_key",
    "themes",
]


def globe(reconstruction="Merdith2021", times=(0, 250, 1),
          projection="orthographic", centre=(0, 0), zoom=1.0, start_time=None,
          title=None, subtitle=None, ocean=True, anchor_plate=0,
          anchor_plates=None, partition_polygons=None):
    """Start a view.

    `times` is `(start, end, step)` in Ma -- deep time at the larger number, the
    present at 0. The step is the reconstruction interval, and 1 Myr is the default
    because resolved plate topologies change discontinuously: a coarser series does
    not smooth, it jumps.

    `projection` is 'orthographic' (a rotatable globe), 'robinson' (a flat world map
    with a pannable central meridian) or 'spilhaus' (a fixed Southern Ocean view).

    `centre` is (lon, lat) for the opening view; `start_time` is the age it opens at,
    defaulting to about a third of the way into the range.

    `reconstruction` is a model name (today's single-model page, unchanged) or a
    list of names -- a page built on more than one model exports every one of them
    and offers a live, in-browser toggle between them, rather than picking one at
    build time. `anchor_plate` is the reference plate held fixed for the single/
    primary model; `anchor_plates`/`partition_polygons` are `{model_name: value}`
    overrides for a specific model in a multi-model list (`partition_polygons`
    is 'static', the default, or 'continents', for a model whose
    ReconstructionModel has continent polygons but no static polygons -- see
    `View.__init__`).
    """
    return View(reconstruction=reconstruction, times=times, projection=projection,
                centre=centre, zoom=zoom, start_time=start_time, title=title,
                subtitle=subtitle, ocean=ocean, anchor_plate=anchor_plate,
                anchor_plates=anchor_plates, partition_polygons=partition_polygons)


def themes(lightness=None, temperature=None):
    """The available Themes, optionally filtered.

        geode.themes(lightness="light", temperature="warm")

    A Theme is a coherent set of colours, line weights and decoration sizes for the
    map's furniture -- the page, the water, the land, the coastline pen, the boundary
    and velocity inks. Never for the data: a Theme that could recolour a data ramp
    would silently change what a map means.

    Read out of the JS table rather than duplicated here, so the two cannot drift.
    """
    import json
    import os
    import re

    path = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "js", "themes.js"))
    with open(path) as fh:
        source = fh.read()

    out = []
    for block in re.finditer(r"\{\s*id:\s*'([a-z]+)'(.*?)\n  \},", source, re.S):
        theme_id, body = block.group(1), block.group(2)
        entry = {"id": theme_id}
        for name in ("lightness", "temperature", "outline", "description"):
            m = re.search(r"\b{}:\s*'([^']*)'".format(name), body)
            if m:
                entry[name] = m.group(1)
        m = re.search(r"\bweight:\s*([0-9.]+)", body)
        if m:
            entry["weight"] = float(m.group(1))
        out.append(entry)

    if lightness:
        out = [t for t in out if t.get("lightness") == lightness]
    if temperature:
        out = [t for t in out if t.get("temperature") == temperature]

    if not out:
        raise ValueError("no theme matches lightness={!r} temperature={!r}".format(
            lightness, temperature))
    return out
