/*
 * Projecting a polyline onto a canvas path, with horizon handling.
 *
 * This is the loop every vector layer on a globe needs: walk a run of pre-computed unit
 * vectors, project each one, and lift the pen wherever the line passes behind the
 * horizon rather than clamping it -- clamping smears lines across the disc as the globe
 * turns. It was written twice (coastlines and plate boundaries) before being pulled out
 * here.
 *
 * Vertices are stored as a flat Float64Array of xyz triples so a whole layer is one
 * allocation, and so the per-frame cost is a projection per point and nothing else.
 */

/**
 * Add one polyline to `path`.
 *
 * @param path      anything with moveTo/lineTo: a CanvasRenderingContext2D or a Path2D
 * @param project   (vec3) => [x, y, depth] | null
 * @param xyz       flat Float64Array of unit vectors
 * @param offset    index of the first vertex (in vertices, not floats)
 * @param count     number of vertices
 * @param closed    repeat the first vertex at the end, for rings
 * @param projected optional array to receive the projected points, nulls included, so
 *                  the caller can reuse them without projecting twice
 * @returns the number of points actually drawn
 */
export function tracePolyline(path, project, xyz, offset, count,
                              { closed = false, projected = null } = {}) {
  if (projected) projected.length = 0;

  const v = [0, 0, 0];
  let pen = false;
  let drawn = 0;

  const limit = closed ? count + 1 : count;
  for (let k = 0; k < limit; k++) {
    const i = (offset + (k % count)) * 3;
    v[0] = xyz[i]; v[1] = xyz[i + 1]; v[2] = xyz[i + 2];

    const p = project(v);
    if (projected) projected.push(p);

    if (!p) { pen = false; continue; }
    if (pen) path.lineTo(p[0], p[1]);
    else { path.moveTo(p[0], p[1]); pen = true; }
    drawn++;
  }
  return drawn;
}

/**
 * Pack an array of [lon, lat] rings into one flat unit-vector buffer.
 *
 * Returns { xyz, runs } where each run is { offset, count }, matching the argument
 * order tracePolyline expects.
 */
export function packLonLat(rings, lonLatToVec3) {
  let total = 0;
  for (const r of rings) total += r.length;

  const xyz = new Float64Array(total * 3);
  const runs = [];
  const tmp = [0, 0, 0];
  let o = 0;

  for (const ring of rings) {
    for (let i = 0; i < ring.length; i++) {
      lonLatToVec3(ring[i][0], ring[i][1], tmp);
      xyz[(o + i) * 3] = tmp[0];
      xyz[(o + i) * 3 + 1] = tmp[1];
      xyz[(o + i) * 3 + 2] = tmp[2];
    }
    runs.push({ offset: o, count: ring.length });
    o += ring.length;
  }
  return { xyz, runs };
}
