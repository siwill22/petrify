/*
 * Run with: node --test test/
 *
 * The pieces a host needs to let a user drag plates around: composing rotations from
 * gestures, reading them back as an Euler pole and angle, telling which ring was clicked,
 * and PolygonLayer taking rotations from the caller rather than a table. The ring tests
 * deliberately use the two places a lon/lat test goes wrong -- a pole inside the ring,
 * and a ring across the antimeridian -- plus the far-side-of-the-globe case.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  quatFromPoleAngle, quatToMat3, mat3Apply,
  quatMultiply, quatConjugate, quatBetween, quatToPoleAngle, quatFromAxisAngle,
} from '../js/rotations.js';
import {
  DEG, lonLatToVec3, vec3ToLonLat, ringSignedArea, ringContains, ringCentroid,
} from '../js/sphere.js';
import { PolygonLayer } from '../js/polygons.js';

const close = (a, b, eps = 1e-9) => Math.abs(a - b) < eps;
const rotate = (q, v) => mat3Apply(quatToMat3(q), v);
const vecClose = (a, b, eps = 1e-9) => a.every((x, i) => close(x, b[i], eps));

/** Pack lon/lat pairs into a flat xyz buffer, densified to <= 1 degree along great circles. */
function ring(lonlats) {
  const out = [];
  for (let k = 0; k < lonlats.length; k++) {
    const a = lonLatToVec3(...lonlats[k]);
    const b = lonLatToVec3(...lonlats[(k + 1) % lonlats.length]);
    const theta = Math.acos(Math.max(-1, Math.min(1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2])));
    const n = Math.max(1, Math.ceil(theta / DEG));
    for (let i = 0; i < n; i++) {
      const t = i / n;
      const s0 = theta ? Math.sin((1 - t) * theta) / Math.sin(theta) : 1 - t;
      const s1 = theta ? Math.sin(t * theta) / Math.sin(theta) : t;
      out.push(s0 * a[0] + s1 * b[0], s0 * a[1] + s1 * b[1], s0 * a[2] + s1 * b[2]);
    }
  }
  return { buf: Float64Array.from(out), count: out.length / 3 };
}

test('quatBetween carries a exactly onto b', () => {
  for (const [a, b] of [
    [[10, 20], [40, -5]], [[-170, 60], [170, 55]], [[0, 89], [180, 89]], [[30, 0], [31, 0]],
  ]) {
    const va = lonLatToVec3(...a), vb = lonLatToVec3(...b);
    assert.ok(vecClose(rotate(quatBetween(va, vb), va), vb, 1e-12), `${a} -> ${b}`);
  }
  assert.deepEqual(quatBetween([1, 0, 0], [1, 0, 0]), [0, 0, 0, 1]);
});

test('quatMultiply applies the right-hand factor first', () => {
  const a = quatFromPoleAngle(0, 90, 90);     // about the z axis
  const b = quatFromPoleAngle(0, 0, 90);      // about the x axis
  const v = [0, 1, 0];
  const expected = rotate(a, rotate(b, v));
  assert.ok(vecClose(rotate(quatMultiply(a, b), v), expected));
  // and the conjugate undoes a rotation
  assert.ok(vecClose(rotate(quatMultiply(quatConjugate(a), a), v), v));
});

test('pole and angle round-trip through a quaternion', () => {
  for (const [lon, lat, ang] of [[30, 45, 20], [-120, -10, 170], [179, 60, 5], [0, -89, 90]]) {
    const r = quatToPoleAngle(quatFromPoleAngle(lon, lat, ang));
    assert.ok(close(r.lon, lon, 1e-9) && close(r.lat, lat, 1e-9) && close(r.angle, ang, 1e-9),
      `${lon},${lat},${ang} -> ${r.lon},${r.lat},${r.angle}`);
  }
  // A negative angle comes back as the antipodal pole with a positive one -- the same rotation.
  const r = quatToPoleAngle(quatFromPoleAngle(30, 45, -20));
  assert.ok(close(r.lon, -150) && close(r.lat, -45) && close(r.angle, 20));
  assert.deepEqual(quatToPoleAngle([0, 0, 0, 1]), { lon: 0, lat: 90, angle: 0 });
});

