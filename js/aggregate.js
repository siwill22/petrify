/*
 * Aggregated point summaries: one glyph per occupied cell, not one per point.
 *
 * PointLayer answers "where is each thing?". This answers "what is here, and how much of
 * it?" -- the question a dataset of tens of thousands of occurrences cannot answer as
 * individual symbols, because at ~3.4 px they overlap into a density cloud in which no
 * category is legible.
 *
 * ---- What this module does NOT know ----------------------------------------------------
 *
 * It does not know the grid. Cell centres arrive in the payload as plain lon/lat, so the
 * binning scheme is entirely the exporter's business -- an equal-area ring grid, a HEALPix
 * one, whichever a given dataset was exported with -- with no change here. That seam exists
 * because the alternative is the same tessellation implemented twice in two languages,
 * drifting apart silently; the consequence is the renderer cannot draw cell BOUNDARIES,
 * only marks at cell centres, which is a deliberate trade and not an oversight.
 *
 * It also does not know what the categories mean. Same rule as PointLayer: nothing in js/
 * has ever needed to know what a "deposit" or a "species" is.
 *
 * ---- Counts vs density -------------------------------------------------------------------
 *
 * Two different Python exporters feed this one class. `build_aggregates()` ships discrete
 * `counts`/`richness` per cell -- `'pie'`/`'dominant'` mode reads those. `build_density()`
 * ships a continuous `density` value per cell instead, from a spherical KDE -- `'density'`
 * mode reads that. A payload has one shape or the other, never both, and the active `mode`
 * option decides which fields a frame is expected to carry.
 *
 * ---- Why frames are not interpolated ---------------------------------------------------
 *
 * PointLayer slerps between samples because a point is a rigid body with correspondence
 * across time -- the same deposit, somewhere else. A CELL has no such correspondence: as
 * plates move, occurrences cross cell boundaries, so a cell's contents change by
 * membership rather than by motion. Interpolating a count would invent fractional
 * occurrences in a cell that may not be the same cell's neighbour at all. So frames cut
 * hard, at the nearest sample, for the same reason BoundarySeries does.
 */

import { lonLatToVec3 } from './sphere.js';
import { fetchMaybeGzippedJSON, fetchMaybeGzippedBinary } from './gzipFetch.js';

/** Decode _pack_frames_binary()'s layout (see SCHEMA.md's density.json section) into
 *  the SAME `{[timeKey]: {cells, density}}` shape a JSON-embedded `frames` object has --
 *  Uint32Array/Float32Array VIEWS over the buffer, not copies, and no JSON number
 *  parsing at all. `times` is the payload's own already-parsed `times` array; frame i
 *  in the blob corresponds to `times[i]` by construction (both built by iterating the
 *  SAME list in the SAME order on the Python side), keyed here the identical way
 *  `setTime()` already looks a frame up (`String(times[i])`). */
function unpackFramesBinary(buf, times) {
  const view = new DataView(buf);
  const magic = String.fromCharCode(view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3));
  if (magic !== 'DNSF') throw new Error(`frames_ref: bad magic "${magic}"`);
  const version = view.getUint32(4, true);
  if (version !== 1) throw new Error(`frames_ref: unsupported version ${version}`);
  const nFrames = view.getUint32(8, true);
  const frames = {};
  let offset = 12;
  for (let i = 0; i < nFrames; i++) {
    const count = view.getUint32(offset, true);
    offset += 4;
    const cells = new Uint32Array(buf, offset, count);
    offset += count * 4;
    const density = new Float32Array(buf, offset, count);
    offset += count * 4;
    frames[String(times[i])] = { cells, density };
  }
  return frames;
}

/** "rgb(r, g, b)" -> [r, g, b]. Every category fill in the catalog is written this
 *  plain form (see e.g. prep_geochem.py's CATEGORIES) -- no alpha, no hsl -- so a
 *  small dedicated parser is simpler and more honest about what is actually
 *  supported here than reaching for a general CSS-colour library. Falls back to a
 *  mid grey for anything else, the same "an unnamed category looks like a data
 *  problem" spirit DEFAULT_OPTIONS.fill states for the categorical case. */
