# Time-axis direction is old-left, present-right — and a scrub and its chart share one range

`explorer.js`'s `mountExplorer` already runs its scrub backwards: deep time
on the left, present on the right.

```
scrub.type='range'; scrub.min=String(minTime); scrub.max=String(maxTime);
// The slider runs backwards -- present at the right, deep time at the left...
const sliderToTime = (v) => maxTime - (Number(v) - minTime);
```

The comment explains the *why* — this was chosen over a CSS `scaleX(-1)`
flip specifically to keep arrow-key semantics sane: pressing the right arrow
still increases the DOM value, which this mapping turns into *younger*, so
"right" reads as "forward in time" both visually and via keyboard. Its
paired `timeseries-panel.js` (`toX = (t) => x0 + ((hi-t)/span)*(x1-x0)`) was
built to match it pixel-for-pixel, fed the same `[minTime, maxTime]` range at
construction — a real single source of truth for that one pairing.

None of this was ever written down as a rule of the library, only lived as
one file's comment. That's why it independently reappeared, correctly, in
four consumers that never call `mountExplorer` at all — `detrital-zircons`,
`lips`, `plate-boundaries`, and `TropicalSutures` each hand-roll their own
`<input type=range>` plus a canvas chart, copying the same
`sliderToTime`-shaped logic near-verbatim by convergent evolution rather
than by depending on this repo's own answer. Meanwhile Geode's own
lil-gui/native-`<input>` age controls (nine wrapper viewers plus PlateTree,
none of which touch this library for their time control) independently
landed on the *opposite* direction — `min` bound to the youngest age, so
dragging right moves deeper into the past — because nothing here told them
otherwise.

## The rule

Per ADR-0001's rule 2 (a host-agnostic widget that pairs with the time axis
belongs here), this repo owns the canonical time-axis direction and
alignment contract for the whole family:

1. **Direction.** Right always means "toward the present." A control's
   underlying value may still run low-to-high left-to-right (as
   `mountExplorer`'s DOM `<input>` does, precisely to keep arrow-key
   semantics correct) — what's fixed is the *displayed* mapping: leftmost
   position is the oldest age in range, rightmost is the youngest.
2. **One range, not two literals.** A scrub and any chart sharing its axis
   are built from the same `{minTime, maxTime}` object, passed to both, not
   from two independently-computed numbers that happen to agree by hand.
   `mountExplorer` + `timeseries-panel.js` already do this when used
   together; any new scrub+chart pairing, whether or not it goes through
   `mountExplorer`, must do the same.
3. **Labeling.** Where an end label exists at all, the present end reads
   literally as present, not just as a bare number — e.g. `Present (0 Ma)`,
   not just `0 Ma` — so the direction is legible without the reader having to
   notice which side the numbers grow from. The oldest end shows its actual
   (usually dynamic, per-dataset) value with a unit, e.g. `1000 Ma`.

## Consequences

- `explorer.js` + `timeseries-panel.js` remain the reference implementation.
  A new scrolly-story page should call `mountExplorer` rather than
  hand-rolling a fifth copy of the scrub+chart pair.
- The four existing hand-rolled copies (`detrital-zircons`, `lips`,
  `plate-boundaries`, `TropicalSutures`) already satisfy rule 1 by luck, not
  by depending on this file. They are not required to switch to
  `mountExplorer` outright, but should adopt rule 3's labeling convention
  (and rule 2's shared-range wiring, where it's currently two literals that
  merely happen to match) the next time either is touched.
- Consumers whose UI paradigm can't call this library directly (Geode's
  lil-gui panels are the current example) still owe rule 1–3 in spirit; see
  that consumer's own pointer ADR for how it applies the mapping without a
  native `<input type=range>` to invert.
