# Changelog

Every entry says why, not just what — see `docs/adr/0001` for why that
matters here: a consumer (often an agent session with no other context)
decides whether to update by reading this file, not by reading the diff.

## v0.11.0

The drawer could already say how to set up the environment and show exactly what
`geode` built (the View Script). Neither says what the author's own analysis
actually did — the fetch/filter/classify steps in the notebook's data block, which
`geode` deliberately never sees.

- **Added `view.notebook(path, steps=[...])`.** An optional, author-written,
  one-line-per-step account of the analysis, shown in "Reproduce this yourself"
  before the setup instructions. Deliberately not derived from anything: the data
  block is arbitrary code outside `View`'s knowledge, and a tool guessing at a
  summary would risk claiming an audit trail it does not have — the same failure
  mode ADR-0048 already rules out for the View Script. Kept as a keyword on the
  existing `.notebook()` verb rather than a new one, per ADR-0046's cap on the verb
  surface.
- Renamed the `requirements` list's CSS class from `.dtm-prov-reqs` to the shared
  `.dtm-prov-list`, since `steps` needed the identical numbered-list styling and two
  copies of the same rule serve nothing.
- Validated on the zircons case study's own (modest) analysis — four real steps.
  A richer example exists (`StoryMaps/detrital-zircons`'s classifier pipeline) but
  predates `petrify`'s Python API entirely; migrating it is separate, future work.

## v0.13.0

The zircons case study's four static distance-to-subduction-zone heatmaps
(bundled into the provenance drawer as evidence, v0.12.0) were always meant to
prove the plumbing before this: one live, filterable version of the same idea,
in the viewer itself.

- **Added `view.distance_heatmap(samples, baseline, ...)`.** A new chart above
  the time slider — a quantile-shift bar over a time/distance density heatmap,
  with toggle buttons per category (e.g. rock type, tectonic setting) that
  recompute both live, client-side. Unlike every other verb, it does not
  compute anything itself: `samples`/`baseline` are DataFrames the caller
  already produced (typically via `gprm.utils.molchan`), because that analysis
  is expensive, exploratory, and often keyed to a different dataset than the
  view's own `.points()`. See Geode's ADR-0052 for the full reasoning, and why
  the baseline ships as precomputed deciles rather than raw points.
- **Added active-point sizing to the `age_window` display rule.** A point
  within the highlight window now renders 50% larger, and — new — inactive
  (faint) points are fully inert to hover: no tooltip, no spiderfy
  participation, whether as an anchor or as a fanned-out member. Traced through
  `points.js`'s `pick()`/`_nearestDrawn()`/`_membersNear()`, gated on a new
  `active` flag that rides along in the same per-point style cache `restyle()`
  already merges arbitrary hook output into — no new storage needed.
- `recipe.charts` and `recipe.distanceHeatmap` both mount into the same slot
  above the slider and both stay generically available; a page uses one or the
  other, not by any special-casing in `explorer.js`.

## v0.12.0

`steps` says what the analysis does; nothing said whether there is anything to
check that against. A reader curious about a specific step -- "does this sample
really sit closer to subduction zones than chance?" -- had no way to see evidence
for it without leaving the page.

- **Added `view.notebook(path, figures=[{"path": ..., "caption": ...}, ...])`.**
  Static images, bundled into the export and rendered under "What the analysis
  does" right after `steps`, as supporting evidence. Nothing checks a figure
  against the steps or against the view -- the caption carries the honesty, same
  as `cites()` does for a package this drawer cannot show directly. A figure from
  a genuinely separate analysis (different dataset, different repository) must
  say so in its own caption.
- First real use: the zircons case study's four distance-to-subduction-zone
  heatmaps (mafic/felsic x convergent/rift), from a distinct, soon-to-be-published
  whole-rock compilation -- not the dataset `igneous_zircons.py` itself uses, and
  its caption says so.

## v0.11.1

v0.11.0's "Reproduce this yourself" ran the concept (`steps`) straight into the
implementation (the file, `requirements`, the code) with no visual break — a
reader scanning for where "what it does" ends and "how to run it" begins had
nothing to look for.

- **Split the section under two subheadings** when `steps` is present: "What the
  analysis does" (the concept), then "Running it yourself" (the file, setup, code).
  Absent `steps`, the section renders exactly as before — no new heading noise for
  pages that only have `requirements`.

## v0.10.2

Testing v0.10.1's own instructions end to end — clone the repo, `pip install -e
python/`, run the notebook from that same directory — reproduced a real
`AttributeError: module 'petrify' has no attribute 'export_series'`.

- **Fixed `geode.build._petrify()`'s import, which the rename made fragile.**
  The repository a reader clones is itself named `petrify`, so running the
  notebook from the directory `git clone` created leaves an empty `petrify/`
  sitting in the current directory. Python's import system builds a namespace
  package out of that empty directory before pip's editable-install finder
  ever gets a chance to run — `import petrify` "succeeds" but returns
  something with no `export_series` and no `__file__`. `_petrify()` was only
  catching `ImportError`, which this case never raises. It now also checks for
  the attribute, and on failure drops the shadowed module from `sys.modules`
  before re-importing with the real path prepended. Reproduced and confirmed
  fixed in a throwaway venv following the documented steps exactly, not just
  reasoned through.

## v0.10.1

The provenance drawer's own first real user hit two problems in one sitting:
the run-it-yourself steps sat at the bottom, past the reference material nobody
should try to run, and the actual sequence — checked package by package, not run
end to end — still failed on a transitive dependency nobody's README mentioned.

- **Reordered the drawer around what a reader with no context does first.**
  "Reproduce this yourself" (the notebook + `requirements`, the one tier with a
  real setup cost) now comes right after the free "change a colour" tier, not
  after the View Script. The View Script moved down and now says outright — not
  as an implied caveat — "you do not need to do anything with this."
- **The notebook's own filename and download link are now shown directly in
  "Reproduce this yourself"**, derived from the file's `url`, not from prose a page
  author has to keep in sync by hand. A reader following a numbered list to
  `python <notebook>.py` no longer has to infer which earlier heading that file
  came from.
- **Added `file.role` ('notebook' | 'viewScript')** so the host can tell these two
  well-known files apart structurally instead of matching on `title` text. Any
  other bundled file still renders, generically, in a fallback loop.
- **The zircons case study's `requirements` needed a fourth package.** A bare
  `conda create ... python pygmt pygplates` has no `setuptools`; `gprm`'s own
  dependency `PlateTectonicTools` still imports the `pkg_resources` API current
  `setuptools` has dropped. Fixed with `"setuptools<81"` pinned into the create
  line — found only by actually running the sequence, not by checking each
  package's own channel in isolation. See `SCHEMA.md`'s new note on this.

## v0.10.0

Renamed from `deep-time-map` to `petrify`, and the repository made public.

The old name described what the library draws (a map of deep time); the new
one describes what it draws it *of* — the rock surface, imaged from outside,
with no claim on anything beneath it. That distinction matters now that the
Geode monorepo pulls this in as a sibling: Geode looks into the Earth, this
library only ever renders its outer shell.

- **Python package renamed `deep_time_map` → `petrify`.** Console scripts
  follow: `petrify-export`, `petrify-verify`, `petrify-timeseries`.
- **No behaviour changed.** Every consumer needs its import paths and vendored
  copies updated, not its code re-tested.
- GitHub renames redirect the old URL automatically, but pinned submodules and
  hand-vendored copies do not update themselves — see the repos that vendor
  this one for their own follow-up.

## v0.9.1

v0.9.0 shipped the provenance drawer but buried it: "How this was made" sat as a
small bordered button at the very bottom of the legend, past the credits, so a
reader who did not scroll never saw it. And once opened it showed the View Script
and the notebook without saying which of them a curious reader could actually *run*
— the View Script closes over data that only exists in the author's session, so
"here is the code" was true but misleading about what came next.

- **The button reads "View the code" and opens first, not last.** Full-width,
  filled with the Theme's cool accent rather than a subtle border, and the first
  thing `mountExplorer` appends to the legend panel.
- **The drawer now states three tiers, not two.** (1) `view.json` itself — always
  offered, downloadable, no Python needed to change a colour or a hover field. (2)
  The View Script — for reading and checking, explicitly labelled as not runnable
  standalone. (3) The author's notebook, now with an optional `requirements` list
  (`view.notebook(path, requirements=[...])`) spelling out what actually reproducing
  the analysis needs — a conda environment, a non-PyPI package, an expected runtime.
  Author-supplied, not guessed: the section is simply absent if none were given.
- **Every shown file gets its own download link**, not just a fetched-and-displayed
  `<pre>` block — "read this" and "keep a copy of this" were previously the same
  action and now are not.

## v0.9.0

The library could draw a reconstruction but not *be* one. This release closes that:
it now ships the raster host the layers draw over, a whole page built from a recipe,
and a Python API that emits both. A consumer no longer needs a hand-written renderer
and a hand-written page to use any of it.

- **Added `js/raster-globe.js` and `js/camera.js`: the WebGL projection host.**
  Moved in from a consumer repo where 710 lines of shader and camera maths sat
  outside the library that depended on them — orthographic, Spilhaus and Robinson,
  each a fragment shader inverting the projection per pixel, with a 2-D overlay
  using the identical camera maths so vectors register with the raster exactly.

  It was never consumer-specific (no host-repo references in any of it), and keeping
  it out meant the library could draw layers but had nothing to draw them *on*. The
  class is `RasterGlobe` rather than the old `Globe`, because `Orthographic` and
  `Robinson` in this barrel are projectors and a third thing called `Globe` invited
  exactly the confusion the rename removes.

  `camera.js` imports `sphere.js` and `robinson.js` rather than redefining them; the
  original had its own copies of the unit-vector helpers and the published Robinson
  table. Its `robinsonForward()` takes a third `centreLonDeg` argument and is
  therefore NOT re-exported from the barrel, where `robinson.js`'s two-argument
  function of the same name already lives.

  Not moved: the antimeridian seam splitter, which depends on a vendored copy of
  d3-geo's clip. `robinson.js`'s own `meridianCrossing()`/`wrapLonDelta()` are the
  library-native path for the same problem.

- **Added `js/explorer.js` + `js/explorer.css`: a whole Explorer page from a recipe.**
  An *Explorer* is a globe, standard layers, a time slider, a legend, hover popups
  and no authored narrative. Measured across seven hand-written pages in one
  consumer repo: `setTime` and `prefetchAll` in 7/7, `scheduleRender` in 5/7,
  `parseHash` and `attachCollapse` in 4/7, across 4,773 lines. On the page used as
  the case study, roughly eight lines of plumbing per line of genuine decision.

  `mountExplorer(recipe, root)` is that plumbing, written once. The recipe is data,
  not code — it round-trips through JSON, which is what lets a generator emit it and
  what lets a published page show the decisions that produced it.

  Deliberately NOT covered: Narratives — scroll choreography, authored camera moves,
  prose interleaved with the map. Three such pages exist and each is large for an
  unrelated reason, so absorbing them would mean three different escape hatches.
  That is a stated ceiling, not a gap. The one hatch that does ship is `styleJs`,
  a module supplying a point's style directly, for genuinely bespoke symbols.

  Exactly two Display Rules ship (`constant`, `age_window`), each justified by a
  page that exists. A third ships when a real page needs one; growing the list
  speculatively is how an API becomes a DSL with bad syntax.

- **Added `python/geode/`: the notebook API.** Nine verbs (`globe`, `continents`,
  `boundaries`, `velocities`, `points`, `timeseries`, `theme`, `caption`, `export`)
  that turn a DataFrame into a standalone offline viewer. It sits on
  `petrify`'s exporters rather than replacing them.

  What it does not do is reduce anyone's Python. Measured on the case study: of the
  341 lines in that page's build scripts, this removes about 35 — the `sys.path`
  boilerplate and one long `export_points(...)` call. The wrangling and the science
  are the author's and stay theirs. What it removes is the JavaScript.

  Two things it adds that neither half had:

  *A cache.* Every export is content-addressed on the parameters that determine it,
  so re-running a view block to change a colour costs nothing, and a reader who
  re-runs it against an exported artifact needs no pygplates. That draws the honest
  boundary: colours, grouping, sizes, hover fields and Theme are free; the model and
  the time range are not, because those are data decisions.

  *A View Script.* Every verb logs its own call, so `export()` emits a canonical,
  ordered, minimal script that provably reproduces the view — faithful by
  construction, and immune to the out-of-order cell execution that makes a notebook
  an unreliable record of itself. The exported page shows it in a drawer, beside a
  statement of what it cannot contain: the analysis libraries, which are named and
  pinned rather than pretended at.

## v0.8.0

- **Added `js/themes.js`: named, coherent looks for map furniture.** Consumers had
  each picked their own land/ocean/background independently and then hand-matched
  them to this library's `DEFAULT_STYLE`, which meant the "house look" was split
  across repos with no single definition. A Theme now assigns one colour per ROLE
  (`page`, `water`, `land`, `outline`, five accents, two speed ramps) and elements
  claim a role rather than a colour, so a drawable added later inherits every Theme
  without any Theme being edited.

  Nine Themes ship, covering every cell of the (lightness x temperature) grid so a
  plain-language request cannot land on nothing. Each also carries `weight` (one
  scalar over every stroke width and decoration size) and `outline`
  (`contrast`/`shade`/`none`), because two coherent looks can differ in ink weight
  alone -- a "for kids" map is thick lines and big subduction triangles, not a hue.

  `boundaryStyle()` returns COMPLETE per-type entries on purpose: `BoundaryLayer`
  shallow-merges caller style over `DEFAULT_STYLE`, so a partial `{ridge: {stroke}}`
  silently drops that type's `width`.

- **Added `js/colour.js`.** sRGB/Lab/CIEDE2000 plus Machado (2009) CVD simulation.
  Not test-only: `outline: 'shade'` derives a pen from the land fill via
  `withLightnessOf()`, and the Theme legibility gate measures with `distanceUnder()`.

  `withLightnessOf` takes the fill's hue and chroma at the pen's reserved lightness,
  rather than offsetting from the page by a fixed amount. The offset version failed
  every `shade` Theme for a structural reason: land is mid-lightness by nature, so a
  fixed offset lands the pen on top of whichever accent already holds that rung.

  DEFAULT_STYLE and the velocity defaults are unchanged; nothing here alters
  existing behaviour unless a consumer opts in by passing a Theme's resolved style.

## v0.7.1

- **Typed the JSDoc on `projectRings` and `tracePolyline`.** This repo ships no `.d.ts`;
  TypeScript consumers get their types by inference from the source. That made two of the
  newest signatures unusable from TS: `{ fillable: [], seam: [] }` infers as `never[]`, and
  `tracePolyline`'s `seam = null` default narrows to exactly `null` — rejecting the very
  function the parameter exists to accept. No behaviour change; the tests are unchanged and
  still pass.

## v0.7.0

- **`PolygonLayer.projectRings(projector)`: where the continents land, without drawing
  them.** Returns `{ fillable, seam }` — flat `[x, y, …]` arrays that are already
  limb-clamped, consistently wound and closed, plus the rings that straddle a flat map's
  seam and may therefore only be stroked. `draw()` is now written in terms of it, so there
  is one tracing loop rather than two that can drift.

  The consumer that forced this is Geode's Old Map viewer, which builds a graded coastal
  wash by stroking the coastline repeatedly at growing widths, and clips it to the land
  side. That needs the *path*, not a finished drawing, and there is no way to recover one
  from `draw()` — a recording shim around the context cannot tell the fillable rings from
  the seam-diverted ones, which is exactly the distinction that matters. Reproducing the
  loop downstream would have meant copying the limb clamping, the winding fix and the seam
  test: the three parts of this layer that are actually difficult, and the three most
  likely to drift out of sync. Per ADR-0001 they stay here.

  Buffers are allocated per call rather than reusing `draw()`'s single scratch array, since
  the caller keeps them.

## v0.6.0

- **`Robinson`: a second reference projector, and the first one with an edge rather than a
  horizon.** Two consumers had independently grown their own copy of the same 19-entry
  Robinson table — Geode for a WebGL plane, StoryMaps for a canvas — and they had drifted
  into *different behaviour at the antimeridian*, which is the part that actually matters.
  The table, `robinsonForward`/`robinsonInverse` and `meridianCrossing` are exported
  separately from the projector class precisely because a consumer with its own renderer
  (Geode's shader, which genuinely belongs downstream under ADR-0001's rule) wants the
  arithmetic without the canvas projector, and should read the same numbers rather than a
  second transcription of them.

- **Lines are now broken at a flat map's seam, via an optional `seamSplit(a, b)` on the
  projector.** Every layer here was written against a camera, where a point is either
  visible or behind the horizon. A whole-world flat map is cut open somewhere instead, and
  a segment spanning that cut is one small step on the sphere but a leap from one side of
  the canvas to the other — drawn as-is it streaks across the entire map, which reads as a
  data problem rather than a projection one. `tracePolyline` takes an optional `seam` hook
  and `BoundaryLayer` passes it through, including for the trench triangles, which would
  otherwise be strewn along the phantom segment. The crossing is solved on the sphere, not
  by interpolating longitude: a great circle's longitude is nowhere near linear in its
  latitude at high latitude, and this library already resolves subduction-triangle sides on
  the sphere for the same reason.

  **`PolygonLayer` degrades rather than lies.** A ring straddling the seam cannot be
  filled — its vertices are split between the two map edges, so any closed path through
  them sweeps back across the map and fills the ocean. Cutting a ring into per-side pieces
  and closing each along the map boundary is a harder, separate job this does not do yet.
  Until it does, such a ring is *outlined* — stroked with the same pen, broken properly at
  the seam — while every ring that does not touch the seam still fills normally. A correct
  outline beats both filling it wrongly and dropping it silently. Detected from the
  projected points the layer already computes, via the projector's optional `mapHalfWidth`,
  so the fast path stays free: the per-ring cost documented in that file is in
  microseconds, and two extra `atan2` per vertex would not have been.

- Adds `test/`, run with `node --test "test/*.test.mjs"` — Node's built-in runner, so the
  repo stays dependency-free.

## v0.5.0

- **`AggregateLayer` + `aggregates.json`: summarised points, one glyph per equal-area
  cell.** `PointLayer` answers "where is each thing?"; a dataset of tens of thousands of
  occurrences cannot answer "what is here, and how much of it?" that way, because at ~3.4 px
  a symbol the categories overlap into a cloud in which none is legible. Measured on the
  motivating dataset: 68,641 PBDB coral occurrences, where the individual-symbol view is a
  density smear and the question being asked ("which taxon dominates here, and when did that
  change?") is invisible in it. The layer draws a pie or a dominant-category disc per
  occupied cell, sized by count or by distinct-taxon richness.
  Three things it deliberately does **not** do, each documented in SCHEMA.md: it does not
  implement the grid (cell centres ship in the payload, so swapping to HEALPix is an
  exporter-only change and the same tessellation is never written twice in two languages);
  it does not interpolate between frames (a cell has no correspondence across time the way a
  rigid point does — contents change by membership, not motion — so it cuts hard like
  `BoundarySeries`); and it does not know what the categories mean, per ADR-0001.

- **`petrify.aggregate`: `EqualAreaGrid`, `build_aggregates`, `build_latitude`.**
  The grid is exactly equal-area by construction — rings of equal area (equal steps in
  sin φ), each cut into the same number of longitude divisions, so every cell is exactly
  `4π / (rings × lon_cells)` steradians. `verify_equal_area()` checks it by Monte Carlo
  and it passes to within √n counting noise. This is not a refinement over a lon/lat
  degree grid, it is a correctness fix: degree cells shrink as cos φ, so counting into
  them inflates apparent density toward the poles, which is fatal for exactly the
  latitudinal-gradient question this was built for. Cost, stated rather than hidden: polar
  cells are elongated north–south. No new dependency — `healpy` is not installed in the
  environment this was written for, and adding one to get a better aspect ratio was not
  worth it when the property that matters (equal area) is available in five lines.
  `build_aggregates()` reuses the **same** `rotations` block `build_points()` writes and
  duplicates `PointLayer.isLive()`'s rule exactly, including the `plate_begin_age` check
  v0.4.0 added — if either diverged, a pie would summarise points the map does not draw
  and nothing on screen would show it.

- **`attachLatitudePanel` + `latitude.json`: the whole record on two axes.** A globe shows
  one age; scrubbing reveals a trend only to someone already watching for one, and never in
  a screenshot. Latitude up, age across, coloured by the active Grouping. Two axis
  conventions that differ from `aggregates.json`'s and are documented at both ends: age bins
  keep their **true widths** (ICS stages run from under 1 Myr to 21.6, and equal columns
  would hide that a long bin accumulates more taxa by lasting longer), and latitude bands
  are equal in **degrees, not area** (this axis is read as latitude; equal-area bands would
  be nonlinear and put the tropics across most of the height). Band totals are therefore not
  area-normalised, which SCHEMA.md says outright so nobody reads them as densities.
  A DOM module, so it stays out of `index.js` alongside `hover.js` and
  `timeseries-panel.js`.

- **`PointLayer.load()` and the new loaders go through `fetchMaybeGzippedJSON`.**
  `gzipFetch.js` has existed since the boundary frames needed it, but `PointLayer` still
  used a bare `fetch().json()`, so the one payload most likely to run to megabytes was the
  one that could not be pre-gzipped. A real export measured while writing this release is
  68,641 points; a static host (GitHub Pages) will not compress that for you. Behaviour on
  a non-`.gz` URL is byte-for-byte unchanged, since the helper sniffs the gzip magic number
  rather than trusting the extension.

- **`rotation_block()` is public.** It was `_rotation_block`, private, and the new
  aggregation path needs exactly it: a consumer summarising the same points a different
  way must reconstruct them with the identical rotations rather than making an
  independent `pygplates.reconstruct` call. Reaching into a private name to get that
  would have been the wrong seam.

- **`attachLatitudePanel` builds its return object before bootstrapping.** Its
  `setGrouping()` returns `api` so calls can chain, and the bootstrap call at the end
  of the function ran while that `const` was still in its temporal dead zone -- a
  `ReferenceError` on first load, every single time. Found by the first consumer to
  actually render it. Recorded because "returns itself for chaining" plus "called
  during construction" is a combination that will recur.

- **Time direction is an explicit option on BOTH panels, and they share one axis
  geometry.** `timeseries.js` had always put the oldest time on the left; the new
  `latitude-panel.js` put the present there. Stacked in a consumer, as they are meant to
  be, that produced two charts on what a reader takes to be one shared age axis running
  in opposite directions -- which does not look like a misconfiguration, it looks like the
  data is wrong. Both now take `timeDirection: 'oldest-left' | 'present-left'`, defaulting
  to this library's original `'oldest-left'` so nothing that exists today changes.
  Direction alone was not enough: the latitude panel also reserved a 30 px gutter for its
  latitude labels while the time-series panel insets by half a thumb width, so a given age
  still landed up to 23 px apart. The latitude labels moved inside the plot and both now
  inset by the same `inset`, so the axes agree by construction. The consuming viewer
  measures it (`npm run check:paleobio` reports the two cursors' x at five ages).

- **`attachLatitudePanel` builds its return object before bootstrapping.** A diversity-through-time curve is a CSV and
  `timeseries-panel.js` already reads those, so the consumer that motivated this release
  needs nothing new for it. Recorded because the obvious move was to add one.

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

- **`petrify.timeseries`** — arc length of boundary segments, grouped
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
