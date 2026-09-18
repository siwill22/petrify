# Data contract

What `python/` writes and `js/` reads. Changing either side without the other is the
failure this repo exists to prevent.

## `boundaries.json` — series manifest

```json
{
  "model": "Merdith2021",
  "anchor_plate_id": 0,
  "time_step": 1,
  "tessellate_degrees": 0.5,
  "pygplates_version": "1.0.0",
  "note": "...",
  "frames": [
    { "time": 0, "file": "frames/boundaries_000Ma.geojson",
      "features": 248, "subduction": 94 }
  ]
}
```

`frames[].features` and `subduction` are informational — useful for spotting where a
model's coverage thins without opening every frame. `BoundarySeries` sorts by `time` and
resolves `file` relative to the manifest's own URL.

## `frames/boundaries_<NNN>Ma.geojson` — one snapshot

A GeoJSON `FeatureCollection` of `LineString`s, already reconstructed to its time.

| property | type | notes |
|---|---|---|
| `boundary_type` | string | `subduction` \| `ridge` \| `transform` \| `other` — what the renderer styles on |
| `feature_type` | string | raw gpml type, e.g. `gpml:SubductionZone` |
| `polarity` | string \| null | `Left` \| `Right` \| `Unknown`; null when not a subduction zone |
| `overriding_plate_id` | int \| null | subduction only; null where it couldn't be determined |
| `subducting_plate_id` | int \| null | subduction only |
| `plate_ids` | [int] | plate IDs of the topologies sharing this sub-segment |
| `name` | string | feature name, often empty |

Plus a top-level `meta` carrying model, time, anchor plate, pygplates version,
tessellation, and the two notes below spelled out in full.

### Two things this file is not

**Not interpolatable.** Resolved topologies change discontinuously between timesteps —
sub-segments appear, vanish and split at triple junctions, and the feature count can
halve from one Myr to the next. There is no correspondence between one frame's features
and the next's, so frames are shown with hard cuts. This is the honest representation,
not a shortcut.

**Not RFC 7946-clean.** Lines are deliberately **not** split at ±180°. The renderer works
in 3-D unit vectors where the dateline is not special, and splitting would only introduce
spurious pen-lifts. Loading these into a 2-D GIS will draw horizontal streaks across any
feature that crosses the antimeridian.

This stays true of the *data*, and is now handled at *draw* time for projections that
need it: a flat projector supplies `seamSplit(a, b)` and `tracePolyline` breaks the line
at its map edge (see `js/robinson.js`). Splitting in the file would still be wrong — where
the cut belongs depends on the projector's central meridian, which the exporter cannot
know, and a globe wants no cut at all.

### Polarity convention

`polarity` follows `gpml:subductionPolarity`: it names the side of the line, **in vertex
order**, on which the **overriding** plate lies. The renderer draws triangles on that
side, pointing away from the trench. Verified against resolved plate polygons on two
independent models — see the README.

## `velocities.json` — the whole series in one file

```json
{
  "model": "Merdith2021",
  "units": "km/Myr (numerically equal to mm/yr; divide by 10 for cm/yr)",
  "delta_time": 1.0,
  "healpix_n": 8,
  "note": "...",
  "lon": [ ... 768 values ... ],
  "lat": [ ... 768 values ... ],
  "frames": { "0": [[east...], [north...]], "1": [[...], [...]] }
}
```

The domain points are a **fixed HEALPix sampling grid, identical at every time** — they
are a sampling grid, not something being reconstructed. That is why `lon`/`lat` appear
once in the header and each frame is only two arrays. It is also why the entire 0–250 Ma
velocity series is ~2 MB (≈480 kB gzipped) while a *single* boundary frame is ~113 kB.

- `frames` is keyed by **stringified integer time**.
- Each value is `[east[], north[]]`, parallel to `lon`/`lat`.
- `(0, 0)` means the point fell outside every resolved topology at that time. The
  renderer skips those, along with anything below its `minSpeed`.