function parseRgb(css) {
  const m = /rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)/.exec(css || '');
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : [150, 160, 172];
}

/** Join a `grid.geometry_ref` (e.g. "../../_grid/healpix_n512.json.gz") against the
 *  density/aggregates file's OWN url -- plain string/segment math, not `new URL()`, so
 *  this works whether `url` is absolute or a plain relative path (both are valid `fetch()`
 *  arguments, but only one of those is a valid `new URL()` base). */
function resolveRelative(url, ref) {
  const parts = url.split('/');
  parts.pop();
  for (const seg of ref.split('/')) {
    if (seg === '..') parts.pop();
    else if (seg !== '.') parts.push(seg);
  }
  return parts.join('/');
}

// One shared grid file (e.g. HEALPix nside=512's cell_centres/cell_bounds, ~300MB of
// JSON text uncompressed) can be referenced by every dataset and bandwidth variant at
// that resolution -- fetch and gunzip it once per session, not once per `load()` call,
// keyed by its resolved URL so switching dataset/bandwidth at the SAME resolution reuses
// the in-flight or already-parsed result instead of re-fetching identical bytes.
const _geometryCache = new Map();
function loadGridGeometry(url) {
  let p = _geometryCache.get(url);
  if (!p) {
    p = fetchMaybeGzippedJSON(url);
    _geometryCache.set(url, p);
  }
  return p;
}

// Same fetch-once-per-URL cache as grid geometry above, for a `frames_ref` binary blob
// -- see AggregateLayer.load()'s own comment for why this still helps even though
// frames data is dataset/bandwidth-specific rather than shared.
const _framesCache = new Map();
function loadFramesBinary(url) {
  let p = _framesCache.get(url);
  if (!p) {
    p = fetchMaybeGzippedBinary(url);
    _framesCache.set(url, p);
  }
  return p;
}

