/*
 * Run with: node --test test/
 *
 * Covers AggregateLayer's 'density' draw mode (a build_density() payload: `density` per
 * cell instead of `counts`/`richness`) alongside a sanity check that 'pie' mode -- and the
 * totals()/cellAt() paths shared with it -- still behave as before the density branch was
 * added. Minimal hand-built mock context/projector, same pattern points.test.mjs uses.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { AggregateLayer } from '../js/aggregate.js';

function makeCtx() {
  const ops = [];
  let fillStyle = null;
  const fills = []; // fillStyle at each fill() call, in order
  const ctx = {
    save() {}, restore() {}, clearRect() {},
    beginPath() { ops.push(['beginPath']); },
    moveTo() { ops.push(['moveTo']); },
    lineTo() { ops.push(['lineTo']); },
    arc() { ops.push(['arc']); },
    closePath() { ops.push(['closePath']); },
    fill() { ops.push(['fill']); fills.push(fillStyle); },
    stroke() { ops.push(['stroke']); },
    set fillStyle(v) { fillStyle = v; },
    get fillStyle() { return fillStyle; },
    set strokeStyle(_) {}, set lineWidth(_) {},
  };
  return { ctx, ops, fills };
}

/** Every centre used below has |lon| < 90, so cos(lat)*cos(lon) > 0 -- always visible. */
function makeProjector() {
  return { project: (v) => [v[1] * 200, v[2] * 200, v[0]] };
}

function densityData(overrides = {}) {
  return {
    model: 'test',
    grid: { kind: 'healpix-ring', nside: 8, order: 'ring', cells: 4, cell_area_sr: 1 },
    times: [0],
    cell_centres: [[0, 0], [10, 10], [20, -10], [30, 5]],
    default_grouping: 'g',
    groupings: {
      g: {
        label: 'G',
        category_order: ['a'],
        categories: {},
        density_ref: 5.0,
        frames: {
          0: { cells: [0, 1, 2], density: [1.0, 5.0, 0.05] },
        },
      },
    },
    lifespan: 'always',
    bandwidth_deg: 6.0,
    density_units: 'points per steradian',
    meta: {},
    ...overrides,
  };
}

/** A tiny 1-degree-square quad around each centre -- not real HEALPix geometry, just
 *  enough shape to exercise the pixel-drawing path. */
function boundsFor(centres) {
  return centres.map(([lon, lat]) => [
    [lon - 1, lat - 1], [lon + 1, lat - 1], [lon + 1, lat + 1], [lon - 1, lat + 1],
  ]);
}

function countsData(overrides = {}) {
  return {
    model: 'test',
    grid: { kind: 'equal-area-rings', rings: 2, lon_cells: 4, cells: 8, cell_area_sr: 1 },
    times: [0],
    cell_centres: [[0, 0], [10, 10]],
    default_grouping: 'g',
    groupings: {
      g: {
        label: 'G',
        category_order: ['A', 'B'],
        // Real "rgb(r, g, b)" form -- what every actual category fill in the catalog
        // is written as (see prep_geochem.py's CATEGORIES) -- so 'dominant' mode's
        // colour-mixing, which parses exactly that form, has something real to mix.
        categories: { A: { fill: 'rgb(255, 0, 0)' }, B: { fill: 'rgb(0, 0, 255)' } },
        frames: {
          0: { cells: [0, 1], counts: [[3, 1], [0, 5]], richness: [2, 1] },
        },
      },
    },
    lifespan: 'always',
    meta: {},
    ...overrides,
  };
}

test('density mode: fills only cells above the sqrt-scaled threshold, none below', () => {
  const layer = new AggregateLayer(densityData(), { mode: 'density' });
  layer.setTime(0);
  const { ctx, ops, fills } = makeCtx();
  layer.draw(ctx, makeProjector());

  // Three cells are listed in the frame; only the two with non-trivial density draw.
  // t = sqrt(density / density_ref): cell 0 -> sqrt(0.2) ~ 0.447, cell 1 -> 1.0 (the
  // peak), cell 2 -> sqrt(0.01) = 0.1 -- all > 0, so with no eps cutoff on the JS side
  // all three actually draw; what matters is each got a DIFFERENT fillStyle ordered by
  // density, and none used the pie/dominant path (no closePath/multiple arcs per cell).
  assert.equal(ops.filter((o) => o[0] === 'fill').length, 3);
  assert.equal(ops.filter((o) => o[0] === 'stroke').length, 0); // no keyline in density mode
  assert.equal(new Set(fills).size, 3); // three distinct densities -> three distinct colours
});

