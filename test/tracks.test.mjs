/*
 * Run with: node --test test/
 *
 * Track layers (first consumer: PlateNet's net-characteristics viewer): `connectBy`
 * splitting one connected line into several, composed glyphs (`mark`, `hollow`, the
 * `x` and `none` symbols), and a trajectory payload as `petrify.build_tracks` writes
 * it -- positions supplied per time, with null where a track has none.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PointLayer } from '../js/points.js';

/** A minimal recording 2-D context, as in points.test.mjs. */
function makeCtx() {
  const ops = [];
  let strokeStyle = null;
  let fillStyle = null;
  const strokes = [];
  let path = [];
  const ctx = {
    save() {}, restore() {},
    beginPath() { path = []; ops.push(['beginPath']); },
    moveTo(x, y) { path.push(['M', x, y]); ops.push(['moveTo', x, y]); },
    lineTo(x, y) { path.push(['L', x, y]); ops.push(['lineTo', x, y]); },
    arc() { ops.push(['arc']); },
    rect() { ops.push(['rect']); },
    closePath() {},
    fill() { ops.push(['fill', fillStyle]); },
    stroke() {
      ops.push(['stroke', strokeStyle]);
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

/** Looking down +x; (y, z) on screen. */
const projector = {
  project(v) {
    if (v[0] <= 0) return null;
    return [v[1] * 200, v[2] * 200, v[0]];
  },
};

function fixed(points) {
  const lons = points.map((p) => p.lon);
  const lats = points.map((p) => p.lat);
  return {
    transport: 'trajectory',
    times: [0, 10],
    points,
    categories: {},
    frames: { 0: [lons, lats], 10: [lons.slice(), lats.slice()] },
  };
}

test('connectBy: one line per distinct value, never joined across values', () => {
  const layer = new PointLayer(fixed([
    { lon: 0, lat: 0, segment: 'a' },
    { lon: 5, lat: 0, segment: 'a' },
    { lon: 10, lat: 0, segment: 'b' },
    { lon: 15, lat: 0, segment: 'b' },
  ]), {
    spiderfy: false, lifespan: 'always', connectLive: true, connectBy: 'segment',
    style: () => ({ fill: 'rgb(1, 2, 3)', symbol: 'none' }),
  });
  layer.setTime(0);
  const { ctx, strokes } = makeCtx();
  layer.draw(ctx, projector);

  // a: 0-5, b: 10-15. Without connectBy there would also be 5-10.
  assert.equal(strokes.length, 2);
});

test('symbol none: nothing is drawn, but the point can still be picked', () => {
  const layer = new PointLayer(fixed([{ lon: 0, lat: 0 }]), {
    spiderfy: false, lifespan: 'always', style: () => ({ symbol: 'none' }),
  });
  layer.setTime(0);
  const { ctx, ops } = makeCtx();
  layer.draw(ctx, projector);

  assert.equal(ops.filter((o) => o[0] === 'fill' || o[0] === 'stroke').length, 0);
  assert.equal(layer.pick(0, 0).index, 0);
});

test('mark: a plus inside a filled diamond is two extra strokes in the keyline colour', () => {
  const layer = new PointLayer(fixed([{ lon: 0, lat: 0 }]), {
    spiderfy: false, lifespan: 'always', keyline: 'black',
    style: () => ({ symbol: 'diamond', fill: 'red', mark: 'plus' }),
  });
  layer.setTime(0);
  const { ctx, ops, strokes } = makeCtx();
  layer.draw(ctx, projector);

  assert.equal(ops.filter((o) => o[0] === 'fill').length, 1);
  // The diamond's keyline, then the mark.
  assert.equal(strokes.length, 2);
  assert.equal(strokes[1].color, 'black');
  assert.equal(strokes[1].path.filter((s) => s[0] === 'M').length, 2);
});

test('hollow: the outline is stroked in the fill colour and nothing is filled', () => {
  const layer = new PointLayer(fixed([{ lon: 0, lat: 0 }]), {
    spiderfy: false, lifespan: 'always',
    style: () => ({ symbol: 'circle', fill: 'red', hollow: true, mark: 'dot' }),
  });
  layer.setTime(0);
  const { ctx, ops } = makeCtx();
  layer.draw(ctx, projector);

  const fills = ops.filter((o) => o[0] === 'fill');
  const strokes = ops.filter((o) => o[0] === 'stroke');
  // The only fill is the dot mark, in the symbol's own colour.
  assert.deepEqual(fills, [['fill', 'red']]);
  assert.deepEqual(strokes, [['stroke', 'red']]);
});

test('track payload: moves between supplied positions and hides where there is none', () => {
  // One track along the equator: lon 0 at 0 Ma, 20 at 10 Ma, none at 20 Ma.
  const layer = new PointLayer({
    transport: 'trajectory',
    times: [0, 10, 20],
    points: [{ lon: 0, lat: 0, track: 'D+' }],
    categories: {},
    frames: { 0: [[0], [0]], 10: [[20], [0]], 20: [[null], [null]] },
  }, { spiderfy: false, lifespan: 'always' });

  layer.setTime(5);
  assert.equal(layer._live[0], 1);
  // Slerped halfway along the great circle: lon 10 on the equator.
  assert.ok(Math.abs(Math.atan2(layer._xyz[1], layer._xyz[0]) * 180 / Math.PI - 10) < 1e-9);

  layer.setTime(20);
  assert.equal(layer._live[0], 0);
});