export const DEFAULT_OPTIONS = {
  /*
   * 'pie'       proportional wedges per category. Reads composition directly, which is
   *             what "which taxon dominates here" asks for.
   * 'dominant'  a single filled disc in the majority category's colour. Less information,
   *             far more legible when cells are small or numerous.
   * 'density'   a continuous colour-ramp field from a `build_density()` payload -- see
   *             the module comment below. Reads `frame.density`, not `frame.counts`, and
   *             ignores `sizeBy`/`mode`-specific fill entirely.
   */
  mode: 'pie',

  /* 'density' mode only. Cell radius scales with grid.cell_area_sr (not sizeRef, which
   * is a counts concept) so neighbouring cells' discs touch and read as one field rather
   * than as separate dots -- see `_densityRadius()`. */
  densityRadiusScale: 1.35,

  /* 'density' mode only. How a normalised [0, 1] density value maps to colour: 'linear'
   * shows only the brightest peaks, 'sqrt' (default) pulls up the mid range so a spread
   * pattern stays legible, 'log' pulls up the faint tail furthest -- for a sparse dataset
   * where even a little bit of signal away from the peak matters. */
  densityScale: 'sqrt',

  /* 'density' mode only. 0 (default) leaves the response curve continuous, one colour
   * per cell's own exact value. A positive integer instead quantises it into that many
   * discrete steps BEFORE densityRamp sees it, so neighbouring cells share flat colour
   * bands with a hard edge between them -- a discrete colormap, closer to a weather-radar
   * plot than a smooth photographic gradient (NOT a contour: a contour is a LINE at a
   * threshold value; this only re-buckets each cell's own fill colour, still an image).
   * As a side effect this makes the underlying HEALPix grid's true pixel edges land ON a
   * colour boundary far more often, which is what makes them actually read as pixels
   * rather than blend away as an imperceptibly smooth ramp -- see `_densityT()`. */
  densityBands: 0,

  /* 'density' mode only. (t, alpha) -> CSS colour for a normalised density in [0, 1].
   * A true HEALPix pixel is drawn OPAQUE (see `_drawCell`), so the colour itself -- not
   * alpha blending -- has to carry the whole range from "barely present" to "peak", which
   * is why this is a proper dark-to-bright sequential ramp (dark red-brown -> orange ->
   * pale yellow) rather than one hue at varying alpha. Deliberately provisional -- a real
   * dataviz-skill palette belongs to the consuming page via this option, not hardcoded here.
   * `alpha` is still honoured for the no-cell_bounds disc fallback, which DOES still
   * benefit from translucency to blend overlapping discs into a field. */
  densityRamp: (t, alpha = 1) => {
    const stops = [[26, 9, 9], [178, 60, 20], [255, 170, 40], [255, 240, 150]];
    const n = stops.length - 1;
    const f = Math.max(0, Math.min(1, t)) * n;
    const i = Math.min(n - 1, Math.floor(f));
    const k = f - i;
    const [r0, g0, b0] = stops[i];
    const [r1, g1, b1] = stops[i + 1];
    const r = Math.round(r0 + (r1 - r0) * k);
    const g = Math.round(g0 + (g1 - g0) * k);
    const b = Math.round(b0 + (b1 - b0) * k);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  },

  /*
   * Glyph radius, in px, for a cell holding `sizeRef` items. Area is made proportional to
   * the count (radius ~ sqrt), which is the only scaling that does not mislead: a cell
   * with four times the occurrences should look four times as big, and radius-proportional
   * sizing would make it sixteen.
   */
  minRadius: 3,
  maxRadius: 14,
  sizeRef: 40,

  /* What drives glyph size. 'total' is occurrence count; 'richness' is distinct taxa in
   * the cell -- a different question, and the honest one when the curve alongside is a
   * richness curve. 'none' draws every cell the same size. */
  sizeBy: 'total',

  keyline: 'rgba(6, 11, 20, 0.85)',
  keylineWidth: 0.9,

  /* Fallback for a category the palette does not name. Deliberately drab: an unnamed
   * category is a data problem and should look like one rather than like a real class. */
  fill: 'rgba(150, 160, 172, 0.85)',

  hitRadius: 12,

  highlight: 'rgba(255, 255, 255, 0.95)',
  highlightWidth: 1.6,
};

export class AggregateLayer {
  /**
   * @param data     an `aggregates.json` payload -- see SCHEMA.md
   * @param options  see DEFAULT_OPTIONS; `grouping` selects which declared Grouping is
   *                 active, defaulting to the payload's own `default_grouping`
   */
  constructor(data, options = {}) {
    this.data = data;
    this.meta = data.meta || {};
    this.times = data.times;
    this.grid = data.grid || {};
    this.centres = data.cell_centres;
    this.options = { ...DEFAULT_OPTIONS, ...options };
    this.visible = true;

    if (!Array.isArray(this.centres)) {
      throw new Error('aggregates.json: cell_centres missing -- the renderer does not '
                      + 'implement the grid, so centres must be shipped');
    }

    // Present-day-frame unit vector per cell centre. Cells do NOT move: a cell is a region
    // of the reconstructed globe, and the exporter has already decided which occurrences
    // fall in it at each time. Nothing here rotates.
    this._xyz = new Float64Array(this.centres.length * 3);
    const tmp = [0, 0, 0];
    for (let i = 0; i < this.centres.length; i++) {
      lonLatToVec3(this.centres[i][0], this.centres[i][1], tmp);
      this._xyz[i * 3] = tmp[0];
      this._xyz[i * 3 + 1] = tmp[1];
      this._xyz[i * 3 + 2] = tmp[2];
    }

    // Optional: exact cell-boundary vertices (currently only `HealpixGrid` ships them --
    // see `cell_bounds` in SCHEMA.md), one unit vector per corner, same "no seam" reasoning
    // as `_xyz` above -- each corner is its own independent 3-D vector, so a cell straddling
    // the antimeridian needs no special casing. `null` when absent, which 'density' mode
    // falls back on to draw a disc instead of a true pixel (see `_drawCell`).
    this.bounds = data.cell_bounds || null;
    this._boundsXyz = null;
    if (this.bounds) {
      const nc = this.bounds.length;
      this._boundsXyz = new Float64Array(nc * 4 * 3);
      for (let i = 0; i < nc; i++) {
        for (let c = 0; c < 4; c++) {
          const [lon, lat] = this.bounds[i][c];
          lonLatToVec3(lon, lat, tmp);
          const o = (i * 4 + c) * 3;
          this._boundsXyz[o] = tmp[0];
          this._boundsXyz[o + 1] = tmp[1];
          this._boundsXyz[o + 2] = tmp[2];
        }
      }
    }

    this.groupingNames = Object.keys(data.groupings || {});
    if (!this.groupingNames.length) throw new Error('aggregates.json: no groupings declared');
    this.grouping = null;
    // Set only by a build_density() payload; undefined for a build_aggregates() one, in
    // which case 'density' mode has nothing to normalise against and draws nothing.
    this.densityRef = null;
    this.setGrouping(options.grouping || data.default_grouping || this.groupingNames[0]);

    this.currentTime = null;
    this.hovered = -1;
    this._frame = null;      // the active grouping's frame at the current time
    this._screen = [];       // parallel to _frame.cells; null where not drawn
    this._v = [0, 0, 0];
  }

