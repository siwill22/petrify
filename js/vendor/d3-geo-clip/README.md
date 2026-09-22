# d3-geo-clip

A trimmed, dependency-free copy of [d3-geo](https://github.com/d3/d3-geo) v3.1.1's
antimeridian clip-and-rejoin algorithm -- the standard, battle-tested solution to a
generic problem: a polygon ring or line that crosses the +-180 degree seam of a flat
world map needs to be split into valid pieces there, or it draws a spurious line/fill
straight across the map. This is not specific to Robinson; every non-orthographic,
non-Spilhaus flat projection this codebase might add later hits the identical problem,
which is why this is vendored as its own small module rather than folded into
`shared/js/globe.js` directly.

Only the files needed for antimeridian clipping are included -- not all of d3-geo (no
projections, no `d3.geoPath`, no circle/rectangle clipping, no resampling). Two of the
original files each had one import from `d3-array`, both inlined here so this directory
stays dependency-free, matching `petrify`'s own "dependency-free ES modules"
principle:

- `polygonContains.js` used `d3-array`'s `Adder` (a Neumaier/Shewchuk compensated
  summation class, needed for a numerically stable point-in-polygon winding test). The
  class is copied verbatim from `d3-array`'s own `src/fsum.js` into a local
  `Adder.js`, cited there.
- `clip/index.js` used `d3-array`'s `merge` (flatten one level of nesting). Replaced
  with a one-line local equivalent.

Nothing else was changed -- every other file is an unmodified copy of the corresponding
d3-geo v3.1.1 source file (see each file's own header comment for its exact upstream
path), including comments, variable names and code style, so a future diff against a
newer d3-geo release stays meaningful.

## What this does NOT include

d3-geo's clipping only splits geometry at the seam -- it has no idea what a Robinson
(or any other) projection's raw x/y formula is, and no idea that this codebase
reconstructs plate positions over geological time before anything gets projected at
all. `shared/js/robinsonSeams.js` is the small adapter that:

1. Takes an already-time-reconstructed ring or line, as plain lon/lat degree pairs
   (this codebase's own reconstruction math, nothing to do with d3-geo).
2. Shifts longitude so the CURRENT pannable central meridian sits at the fixed seam
   `clipAntimeridian` always cuts at (lambda = +-pi) -- d3-geo's own `rotation.js` was
   not vendored for this, a plain add/subtract is all a longitude-only shift needs.
3. Feeds the shifted ring/line through `clipAntimeridian` (this directory) via its
   stream interface (`polygonStart`/`lineStart`/`point`/`lineEnd`/`polygonEnd`), reading
   the result out of a small custom sink instead of `d3.geoStream`/`d3.geoPath` (neither
   of which is included here -- this codebase draws to a plain 2D canvas itself).
4. Shifts the results back and hands them to this codebase's own Robinson forward
   projection (`shared/js/geo.js`'s `robinsonForward`) for the actual pixel coordinates.

## Licence

ISC (see `LICENSE`), Copyright 2010-2024 Mike Bostock and d3-geo's contributors. Only
the antimeridian-clipping subset is reproduced here; see the upstream project for the
rest of d3-geo.
