"""
Gridded fields through time, as the equirectangular textures `RasterGlobe` samples.

`RasterGlobe` already draws one image per age and cross-fades between neighbours --
the StoryMaps paleogeography pages are built on it. What it never had was a way IN
for someone holding numbers rather than pictures: a model run, a set of grids, an
xarray. This module is that adapter, and nothing else. Colour is decided here, once,
because the texture is RGB: the browser samples colours, not values.

Two facts about the texture the output has to match exactly, both read off the
shader (`raster-globe.js`, `shade()`):

* `u = lon / 360 + 0.5`, `v = 0.5 - lat / 180`. Texel CENTRES therefore sit at
  `lon = -180 + (i + 0.5) * 360 / W` and `lat = 90 - (j + 0.5) * 180 / H`, row 0 at
  the north. A grid whose nodes sit ON whole degrees (both poles included, the usual
  GMT gridline registration) is half a cell off that, which is why the input is
  always resampled rather than copied pixel for pixel.
* RGB, no alpha. A cell with no value gets `nan_colour`, not transparency.

Resampling is bilinear in lon/lat, periodic across the antimeridian. That is a
display operation, not an analysis one: on a sphere it is exact at the input nodes
and smooth between them, which is what a picture needs. Nothing here should be used
to compute a number.

Only numpy and the standard library: the PNG writer is twenty lines of zlib rather
than a Pillow dependency for the sake of one call.
"""

import json
import os
import struct
import zlib

import numpy as np

SCALES = ("linear", "log")


# ---- colour ------------------------------------------------------------------

def colour_stops(colours, n=16):
    """A list of '#rrggbb' stops from a list of hex strings or a matplotlib name.

    A name needs matplotlib, imported only on that path -- a caller passing their own
    stops never pays for it.
    """
    if isinstance(colours, str):
        try:
            from matplotlib import colormaps
        except ImportError as exc:
            raise ImportError(
                "a colour map by name ({!r}) needs matplotlib; pass a list of hex "
                "stops instead".format(colours)) from exc
        cmap = colormaps[colours]
        out = []
        for x in np.linspace(0.0, 1.0, n):
            r, g, b, _ = cmap(float(x))
            out.append("#{:02x}{:02x}{:02x}".format(
                int(round(r * 255)), int(round(g * 255)), int(round(b * 255))))
        return out
    stops = [str(c) for c in colours]
    if len(stops) < 2:
        raise ValueError("need at least two colour stops")
    return stops


def _hex_to_rgb(hex_colour):
    h = hex_colour.lstrip("#")
    if len(h) != 6:
        raise ValueError("colour {!r} is not #rrggbb".format(hex_colour))
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def colourise(values, stops, vmin, vmax, scale="linear", nan_colour="#1b2733"):
    """Map a 2-D array to uint8 RGB through evenly spaced colour stops.

    Values outside [vmin, vmax] clamp to the end colours. Under `scale='log'` the
    range is still given in data units; non-positive values count as missing.
    """
    if scale not in SCALES:
        raise ValueError("scale must be one of {}".format(SCALES))
    v = np.asarray(values, dtype=float)
    lo, hi = float(vmin), float(vmax)
    if scale == "log":
        with np.errstate(divide="ignore", invalid="ignore"):
            v = np.where(v > 0, np.log10(v), np.nan)
        lo, hi = np.log10(lo), np.log10(hi)
    span = hi - lo if hi > lo else 1.0
    x = np.clip((v - lo) / span, 0.0, 1.0)

    table = np.array([_hex_to_rgb(s) for s in stops], dtype=float)
    pos = np.linspace(0.0, 1.0, len(table))
    missing = ~np.isfinite(x)
    x = np.where(missing, 0.0, x)
    rgb = np.stack([np.interp(x, pos, table[:, k]) for k in range(3)], axis=-1)
    rgb[missing] = _hex_to_rgb(nan_colour)
    return np.round(rgb).astype(np.uint8)


# ---- resampling --------------------------------------------------------------

def texture_coordinates(width):
    """Texel-centre longitudes and latitudes for a `width` x `width/2` texture."""
    height = width // 2
    lon = -180.0 + (np.arange(width) + 0.5) * 360.0 / width
    lat = 90.0 - (np.arange(height) + 0.5) * 180.0 / height
    return lon, lat


