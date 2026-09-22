// Verbatim copy of d3-geo v3.1.1's src/pointEqual.js. See ../README.md.

import {abs, epsilon} from "./math.js";

export default function(a, b) {
  return abs(a[0] - b[0]) < epsilon && abs(a[1] - b[1]) < epsilon;
}
