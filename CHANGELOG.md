# Changelog

Every entry says why, not just what — see `docs/adr/0001` for why that
matters here: a consumer (often an agent session with no other context)
decides whether to update by reading this file, not by reading the diff.

## v0.3.0

- **`points_from_dataframe()` exposes `plate_begin_age`** — the assigned static
  polygon's own begin age, alongside the `plate_id` it already returned. Motivated by a
  real bug in a downstream consumer (StoryMaps' `detrital-zircons` prototype): a sample
  older than the static polygon its plate id came from silently reconstructed as
  motionless, since `pygplates` holds a plate's oldest defined rotation fixed rather
  than erroring on an over-old query, instead of any visible failure. Geode's own
  Plate-Frame Point feature (`docs/adr/0025`) already enforces exactly this rule
  (`age > assignedFeature.beginAge` -> no plate here yet) but only for that one
  click-time, TypeScript-side path — this is the same fact made available to any
  Python-side consumer of `points_from_dataframe`/`build_points`, most relevantly
  Geode's own planned `prep_paleomag.py` (`docs/adr/0029`), which has the identical
  failure mode for a VGP reconstructed to an age older than its sample site's assigned
  polygon. A mechanism, not a policy: this adds the field, it does not filter or null
  anything out — what a caller does with an over-old point (exclude at build time,
  flag at render time, ignore) is left to it, the same "export carries WHERE, renderer
  decides WHEN" split `age` itself already follows. `null` (not `Infinity`, not valid
  JSON) for a point that fell outside every polygon.

## v0.2.0

- **`deep_time_map.timeseries`** — arc length of boundary segments, grouped
  through time, migrated from StoryMaps' `build_timeseries.py` (see
  `docs/adr/0001`: it satisfies the scope rule as much as anything else
  here, and was only ever in StoryMaps by accident of where development
  started). Grouping is a caller-supplied function, not fixed to
  `boundary_type` — the built-in grouping (the CLI's default) is one case
  of it, not the only one. Output is `boundary_length.csv`, documented
  as a data contract in `SCHEMA.md` alongside the JSON files, since
  `js/timeseries.js` already reads this shape.

## v0.1.0 — first tagged release

Everything before this point predates the tagging discipline in
`docs/adr/0001`, so it's bundled into one release rather than reconstructed
as several. Extracted from the StoryMaps `plate-boundaries` prototype with
boundaries, velocities, and the `Orthographic` reference projector, then
grew:

- **`PointLayer`** — symbolised points on the sphere, with hit-testing and
  cluster spiderfy so overlapping points at one location can be inspected
  individually. For deposits, samples, or any other point dataset a
  consumer wants to place on the globe.
- **`PolygonLayer`** — flat canvas fill for arbitrary sphere polygons
  (continents to start), for consumers without a 3D engine to draw a lit
  mesh with. `BoundarySeries` scrubbing was also made synchronous when the
  target frame is already cached, so scrubbing over prefetched frames no
  longer costs two renders per slider event.
- **Time-series charts** (`timeseries.js`, `timeseries-panel.js`) — a
  host-agnostic widget keyed to the same reconstruction time as the globe,
  for e.g. boundary length by type through time. Belongs here on the same
  "no WebGL/consumer-manifest knowledge required" basis as the geometry
  layers, even though it never calls `project()`.
- **Transparent gzip support** (`gzipFetch.js`) — `BoundaryLayer`,
  `BoundarySeries`, and `VelocityField` now sniff the gzip magic number
  in a fetched response rather than trusting the URL or
  `Content-Encoding`, so a large deployment (e.g. Geode, packing
  thousands of per-age frames under GitHub Pages' 1 GB cap) can pre-gzip
  its JSON without callers needing to know.