- HEALPix gives `12 * healpix_n²` points; `n=8` → 768, mean spacing ~7°.

Azimuth from these components is `atan2(east, north)` — clockwise from north, **not** the
`atan2(y, x)` of ordinary maths.

## `points.json` — symbolised points through time

Points are **rigid bodies with correspondence across time**: a deposit is the same deposit
at 0 and 250 Ma, just somewhere else. That is what makes interpolating them legitimate,
where interpolating boundary frames is not. It also means positions can be shipped two
ways, and the file says which via `transport`.

Common to both:

```json
{
  "model": "Merdith2021",
  "transport": "rotations",
  "anchor_plate_id": 0,
  "times": [0, 1, 2, ...],
  "categories": { "Cu-por": { "symbol": "hexagon", "family": "Cu", "label": "Porphyry Cu" } },
  "points": [
    { "lon": -66.28, "lat": -27.3708, "age": 5.5, "plate_id": 22032, "plate_begin_age": 45.0,
      "type": "Cu-por", "name": "Agua Rica", "country": "Argentina", "cu_mt": 7.3962 }
  ],
  "meta": { "source": "...", "doi": "...", "caption": "..." }
}
```

- `points[].lon/lat` is the **present-day** position, and `age` the event age in Ma.
- **Positions exist at every time, regardless of age.** The export carries *where*; the
  renderer decides *when*. That separation is load-bearing: a consumer showing points in a
  window *around* their age needs positions from before the point existed, and an export
  that filtered by age would have thrown those away.
- `plate_begin_age` is the assigned static polygon's own begin age -- how far back that
  specific piece of crust is a meaningful assignment at all, `null` if the point fell
  outside every polygon (`plate_id: 0`). Same split as `age` above: the export carries
  *how far back this plate id is geologically valid*, the renderer/consumer decides
  what to do with a point whose own `age` exceeds it (exclude, flag, or ignore) --
  `pygplates` does not error on an over-old reconstruction, it silently holds the
  oldest defined rotation pole fixed, so a point past its assigned polygon's begin age
  just stops moving rather than reporting anything wrong.
- Everything else in a `points[]` entry is free-form metadata, carried through untouched
  for the popup. Keys are whatever `--point-fields` mapped.
- `categories` keys match `points[].type`. **No colours here**: the palette belongs to the
  consuming page, not the data — rebuilding the JSON to restyle a map would be the wrong
  seam. Use `PointLayer`'s `style` option instead.

### `transport: "rotations"`

```json
"rotations": { "101": [[pole_lon, pole_lat, angle_deg], ...one per time...] }
```

Keyed by **stringified plate ID**. To place a point at time *t*, slerp its plate's
bracketing finite rotations and apply the result to the point's unit vector. This is the
same `[lon, lat, angle]` triple format the StoryMaps coastline data uses.

### `transport: "trajectory"`

```json
"frames": { "100": [[lon...], [lat...]] }
```

Keyed by stringified integer time, parallel to `points`. **`null` means no position could
be computed** (no rotation for that plate at that time) — not "did not exist". For a model
covering the requested range there should be none.

### Deciding when a point is drawn

That is the renderer's job, via `PointLayer`'s `lifespan` option:

| `lifespan` | shown when | for |
|---|---|---|
| `'since'` *(default)* | `time <= age` | things that form and persist — a deposit still exists today |
| `'window'` | `abs(time - age) <= ageWindow` | showing *when* something happened; the map becomes a moving slice through time |
| `'range'` | `to <= time <= from` | data carrying its own interval, in Ma, with `from >= to` |
| `'always'` | always | ignore age entirely |

Points with **no age are always shown** — a missing age means unknown, and hiding a point
because nobody recorded its age would quietly drop data.

Independent of `lifespan`, `isLive()` also enforces `plate_begin_age`: a point is never
live at a time older than its assigned plate's own begin age (or, for `plate_id: 0`,
older than the present day) — see `PointLayer.isLive()`'s own doc comment. Reconstructing
further back doesn't error, `pygplates` just holds the oldest defined rotation pole
fixed, so without this check an over-old point would silently stop moving instead of
disappearing.