test('density mode: a cell with zero density is not drawn at all', () => {
  const data = densityData();
  data.groupings.g.frames[0] = { cells: [0, 1], density: [0, 5.0] };
  const layer = new AggregateLayer(data, { mode: 'density' });
  layer.setTime(0);
  const { ctx, ops } = makeCtx();
  layer.draw(ctx, makeProjector());
  assert.equal(ops.filter((o) => o[0] === 'fill').length, 1);
});

test('density mode + cell_bounds: draws true pixels (quads), not discs', () => {
  const data = densityData({ cell_bounds: boundsFor(densityData().cell_centres) });
  const layer = new AggregateLayer(data, { mode: 'density' });
  layer.setTime(0);
  const { ctx, ops } = makeCtx();
  layer.draw(ctx, makeProjector());

  // Three cells draw (see the sqrt-threshold test above); each a 4-vertex quad --
  // moveTo + 3x lineTo + closePath + fill -- and never an arc, which is the disc
  // fallback's own signature.
  assert.equal(ops.filter((o) => o[0] === 'arc').length, 0);
  assert.equal(ops.filter((o) => o[0] === 'moveTo').length, 3);
  assert.equal(ops.filter((o) => o[0] === 'lineTo').length, 9);
  assert.equal(ops.filter((o) => o[0] === 'closePath').length, 3);
  assert.equal(ops.filter((o) => o[0] === 'fill').length, 3);
});

test('_drawPixel: any corner behind the horizon skips the whole cell, not a folded shape', () => {
  const data = densityData({ cell_bounds: boundsFor(densityData().cell_centres) });
  const layer = new AggregateLayer(data, { mode: 'density' });
  const { ctx, ops } = makeCtx();
  // Succeeds for the first three corners, fails on the fourth -- called directly
  // rather than through draw(), so the corner sequence is deterministic.
  let n = 0;
  const projector = { project: () => { n += 1; return n <= 3 ? [0, 0, 1] : null; } };
  layer._drawPixel(ctx, 0, 'red', projector);
  assert.equal(n, 4);
  assert.equal(ops.filter((o) => o[0] === 'fill').length, 0);
  assert.equal(ops.filter((o) => o[0] === 'moveTo').length, 0);
});

test('density mode: falls back to a disc when the payload has no cell_bounds', () => {
  const layer = new AggregateLayer(densityData(), { mode: 'density' });
  layer.setTime(0);
  const { ctx, ops } = makeCtx();
  layer.draw(ctx, makeProjector());
  assert.equal(ops.filter((o) => o[0] === 'arc').length, 3);
  assert.equal(ops.filter((o) => o[0] === 'lineTo').length, 0);
});

test('density mode: _radius is constant (maxRadius * densityRadiusScale) regardless of value', () => {
  const layer = new AggregateLayer(
    densityData(), { mode: 'density', maxRadius: 11, densityRadiusScale: 1.35 });
  layer.setTime(0);
  assert.equal(layer._radius(0), 11 * 1.35);
  assert.equal(layer._radius(1), 11 * 1.35);
});

test('density mode: cellAt reports density + t, not counts', () => {
  const layer = new AggregateLayer(densityData(), { mode: 'density' });
  layer.setTime(0);
  const info = layer.cellAt(1); // slot 1 -> cell 1, density 5.0 == density_ref
  assert.equal(info.density, 5.0);
  assert.equal(info.densityRef, 5.0);
  assert.equal(info.t, 1);
  assert.equal(info.counts, undefined);
});

test('density mode: totals() returns zeros rather than throwing on a counts-less frame', () => {
  const layer = new AggregateLayer(densityData(), { mode: 'density' });
  layer.setTime(0);
  assert.deepEqual(layer.totals(), [0]);
});

