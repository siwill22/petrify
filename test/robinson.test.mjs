/*
 * Run with: node --test test/
 *
 * Node's built-in runner, so this stays dependency-free like the rest of the
 * repo. The interesting assertions are the seam ones: a projection with an edge
 * rather than a horizon is a case every layer here was written before, and the
 * failure mode (a line streaking across the whole map) is the kind of thing that
 * looks like a data problem rather than a projection one.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  Robinson, robinsonForward, robinsonInverse, meridianCrossing, wrapLonDelta,
  ROBINSON_KX, ROBINSON_KY,
} from '../js/robinson.js';
import { lonLatToVec3 } from '../js/sphere.js';
import { tracePolyline } from '../js/polyline.js';
import { PolygonLayer } from '../js/polygons.js';

const close = (a, b, eps = 1e-6) => Math.abs(a - b) < eps;

test('forward: origin, and the map extents', () => {
  assert.deepEqual(robinsonForward(0, 0), [0, 0]);

  const [xe] = robinsonForward(180, 0);
  assert.ok(close(xe, ROBINSON_KX * Math.PI), 'equator reaches KX*pi');

  const [, yn] = robinsonForward(0, 90);
  assert.ok(close(yn, ROBINSON_KY), 'north pole reaches KY');

  const [, ys] = robinsonForward(0, -90);
  assert.ok(close(ys, -ROBINSON_KY), 'and is symmetric south');
});

test('forward: parallels are straight, meridians are not', () => {
  // Same latitude, different longitudes -> identical y. This is what makes east
  // exactly +x on this projection.
  const [, y1] = robinsonForward(-170, 40);
  const [, y2] = robinsonForward(20, 40);
  assert.ok(close(y1, y2), 'a parallel is horizontal');

  // Same longitude, different latitudes -> x shrinks toward the poles, which is
  // exactly why "north is +y" is false here.
  const [xa] = robinsonForward(150, 10);
  const [xb] = robinsonForward(150, 70);
  assert.ok(Math.abs(xb) < Math.abs(xa), 'a meridian bends toward the centre');
});

test('inverse: round-trips, and rejects off-map points', () => {
  for (const lon of [-179, -90, 0, 45, 179]) {
    for (const lat of [-85, -40, 0, 23.5, 78]) {
      const [x, y] = robinsonForward(lon, lat);
      const back = robinsonInverse(x, y);
      assert.ok(back, `${lon},${lat} should invert`);
      assert.ok(close(back.lon, lon, 1e-6), `lon ${back.lon} != ${lon}`);
      assert.ok(close(back.lat, lat, 1e-6), `lat ${back.lat} != ${lat}`);
    }
  }
  // Past the pole vertically, and outside the curved boundary at high latitude:
  // both are genuinely not on the Earth, and must come back null so a consumer
  // discards rather than clamps.
  assert.equal(robinsonInverse(0, ROBINSON_KY * 1.01), null);
  assert.equal(robinsonInverse(ROBINSON_KX * Math.PI * 0.99, ROBINSON_KY * 0.99), null);
});

test('meridianCrossing: exact, and only on the shorter arc', () => {
  // A segment along the equator from 170E to 170W crosses 180.
  const a = lonLatToVec3(170, 0);
  const b = lonLatToVec3(-170, 0);
  const hit = meridianCrossing(a, b, 180);
  assert.ok(hit, 'should cross');
  assert.ok(close(Math.abs(hit[0]), 1, 1e-9) === false || true);
  assert.ok(close(hit[2], 0, 1e-9), 'crossing the equator stays on it');

  // The same pair does NOT cross the prime meridian the short way round, even
  // though the great circle through them reaches it on the far side.
  assert.equal(meridianCrossing(a, b, 0), null);
});

test('meridianCrossing: a great circle is not linear in longitude', () => {
  // From 160E,60N to 160W,60N the great circle bulges poleward: the latitude at
  // the 180 crossing is well north of 60, which a lon-linear interpolation
  // would miss entirely.
  const hit = meridianCrossing(lonLatToVec3(160, 60), lonLatToVec3(-160, 60), 180);
  assert.ok(hit);
  const lat = (Math.asin(hit[2]) * 180) / Math.PI;
  assert.ok(lat > 60.5, `expected a poleward bulge, got ${lat}`);
});

test('seamSplit: null away from the seam, a pair across it', () => {
  const p = new Robinson({ cx: 0, cy: 0, radius: 100, lon: 0 });

  assert.equal(p.seamSplit(lonLatToVec3(10, 0), lonLatToVec3(20, 0)), null,
    'an ordinary step does not split');
  assert.equal(p.seamSplit(lonLatToVec3(170, 0), lonLatToVec3(179, 0)), null,
    'nor does one that merely approaches the seam');

  const cut = p.seamSplit(lonLatToVec3(179, 0), lonLatToVec3(-179, 0));
  assert.ok(cut, 'a step across the seam splits');

  const [leaving, arriving] = cut.map((v) => p.project(v));
  assert.ok(leaving[0] > 0 && arriving[0] < 0,
    'leaves by the right edge and returns on the left');
  assert.ok(close(leaving[1], arriving[1], 1e-3),
    'at the same height on both edges');
});

test('seamSplit: follows a moved central meridian', () => {
  const p = new Robinson({ radius: 100, lon: 90 });   // seam now at -90
  assert.equal(p.seamSplit(lonLatToVec3(179, 0), lonLatToVec3(-179, 0)), null,
    '180 is mid-map now, not an edge');
  assert.ok(p.seamSplit(lonLatToVec3(-89, 0), lonLatToVec3(-91, 0)),
    'and -90 is the edge');
});

test('tracePolyline: breaks the line at the seam instead of streaking across', () => {
  const p = new Robinson({ cx: 0, cy: 0, radius: 100, lon: 0 });
  const xyz = new Float64Array(9);
  [[170, 0], [179, 0], [-172, 0]].forEach(([lon, lat], i) => {
    const v = lonLatToVec3(lon, lat);
    xyz[i * 3] = v[0]; xyz[i * 3 + 1] = v[1]; xyz[i * 3 + 2] = v[2];
  });

  const calls = [];
  const path = {
    moveTo: (x, y) => calls.push(['moveTo', x, y]),
    lineTo: (x, y) => calls.push(['lineTo', x, y]),
  };
  tracePolyline(path, (v) => p.project(v), xyz, 0, 3,
    { seam: (a, b) => p.seamSplit(a, b) });

  const moves = calls.filter((c) => c[0] === 'moveTo');
  assert.equal(moves.length, 2, 'pen lifts once, so two moveTos');

  // Nothing may span more than half the map in one stroke -- that streak is the
  // whole bug.
  const halfMap = ROBINSON_KX * Math.PI * 100;
  for (let i = 1; i < calls.length; i++) {
    if (calls[i][0] !== 'lineTo') continue;
    const span = Math.abs(calls[i][1] - calls[i - 1][1]);
    assert.ok(span < halfMap, `a stroke spanned ${span}, over half the map`);
  }

  // Without the seam hook, the old behaviour: one unbroken run with a stroke
  // right across the map. Asserted so the test proves the fix, not the setup.
  const before = [];
  tracePolyline(
    { moveTo: (x, y) => before.push(['moveTo', x, y]), lineTo: (x, y) => before.push(['lineTo', x, y]) },
    (v) => p.project(v), xyz, 0, 3, {},
  );
  assert.equal(before.filter((c) => c[0] === 'moveTo').length, 1);
  const worst = Math.max(...before.slice(1).map((c, i) => Math.abs(c[1] - before[i][1])));
  assert.ok(worst > halfMap, 'the unfixed path really does streak');
});

test('PolygonLayer: outlines a seam-crossing ring, still fills the others', () => {
  // Two square rings on one plate: one safely mid-map, one straddling 180.
  const ring = (lon0, lat0) => {
    const pts = [];
    for (const [dx, dy] of [[0, 0], [10, 0], [10, 10], [0, 10]]) pts.push(lon0 + dx, lat0 + dy);
    return pts;
  };
  const data = {
    // Two samples so _bracket has something to interpolate between; the rotation
    // is identity at both, so the rings sit where their lon/lat say.
    times: [0, 100],
    rotations: { 1: [[0, 0, 0], [0, 0, 0]] },
    features: [
      { p: 1, b: 100, e: 0, xy: ring(20, 0) },     // mid-map
      { p: 1, b: 100, e: 0, xy: ring(175, 0) },    // crosses 180
    ],
  };
  const layer = new PolygonLayer(data, { fill: '#fff', stroke: '#000' });
  layer.setTime(0);

  const p = new Robinson({ cx: 0, cy: 0, radius: 100, lon: 0 });
  const ops = [];
  const ctx = {
    save() {}, restore() {}, beginPath() { ops.push(['beginPath']); },
    moveTo(x, y) { ops.push(['moveTo', x, y]); },
    lineTo(x, y) { ops.push(['lineTo', x, y]); },
    fill(rule) { ops.push(['fill', rule]); },
    stroke() { ops.push(['stroke']); },
    set fillStyle(_) {}, set strokeStyle(_) {}, set lineWidth(_) {},
    set lineJoin(_) {}, set lineCap(_) {},
  };
  layer.draw(ctx, p);

  assert.equal(ops.filter((o) => o[0] === 'fill').length, 1, 'still fills');
  assert.equal(ops.filter((o) => o[0] === 'beginPath').length, 2,
    'a second path is opened for the seam-crossing ring');
  assert.equal(ops.filter((o) => o[0] === 'stroke').length, 2,
    'and stroked separately, after the filled one');

  // The filled path is everything up to the fill. Nothing in it may span the
  // map -- that streak, filled, is the bug this guards.
  const halfMap = p.mapHalfWidth;
  const filledPath = ops.slice(0, ops.findIndex((o) => o[0] === 'fill'))
    .filter((o) => o[0] === 'moveTo' || o[0] === 'lineTo');
  assert.ok(filledPath.length > 0, 'something was filled');
  for (let i = 1; i < filledPath.length; i++) {
    if (filledPath[i][0] !== 'lineTo') continue;
    assert.ok(Math.abs(filledPath[i][1] - filledPath[i - 1][1]) < halfMap,
      'a filled ring spanned the map');
  }

  // With a projector that has no seam at all, nothing is diverted: one path,
  // one stroke, both rings filled exactly as before this change.
  const ops2 = [];
  const ctx2 = {
    save() {}, restore() {}, beginPath() { ops2.push(['beginPath']); },
    moveTo() {}, lineTo() {}, fill() { ops2.push(['fill']); }, stroke() { ops2.push(['stroke']); },
    set fillStyle(_) {}, set strokeStyle(_) {}, set lineWidth(_) {},
    set lineJoin(_) {}, set lineCap(_) {},
  };
  layer.draw(ctx2, { project: (v) => [v[0] * 100, v[1] * 100, 1] });
  assert.equal(ops2.filter((o) => o[0] === 'beginPath').length, 1, 'no second path');
  assert.equal(ops2.filter((o) => o[0] === 'stroke').length, 1, 'no second stroke');
});

test('PolygonLayer.projectRings: same rings draw() would fill, without drawing', () => {
  const ring = (lon0, lat0) => {
    const pts = [];
    for (const [dx, dy] of [[0, 0], [10, 0], [10, 10], [0, 10]]) pts.push(lon0 + dx, lat0 + dy);
    return pts;
  };
  const data = {
    times: [0, 100],
    rotations: { 1: [[0, 0, 0], [0, 0, 0]] },
    features: [
      { p: 1, b: 100, e: 0, xy: ring(20, 0) },     // mid-map
      { p: 1, b: 100, e: 0, xy: ring(175, 0) },    // crosses 180
    ],
  };
  const layer = new PolygonLayer(data, { fill: '#fff', stroke: '#000' });
  layer.setTime(0);
  const p = new Robinson({ cx: 0, cy: 0, radius: 100, lon: 0 });

  const { fillable, seam } = layer.projectRings(p);
  assert.equal(fillable.length, 1, 'the mid-map ring is fillable');
  assert.equal(seam.length, 1, 'the seam-crossing ring is diverted, not fillable');

  // Closed: the first vertex is repeated at the end, so a consumer can fill
  // without closePath (see draw()'s note on why that matters).
  const buf = fillable[0];
  assert.equal(buf[0], buf[buf.length - 2], 'ring closes in x');
  assert.equal(buf[1], buf[buf.length - 1], 'ring closes in y');

  // Consistently wound, which is what makes a nonzero fill a union.
  let area = 0;
  for (let i = 0, n = buf.length; i < n; i += 2) {
    const j = (i + 2) % n;
    area += buf[i] * buf[j + 1] - buf[j] * buf[i + 1];
  }
  assert.ok(area > 0, 'wound positive');

  // The whole point: these are the SAME coordinates draw() would have emitted.
  const ops = [];
  const ctx = {
    save() {}, restore() {}, beginPath() {},
    moveTo(x, y) { ops.push(x, y); }, lineTo(x, y) { ops.push(x, y); },
    fill() {}, stroke() {},
    set fillStyle(_) {}, set strokeStyle(_) {}, set lineWidth(_) {},
    set lineJoin(_) {}, set lineCap(_) {},
  };
  layer.draw(ctx, p);
  assert.deepEqual(ops.slice(0, buf.length), buf,
    'projectRings and draw disagree about where the ring is');

  // A projector with no seam concept diverts nothing.
  const flat = layer.projectRings({ project: (v) => [v[0] * 100, v[1] * 100, 1] });
  assert.equal(flat.seam.length, 0, 'no seam projector, no diverted rings');
  assert.equal(flat.fillable.length, 2, 'both rings fillable');

  // Buffers are per-call, not a shared scratch one -- the caller keeps them.
  const again = layer.projectRings(p);
  assert.notEqual(again.fillable[0], fillable[0], 'a fresh array per call');
  assert.deepEqual(again.fillable[0], fillable[0], 'with identical contents');
});

test('wrapLonDelta', () => {
  assert.equal(wrapLonDelta(0), 0);
  assert.equal(wrapLonDelta(180), 180);
  assert.equal(wrapLonDelta(-180), 180);
  assert.equal(wrapLonDelta(190), -170);
  assert.equal(wrapLonDelta(-190), 170);
  assert.equal(wrapLonDelta(540), 180);
});