`'window'` changes the character of a map completely. On the 1987-deposit dataset with
`ageWindow: 5`, a mean of **30 deposits** are visible at once instead of ~1000, and the
Andean porphyry belt resolves into a line along the trench that was invisible before. The
cost is that points outside the page's time range can never appear at all: over 0–250 Ma,
720 of 1987 deposits.

### Choosing a transport

Which is smaller is a property of the dataset, not a matter of taste. Roughly, rotations
win once `effective_points > 2 × distinct_plates`, where "effective" discounts points
absent for much of the range. Two real measurements:

| dataset | points | plates | populated | trajectory | rotations |
|---|---:|---:|---:|---:|---:|
| Whittaker LIPs | 80 | 23 | 33% | **107 kB** | 185 kB |
| Hoggard base metals | 1987 | 174 | ~95% | 6.5 MB | **1.9 MB** |

Rotations are also the better *interpolator*: slerping a finite rotation follows the Euler
rotation the plate actually turns through, whereas slerping two reconstructed positions
cuts a great circle across it. Measured difference between the two mid-step: **≤8 km, or
0.375 px** on a 300 px globe — real, but far below what anyone can see.

Exporting **both** (`--transport both`) is the sharpest check available on either: the same
points reconstructed two independent ways must agree. At sampled times they agree to
0.78 km, which is the trajectory file's own 2 dp rounding floor.

## `aggregates.json` — summarised points, one glyph per cell

What `points.json` cannot answer. At a few pixels a symbol, tens of thousands of points
overlap into a density cloud in which no category is legible, so this ships counts per
**cell** per time instead of positions per point.

```json
{
  "model": "Scotese",
  "grid": { "kind": "equal-area-rings", "rings": 40, "lon_cells": 80,
            "cells": 3200, "cell_area_sr": 0.0039269908 },
  "times": [0, 5, 10],
  "cell_centres": [[-177.75, -77.1614], ...one per cell, indexed by cell id...],
  "default_grouping": "subclass",
  "groupings": {
    "subclass": {
      "label": "Subclass",
      "category_order": ["Rugosa", "Tabulata", "Scleractinia"],
      "categories": { "Rugosa": { "label": "Rugose corals", "fill": "rgb(214,96,77)" } },
      "frames": {
        "0": { "cells": [1204, 1287], "counts": [[0, 0, 31], [0, 0, 7]],
               "richness": [12, 5] }
      }
    }
  },
  "lifespan": "range",
  "richness_field": "genus",
  "meta": { "source": "...", "caption": "..." }
}
```

- `frames` is keyed by **stringified time**, like `points_trajectory.json`'s. Only
  **occupied** cells appear; `cells[i]`, `counts[i]` and `richness[i]` are parallel.
- `counts[i]` is parallel to `category_order`, never to an ad-hoc per-cell ordering — a
  wedge must keep its angular position between frames or the glyph appears to spin.
- `richness` is the count of **distinct** `richness_field` values in the cell, which is a
  different question from the total and usually the one worth plotting.

### The renderer does not implement the grid

`cell_centres` ships lon/lat per cell so `AggregateLayer` never needs to know the
tessellation. That is the same "one implementation, not two" rule the rest of this file is
about: a grid written once in Python and once in JavaScript would drift, and the drift
would be invisible — glyphs in almost the right places. Swapping the grid (HEALPix, say)
is therefore an exporter change with **no** renderer change.

The cost is that a consumer can draw marks *at* cell centres but cannot draw cell
**boundaries**, because it does not know where they are. Deliberate.

### Equal area, exactly, and what it costs

`EqualAreaGrid` divides the sphere into `rings` bands of equal area (equal steps in
sin φ), each cut into `lon_cells` equal divisions. Every cell has area exactly
`4π / (rings × lon_cells)` steradians — by construction, not approximately.
`verify_equal_area()` confirms it by Monte Carlo: uniform points land in each cell in
proportion to its area, so the occupancy histogram is flat to within `√n` counting noise.

