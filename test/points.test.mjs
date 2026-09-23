/*
 * Run with: node --test test/
 *
 * Covers two PointLayer draw modes with no upstream data-shape changes: the
 * angular-radius ring (a style hook returning `ringRadiusDeg`) and the connected-line
 * mode (`connectLive`). Both are exercised with a minimal hand-built mock context and
 * projector rather than a real canvas -- the same pattern robinson.test.mjs already
 * uses for PolygonLayer.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PointLayer } from '../js/points.js';

/** A minimal recording 2-D context: enough surface for PointLayer.draw(). */
function makeCtx() {
  const ops = [];
  let strokeStyle = null;
  let fillStyle = null;
  const strokes = [];   // one entry per stroke() call: { color, path }
  let path = [];

  const ctx = {
    save() {}, restore() {},
    beginPath() { path = []; ops.push(['beginPath']); },
    moveTo(x, y) { path.push(['M', x, y]); ops.push(['moveTo', x, y]); },
    lineTo(x, y) { path.push(['L', x, y]); ops.push(['lineTo', x, y]); },
    arc() { ops.push(['arc']); },
    fill() { ops.push(['fill']); },
    stroke() {
      ops.push(['stroke']);
      strokes.push({ color: strokeStyle, path: path.slice() });
    },
    set fillStyle(v) { fillStyle = v; },
    get fillStyle() { return fillStyle; },
    set strokeStyle(v) { strokeStyle = v; },
    get strokeStyle() { return strokeStyle; },
    set lineWidth(_) {}, set lineJoin(_) {}, set lineCap(_) {},
  };
  return { ctx, ops, strokes };
}

/** A camera looking down +x: visible wherever the unit vector's x component is
 * positive, projected onto the (y, z) plane. Simple, and enough to have a real horizon. */
function makeProjector() {
  return {
    project(v) {
      if (v[0] <= 0) return null;
      return [v[1] * 200, v[2] * 200, v[0]];
    },
  };
}

function baseData(points, overrides = {}) {
  return {
    transport: 'rotations',
    times: [0, 100],
    points,
    categories: {},
    // Identity rotation for plate 0 -- every point stays exactly at its lon/lat.
    rotations: { 0: [[0, 90, 0], [0, 90, 0]] },
    ...overrides,
  };
}

test('ring: absent ringRadiusDeg draws exactly as before (no extra ops)', () => {
  const data = baseData([{ lon: 0, lat: 0, plate_id: 0 }]);
  const layer = new PointLayer(data, { spiderfy: false });
  layer.setTime(0);

  const { ctx, ops } = makeCtx();
  layer.draw(ctx, makeProjector());

  // One symbol: one beginPath, one fill, one stroke. Nothing else.
  assert.equal(ops.filter((o) => o[0] === 'beginPath').length, 1);
  assert.equal(ops.filter((o) => o[0] === 'fill').length, 1);
  assert.equal(ops.filter((o) => o[0] === 'stroke').length, 1);
});

test('ring: a fully visible ring draws as one unbroken closed loop', () => {
  const data = baseData([{ lon: 0, lat: 0, plate_id: 0 }]);
  const layer = new PointLayer(data, {
    spiderfy: false,
    style: () => ({ ringRadiusDeg: 10, ringColor: 'red', ringFill: 'pink' }),
  });
  layer.setTime(0);

  const { ctx, ops } = makeCtx();
  layer.draw(ctx, makeProjector());

  // Two beginPaths now: the ring's own, plus the symbol's.
  assert.equal(ops.filter((o) => o[0] === 'beginPath').length, 2);
  // The ring is closed and every sample is in front of the camera (point 0 faces it
  // dead-on and 10 degrees is nowhere near the limb) -- one moveTo to open the ring's
  // own loop, plus the default circle symbol's own moveTo before its arc().
  const moves = ops.filter((o) => o[0] === 'moveTo');
  assert.equal(moves.length, 2, 'the ring itself should not need more than one moveTo');
  // Both the ring's stroke and fill happened, in addition to the symbol's.
  assert.equal(ops.filter((o) => o[0] === 'fill').length, 2);
  assert.equal(ops.filter((o) => o[0] === 'stroke').length, 2);
});

