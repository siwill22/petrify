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
    { "lon": -66.28, "lat": -27.3708, "age": 5.5, "plate_id": 22032,
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
