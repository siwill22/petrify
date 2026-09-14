# Changelog

Every entry says why, not just what — see `docs/adr/0001` for why that
matters here: a consumer (often an agent session with no other context)
decides whether to update by reading this file, not by reading the diff.

## v0.4.0

- **`PointLayer.isLive()` enforces `plate_begin_age`** — a point is no longer drawn
  (any `lifespan` mode, either transport) at a time older than its assigned plate's own
  begin age, or, for an unassigned point (`plate_id: 0`), any time other than the
  present. v0.3.0 added `plate_begin_age` as pure metadata and left consuming it to
  each caller; a real consumer (Geode's Boucot paleolithology layer) then shipped
  without ever reading it, and a user found the result empirically: geologically real
  Cretaceous mid-Pacific samples (e.g. a Resolution Guyot reef-limestone site) sitting
  frozen at their present-day position century after century as the age slider moved,
  because `plate_id: 0` carries an identity rotation forever. Broader still: 115 of
  8698 points in that same dataset were assigned a real plate whose own static polygon
  begins later than the point's own age — same silent freeze, just less obviously
  wrong since the plate id isn't 0. Both are `pygplates`' own documented behavior
  (holds the oldest defined pole fixed rather than erroring), so nothing was
  "computed incorrectly" — the number was just never checked against the field
  exposed for exactly this purpose. Moving the check into `isLive()` itself, rather
  than leaving every consumer to re-derive it, is what v0.3.0's own changelog entry
  already reasoned through for the export half; this is the render-time half of the
  same fix. Backward compatible: a point from a dataset that never went through
  `points_from_dataframe()` has neither field (`undefined`, not `null`), and
  `isLive()` treats that exactly as before.

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
