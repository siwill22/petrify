/*
 * Fetch and JSON-parse a URL, transparently un-gzipping a `.gz` file.
 *
 * A static host (e.g. GitHub Pages) will not compress large per-frame JSON files for
 * you the way a CDN compresses ordinary responses, so a large deployment may choose to
 * pre-gzip them and rewrite manifests to point at the `.gz` file. The extension alone
 * is not enough to decide whether to decompress -- a server may instead serve the `.gz`
 * with `Content-Encoding: gzip`, in which case the browser has already decoded it by
 * the time this code sees the bytes, and decompressing again would fail. Browsers strip
 * `Content-Encoding` from the readable headers, so this looks for the gzip magic number
 * in the bytes actually received instead. That is correct under either transport, which
 * is what lets a dev server and a static host disagree about this without callers
 * needing to know which one they're talking to.
 */
/** Shared by fetchMaybeGzippedJSON/fetchMaybeGzippedBinary -- fetches `url` and returns
 *  its DECOMPRESSED bytes as an ArrayBuffer, transparently un-gzipping a `.gz` file the
 *  same gzip-magic-number-sniffing way (see the module comment above). */
async function fetchMaybeGzippedBytes(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  const raw = new Uint8Array(await res.arrayBuffer());

  const gzipped = raw.length > 2 && raw[0] === 0x1f && raw[1] === 0x8b;
  if (!url.endsWith('.gz') || !gzipped) return raw.buffer;

  const stream = new Blob([raw]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Response(stream).arrayBuffer();
}

export async function fetchMaybeGzippedJSON(url) {
  const buf = await fetchMaybeGzippedBytes(url);
  return JSON.parse(new TextDecoder().decode(buf));
}

/** Same transparent-gunzip fetch as fetchMaybeGzippedJSON, but for a payload that is
 *  NOT JSON -- a packed binary blob (see aggregate.py's _pack_frames_binary() and
 *  SCHEMA.md's density.json section) too large to spend a JSON.parse() pass on. Returns
 *  the raw decompressed ArrayBuffer; the caller constructs typed-array VIEWS over it
 *  directly, at whatever offsets its own header describes. */
export async function fetchMaybeGzippedBinary(url) {
  return fetchMaybeGzippedBytes(url);
}
