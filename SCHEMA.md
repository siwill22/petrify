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

## Sizes, for planning

Measured for Merdith2021, 0–250 Ma at 1 Myr, `--tessellate 0.5`:

| | raw | gzipped |
|---|---:|---:|
| 251 boundary frames | 28.3 MB | 8.4 MB |
| one boundary frame | ~113 kB | ~38 kB |
| `velocities.json` | 2.1 MB | 479 kB |
| manifest | 24 kB | 2 kB |

Boundary frames are meant to be fetched on demand; `velocities.json` is meant to be
loaded once.