A lon/lat degree grid would have been wrong, not merely coarser: its cells shrink as
cos φ, so counting into one inflates apparent density toward the poles — fatal for
anything read as a latitudinal gradient.

What was traded for that: cells near the poles are elongated north–south (a 40-ring
grid's top ring spans ~13° of latitude against 4.5° of longitude). A HEALPix grid would
fix the aspect ratio, and needs no change outside `aggregate.py`.

### Frames are not interpolated

`PointLayer` slerps between samples because a point is a rigid body with correspondence
across time — the same deposit, somewhere else. A **cell** has no such correspondence: as
plates move, points cross cell boundaries, so a cell's contents change by membership, not
by motion. Interpolating a count would invent fractional occurrences, possibly in a cell
that is not even the neighbour involved. `AggregateLayer` therefore snaps to the nearest
sample and cuts hard, for the same reason `BoundarySeries` does.

### Positions agree with `points.json` by construction

`build_aggregates()` takes the **same** `rotations` block `build_points()` writes and
applies the same rotation, rather than making an independent `pygplates.reconstruct` call.
It also duplicates `PointLayer.isLive()`'s rule exactly (`live_mask`), including the
`plate_begin_age` check. If either diverged, a pie would summarise points the map does not
draw, and nothing on screen would reveal it.

## `latitude.json` — latitude against age

A globe shows one age. This is the whole record on two axes, so a poleward retreat or a
range crossing a new connection is a shape rather than something the reader has to
remember having scrubbed past.

```json
{
  "model": "Scotese",
  "bands": [{ "from": -90, "to": -85 }, ...],
  "bins":  [{ "name": "Aeronian", "from": 440.8, "to": 438.5 }, ...],
  "bin_ages": [439.65, ...],
  "default_grouping": "subclass",
  "groupings": {
    "subclass": {
      "label": "Subclass",
      "category_order": ["Rugosa", "Tabulata", "Scleractinia"],
      "categories": { "Rugosa": { "fill": "rgb(214,96,77)" } },
      "counts": [ [ [0, 0, 3], ...one per band... ], ...one per bin... ]
    }
  }
}
```

- `counts[bin][band][category]`. `bins` are **stratigraphic** intervals with their own
  uneven widths (ICS stages run from under 1 Myr to 21.6), *not* the uniform sampling step
  `aggregates.json` uses. `attachLatitudePanel` draws each bin's column at its true width,
  so the unevenness stays visible — a long bin accumulates more taxa purely by lasting
  longer, which is the easiest way to misread a diversity record.
- `bin_ages[i]` is the age bin *i* was reconstructed at (normally its midpoint). The
  `rotations` passed to `build_latitude()` must be sampled at exactly these ages.
- A record is counted in **exactly one** bin or none (`bin_of == -1`). A record whose age
  range spans two stages constrains neither, and splitting it between them would invent
  precision the fossil does not have.

### Bands are equal in degrees, not equal in area

Unlike `aggregates.json`'s cells, and for a reason: this axis is read as *latitude*.
Equal-area bands (equal in sin φ) would remove the cos φ sampling bias but put a
nonlinear axis in front of the reader, with the tropics filling most of the height. So
band totals are **not** area-normalised — a 60° band covers half the surface of an
equatorial one — and must not be read as densities.

## `continents.json` — reconstructable polygons

Continents, terranes or coastlines. Like points, these are rigid bodies, so geometry ships
**once at its present-day position** with a rotation series per plate. Unlike points there
is no choice of transport: reconstructing 32,000 vertices into 251 frames is not a real
option.

Note where the bytes actually are before optimising anything: the geometry is only **16%**
of this file. The rotation series — 425 plates × 251 times — is the other 84%.

```json
{
  "model": "Merdith2021",
  "anchor_plate_id": 0,
  "simplify_tolerance_degrees": 0.02,
  "times": [0, 1, 2, ...],
  "rotations": { "101": [[pole_lon, pole_lat, angle_deg], ...] },
  "features": [
    { "p": 101, "b": 1800.0, "e": 0.0, "n": "Laurentia", "xy": [lon, lat, lon, lat, ...] }
  ]
}
```

| field | meaning |
|---|---|
| `p` | reconstruction plate ID, keying into `rotations` |
| `b` | begin time, Ma — the **older** end of validity |
| `e` | end time, Ma — the **younger** end. `-99999` means "still exists" (JSON has no `-Infinity`) |
| `n` | feature name, often empty |
| `xy` | flat present-day `lon, lat` pairs, one closed ring |

Draw a feature only while `e <= t <= b`. Rings are **not** split at the antimeridian, for
the same reason boundary lines are not.

### Simplification keeps shared boundaries shared

`simplify_tolerance_degrees` is the furthest any vertex was allowed to move at export.

The important part is not the thinning but **how** it is done. These polygons tile against
one another: in the Merdith2021 continent set, **76% of edges belong to more than one
ring**. Thin each ring on its own and two neighbours end up with two different
approximations of the boundary they share — the seam splits open and ocean shows through a
place where two blocks were touching.

That is not hypothetical. It is what the first version of this exporter did, and the
measurements are unambiguous:

| | vertices | edges shared | seam points in **no** polygon |
|---|---:|---:|---:|
| source | 73,968 | 76% | — |
| per-ring decimation, 0.1° | 39,221 | 43% | **9.3%** |
| topological, 0.02° | 31,914 | 71% | **1.0%** |

The last column samples the midpoint of every boundary segment two source rings shared.
Such a point sits on a seam between two touching blocks, so it must be inside at least one
polygon; where it is inside none, there is a hole in the land.

So the exporter simplifies the set as a whole, not a ring at a time:

1. **Cut every ring into arcs at its junctions** — vertices where the set of incident rings
   changes. This is the TopoJSON rule. Here 11,552 arcs reduce to 6,627 distinct ones.
2. **Simplify each distinct arc once**, endpoints pinned, and give every ring that uses it
   the *identical* vertex list, reversed where the traversal runs the other way. Neighbours
   then agree by construction rather than by luck.

**Douglas–Peucker runs on the sphere, not in lon/lat.** Deviation is the cross-track angle
from the great circle through the arc's endpoints, `asin(|P·n̂|)` with `n̂ = A × B`
normalised — 3-D vector maths on unit vectors. A planar DP would misjudge exactly where
these polygons live: a degree of longitude is 111 km at the equator and a few hundred
metres in northern Greenland, and an arc crossing the antimeridian would look like a line
most of the way round the world. Checked against
`pygplates.GeometryOnSphere.distance` to 3 × 10⁻⁸ rad.

Pick the tolerance from the zoom limit rather than by eye. For a globe of radius
`min(w, h) × 0.42 × zoom`, one device pixel at 4× zoom on a Retina display is about 0.021°,
so 0.02° is invisible at any zoom such a page allows.

### Filling across the horizon

`PolygonLayer` needs one thing the other layers do not: `projector.axis`, the outward unit
vector at the centre of the view. Lines can lift the pen at the horizon; a **filled** shape
crossing the limb cannot, because an open path closes itself with a straight chord and the
continent fills as a lens across the globe.

Given the axis, a vertex behind the horizon is clamped onto the limb and the ring stays
closed. Two things that are not optional:

- **Rings entirely behind the horizon must be skipped, not clamped.** Clamping every vertex
  of a hidden ring collapses it onto the limb, where it traces the whole disc.
- **Every ring must be wound the same way.** `nonzero` fill treats opposite windings as
  holes, so two abutting terranes digitised in opposite senses punch each other out.

Getting either wrong inverts the map — ocean filled, land empty — which is exactly what
happened the first time. A projector without `axis` still works; the layer falls back to
outlines.

## `boundary_length.csv` — a derived time series, optional

Not written by `export`. `python -m deep_time_map.timeseries --data data` reads an
already-exported `frames/boundaries_<NNN>Ma.geojson` series and writes total arc
length in km, grouped by `boundary_type`, per frame:

```csv
time_ma,subduction_km,ridge_km,transform_km,other_km,total_km
0.0,43075.2,67851.9,28940.1,19204.5,159071.7
1.0,43012.8,67902.3,28887.4,19198.0,159000.5
```

- `time_ma` is what `js/timeseries.js`'s `timeColumn()` matches on — any column
  named `time`/`age`/`ma`/`t` (optionally `_`-suffixed) is taken as the time axis;
  first column otherwise.
- Every other numeric column is a plottable series. `seriesFromCsv()` plots all of
  them by default, or a caller-chosen subset via its `spec` argument — `total_km` is
  written for completeness, not because every chart should show it.
- Length is measured **on the sphere**: pygplates' arc length in radians, scaled by
  Earth's radius. Not affected by tessellation density, since subdividing a
  great-circle arc leaves its length unchanged.
- The grouping (`boundary_type`, falling back to `other`) is `timeseries.py`'s
  default, not its only mode — `length_series()` takes any `feature -> group key`
  function, so a consumer measuring something else (e.g. subduction length matched
  against a separate point dataset) gets the same frame-reading and arc-length
  machinery via its own grouping function, and writes its own CSV with `write_csv()`.
  See `python/deep_time_map/timeseries.py`'s own docstring, and docs/adr/0001 for why
  that split — generic arc-length/grouping mechanics here, "what counts as a group"
  left to the caller — is deliberate.

## `view.json` — the Explorer recipe

Not reconstruction data: the display half. Everything a page decides, as data rather
than as JavaScript — which is what lets `python/geode/` emit it, what lets a reader
change a colour and reload without a rebuild, and what lets the page show its own
decisions in a provenance drawer.

`js/explorer.js`'s `mountExplorer(recipe, root)` consumes it.

```json
{
  "explorer": 1,
  "title": "Igneous zircons through deep time",
  "subtitle": "…",
  "projection": "orthographic",
  "theme": "abyssal",
  "camera": { "lon": -60, "lat": 10, "zoom": 1.0, "zoomLimits": [0.6, 4.0] },
  "time": { "start": 0, "end": 1000, "step": 1, "initial": 300, "playRate": 25 },
  "credit": { "model": "Merdith2021", "modelLabel": "Merdith et al. (2021)" },
  "caption": "…",
  "layers": [ … ],
  "charts": { "mode": "shade", "sources": [ … ] },
  "provenance": { "files": [ … ], "libraries": [ … ] }
}
```

- `explorer` is the recipe version and is checked, not ignored. A recipe from a
  future release fails loudly rather than rendering most of itself.
- **Layer order is draw order.** "What sits on top of what" is a decision the recipe
  states rather than one buried in the host: velocity arrows over boundaries because
  where a fast plate meets a trench the arrow is what you want to read against the
  triangles; points over both because they are the only interactive layer, and a
  symbol you cannot see you cannot click.
- `time.start`/`end` are advisory. The host prefers the boundary series' own
  `timeRange`, so a recipe claiming 0–1000 Ma against frames that stop at 410 scrubs
  to 410 rather than into empty space.

### Layer kinds

| `kind` | reads | notes |
|---|---|---|
| `ocean` | — | a filled disc at the globe's limb. For pages with no paleogeography raster: without it the globe has no body and the vectors float on the page background |
| `continents` | `continents.json` | `PolygonLayer`; `fillAlpha`, `strokeAlpha`, `lineWidth` |
| `boundaries` | `boundaries.json` | `BoundarySeries`; style and decoration come from the Theme |
| `velocities` | `velocities.json` | `VelocityField`; `scaleBar` adds a round-speed reference |
| `points` | `points.json` | `PointLayer`; see below |

### Theme tokens

Any colour in a recipe may be a token instead of a hex literal:

- `"@accentCool"` — a Theme role (`js/themes.js`)
- `"@boundary.subduction"` — whatever ink this Theme gives that boundary type
- `"@outline"` — the resolved coastline pen, honouring Outline Treatment

That is what keeps a chart row the same colour as the map line it annotates when the
Theme changes. A hex literal in the recipe would silently stop matching.

### `points` layers

```json
{
  "kind": "points", "id": "points", "url": "data/points.json",
  "lifespan": "since", "size": 3.1,
  "rule": { "type": "age_window", "window": 5, "fade": 0.07 },
  "colours": { "Felsic": "#ff5fae", "Mafic": "#19e88f" },
  "legend": { "title": "Zircon samples", "counts": true },
  "hover": {
    "eyebrow": "type",
    "title": ["sample_id"],
    "rows": [{ "key": "age", "label": "Age", "unit": "Ma",
               "error": "age_error", "errorUnit": "Myr" }],
    "footer": "reference"
  }
}
```

**Display Rules.** Exactly two ship, each justified by a page that exists:

- `{"type": "constant"}` — colour depends only on the group.
- `{"type": "age_window", "window": n, "fade": a}` — bright within `n` Myr of the
  point's own age, faint outside. A function of (point, *current time*),
  re-evaluated on every scrub, which is precisely why it is a rule the browser
  understands rather than a callback the generator could have run.

`fade` is an alpha on the *same* hue, not a second colour: the two have to read as
one category in two states.

A group with no entry in `colours` is assigned a Theme accent on first sight, in a
fixed order following the exported point order — so the assignment is stable across
loads rather than dependent on object-key iteration.

**The escape hatch.** `"styleJs": "data/custom_style.js"` points at a module whose
default export is `(point, category, api) => ({ fill, … })`. It replaces the rule
entirely. This is a supported path, not a failure: a page whose symbols are
genuinely bespoke — pie glyphs sized by sample count, say — belongs there rather
than in a third rule nobody else could use.

### `charts`

```json
{ "mode": "shade", "thumbWidth": 14,
  "sources": [{ "url": "data/chart0.csv",
                "series": { "subduction_km": { "label": "Subduction", "unit": "km",
                                               "colour": "@boundary.subduction" } } }] }
```

`thumbWidth` must match `--dtm-thumb` in `explorer.css`, or the chart's marker
drifts off the slider thumb at the ends of the axis. See `js/timeseries-panel.js`
for the measurement.

### `provenance`

```json
{ "label": "How this was made",
  "files": [{ "title": "View Script", "url": "provenance/view_script.py",
              "note": "Generated from the calls this view actually received." }],
  "libraries": [{ "call": "Zircons.get_mafic_felsic_samples(rock_type=…)",
                  "package": "gprm.datasets", "version": null,
                  "citation": "Puetz, S.J. et al. (2026), …" }] }
```

`files` are fetched and shown verbatim. `libraries` are **named and pinned, not
shown** — they run outside the browser and cannot be pasted into a panel. The drawer
says so rather than omitting them silently, because a panel claiming to show "the
code" while quietly leaving out the package that did the science implies an audit
trail it does not have.

## Sizes, for planning

Measured for Merdith2021, 0–250 Ma at 1 Myr, `--tessellate 0.5`:

| | raw | gzipped |
|---|---:|---:|
| 251 boundary frames | 28.3 MB | 8.4 MB |
| one boundary frame | ~113 kB | ~38 kB |
| `velocities.json` | 2.1 MB | 479 kB |
| `points.json`, rotations, 1987 points | 1.9 MB | 623 kB |
| `points_trajectory.json`, same points | 6.9 MB | 1.8 MB |
| `continents.json`, 845 rings on 426 plates | 3.8 MB | 0.97 MB |
| manifest | 24 kB | 2 kB |

Boundary frames are meant to be fetched on demand; `velocities.json` and `points.json` are
meant to be loaded once.