  static async load(url, options) {
    const data = await fetchMaybeGzippedJSON(url);
    const ref = data.grid && data.grid.geometry_ref;
    if (ref) {
      const geom = await loadGridGeometry(resolveRelative(url, ref));
      data.cell_centres = geom.cell_centres;
      data.cell_bounds = geom.cell_bounds;
    }
    // Same shared-fetch-and-cache treatment as geometry_ref above, but per grouping --
    // a `frames_ref` is dataset/bandwidth-specific (not shared across datasets the way
    // grid geometry is), but the cache still avoids a redundant re-fetch of the exact
    // same URL within one session (e.g. toggling Grid modes back and forth), and
    // dedupes the common case of every grouping in one payload sharing one name.
    for (const g of Object.values(data.groupings || {})) {
      if (!g.frames_ref) continue;
      const buf = await loadFramesBinary(resolveRelative(url, g.frames_ref));
      g.frames = unpackFramesBinary(buf, data.times);
    }
    return new AggregateLayer(data, options);
  }

  /** Switch active Grouping. Several are declared, exactly one is shown -- never overlaid,
   *  since two pie charts on one cell is not a readable mark. */
  setGrouping(name) {
    const g = this.data.groupings[name];
    if (!g) throw new Error(`aggregates.json: no grouping "${name}"`);
    this.grouping = name;
    this.active = g;
    // Category order is fixed by the payload, not by per-cell counts -- otherwise a cell's
    // wedges would reshuffle between frames as the majority changed, which reads as motion
    // that is not in the data.
    this.categories = g.category_order || Object.keys(g.categories || {});
    this._fills = this.categories.map(
      (c) => (g.categories?.[c]?.fill) || this.options.fill,
    );
    this._fillRgb = this._fills.map(parseRgb);
    this.densityRef = g.density_ref ?? null;
    // The series-wide peak occupancy (max total count in any cell at any time) --
    // 'dominant' mode's own equivalent of a density payload's `density_ref`, since a
    // build_aggregates() payload carries no such field itself. Normalises the SAME
    // colour-by-magnitude treatment against the whole series, not a single frame, for
    // the same reason `build_density()` does: a per-frame peak would make every frame
    // look equally intense regardless of how abundance actually changed over time.
    this._totalsRef = 0;
    for (const key in g.frames) {
      for (const row of g.frames[key].counts || []) {
        const t = row.reduce((a, b) => a + (b || 0), 0);
        if (t > this._totalsRef) this._totalsRef = t;
      }
    }
    if (this.currentTime != null) this.setTime(this.currentTime);
    return this;
  }