test('ring area: a spherical cap and an octant', () => {
  // The octant x, y, z >= 0 is exactly one eighth of the sphere: 4 pi / 8.
  const oct = ring([[0, 0], [90, 0], [0, 90]]);
  assert.ok(close(ringSignedArea(oct.buf, 0, oct.count), Math.PI / 2, 1e-9));
  // Reversed, the same ring is clockwise and the area changes sign.
  const rev = ring([[0, 90], [90, 0], [0, 0]]);
  assert.ok(close(ringSignedArea(rev.buf, 0, rev.count), -Math.PI / 2, 1e-9));
});

test('ringContains: ordinary, across the antimeridian, around a pole', () => {
  const box = ring([[170, -10], [-170, -10], [-170, 10], [170, 10]]);
  assert.ok(ringSignedArea(box.buf, 0, box.count) > 0, 'box is counter-clockwise');
  assert.ok(ringContains(box.buf, 0, box.count, lonLatToVec3(180, 0)), 'the antimeridian is inside');
  assert.ok(ringContains(box.buf, 0, box.count, lonLatToVec3(-175, 5)));
  assert.ok(!ringContains(box.buf, 0, box.count, lonLatToVec3(0, 0)), 'lon 0 is not');
  assert.ok(!ringContains(box.buf, 0, box.count, lonLatToVec3(160, 0)));

  // A cap of latitude > 70, walked eastward: counter-clockwise seen from above the pole.
  const cap = ring([0, 60, 120, 180, 240, 300].map((lon) => [lon, 70]));
  assert.ok(ringSignedArea(cap.buf, 0, cap.count) > 0);
  assert.ok(ringContains(cap.buf, 0, cap.count, lonLatToVec3(0, 90)), 'the pole is inside');
  assert.ok(ringContains(cap.buf, 0, cap.count, lonLatToVec3(123, 80)));
  assert.ok(!ringContains(cap.buf, 0, cap.count, lonLatToVec3(123, 60)));
});

test('ringContains: the antipode of the ring is outside, not inside', () => {
  const box = ring([[10, -10], [30, -10], [30, 10], [10, 10]]);
  assert.ok(ringContains(box.buf, 0, box.count, lonLatToVec3(20, 0)));
  // Straight through the Earth from the box's centre: a winding test that ignored the
  // sign would claim this point too.
  assert.ok(!ringContains(box.buf, 0, box.count, lonLatToVec3(-160, 0)));
});

test('ringCentroid: symmetric shapes, and the pole for a polar cap', () => {
  const box = ring([[170, -10], [-170, -10], [-170, 10], [170, 10]]);
  const [lon, lat] = vec3ToLonLat(ringCentroid(box.buf, 0, box.count));
  assert.ok(close(Math.abs(lon), 180, 1e-6) && close(lat, 0, 1e-6), `${lon},${lat}`);

  const cap = ring([0, 60, 120, 180, 240, 300].map((l) => [l, 70]));
  assert.ok(close(vec3ToLonLat(ringCentroid(cap.buf, 0, cap.count))[1], 90, 1e-6));
});

test('PolygonLayer.setRotations drives a layer with no rotation table', () => {
  const layer = new PolygonLayer({
    times: [0],
    rotations: {},
    features: [
      { p: 7, b: Infinity, e: 0, xy: [10, 0, 20, 0, 20, 10] },
      { p: 8, b: Infinity, e: 0, xy: [-50, 0, -40, 0, -40, 10] },
    ],
  });
  assert.equal(layer.currentTime, null);

  const q = quatFromAxisAngle([0, 0, 1], 90 * DEG);   // 90 degrees east about the spin axis
  layer.setRotations(new Map([[7, q]]));
  assert.equal(layer.currentTime, 0, 'becomes drawable');
  const [lon, lat] = vec3ToLonLat([layer.xyz[0], layer.xyz[1], layer.xyz[2]]);
  assert.ok(close(lon, 100, 1e-9) && close(lat, 0, 1e-9), `plate 7 moved to ${lon},${lat}`);

  // Plate 8 was never named, so it was never rotated at all.
  assert.deepEqual(Array.from(layer.xyz.slice(9, 12)), [0, 0, 0]);

  // A plain object works too, and naming plate 8 leaves plate 7 where it was.
  layer.setRotations({ 8: [0, 0, 0, 1] });
  assert.ok(close(vec3ToLonLat([layer.xyz[0], layer.xyz[1], layer.xyz[2]])[0], 100, 1e-9));
  assert.ok(close(vec3ToLonLat([layer.xyz[9], layer.xyz[10], layer.xyz[11]])[0], -50, 1e-9));
});
