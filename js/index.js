/*
 * deep-time-map -- plate reconstructions rendered in the browser.
 *
 * Layers talk to their host through one method:
 *
 *     project(vec3) -> [x, y, depth] | null
 *
 * Supply an Orthographic, or anything of your own that implements it.
 *
 * Note what is NOT here: ./hover.js and ./timeseries-panel.js, which wire pointer events to
 * DOM elements. They are the only modules that touch the DOM, and keeping them out of this
 * barrel means a consumer rendering headlessly or in a worker never loads them. Import them
 * directly if you want them.
 */

export { BoundaryLayer, BoundarySeries, DEFAULT_STYLE } from './boundaries.js';
export { VelocityField } from './velocities.js';
export { PointLayer } from './points.js';
export { PolygonLayer } from './polygons.js';
export {
  TimeSeriesSet, parseCsv, seriesFromCsv, timeColumn, MODES,
} from './timeseries.js';
export { Orthographic } from './orthographic.js';
export { tracePolyline, packLonLat } from './polyline.js';
export {
  quatFromPoleAngle, quatSlerp, quatToMat3, mat3Multiply, mat3Apply,
} from './rotations.js';
export {
  DEG, lonLatToVec3, vec3ToLonLat, tangentFrame,
  cross, normalise, tangentTowards, travel, leftOfTravel,
} from './sphere.js';
