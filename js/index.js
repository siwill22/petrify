/*
 * deep-time-map -- plate reconstructions rendered in the browser.
 *
 * Layers talk to their host through one method:
 *
 *     project(vec3) -> [x, y, depth] | null
 *
 * Supply an Orthographic, or anything of your own that implements it.
 */

export { BoundaryLayer, BoundarySeries, DEFAULT_STYLE } from './boundaries.js';
export { VelocityField } from './velocities.js';
export { Orthographic } from './orthographic.js';
export { tracePolyline, packLonLat } from './polyline.js';
export {
  DEG, lonLatToVec3, vec3ToLonLat, tangentFrame,
  cross, normalise, tangentTowards, travel, leftOfTravel,
} from './sphere.js';
