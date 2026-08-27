# deep-time-map

Render plate reconstructions in the browser: boundary lines with subduction-polarity
triangles, and plate velocity arrows, on any projection you can supply.

Two halves that share one data contract:

- **`python/`** — export a time series from a GPlates reconstruction model
  (via [gprm](https://github.com/siwill22/GPlatesReconstructionModel) + pygplates)
  into web-ready GeoJSON and JSON.
- **`js/`** — dependency-free ES modules that draw that data onto a 2D canvas.

They live in one repo because the schema is the product. Producer and consumer drifting
apart is the failure this is meant to prevent — see [SCHEMA.md](SCHEMA.md).

## The projector interface

Layers know nothing about your renderer except one method:

```js
project(vec3) -> [x, y, depth] | null
```

`vec3` is a unit vector on the sphere; `x`/`y` are canvas pixels; `depth > 0` means the
point is on the visible side. `null` means behind the horizon — don't draw. That's the
entire coupling surface. Supply the bundled `Orthographic`, or your own WebGL globe, or a
D3 projection wrapper.

Nothing assumes your projection preserves orientation. That matters more than it sounds:
see "Why the triangles are hard" below.

## Quickstart

```sh
# export data (needs the pygplates/gprm stack; conda env assumed to be `pygmt17`)
export PYTHONPATH="$PYTHONPATH:$PWD/python"
python -m deep_time_map.export --model Merdith2021 --end 250 --out examples/data

# serve and look at it
python -m http.server 8000
# open http://localhost:8000/examples/globe.html
```

```js
import { BoundarySeries, VelocityField, Orthographic }
  from './js/index.js';

const projector = new Orthographic({ cx: 400, cy: 400, radius: 350, lon: -60, lat: 10 });
const boundaries = await BoundarySeries.load('data/boundaries.json');
const velocities = await VelocityField.load('data/velocities.json');

await boundaries.setTime(100);
velocities.setTime(100);

boundaries.draw(ctx, projector);
velocities.draw(ctx, projector);
```

## Python API

```sh
python -m deep_time_map.export --help
python -m deep_time_map.verify --data data --time 100
```

`--model` takes any name `gprm.datasets.Reconstructions` can fetch — `Merdith2021`
(topologies to 1000 Ma), `Muller2019`, `Muller2022`, `Matthews2016`, `Cao2024` and
others. Models differ a lot in how much detail they carry: Merdith2021 resolves 248
boundary segments at 0 Ma where Muller2019 resolves 501, because one is a 1 Ga global
model and the other a detailed Mesozoic–Cenozoic one.

Or as a library:

```python
from deep_time_map import export_series, verify
export_series(model_name="Merdith2021", start=0, end=250, out_dir="data")
verify(data_dir="data", time=100)
```

`pygplates` and `gprm` are not on PyPI, so they aren't declared as installable
dependencies — they come from the GPlates/conda stack and are expected to be importable
already.

## Why the triangles are hard

The subduction symbol encodes polarity: the triangles sit on the **overriding-plate
side** of the trench and point down-dip. Get the side wrong and the map is confidently,
invisibly wrong — a mirrored polarity looks entirely plausible.

`gpml:subductionPolarity` is `Left` or `Right`, naming the side of the line *in vertex
order* on which the overriding plate lies. The obvious implementation is to take the
projected segment direction `(dx, dy)` and use `(dy, -dx)` as the left normal. That works
— for orthographic, Spilhaus and Mercator. It is an assumption about the host's
projection handedness, and this library has no business making it.

So the side is resolved **on the sphere**: `a × tangent` is left-of-travel, a cross
product of unit vectors, independent of any projection. That direction is then projected
and the screen normal read off the result. One extra projection per triangle; correct
under any projector, including ones that mirror.

`deep_time_map.verify` checks this against the resolved plate polygons by stepping onto
the polarity side and asking which plate the probe lands in. It has been run against two
independent models: Merdith2021 (41 agree / 0 disagree / 8 indeterminate at 100 Ma) and
Muller2019 (46 / 0 / 7). The indeterminate cases are triple junctions and segments shared
by only one topology, where there is no answer to check against.

## Other things that are easy to get subtly wrong

- **Velocity arrows** are drawn along the great circle the plate is actually travelling
  (`b·cos θ + d·sin θ`), with both ends projected independently — not as a fixed-length
  screen line from a projected azimuth. Near the centre the difference is invisible; near
  the limb it decides whether the arrow lies on the surface or stands off it.
- **Azimuth is `atan2(east, north)`**, clockwise from north, not the `atan2(y, x)` of
  ordinary maths. `verify` checks the exported components round-trip to gprm's own
  azimuths.
- **The velocity domain is HEALPix**, not a lon/lat grid, so arrows are equal-area spaced
  instead of smeared over the poles.
- **Decoration spacing is in screen pixels**, not arc length — even arc spacing crowds
  triangles together near the limb.
- **Boundary frames are never interpolated.** Resolved topologies change discontinuously;
  feature counts can halve between adjacent Myr. There is no correspondence to tween.
- **Lines are not split at the antimeridian.** The renderer works in 3-D unit vectors
  where the dateline isn't special. This means the GeoJSON is *not* RFC 7946-clean 2-D
  GIS data — see [SCHEMA.md](SCHEMA.md).

## Layout

```
js/
  index.js         barrel export
  boundaries.js    BoundaryLayer, BoundarySeries
  velocities.js    VelocityField
  orthographic.js  reference projector
  polyline.js      projection + horizon-cull loop
  sphere.js        spherical geometry
python/deep_time_map/
  boundaries.py    resolve topologies -> GeoJSON
  velocities.py    HEALPix velocity field
  export.py        series driver + CLI
  verify.py        the three checks + PyGMT reference figures
examples/globe.html
```

## Licence

MIT.