test('ring: foreshortens near the horizon -- breaks rather than one flat loop', () => {
  // 85 degrees along the equator from the sub-observer point (lon=0): well within the
  // visible hemisphere itself, but a 20-degree ring around it reaches past 90 degrees,
  // i.e. behind the horizon, on its far side.
  const lon = 85;
  const data = baseData([{ lon, lat: 0, plate_id: 0 }]);
  const layer = new PointLayer(data, {
    spiderfy: false,
    style: () => ({ ringRadiusDeg: 20, ringColor: 'red' }),
  });
  layer.setTime(0);

  const { ctx, ops } = makeCtx();
  layer.draw(ctx, makeProjector());

  const moves = ops.filter((o) => o[0] === 'moveTo');
  // More than one moveTo means the pen lifted at least once -- a flat, screen-space
  // ellipse would never do this; a true geographic circle projected point by point does.
  assert.ok(moves.length > 1, `expected the ring to break at least once, got ${moves.length} moveTos`);
  // And it must still have drawn SOMETHING -- the point itself faces the camera.
  const lines = ops.filter((o) => o[0] === 'lineTo');
  assert.ok(lines.length > 0, 'part of the ring should still be visible');
});

test('ring: style hook can vary the ring per point, independent of the symbol', () => {
  const data = baseData([
    { lon: 0, lat: 0, plate_id: 0 },
    { lon: 5, lat: 0, plate_id: 0 },
  ]);
  const layer = new PointLayer(data, {
    spiderfy: false,
    style: (p) => (p.lon === 0 ? { ringRadiusDeg: 5 } : {}),
  });
  layer.setTime(0);

  const { ctx, ops } = makeCtx();
  layer.draw(ctx, makeProjector());

  // Two symbols (2 beginPaths) + one ring (1 beginPath) = 3.
  assert.equal(ops.filter((o) => o[0] === 'beginPath').length, 3);
});

test('connectLive: off by default -- no stroke calls beyond the symbols\' own keylines', () => {
  const data = baseData([
    { lon: 0, lat: 0, plate_id: 0 }, { lon: 5, lat: 0, plate_id: 0 },
  ]);
  const layer = new PointLayer(data, { spiderfy: false });
  layer.setTime(0);

  const { ctx, ops } = makeCtx();
  layer.draw(ctx, makeProjector());
  // One keyline stroke per symbol, nothing more.
  assert.equal(ops.filter((o) => o[0] === 'stroke').length, 2);
});

test('connectLive: strokes a segment between each pair of live points, in input order', () => {
  const data = baseData([
    { lon: 0, lat: 0, plate_id: 0 },
    { lon: 5, lat: 0, plate_id: 0 },
    { lon: 10, lat: 0, plate_id: 0 },
  ]);
  const layer = new PointLayer(data, {
    spiderfy: false,
    connectLive: true,
    style: (p) => ({ fill: `rgb(${p.lon}, 0, 0)` }),
  });
  layer.setTime(0);

  const { ctx, strokes } = makeCtx();
  layer.draw(ctx, makeProjector());

  // 3 symbol keylines + 2 connecting segments (0-1, 1-2).
  const segments = strokes.filter((s) => s.color && s.color.startsWith('rgb('));
  assert.equal(segments.length, 2);
  // Each segment is coloured by its EARLIER vertex.
  assert.equal(segments[0].color, 'rgb(0, 0, 0)');
  assert.equal(segments[1].color, 'rgb(5, 0, 0)');
});

test('connectLive: a non-live point is skipped, not a break in the line', () => {
  // Point 1 has age 50 and lifespan 'since' at time 0 -- wait, 'since' shows while
  // time <= age, so make point 1 too OLD to exist yet at this (early) time by using
  // 'window' instead, centred elsewhere, so it alone is excluded at time 0.
  const data = baseData([
    { lon: 0, lat: 0, plate_id: 0, age: 0 },
    { lon: 5, lat: 0, plate_id: 0, age: 999 },   // far outside the window at time 0
    { lon: 10, lat: 0, plate_id: 0, age: 0 },
  ]);
  const layer = new PointLayer(data, {
    spiderfy: false, connectLive: true, lifespan: 'window', ageWindow: 5,
    style: (p) => ({ fill: `rgb(${p.lon}, 0, 0)` }),
  });
  layer.setTime(0);
  assert.equal(layer._live[1], 0, 'sanity: point 1 should indeed be non-live at time 0');

  const { ctx, strokes } = makeCtx();
  layer.draw(ctx, makeProjector());

  const segments = strokes.filter((s) => s.color && s.color.startsWith('rgb('));
  // Only one connecting segment: point 0 straight through to point 2, point 1 skipped
  // entirely rather than splitting the line into two disconnected pieces.
  assert.equal(segments.length, 1);
  assert.equal(segments[0].color, 'rgb(0, 0, 0)');
});

/**
 * A flat projector with a seam at +-180 longitude -- the shape every real flat
 * Projection's own projector has (Plate Carree/Robinson's `FlatProjector`,
 * Geode's core/flatProjector.ts), reduced to the minimum needed to exercise
 * `_drawConnectedLine()`'s seam handling: `project()` is a simple (lon, lat)
 * screen mapping (always visible, no horizon), and `seamSplit(a, b)` breaks
 * whenever two vectors' longitudes are more than 180 degrees apart -- the
 * same test a REAL flat projector's antimeridian handling uses.
 */