def _prepare_grid(values, lon, lat):
    """Longitudes wrapped to [-180, 180) without a repeated seam, both axes ascending."""
    values = np.asarray(values, dtype=float)
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    if values.shape != (lat.size, lon.size):
        raise ValueError("values have shape {} but lat/lon give ({}, {})".format(
            values.shape, lat.size, lon.size))

    wrapped = (lon + 180.0) % 360.0 - 180.0
    order = np.argsort(wrapped, kind="stable")
    wrapped = wrapped[order]
    values = values[:, order]
    # A grid carrying both -180 and 180 has the seam twice; after wrapping the two
    # land on the same longitude and the second copy would divide by a zero width.
    keep = np.concatenate([[True], np.diff(wrapped) > 1e-9])
    wrapped, values = wrapped[keep], values[:, keep]

    lat_order = np.argsort(lat)
    return values[lat_order], wrapped, lat[lat_order]


def resample(values, lon, lat, width=720):
    """Bilinear resample of a lon/lat grid onto texel centres, periodic in longitude.

    NaN-aware: a texel is the weighted mean of whichever of its four neighbours have
    values, so a coastline does not eat a cell of field on either side.
    """
    grid, glon, glat = _prepare_grid(values, lon, lat)
    tlon, tlat = texture_coordinates(width)
    nlon = glon.size

    # Longitude: periodic. Index of the node at or west of each texel, then wrap.
    ext = np.concatenate([glon, [glon[0] + 360.0]])
    q = np.where(tlon < glon[0], tlon + 360.0, tlon)
    i0 = np.clip(np.searchsorted(ext, q, side="right") - 1, 0, nlon - 1)
    wx = (q - ext[i0]) / (ext[i0 + 1] - ext[i0])
    i1 = (i0 + 1) % nlon

    # Latitude: clamped at the poles rather than extrapolated.
    p = np.clip(tlat, glat[0], glat[-1])
    j0 = np.clip(np.searchsorted(glat, p, side="right") - 1, 0, glat.size - 2)
    wy = (p - glat[j0]) / (glat[j0 + 1] - glat[j0])

    J0, I0 = np.meshgrid(j0, i0, indexing="ij")
    J1, I1 = np.meshgrid(j0 + 1, i1, indexing="ij")
    WY, WX = np.meshgrid(wy, wx, indexing="ij")

    corners = [(grid[J0, I0], (1 - WY) * (1 - WX)), (grid[J0, I1], (1 - WY) * WX),
               (grid[J1, I0], WY * (1 - WX)), (grid[J1, I1], WY * WX)]
    total = np.zeros(J0.shape)
    weight = np.zeros(J0.shape)
    for value, w in corners:
        ok = np.isfinite(value)
        total += np.where(ok, value, 0.0) * w
        weight += np.where(ok, w, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(weight > 1e-12, total / weight, np.nan)


# ---- PNG ---------------------------------------------------------------------

def write_png(path, rgb):
    """An 8-bit RGB PNG, no filtering, standard library only."""
    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    height, width, _ = rgb.shape
    raw = np.concatenate([np.zeros((height, 1), np.uint8),
                          rgb.reshape(height, width * 3)], axis=1).tobytes()

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        fh.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        fh.write(chunk(b"IEND", b""))


# ---- export ------------------------------------------------------------------

def export_raster(frames, lon, lat, out_dir, stops, vmin, vmax, scale="linear",
                  width=720, nan_colour="#1b2733", times=None, quiet=False):
    """Write one PNG per time plus the `frames.json` manifest `RasterGlobe` reads.

    `frames` maps time (Ma) to a 2-D array on (`lat`, `lon`). `times`, if given,
    restricts the export to those ages -- how a coarse preview avoids writing every
    frame. Returns the manifest path.
    """
    os.makedirs(os.path.join(out_dir, "frames"), exist_ok=True)
    wanted = None if times is None else {round(float(t), 6) for t in times}

    entries = []
    for time in sorted(frames, key=float):
        age = float(time)
        if wanted is not None and round(age, 6) not in wanted:
            continue
        rgb = colourise(resample(frames[time], lon, lat, width), stops, vmin, vmax,
                        scale=scale, nan_colour=nan_colour)
        name = "frames/{:g}.png".format(age)
        write_png(os.path.join(out_dir, name), rgb)
        entries.append({"age": age, "file": name})

    if not entries:
        raise ValueError("no raster frames fall on the view's times")

    path = os.path.join(out_dir, "frames.json")
    with open(path, "w") as fh:
        json.dump({"frames": entries, "width": width, "height": width // 2}, fh)
    if not quiet:
        print("  raster: {} frames, {}x{}".format(len(entries), width, width // 2))
    return path