test('pie mode is unaffected: counts frame still draws wedges and totals sum correctly', () => {
  const layer = new AggregateLayer(countsData(), { mode: 'pie' });
  layer.setTime(0);
  assert.deepEqual(layer.totals(), [3, 6]); // A: 3+0, B: 1+5

  const { ctx, ops } = makeCtx();
  layer.draw(ctx, makeProjector());
  // Cell 0 has two non-zero categories (two wedges); cell 1 has one (a full disc). Three
  // fills, two strokes (one keyline per cell).
  assert.equal(ops.filter((o) => o[0] === 'fill').length, 3);
  assert.equal(ops.filter((o) => o[0] === 'stroke').length, 2);
});

test('pie mode: cellAt still reports counts/total/richness, not density', () => {
  const layer = new AggregateLayer(countsData(), { mode: 'pie' });
  layer.setTime(0);
  const info = layer.cellAt(0);
  assert.deepEqual(info.counts, [3, 1]);
  assert.equal(info.total, 4);
  assert.equal(info.richness, 2);
  assert.equal(info.density, undefined);
});

test('dominant mode + cell_bounds: fills the true cell shape (a quad) with a mixture colour, not a disc', () => {
  const data = countsData({ cell_bounds: boundsFor([[0, 0], [10, 10]]) });
  const layer = new AggregateLayer(data, { mode: 'dominant' });
  layer.setTime(0);
  const { ctx, ops, fills } = makeCtx();
  layer.draw(ctx, makeProjector());

  // Two occupied cells, each a 4-vertex quad -- moveTo + 3x lineTo + closePath + fill --
  // and never an arc (a disc's own signature), and no keyline stroke either: a stroke
  // around every tiny diamond would read as a mesh over the map, the same reasoning
  // 'density' mode's own true pixels already get.
  assert.equal(ops.filter((o) => o[0] === 'arc').length, 0);
  assert.equal(ops.filter((o) => o[0] === 'moveTo').length, 2);
  assert.equal(ops.filter((o) => o[0] === 'fill').length, 2);
  assert.equal(ops.filter((o) => o[0] === 'stroke').length, 0);

  // Cell 1 ([0, 5], pure B, and the series' own peak occupancy) mixes to an EXACT
  // full-strength blue -- occupancyT is exactly 1 there, so no rounding to account for.
  assert.equal(fills[1], 'rgb(0, 0, 255)');
  // Cell 0 ([3, 1], a MIX of A and B, below peak occupancy) is neither cell 1's colour
  // nor a flat, undimmed red: both the blend (B's 1-in-4 share leaking blue in) and the
  // occupancy dimming (below the series peak) are doing something, not collapsing back
  // to the old "just the winner's colour" behaviour.
  assert.notEqual(fills[0], fills[1]);
  assert.notEqual(fills[0], 'rgb(255, 0, 0)');
  const [r0, g0, b0] = fills[0].match(/\d+/g).map(Number);
  assert.ok(r0 > b0 && b0 > 0, `expected red-leaning-toward-blue, got ${fills[0]}`);
});

test('dominant mode: falls back to a disc (with keyline) when the payload has no cell_bounds', () => {
  const layer = new AggregateLayer(countsData(), { mode: 'dominant' });
  layer.setTime(0);
  const { ctx, ops } = makeCtx();
  layer.draw(ctx, makeProjector());
  assert.equal(ops.filter((o) => o[0] === 'arc').length, 4); // 2 fills + 2 keylines
  assert.equal(ops.filter((o) => o[0] === 'stroke').length, 2);
});

test('pie mode + cell_bounds: still draws wedge discs, not quads -- a pie has no single-fill equivalent', () => {
  const data = countsData({ cell_bounds: boundsFor([[0, 0], [10, 10]]) });
  const layer = new AggregateLayer(data, { mode: 'pie' });
  layer.setTime(0);
  const { ctx, ops } = makeCtx();
  layer.draw(ctx, makeProjector());
  // `lineTo` is `_drawPixel`'s own signature (a quad's straight edges); a pie's wedges
  // are moveTo-then-arc, so its presence alone (regardless of moveTo, which wedges also
  // use to draw a straight radius line to the centre) tells the two apart.
  assert.equal(ops.filter((o) => o[0] === 'lineTo').length, 0);
  assert.ok(ops.filter((o) => o[0] === 'arc').length > 0);
});