function makeSeamProjector() {
  const DEG = Math.PI / 180;
  function lonLat(v) {
    return { lon: Math.atan2(v[1], v[0]) / DEG, lat: Math.asin(v[2]) / DEG };
  }
  function geoVec(lon, lat) {
    const la = lat * DEG; const lo = lon * DEG; const c = Math.cos(la);
    return [c * Math.cos(lo), c * Math.sin(lo), Math.sin(la)];
  }
  return {
    project(v) {
      const { lon, lat } = lonLat(v);
      return [lon * 2, -lat * 2, 1];
    },
    seamSplit(a, b) {
      const la = lonLat(a); const lb = lonLat(b);
      if (Math.abs(la.lon - lb.lon) <= 180) return null;
      const seamLat = (la.lat + lb.lat) / 2;
      return la.lon > 0
        ? [geoVec(180, seamLat), geoVec(-180, seamLat)]
        : [geoVec(-180, seamLat), geoVec(180, seamLat)];
    },
  };
}

test('connectLive: a segment crossing the antimeridian breaks at the seam, not straight across', () => {
  // 20 degrees apart on the sphere (170 -> -170 the short way round), but a huge jump in
  // raw longitude -- exactly the case Map Orientation makes common (docs/plans -- Geode's
  // Map Orientation session: an oblique aspect can put the seam somewhere data crosses
  // often, not just the antimeridian a dataset happened to avoid).
  const data = baseData([
    { lon: 170, lat: 0, plate_id: 0 },
    { lon: -170, lat: 0, plate_id: 0 },
  ]);
  const layer = new PointLayer(data, {
    spiderfy: false, connectLive: true,
    style: (p) => ({ fill: `rgb(${p.lon < 0 ? 0 : p.lon}, 0, 0)` }),
  });
  layer.setTime(0);

  const { ctx, strokes } = makeCtx();
  layer.draw(ctx, makeSeamProjector());

  const segments = strokes.filter((s) => s.color && s.color.startsWith('rgb('));
  // One straight segment would be a single stroke spanning the whole map (170 to -170 in
  // screen x, a jump of 680 units under this projector's *2 scale). Seam-broken, it is TWO
  // strokes instead, each running from a real point to its own nearby map edge.
  assert.equal(segments.length, 2, 'expected the seam crossing to produce two strokes, not one straight line');
  for (const s of segments) {
    const xs = s.path.filter((p) => p[0] === 'M' || p[0] === 'L').map((p) => p[1]);
    const span = Math.max(...xs) - Math.min(...xs);
    assert.ok(span < 100, `each half of a seam-broken segment should be short, got span ${span}`);
  }
});

test('connectLive: breaks at the horizon, unlike a skipped dead point', () => {
  // Point 1 sits behind the camera (lon=180 with this projector's +x-facing camera) --
  // live, but never given a screen position at all.
  const data = baseData([
    { lon: 0, lat: 0, plate_id: 0 },
    { lon: 180, lat: 0, plate_id: 0 },
    { lon: 10, lat: 0, plate_id: 0 },
  ]);
  const layer = new PointLayer(data, {
    spiderfy: false, connectLive: true,
    style: (p) => ({ fill: `rgb(${p.lon}, 0, 0)` }),
  });
  layer.setTime(0);
  assert.equal(layer._live[1], 1, 'sanity: point 1 is live, just off-camera');

  const { ctx, strokes } = makeCtx();
  layer.draw(ctx, makeProjector());

  const segments = strokes.filter((s) => s.color && s.color.startsWith('rgb('));
  // No segment reaches across the culled point -- 0 and 2 are NOT joined.
  assert.equal(segments.length, 0);
});

/* ---- plate_forced: an explicit anchor-plate override stays visible at every time --- */

test('plate_forced: an untrusted plate-0 point (partitioning fell through) is hidden before the present', () => {
  const data = baseData([{ lon: 0, lat: 0, plate_id: 0, age: 100 }]);
  const layer = new PointLayer(data, { spiderfy: false });
  assert.equal(layer.isLive(layer.points[0], 50), false);
  assert.equal(layer.isLive(layer.points[0], 0), true);
});

test('plate_forced: an explicit anchor-plate override stays visible at every time', () => {
  const data = baseData([{ lon: 0, lat: 0, plate_id: 0, plate_forced: true, age: 100 }]);
  const layer = new PointLayer(data, { spiderfy: false });
  assert.equal(layer.isLive(layer.points[0], 50), true);
  assert.equal(layer.isLive(layer.points[0], 0), true);
});
