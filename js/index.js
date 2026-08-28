/*
 * deep-time-map -- plate reconstructions rendered in the browser.
 *
 * Layers talk to their host through one method:
 *
 *     project(vec3) -> [x, y, depth] | null
 *
 * Supply an Orthographic, or anything of your own that implements it.
 *
 * Note what is NOT here: ./hover.js, which wires pointer events to a DOM popup. It is the
 * only module that touches the DOM, and keeping it out of this barrel means a consumer
 * rendering headlessly or in a worker never loads it. Import it directly if you want it.
 */

export { BoundaryLayer, BoundarySeries, DEFAULT_STYLE } from './boundaries.js';
export { VelocityField } from './velocities.js';
export { PointLayer } from './points.js';
export { Orthographic } from './orthographic.js';
export { tracePolyline, packLonLat } from './polyline.js';
export {
  quatFromPoleAngle, quatSlerp, quatToMat3, mat3Multiply, mat3Apply,
} from './rotations.js';
export {
  DEG, lonLatToVec3, vec3ToLonLat, tangentFrame,
  cross, normalise, tangentTowards, travel, leftOfTravel,
} from './sphere.js';