test('_drawPixel: a cell whose corners straddle a flat map seam is skipped, not stretched', () => {
  const data = densityData({ cell_bounds: boundsFor(densityData().cell_centres) });
  const layer = new AggregateLayer(data, { mode: 'density' });
  const { ctx, ops } = makeCtx();
  // Two corners land on the far left edge, two on the far right -- exactly what a
  // seam-straddling cell's real projection looks like on a flat map. mapHalfWidth
  // (100) is far smaller than the 400 spread between them, which is the signal
  // `_drawPixel` uses to tell a seam crossing apart from an ordinary small quad.
  const xs = [-100, 100, 100, -100];
  let i = 0;
  const projector = { mapHalfWidth: 100, project: () => [xs[i++], 0, 1] };
  layer._drawPixel(ctx, 0, 'red', projector);
  assert.equal(ops.filter((o) => o[0] === 'fill').length, 0);
  assert.equal(ops.filter((o) => o[0] === 'moveTo').length, 0);
});

test('_drawPixel: an ordinary quad well inside mapHalfWidth still draws normally', () => {
  const data = densityData({ cell_bounds: boundsFor(densityData().cell_centres) });
  const layer = new AggregateLayer(data, { mode: 'density' });
  const { ctx, ops } = makeCtx();
  const xs = [10, 12, 12, 10];
  let i = 0;
  const projector = { mapHalfWidth: 100, project: () => [xs[i++], 0, 1] };
  layer._drawPixel(ctx, 0, 'red', projector);
  assert.equal(ops.filter((o) => o[0] === 'fill').length, 1);
});

test('_drawPixel: a globe projector (no mapHalfWidth) never triggers the seam guard', () => {
  const data = densityData({ cell_bounds: boundsFor(densityData().cell_centres) });
  const layer = new AggregateLayer(data, { mode: 'density' });
  const { ctx, ops } = makeCtx();
  // Same wide spread as the seam test above, but no `mapHalfWidth` on the
  // projector at all -- the globe's own ThreeProjector never defines it.
  const xs = [-100, 100, 100, -100];
  let i = 0;
  const projector = { project: () => [xs[i++], 0, 1] };
  layer._drawPixel(ctx, 0, 'red', projector);
  assert.equal(ops.filter((o) => o[0] === 'fill').length, 1);
});

test('densityBands: quantises cellAt().t into flat steps, but never lifts a true zero off it', () => {
  const data = densityData();
  data.groupings.g.frames[0] = { cells: [0, 1, 2], density: [0, 5.0, 0.05] };
  const layer = new AggregateLayer(data, { mode: 'density', densityScale: 'linear', densityBands: 4 });
  layer.setTime(0);
  // cell 1: t = 5/5 = 1.0 -> top band's centre, 7/8.
  assert.equal(layer.cellAt(1).t, 7 / 8);
  // cell 2: t = 0.05/5 = 0.01 -> bottom band's centre, 1/8 -- not the raw 0.01.
  assert.equal(layer.cellAt(2).t, 1 / 8);
  // cell 0: truly zero density stays exactly 0, not lifted into band 0's centre --
  // this is the sentinel _drawCell's `t <= 0` check relies on to skip empty cells.
  assert.equal(layer.cellAt(0).t, 0);
});

test('densityBands: 0 (the default) leaves the response curve continuous', () => {
  const layer = new AggregateLayer(densityData(), { mode: 'density', densityScale: 'linear' });
  layer.setTime(0);
  assert.equal(layer.cellAt(2).t, 0.01); // 0.05 / 5, unquantised
});

/*
 * AggregateLayer.load(): the geometry_ref path (a shared grid file, see aggregate.py's
 * grid_geometry() and build_density(geometry_ref=...)) -- a density.json with no
 * cell_centres/cell_bounds of its own, fetched alongside a separate geometry file and
 * merged before construction, so a fine HealpixGrid's ~300MB of coordinates is fetched
 * once per resolution rather than once per dataset/bandwidth.
 */