  /** Label for a category key, falling back to the key itself. */
  labelFor(category) {
    return this.active.categories?.[category]?.label || category;
  }

  /** Fill for a category key. */
  fillFor(category) {
    const i = this.categories.indexOf(category);
    return i < 0 ? this.options.fill : this._fills[i];
  }

  /** Nearest sample index to a time in Ma. Nearest, not bracketing -- see the module
   *  comment for why cell counts are never interpolated. */
  _nearest(time) {
    const times = this.times;
    let best = 0;
    let bestD = Infinity;
    for (let i = 0; i < times.length; i++) {
      const d = Math.abs(times[i] - time);
      if (d < bestD) { bestD = d; best = i; }
    }
    return best;
  }

  setTime(time) {
    this.currentTime = time;
    const key = String(this.times[this._nearest(time)]);
    this._frame = this.active.frames[key] || null;
    this._screen.length = this._frame ? this._frame.cells.length : 0;
    this._screen.fill(null);
    return this;
  }

  /** Occupied cells at the current time. */
  get cellCount() {
    return this._frame ? this._frame.cells.length : 0;
  }

  /** Totals per category across every occupied cell at the current time -- what a legend
   *  showing "how much of each is on screen right now" needs. Categories are a
   *  build_aggregates() concept; a build_density() frame has none, so this returns all
   *  zero rather than reading a `counts` field that frame does not have. */
  totals() {
    const out = this.categories.map(() => 0);
    if (!this._frame || !this._frame.counts) return out;
    for (const row of this._frame.counts) {
      for (let c = 0; c < out.length; c++) out[c] += row[c] || 0;
    }
    return out;
  }

  /** Normalise a raw density value to [0, 1] against the active Grouping's series-wide
   *  peak, then apply the configured response curve -- see DEFAULT_OPTIONS.densityScale.
   *  Against the series max (not the current frame's own max), same reasoning as
   *  build_density() itself: a per-frame normalisation would make every frame's peak
   *  look equally intense regardless of how abundance actually changed over time. */
  _densityT(value) {
    if (!this.densityRef) return 0;
    const t = Math.max(0, Math.min(1, value / this.densityRef));
    const scaled = (() => {
      switch (this.options.densityScale) {
        case 'linear': return t;
        case 'log': return Math.log1p(t * 9) / Math.log(10); // log1p keeps t=0 -> 0
        case 'sqrt':
        default: return Math.sqrt(t);
      }
    })();
    const bands = this.options.densityBands;
    if (!bands || bands < 1 || scaled <= 0) return scaled;
    // Quantise to the CENTRE of the band scaled falls in, not its lower edge -- a band's
    // colour then represents its own middle value rather than always undershooting.
    // scaled > 0 is excluded above so an actually-empty cell still maps to exactly 0,
    // the `t <= 0` sentinel _drawCell uses to skip drawing it at all.
    return (Math.min(bands - 1, Math.floor(scaled * bands)) + 0.5) / bands;
  }

  /** Composition, as an RGB blend of every category present -- weighted by each one's
   *  SHARE of `total`, not just the winner take all. A cell 70% volcanic/30% plutonic
   *  reads as an orange leaning toward blue, not a flat orange indistinguishable from a
   *  cell that is 100% volcanic -- the "mixture" half of 'dominant' mode's colour. */
  _mixColor(counts, total) {
    let r = 0; let g = 0; let b = 0;
    for (let c = 0; c < counts.length; c++) {
      const n = counts[c] || 0;
      if (!n) continue;
      const w = n / total;
      const rgb = this._fillRgb[c] || [150, 160, 172];
      r += rgb[0] * w; g += rgb[1] * w; b += rgb[2] * w;
    }
    return [r, g, b];
  }

