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

## Sizes, for planning

Measured for Merdith2021, 0–250 Ma at 1 Myr, `--tessellate 0.5`:

| | raw | gzipped |
|---|---:|---:|
| 251 boundary frames | 28.3 MB | 8.4 MB |
| one boundary frame | ~113 kB | ~38 kB |
| `velocities.json` | 2.1 MB | 479 kB |
| `points.json`, rotations, 1987 points | 1.9 MB | 623 kB |
| `points_trajectory.json`, same points | 6.5 MB | 1.7 MB |
| manifest | 24 kB | 2 kB |

Boundary frames are meant to be fetched on demand; `velocities.json` and `points.json` are
meant to be loaded once.
