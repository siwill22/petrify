# deep-time-map

Render plate reconstructions in the browser: reconstructed continent polygons, boundary
lines with subduction-polarity triangles, plate velocity arrows, symbolised point datasets
you can hover for detail, and time-series charts sharing the map's clock — on any
projection you can supply.

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
entire coupling surface. Supply a bundled projector — `Orthographic` (a camera on a globe)
or `Robinson` (a flat whole-world map) — or your own WebGL globe, or a
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
import { BoundarySeries, VelocityField, PointLayer, PolygonLayer, Orthographic }
  from './js/index.js';

const projector = new Orthographic({ cx: 400, cy: 400, radius: 350, lon: -60, lat: 10 });
const continents = await PolygonLayer.load('data/continents.json');
const boundaries = await BoundarySeries.load('data/boundaries.json');
const velocities = await VelocityField.load('data/velocities.json');
const points = await PointLayer.load('data/points.json');

continents.setTime(100);
await boundaries.setTime(100);
velocities.setTime(100);
points.setTime(100);

continents.draw(ctx, projector);  // first: it is the ground everything else sits on
boundaries.draw(ctx, projector);
velocities.draw(ctx, projector);
points.draw(ctx, projector);      // last: symbols you cannot see, you cannot click
```

## Polygons across the horizon

`PolygonLayer` reconstructs rigid blocks -- continents, terranes, coastlines -- by rotating
present-day geometry, and it is the one layer that needs more than `project()` from its
host: `projector.axis`, the outward unit vector at the centre of the view.

Lines can lift the pen where they pass behind the globe. A **filled** shape cannot: an open
path closes itself with a straight chord, so a continent straddling the limb fills as a
lens across the map. With the axis, vertices behind the horizon are clamped onto the limb
and the ring stays closed. Without it, the layer draws outlines and nothing breaks.

Two rules that look like details and are not -- getting either wrong inverts the map, ocean
filled and land empty:

- Rings **entirely** behind the horizon are skipped, not clamped. Clamping every vertex of
  a hidden ring collapses it onto the limb, where it traces the whole disc.
- Every ring is wound the same way before filling, because `nonzero` treats opposite
  windings as holes and abutting terranes are not always digitised in the same sense.

A third rule, about speed rather than correctness: **do not call `ctx.closePath()`** while
accumulating many rings into one path. It is not O(1) in Blink once subpaths are piling up,
so it goes quadratic in ring count. Measured on 727 rings of ~19,500 points, filled and
stroked: **13.2 ms with it, 0.4 ms without**, and a `Path2D` behaves identically. Rings here
repeat their first vertex instead, which closes them for free. On the plate-boundaries page
that one call was 75% of all main-thread time while scrubbing the time slider.

The same applies to `VelocityField`, which draws a whole arrow field as one stroked path and
one filled path rather than two canvas calls per arrow: 803 draw calls per frame became 131.

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

## Time series on the same clock

A globe says what the Earth looked like at time *t*. It cannot say what CO₂, sea level or
total boundary length were doing while that happened. `TimeSeriesSet` charts those beside
the map, keyed to the same reconstruction time.

Input is **CSV, parsed in the browser** — no build step, so adding a record means dropping
a file next to the page:

```js
import { attachTimeSeries } from './js/timeseries-panel.js';

const panel = await attachTimeSeries({
  element: document.getElementById('timeseries'),
  sources: [{ url: 'data/co2.csv', series: { co2_ppm: { label: 'CO₂', unit: 'ppm' } } }],
  range: [0, 250],
  onSeek: setTime,          // clicking the chart scrubs time
  onRender: scheduleRender, // repaint on the host's frame, not a private loop
});
```

One stacked row per quantity, sharing the x axis. Units differ — ppm, metres, km — so a
shared y axis is not on offer.

### Which way is forwards

The one thing worth reading twice. Time is in Ma, counting **down** toward the present, and
the axis is drawn oldest-left so it matches a slider that reads like a timeline. Moving
*forward* in time therefore moves **rightward**, and at time `T` everything that has already
happened is *older* — `t >= T` — and lies to the **left** of the marker.

`mode` chooses how that is expressed, and it is meant to be a reader-facing control:

| mode | what is drawn |
|---|---|
| `marker` | the whole record, with a rule and a dot at the current time |
| `shade` | the whole record, with what is still to come dimmed |
| `reveal` | only `t >= T`, so the curve draws itself as time advances |
| `always` | the whole record, no marker |

Reversing that comparison gives a chart that looks entirely plausible and is exactly
backwards, which is why it has a test to itself.

### Details that are not decoration

- **A blank cell is `null`, never `0`.** A gap lifts the pen. Drawing a missing value as
  zero invents a collapse that is not in the record.
- **Values do not interpolate across a gap**, for the same reason.
- **Decimation is min/max per pixel column**, so a 100k-row record costs what the panel is
  wide and a one-sample spike still shows. Naive subsampling steps straight over spikes.
- **Pixel geometry is computed on resize, not per frame.** When only the time changes the
  curve has not moved; only the split point has, and that is a binary search.
- **The marker is a `fillRect` on integer bounds**, not a 1 px stroke. A stroke at a
  fractional x smears across two columns at half strength; snapping the stroke instead moves
  it by up to a whole pixel. This is crisp *and* within half a pixel — which matters when
  the marker is supposed to sit on a slider thumb.

### Lining up with a range input

If the chart sits above a slider, the marker should sit on the thumb — one time axis, not
two. A range input places its thumb **centre** at

```
x = left + W/2 + fraction * (width - W)
```

because the thumb has to stay inside the track at both ends. So inset the plot by `W/2`
(`thumbWidth` does this). It only works if `W` is known, which means styling the thumb
rather than leaving it native — native widths differ between browsers. Measured on the
plate-boundaries page, marker against thumb: **+0.5 px at 0, 125 and 250 Ma, at two panel
widths.**

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

Continent polygons come from `--polygons continents` (or `coastlines`, or `static` --
models differ in which they carry, and Merdith2021 has continents but no coastlines):

```sh
python -m deep_time_map.export --polygons continents --tolerance 0.02 --out data
```

`--tolerance` is the furthest a vertex may move, in degrees of arc. Simplification is
**topological**: rings are cut into arcs at their junctions and each shared arc is
simplified once, so two blocks that touch go on touching. See
[SCHEMA.md](SCHEMA.md#simplification-keeps-shared-boundaries-shared) — thinning each ring
on its own instead opens holes at 9% of the seams between blocks.

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
  index.js         barrel export (everything except the DOM-touching modules)
  boundaries.js    BoundaryLayer, BoundarySeries
  velocities.js    VelocityField
  polygons.js      PolygonLayer -- reconstructed continents, filled across the horizon
  points.js        PointLayer -- symbols, both transports, hit-testing
  timeseries.js    TimeSeriesSet, parseCsv -- charts keyed to the reconstruction time
  hover.js            attachHover -- DOM, not in the barrel
  timeseries-panel.js attachTimeSeries -- DOM, not in the barrel
  orthographic.js  reference projector
  polyline.js      projection + horizon-cull loop
  rotations.js     quaternions: finite rotations and slerp
  sphere.js        spherical geometry
python/deep_time_map/
  boundaries.py    resolve topologies -> GeoJSON
  velocities.py    HEALPix velocity field
  polygons.py      present-day rings + per-plate rotation series
  points.py        reconstruct point datasets, either transport
  export.py        series driver + CLI
  verify.py        the checks + PyGMT reference figures
examples/globe.html
```

## Licence

MIT.