  /** How occupied this cell is, normalised against the series-wide peak (see
   *  `_totalsRef` in setGrouping()) and square-root scaled for the same reason
   *  `_densityT`'s own 'sqrt' default is: it pulls up the mid-range so a moderately
   *  full cell still reads as populated rather than only the single busiest cell in
   *  the whole series standing out. The "magnitude" half of 'dominant' mode's colour. */
  _occupancyT(total) {
    if (!this._totalsRef) return 1;
    return Math.sqrt(Math.max(0, Math.min(1, total / this._totalsRef)));
  }

  /** `_mixColor`'s hue, scaled by `_occupancyT`'s magnitude toward a dim floor rather
   *  than toward black -- a cell with almost no data still reads as faintly present
   *  (composition still legible), not indistinguishable from the backdrop. */
  _mixedFill(counts, total) {
    const [r, g, b] = this._mixColor(counts, total);
    const floor = 0.18;
    const k = floor + (1 - floor) * this._occupancyT(total);
    return `rgb(${Math.round(r * k)}, ${Math.round(g * k)}, ${Math.round(b * k)})`;
  }

  /** Everything known about one occupied-cell slot, for a popup. */
  cellAt(slot) {
    if (!this._frame || slot < 0 || slot >= this._frame.cells.length) return null;
    const cell = this._frame.cells[slot];
    const centre = this.centres[cell];
    if (this._frame.density) {
      const density = this._frame.density[slot];
      return {
        cell, centre, density, densityRef: this.densityRef, t: this._densityT(density),
      };
    }
    const counts = this._frame.counts[slot];
    const total = counts.reduce((a, b) => a + (b || 0), 0);
    return {
      cell,
      centre,
      counts,
      total,
      richness: this._frame.richness ? this._frame.richness[slot] : null,
      categories: this.categories,
      byCategory: Object.fromEntries(
        this.categories.map((c, i) => [c, counts[i] || 0]).filter(([, n]) => n > 0),
      ),
    };
  }

  _radius(slot) {
    if (this.options.mode === 'density') {
      // A fixed radius, not one scaled by value -- 'density' reads composition through
      // COLOUR (see _drawCell), the same way a raster heatmap does. Varying disc size on
      // top of that would be a second, redundant encoding of the same quantity.
      // Scaled UP by densityRadiusScale (not left at maxRadius) because adjacent grid
      // cells' discs need to actually overlap to read as one field rather than a
      // polka-dot pattern -- a HEALPix grid's own spacing puts cell centres roughly
      // 2*maxRadius apart on screen, so same-radius discs only just touch at their
      // edges instead of blending.
      return this.options.maxRadius * this.options.densityRadiusScale;
    }
    const { sizeBy, minRadius, maxRadius, sizeRef } = this.options;
    if (sizeBy === 'none') return (minRadius + maxRadius) / 2;
    const counts = this._frame.counts[slot];
    const n = sizeBy === 'richness' && this._frame.richness
      ? this._frame.richness[slot]
      : counts.reduce((a, b) => a + (b || 0), 0);
    if (!n) return 0;
    // Area proportional to n: r = maxR * sqrt(n / sizeRef), clamped.
    const r = maxRadius * Math.sqrt(n / sizeRef);
    return Math.max(minRadius, Math.min(maxRadius, r));
  }

  draw(ctx, projector) {
    const screen = this._screen;
    screen.fill(null);
    if (!this.visible || !this._frame) return;

    const cells = this._frame.cells;
    const v = this._v;
    for (let s = 0; s < cells.length; s++) {
      const i3 = cells[s] * 3;
      v[0] = this._xyz[i3];
      v[1] = this._xyz[i3 + 1];
      v[2] = this._xyz[i3 + 2];
      // null behind the horizon, which makes the cell unpickable as well as invisible.
      screen[s] = projector.project(v);
    }

    // Painter's algorithm, far first, so a near glyph overlaps a far one rather than the
    // cell index deciding. Small glyphs last within the same depth so they are not buried.
    const order = [];
    for (let s = 0; s < cells.length; s++) if (screen[s]) order.push(s);
    order.sort((a, b) => (screen[b][2] - screen[a][2]) || (this._radius(b) - this._radius(a)));

    for (const s of order) this._drawCell(ctx, s, screen[s], projector);

    if (this.hovered >= 0 && screen[this.hovered]) {
      const [x, y] = screen[this.hovered];
      ctx.beginPath();
      ctx.arc(x, y, this._radius(this.hovered) + 3, 0, Math.PI * 2);
      ctx.strokeStyle = this.options.highlight;
      ctx.lineWidth = this.options.highlightWidth;
      ctx.stroke();
    }
  }

