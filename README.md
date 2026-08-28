# deep-time-map

Render plate reconstructions in the browser: boundary lines with subduction-polarity
triangles, plate velocity arrows, and symbolised point datasets you can hover for detail
— on any projection you can supply.

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
import { BoundarySeries, VelocityField, PointLayer, Orthographic }
  from './js/index.js';

const projector = new Orthographic({ cx: 400, cy: 400, radius: 350, lon: -60, lat: 10 });
const boundaries = await BoundarySeries.load('data/boundaries.json');
const velocities = await VelocityField.load('data/velocities.json');
const points = await PointLayer.load('data/points.json');

await boundaries.setTime(100);
velocities.setTime(100);
points.setTime(100);

boundaries.draw(ctx, projector);
velocities.draw(ctx, projector);
points.draw(ctx, projector);      // last: symbols you cannot see, you cannot click
```

## Points, and picking

`PointLayer` is the only layer that answers *what is under the pointer?*

```js
const hit = points.pick(x, y);    // { index, point, x, y } | null
```

Two rules make that answer trustworthy:

- **It reads the positions `draw()` cached**, rather than recomputing them. If picking and
  drawing ever disagreed, the popup would describe a symbol other than the one under the
  cursor — and nothing in a screenshot would show it.
- **A point behind the horizon projects to `null`, so it is unpickable as well as
  invisible.** Occlusion is not a special case; it falls out of the projector contract.

For hover popups there is `js/hover.js`, which is deliberately **not** exported from
`index.js` — it is the only module that touches the DOM, so a consumer rendering in a
worker never loads it:

```js
import { attachHover } from './js/hover.js';

attachHover({
  element: canvas, popup: document.getElementById('popup'),
  layer: points, render: draw,
  format: (p) => `<b>${p.name}</b> ${p.age} Ma`,
});
```

It handles the parts that are fiddly rather than hard: one pick per animation frame,
suppressing hover mid-drag so releasing a globe drag does not pin a popup, flipping the
popup at the viewport edge, and keeping it click-through until pinned (a popup that takes
the pointer while merely hovering steals the hover from the canvas and flickers).

### Spiderfy: reaching points inside a pile

Real point datasets cluster — ore districts, sample sites, stations — and on a globe a
district is a few pixels across. `pick()` resolves to the nearest centre, so a buried point
may own a sliver of canvas one or two pixels wide, or none at all. Measured on a
1987-deposit set: **1.6% of drawn points own no pickable area whatsoever, and 9% own less
than a 5x5 px target.** Zooming barely helps; the clusters are real geography, not a
rendering artefact.

Resting the pointer on a pile fans it apart, each symbol drawing a leader line back to
where it really is:

```js
PointLayer.load(url, { spiderfy: true, clusterRadius: 14 });   // on by default
```

- Members are gathered within `clusterRadius` **of the point nearest the cursor**, not of
  the cursor itself, so the cluster does not shift as the pointer jitters.
- Deliberately a direct radius, **not connected components**: clusters are often chains
  (a volcanic arc is a line of near-neighbours), and transitive grouping would swallow a
  whole belt into one group and fan it across the globe.
- Up to 9 members lay out on a circle; beyond that an Archimedean spiral, because a ring
  big enough for 25 puts its feet so far out that the leader lines cross.
- The fan **opens toward the canvas centre**, so a cluster near the limb or a window edge
  unfolds inward instead of off-screen.
- While fanned, `pick()` returns the displaced positions and a member's *true* position is
  no longer pickable — otherwise the pile you just escaped is still sitting under the
  leader lines' convergence point.

`attachHover` supplies the timing: a `spiderfyDwell` of stillness before opening (an
instant trigger makes a dense map churn apart and back as the pointer sweeps across it),
hysteresis sized to the fan's own extent before collapsing, and collapse on drag, zoom,
Escape or a time change.

**When** a point is drawn is the renderer's decision, not the data's — `lifespan` is
`'since'` (form and persist), `'window'` (within `ageWindow` Myr of its age), `'range'`
(its own `from`/`to`), or `'always'`:

```js
PointLayer.load('data/points.json', { lifespan: 'window', ageWindow: 5 });
```

The export deliberately reconstructs every point at every time so this stays a rendering
choice. An export that filtered by age would make `'window'` impossible — the older half
of every window is *before* the point existed.

Colours are **not** in the exported data either — the palette belongs to the page, not the
dataset. Use the `style` hook:

```js
PointLayer.load('data/points.json', {
  style: (point, category) => ({ fill: MY_PALETTE[category.family] }),
});
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

Point datasets ride along with `--points`, which takes any CSV with `Longitude`,
`Latitude` and (for appearance through time) an `Age` column in Ma. Plate IDs are assigned
by partitioning against the model's static polygons, so the input needs none:

```sh
python -m deep_time_map.export --points deposits.csv --transport both \
  --point-fields "name=Deposit,country=Country,cu_mt=Cu (Mt)" --out data
```

`--transport both` writes each representation. That costs build time but buys the sharpest
check in the repo: the same points reconstructed two independent ways must agree, which
tests rotation composition, anchor-plate handling and the browser's slerp at once. On a
1987-point dataset they agree to **0.78 km** at sampled times — the trajectory file's own
rounding floor.

Or as a library:

```python
from deep_time_map import export_series, export_points, verify
export_series(model_name="Merdith2021", start=0, end=250, out_dir="data")
export_points(gdf, model_name="Merdith2021", transport="rotations", out_dir="data")
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
  index.js         barrel export (everything except hover.js)
  boundaries.js    BoundaryLayer, BoundarySeries
  velocities.js    VelocityField
  points.js        PointLayer -- symbols, both transports, hit-testing
  hover.js         attachHover -- the only DOM-touching module, not in the barrel
  orthographic.js  reference projector
  polyline.js      projection + horizon-cull loop
  rotations.js     quaternions: finite rotations and slerp
  sphere.js        spherical geometry
python/deep_time_map/
  boundaries.py    resolve topologies -> GeoJSON
  velocities.py    HEALPix velocity field
  points.py        reconstruct point datasets, either transport
  export.py        series driver + CLI
  verify.py        the checks + PyGMT reference figures
examples/globe.html
```

## Licence

MIT.
