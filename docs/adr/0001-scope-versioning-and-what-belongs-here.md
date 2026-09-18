# What belongs in petrify, and how consumers depend on it

petrify was extracted from a StoryMaps prototype (`88c29c6`) and has
since been vendored by at least three consumers: Geode's mantle/paleoclimate
viewers (`viewer/src/core/boundaries.ts`, wrapping a three.js camera in the
library's own projector contract), the StoryMaps `plate-boundaries`
prototype it came from, and Geode's generator, which copies the vendored
`js/` into every externally-generated standalone viewer repo. A fourth
consumer is only a matter of time.

Until now none of that was written down anywhere, and it showed. A
three-commit branch (`points-and-spiderfy`: `PointLayer`, `PolygonLayer`,
time-series charts) sat unmerged for a while and was vendored directly by
two consumers as if it were a release, because there was no release to
point at instead — the only way to depend on the work was to pin a branch
tip. And when Geode independently built a coastline/land-fill pipeline
(`core/coastlines.ts`, lit triangulated 3D mesh geometry), nobody checked
whether that capability belonged here instead — it happened to be the right
call (the two are genuinely different rendering technology, canvas overlay
vs. WebGL mesh), but that was luck, not process. Both problems have the
same root cause: this repo's scope and its consumption contract were never
made explicit, so every consumer — including agent sessions with no memory
of a prior conversation — had to reinvent the answer each time.

This ADR exists so that doesn't have to happen again. It also assumes most
future work on this repo and its consumers is agent-driven: a rule that
lives only in a maintainer's head is not discoverable by a fresh session,
so anything meant to constrain future work has to live in a file the next
session can actually find.

## The rule: what belongs upstream here

A capability belongs in petrify, not in a consumer, if it satisfies
**either**:

1. **It's expressible purely in sphere geometry plus the host contract** —
   `project(vec3) -> [x, y, depth] | null` — with no knowledge of WebGL,
   three.js, GPU textures, or any consumer's own data/manifest format. The
   subduction-polarity triangles, the great-circle velocity arrows, and
   `PointLayer`'s spiderfy/hit-testing are this category.
2. **It's a host-agnostic widget that pairs with the time axis** — a chart,
   a legend, a geomagnetic-polarity (GPTS) bar — again with no WebGL/GPU/
   consumer-manifest knowledge required. `timeseries-panel.js` already
   lives here on exactly this basis, even though it never calls
   `project()` at all; a GPTS bar belongs by the same reasoning.

It stays downstream, in the consumer, if it genuinely needs that consumer's
own rendering pipeline or data model — Geode's `coastlines.ts` is the
worked example: real triangulated, lit, textured 3D mesh geometry,
depth-tested against a WebGL data sphere, built from Geode's own binary
format. No canvas overlay can be that, so it stays in Geode's `core/`
regardless of how "generic" the idea of coastline fill sounds in the
abstract. Scope is decided by what the capability technically requires,
never by which repo happens to be asking for it.

### Split features

A feature is often a mix: a generic geometric/rendering half plus a
domain-specific data half. Querying a live species database, deciding what
counts as "zircon evidence" near a trench, rendering a consumer's own
raster on the sphere — none of these are sphere-geometry-only, but each has
a reusable core buried in it (point-in-radius on a sphere, arc-length of
boundary segments matching some filter, a click-to-chart interaction).
**Split these rather than declaring the whole feature upstream or
downstream**: the generic half becomes a primitive here; the domain-specific
half stays in the consumer as an adapter that produces the same plain JSON
shape petrify already knows how to render (a `points.json`,
a `boundaries.json` frame) — the same pattern `python/petrify/export.py`
already uses. Nothing in `js/` has ever needed to know what a "deposit" or
a "species" is, and that should stay true.

### StoryMaps has no special claim

StoryMaps is where this library happened to start, not its design home.
Code sitting in StoryMaps today (`plate-boundaries/build/build_timeseries.py`,
which computes boundary length by type per frame) is there by the accident
of where development began, not because StoryMaps is entitled to hold
generic logic. When something there is recognized as satisfying the rule
above — as `build_timeseries.py`'s core arc-length computation now is,
being the generic half of the planned zircon-evidence feature — it moves
into `python/petrify/` on the same terms as anything else. StoryMaps
is a consumer like Geode or any future one, not "core."

## Versioning and how consumers depend on this repo

Consumers pin a specific point in this repo's history — a git submodule
(Geode, StoryMaps) or, for externally-generated standalone viewers, a raw
copy of `js/` with no git history at all. Both need a stable, checkable
answer to "what version is this, and is it behind."

- **Tag every merge to `main`**, following semver, as soon as the merged
  work is a coherent unit — not batched into a long-lived branch first (see
  below).
- **Keep `CHANGELOG.md`** in this repo's own prose style — *why*, not just
  *what* — so a consumer deciding whether to update can read one entry
  instead of a diff. "Add `PolygonLayer` — canvas fill for continents, for
  consumers without a 3D engine" is the right amount of detail; "Add
  PolygonLayer" is not enough for an agent with no other context to decide
  whether an update matters to it.
- **Submodule consumers pin to tags, never to a branch tip.** `git -C
  vendor/petrify describe --tags` should always answer cleanly. This
  is what would have kept `points-and-spiderfy` from being vendored
  mid-flight — there would have been nothing tagged to point at until it
  was actually done.
- **Copy-vendored consumers (the generator's output) get the version
  stamped into the copy itself** — e.g. a `/* vendored from petrify
  vX.Y.Z */` comment at the top of the copied `index.js` — since a git-less
  copy has no other way to answer "what version is this" a year later.

### Branch lifetime

Prefer small, independently-mergeable increments over long-lived feature
branches. A branch that's about to be depended on by a consumer should be
merged and tagged first, never vendored as a branch tip — if a feature
genuinely needs more than one session, that's fine, but nothing outside
this repo should reach for it until it lands on `main` and gets a tag.

## Consequences

- New work here starts by asking "does this satisfy the rule above,"
  checkable against this file, not reasoned from scratch.
- A consumer (this repo's own agent sessions included) never depends on an
  unmerged branch. If the capability isn't tagged, it isn't available yet.
- `build_timeseries.py`'s generic arc-length-by-type logic should migrate
  from StoryMaps into `python/petrify/` — flagged here as a known
  follow-up, not yet done.
- Geode carries a short pointer ADR (`docs/adr/0028` there) referencing
  this file, plus the Geode-specific consequence: check this rule before
  building sphere/geometry capability in `core/`, and pin the submodule to
  tags.