  _drawCell(ctx, slot, p, projector) {
    if (this.options.mode === 'density') {
      const t = this._densityT(this._frame.density[slot]);
      if (t <= 0) return;
      if (this._boundsXyz && projector) {
        // Solid, not translucent: a true HEALPix pixel is opaque and tiles exactly
        // with its neighbours, so it needs none of a disc's alpha-blending to read
        // as a field -- the colour ramp itself carries the value.
        this._drawPixel(ctx, this._frame.cells[slot], this.options.densityRamp(t, 1), projector);
        return;
      }
      // Fallback for a payload with no cell_bounds (a grid without .bounds(), e.g.
      // EqualAreaGrid) -- the old overlapping-disc approximation, where alpha = t
      // still does useful work blending neighbours into a field.
      const r = this._radius(slot);
      if (r <= 0) return;
      const [x, y] = p;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = this.options.densityRamp(t, t);
      ctx.fill();
      // No keyline in density mode: a stroke around every disc reads as a grid over the
      // field it is meant to look continuous, which is exactly what pie/dominant's
      // keyline is for (telling discrete glyphs apart) and exactly what this mode isn't.
      return;
    }

    const r = this._radius(slot);
    if (r <= 0) return;
    const [x, y] = p;

    const counts = this._frame.counts[slot];
    const total = counts.reduce((a, b) => a + (b || 0), 0);
    if (!total) return;

    if (this.options.mode === 'dominant') {
      // A single flat hue per cell (the old plain "majority category colour", with no
      // regard for how much data actually backs that majority) reads as a scatter of
      // same-looking marks regardless of whether a cell holds 3 points or 3,000 -- see
      // _mixColor()/_occupancyT()'s own doc comments for what replaces it: a colour
      // that carries BOTH composition (hue, blended across every category present, not
      // just the winner) AND magnitude (how saturated/bright that hue is).
      const fill = this._mixedFill(counts, total);
      if (this._boundsXyz && projector) {
        // The grid's own true cell shape (a HEALPix diamond), tiling into one
        // continuous image of pixels. No keyline (see the `return` below): a stroke
        // around every tiny diamond reads as a mesh drawn over the map, the same
        // reason 'density' mode draws its own pixels with none.
        this._drawPixel(ctx, this._frame.cells[slot], fill, projector);
        return;
      }
      // Fallback for a grid with no cell_bounds (EqualAreaGrid does not ship
      // `.bounds()` -- see aggregate.py) -- a disc standing in for the true cell,
      // same as 'density' mode's own disc fallback.
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = fill;
      ctx.fill();
    } else {
      // Start at 12 o'clock and go clockwise, which is how a pie is read. Category order
      // is the payload's, so a wedge keeps its angular position between frames.
      let a0 = -Math.PI / 2;
      for (let c = 0; c < counts.length; c++) {
        const n = counts[c] || 0;
        if (!n) continue;
        const a1 = a0 + (n / total) * Math.PI * 2;
        ctx.beginPath();
        // A single-category cell is a full disc: moveTo(centre) on a 2pi arc leaves a
        // visible radius line where the path closes.
        if (n === total) {
          ctx.arc(x, y, r, 0, Math.PI * 2);
        } else {
          ctx.moveTo(x, y);
          ctx.arc(x, y, r, a0, a1);
          ctx.closePath();
        }
        ctx.fillStyle = this._fills[c];
        ctx.fill();
        a0 = a1;
      }
    }

    if (this.options.keylineWidth > 0) {
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.strokeStyle = this.options.keyline;
      ctx.lineWidth = this.options.keylineWidth;
      ctx.stroke();
    }
  }