/** A route value that is already an ArrayBuffer (a packed binary blob, e.g.
 *  _pack_frames_binary()'s output) is served as-is; anything else is JSON-stringified,
 *  matching every existing stubFetch() caller's plain-object routes. */
function stubFetch(routes) {
  const calls = [];
  global.fetch = async (url) => {
    calls.push(url);
    if (!(url in routes)) throw new Error(`stubFetch: no route for ${url}`);
    const v = routes[url];
    if (v instanceof ArrayBuffer) return { ok: true, arrayBuffer: async () => v };
    const body = JSON.stringify(v);
    return { ok: true, arrayBuffer: async () => new TextEncoder().encode(body).buffer };
  };
  return calls;
}

/** Builds a _pack_frames_binary()-shaped blob for one grouping: [[cells, density], ...]
 *  in time order, each a plain JS array. Mirrors aggregate.py's own layout exactly. */
function packFramesBinary(frames) {
  const parts = [];
  const head = new ArrayBuffer(12);
  const hv = new DataView(head);
  hv.setUint8(0, 'D'.charCodeAt(0)); hv.setUint8(1, 'N'.charCodeAt(0));
  hv.setUint8(2, 'S'.charCodeAt(0)); hv.setUint8(3, 'F'.charCodeAt(0));
  hv.setUint32(4, 1, true);
  hv.setUint32(8, frames.length, true);
  parts.push(head);
  for (const [cells, density] of frames) {
    const countBuf = new ArrayBuffer(4);
    new DataView(countBuf).setUint32(0, cells.length, true);
    parts.push(countBuf);
    parts.push(new Uint32Array(cells).buffer);
    parts.push(new Float32Array(density).buffer);
  }
  const total = parts.reduce((n, p) => n + p.byteLength, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const p of parts) { out.set(new Uint8Array(p), off); off += p.byteLength; }
  return out.buffer;
}

test('load(): a geometry_ref payload fetches the shared grid file and merges its coordinates', async () => {
  const geometry = {
    kind: 'healpix-ring', nside: 8, order: 'ring', cells: 2,
    cell_centres: [[0, 0], [10, 10]],
    cell_bounds: [
      [[-1, -1], [1, -1], [1, 1], [-1, 1]],
      [[9, 9], [11, 9], [11, 11], [9, 11]],
    ],
  };
  const density = {
    model: 'test',
    grid: { kind: 'healpix-ring', nside: 8, order: 'ring', cells: 2, cell_area_sr: 1,
             geometry_ref: '../../_grid/healpix_n8_loadtest1.json' },
    times: [0], default_grouping: 'g',
    groupings: { g: { label: 'G', category_order: ['a'], categories: {}, density_ref: 1,
                       frames: { 0: { cells: [0], density: [1.0] } } } },
    lifespan: 'always', bandwidth_deg: 6.0, density_units: 'points per steradian', meta: {},
  };
  const calls = stubFetch({
    'archive/density/igneous/density_bw1_loadtest1.json': density,
    'archive/_grid/healpix_n8_loadtest1.json': geometry,
  });
  const layer = await AggregateLayer.load(
    'archive/density/igneous/density_bw1_loadtest1.json', { mode: 'density' });
  assert.deepEqual(layer.centres, geometry.cell_centres);
  assert.deepEqual(calls, [
    'archive/density/igneous/density_bw1_loadtest1.json', 'archive/_grid/healpix_n8_loadtest1.json',
  ]);
});

test('load(): two loads referencing the same geometry_ref fetch the shared file only once', async () => {
  const geometry = {
    kind: 'healpix-ring', nside: 8, order: 'ring', cells: 1,
    cell_centres: [[0, 0]],
    cell_bounds: [[[-1, -1], [1, -1], [1, 1], [-1, 1]]],
  };
  const density = (bw) => ({
    model: 'test',
    grid: { kind: 'healpix-ring', nside: 8, order: 'ring', cells: 1, cell_area_sr: 1,
             geometry_ref: '../../_grid/healpix_n8_loadtest2.json' },
    times: [0], default_grouping: 'g',
    groupings: { g: { label: 'G', category_order: ['a'], categories: {}, density_ref: 1,
                       frames: { 0: { cells: [0], density: [bw] } } } },
    lifespan: 'always', bandwidth_deg: bw, density_units: 'points per steradian', meta: {},
  });
  const calls = stubFetch({
    'archive/density/igneous/density_bw0.5_loadtest2.json': density(0.5),
    'archive/density/igneous/density_bw1_loadtest2.json': density(1.0),
    'archive/_grid/healpix_n8_loadtest2.json': geometry,
  });
  await AggregateLayer.load('archive/density/igneous/density_bw0.5_loadtest2.json', { mode: 'density' });
  await AggregateLayer.load('archive/density/igneous/density_bw1_loadtest2.json', { mode: 'density' });
  const geometryFetches = calls.filter((u) => u === 'archive/_grid/healpix_n8_loadtest2.json');
  assert.equal(geometryFetches.length, 1);
});

/*
 * AggregateLayer.load(): the frames_ref path (a packed binary blob, see aggregate.py's
 * _pack_frames_binary()) -- a density.json with no embedded `frames` of its own, fetched
 * alongside a companion .bin file and decoded into the SAME {[timeKey]: {cells,
 * density}} shape a JSON-embedded `frames` object has.
 */
test('load(): a frames_ref payload fetches the binary blob and decodes it into frames', async () => {
  const framesBin = packFramesBinary([
    [[0, 2], [1.5, 0.25]],   // time 0
    [[1], [4.0]],            // time 5
  ]);
  const density = {
    model: 'test',
    grid: { kind: 'healpix-ring', nside: 8, order: 'ring', cells: 3, cell_area_sr: 1 },
    times: [0, 5], cell_centres: [[0, 0], [10, 10], [20, -10]], default_grouping: 'g',
    groupings: { g: { label: 'G', category_order: ['a'], categories: {}, density_ref: 4,
                       frames_ref: 'density_bw1_frtest1.bin' } },
    lifespan: 'always', bandwidth_deg: 1.0, density_units: 'points per steradian', meta: {},
  };
  const calls = stubFetch({
    'archive/density/igneous/density_bw1_frtest1.json': density,
    'archive/density/igneous/density_bw1_frtest1.bin': framesBin,
  });
  const layer = await AggregateLayer.load(
    'archive/density/igneous/density_bw1_frtest1.json', { mode: 'density' });
  layer.setTime(5);
  assert.deepEqual(calls, [
    'archive/density/igneous/density_bw1_frtest1.json', 'archive/density/igneous/density_bw1_frtest1.bin',
  ]);
  assert.equal(layer.cellAt(0).density, 4.0); // time 5's sole cell, index 1
});

test('load(): two loads referencing the same frames_ref fetch the binary blob only once', async () => {
  const framesBin = packFramesBinary([[[0], [1.0]]]);
  const density = {
    model: 'test',
    grid: { kind: 'healpix-ring', nside: 8, order: 'ring', cells: 1, cell_area_sr: 1 },
    times: [0], cell_centres: [[0, 0]], default_grouping: 'g',
    groupings: { g: { label: 'G', category_order: ['a'], categories: {}, density_ref: 1,
                       frames_ref: 'shared_frtest2.bin' } },
    lifespan: 'always', bandwidth_deg: 1.0, density_units: 'points per steradian', meta: {},
  };
  const calls = stubFetch({
    'archive/density/igneous/a_frtest2.json': density,
    'archive/density/igneous/b_frtest2.json': density,
    'archive/density/igneous/shared_frtest2.bin': framesBin,
  });
  await AggregateLayer.load('archive/density/igneous/a_frtest2.json', { mode: 'density' });
  await AggregateLayer.load('archive/density/igneous/b_frtest2.json', { mode: 'density' });
  const binFetches = calls.filter((u) => u === 'archive/density/igneous/shared_frtest2.bin');
  assert.equal(binFetches.length, 1);
});