  /**
   * A true HEALPix cell, filled as its own quad rather than approximated by a disc at its
   * centre -- what makes 'density' mode read as actual rendered pixels instead of a blurred
   * blob of overlapping circles. `cell` indexes `_boundsXyz`'s four corners directly (not
   * `slot`, which only indexes the current frame's occupied-cell list).
   *
   * Any corner behind the horizon skips the whole cell rather than drawing a folded or
   * clipped shape -- the same "off, not distorted" choice PointLayer and BoundarySeries make
   * at the limb. Known limitation, not fixed here: a cell straddling a flat projection's
   * antimeridian seam can still streak across the map, the way an unsplit line would --
   * `js/robinson.js`'s `seamSplit`/`tracePolyline` solve that for lines and polygons with
   * many vertices; a four-corner quad has not needed that machinery yet because the globe is
   * this mode's primary view and the seam only ever touches a thin sliver of cells.
   */
  _drawPixel(ctx, cell, fill, projector) {
    const o = cell * 4 * 3;
    const v = this._v;
    const xs = [];
    const ys = [];
    for (let c = 0; c < 4; c++) {
      v[0] = this._boundsXyz[o + c * 3];
      v[1] = this._boundsXyz[o + c * 3 + 1];
      v[2] = this._boundsXyz[o + c * 3 + 2];
      const sp = projector.project(v);
      if (!sp) return;
      xs.push(sp[0]);
      ys.push(sp[1]);
    }
    // Seam guard: on a flat map (globe has no `mapHalfWidth`, see below), a cell
    // whose corners straddle the antimeridian projects to BOTH edges of the map
    // at once, so its quad stretches corner-to-corner across the whole thing --
    // the same corrupted-streak failure `PolygonLayer.projectRings()` guards
    // against for filled rings (polygons.js). A true HEALPix cell is never more
    // than a few degrees wide, so any span anywhere near half the map's width
    // can only be that seam, not real cell geometry: skip the whole cell, the
    // same "off, not distorted" choice a horizon-crossing corner already gets
    // just above, rather than draw the fold.
    const halfWidth = projector.mapHalfWidth ?? 0;
    if (halfWidth && Math.max(...xs) - Math.min(...xs) > halfWidth) return;
    ctx.beginPath();
    ctx.moveTo(xs[0], ys[0]);
    ctx.lineTo(xs[1], ys[1]);
    ctx.lineTo(xs[2], ys[2]);
    ctx.lineTo(xs[3], ys[3]);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
  }

  /**
   * Which occupied cell is at this canvas position, or null.
   *
   * Reads the positions the last draw() computed, for the same reason PointLayer.pick()
   * does: if picking recomputed them, the popup could describe a glyph other than the one
   * under the cursor, and that is invisible in a screenshot.
   *
   * Hit radius is the glyph's own radius where that exceeds `hitRadius`, so a large pie is
   * hittable across its whole face rather than only near its centre.
   */
  pick(x, y) {
    if (!this.visible || !this._frame) return null;
    let best = -1;
    let bestD = Infinity;
    for (let s = 0; s < this._screen.length; s++) {
      const p = this._screen[s];
      if (!p) continue;
      const d = Math.hypot(p[0] - x, p[1] - y);
      const reach = Math.max(this.options.hitRadius, this._radius(s));
      if (d <= reach && d < bestD) { bestD = d; best = s; }
    }
    if (best < 0) return null;
    return { slot: best, x: this._screen[best][0], y: this._screen[best][1], ...this.cellAt(best) };
  }

  /** Ring-highlight one occupied-cell slot (or none, for null). Purely visual. */
  highlight(slot) {
    this.hovered = slot == null ? -1 : slot;
    return this;
  }
}
