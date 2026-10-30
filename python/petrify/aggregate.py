"""Bin reconstructed points into equal-area cells, and into latitude x age.

PointLayer answers "where is each thing?". A dataset of tens of thousands of points cannot
answer "what is here, and how much of it?" that way -- at a few pixels a symbol, categories
overlap into a cloud. So this module summarises: one glyph per occupied cell for the map
(`build_aggregates`), and a latitude-against-age matrix for a panel (`build_latitude`).

---- Why the grid lives here and not in js/ -----------------------------------------------

The renderer receives cell CENTRES as plain lon/lat and never implements a tessellation.
The alternative is the same grid written twice in two languages, drifting silently -- the
failure SCHEMA.md exists to prevent. The cost is that a consumer can draw marks at cell
centres but not cell boundaries, which is a deliberate trade.

---- Equal area is the whole point --------------------------------------------------------

`EqualAreaGrid` divides the sphere into rings of equal area (equal steps in sin(latitude))
and gives every ring the same number of longitude divisions. Every cell then has area
exactly 4*pi / (rings * lon_cells) steradians -- not approximately, by construction, and
`verify_equal_area()` checks it.

A lon/lat degree grid would not do: its cells shrink as cos(latitude), so counting into one
inflates apparent density toward the poles. For anything being read as a latitudinal
gradient that is not a rounding detail, it is the answer.

The cost, stated plainly: cells near the poles are elongated north-south (the top ring of a
40-ring grid spans ~13 degrees of latitude but only 4.5 of longitude). Aspect ratio was
traded for exact equality of area. `HealpixGrid` below fixes the aspect ratio and needs no
renderer change -- only a different implementation of `cell_of`/`centres` here. Both grids
ship side by side rather than one replacing the other, so a dataset can be exported under
either (or compared against `build_density()`'s continuous KDE) without committing in
advance to which reads best.

---- Density is a different question from counts, and gets its own function -------------

`build_aggregates()` answers "how many things are in this cell" -- a discrete count, tied
to one grid's cells. `build_density()` answers "how concentrated is the underlying process
here" -- a continuous spherical KDE (von Mises-Fisher kernel, the sphere's analogue of a
Gaussian), evaluated at a grid's cell centres but not itself binned into them. It is
*sampled* density, not true abundance: two datasets with identical KDE shape can differ in
how completely they were collected, which is exactly what `build_aggregates()`'s raw counts
are for showing alongside, not what this function corrects for.

---- Reconstruction agrees with the renderer by construction -------------------------------

Positions are computed from the SAME `rotations` block that goes into `points.json`, by the
same Rodrigues rotation the JS applies, rather than by an independent `pygplates.reconstruct`
call. If the two disagreed, a pie chart would summarise points that are not where the map
draws them, and nothing on screen would reveal it.
"""

import math

import numpy as np

__all__ = [
    "EqualAreaGrid",
    "HealpixGrid",
    "build_aggregates",
    "build_density",
    "build_latitude",
    "equal_degree_bands",
    "live_mask",
    "reconstruct",
    "verify_equal_area",
]


class EqualAreaGrid:
    """Rings of equal area, each cut into the same number of longitude cells.

    @param rings  number of latitude rings. `lon_cells` defaults to 2*rings, which makes
                  equatorial cells roughly square; total cells is then 2*rings**2.
    """

    kind = "equal-area-rings"

    def __init__(self, rings=40, lon_cells=None):
        if rings < 2:
            raise ValueError("rings must be >= 2")
        self.rings = int(rings)
        self.lon_cells = int(lon_cells if lon_cells is not None else 2 * rings)
        self.n_cells = self.rings * self.lon_cells

    @property
    def cell_area_sr(self):
        """Steradians per cell -- identical for every cell, by construction."""
        return 4.0 * math.pi / self.n_cells

    def cell_of(self, lon, lat):
        """Cell index for arrays of lon/lat in degrees. Vectorised."""
        lon = np.asarray(lon, dtype=float)
        lat = np.asarray(lat, dtype=float)
        s = np.sin(np.radians(np.clip(lat, -90.0, 90.0)))
        iy = np.floor((s + 1.0) * 0.5 * self.rings).astype(np.int64)
        np.clip(iy, 0, self.rings - 1, out=iy)
        # `% 360` first so a longitude arriving as 180 or 360 lands in the first cell
        # rather than one past the end.
        ix = np.floor(((lon + 180.0) % 360.0) / 360.0 * self.lon_cells).astype(np.int64)
        np.clip(ix, 0, self.lon_cells - 1, out=ix)
        return iy * self.lon_cells + ix

    def centres(self):
        """[[lon, lat], ...] for every cell, indexed as `cell_of` returns.

        The centre is the ring's equal-area midpoint (midway in sin(latitude), not in
        degrees) so a glyph sits at the centroid of the area it summarises rather than
        biased poleward.
        """
        iy = np.repeat(np.arange(self.rings), self.lon_cells)
        ix = np.tile(np.arange(self.lon_cells), self.rings)
        s = -1.0 + 2.0 * (iy + 0.5) / self.rings
        lat = np.degrees(np.arcsin(np.clip(s, -1.0, 1.0)))
        lon = -180.0 + (ix + 0.5) * 360.0 / self.lon_cells
        return [[round(float(a), 4), round(float(b), 4)] for a, b in zip(lon, lat)]

    def describe(self):
        """This grid's own construction params, for the exported `grid` metadata block."""
        return {"rings": self.rings, "lon_cells": self.lon_cells}


class HealpixGrid:
    """True HEALPix cells: equal area, like `EqualAreaGrid`, but also near-equal shape,
    which `EqualAreaGrid` trades away at the poles (see the module docstring).

    Backed by `astropy_healpix`'s healpy-compatible shim
    (`astropy_healpix.healpy`) -- already a `pygmt17` dependency via `gprm`, so this needs
    no new package. A hand-rolled ang2pix was deliberately not written: HEALPix's
    hierarchical indexing is exactly the kind of "looks simple, is not" geometry this
    codebase defers to a real implementation for rather than re-deriving.

    @param nside  must be a power of two (HEALPix's own constraint). `12 * nside**2`
                  cells result; `nside=16` -> 3072, close to `EqualAreaGrid`'s common
                  `rings=40` default (3200).
    """

    kind = "healpix-ring"

    def __init__(self, nside=16):
        nside = int(nside)
        if nside <= 0 or (nside & (nside - 1)) != 0:
            raise ValueError(f"nside must be a positive power of two, got {nside}")
        self.nside = nside
        self.n_cells = 12 * nside * nside

    @property
    def cell_area_sr(self):
        from astropy_healpix import healpy as hp
        return float(hp.nside2pixarea(self.nside))

    def cell_of(self, lon, lat):
        """Cell index for arrays of lon/lat in degrees. Vectorised."""
        from astropy_healpix import healpy as hp
        lon = np.asarray(lon, dtype=float)
        lat = np.asarray(lat, dtype=float)
        return hp.ang2pix(self.nside, lon, lat, nest=False, lonlat=True)

    def centres(self):
        """[[lon, lat], ...] for every cell, indexed as `cell_of` returns. Longitude
        wrapped to [-180, 180) to match `EqualAreaGrid.centres()`'s convention."""
        from astropy_healpix import healpy as hp
        lon, lat = hp.pix2ang(self.nside, np.arange(self.n_cells), nest=False, lonlat=True)
        lon = ((lon + 180.0) % 360.0) - 180.0
        return [[round(float(a), 4), round(float(b), 4)] for a, b in zip(lon, lat)]

    def describe(self):
        return {"nside": self.nside, "order": "ring"}

    def bounds(self):
        """[[[lon, lat], x4], ...] -- the four corner vertices of every cell, in the
        SAME 3-D-unit-vector geometry `centres()`'s lon/lat encodes, so a cell
        straddling the antimeridian needs no special casing here: each corner is
        wrapped into [-180, 180) independently, same as `centres()`, and the renderer
        rebuilds a seamless 3-D vector per corner rather than interpolating in lon/lat.

        This is what makes true HEALPix "pixels" possible in the renderer (filled
        quads, not marks at a centre) -- see `js/aggregate.js`'s `'density'` mode.
        `hp.boundaries` gives exact cell edges (not an approximation), vectorised
        across every cell in one call.
        """
        from astropy_healpix import healpy as hp
        b = hp.boundaries(self.nside, np.arange(self.n_cells), step=1, nest=False)
        # b: (n_cells, 3, 4) -- reshape to (n_cells*4, 3) for one vec2ang call.
        vecs = b.transpose(0, 2, 1).reshape(-1, 3)
        lon, lat = hp.vec2ang(vecs, lonlat=True)
        lon = (((lon + 180.0) % 360.0) - 180.0).reshape(self.n_cells, 4)
        lat = lat.reshape(self.n_cells, 4)
        return [[[round(float(lo), 4), round(float(la), 4)] for lo, la in zip(*cell)]
                for cell in zip(lon, lat)]


def verify_equal_area(grid, samples=2_000_000, seed=0):
    """Monte-Carlo check that every cell really does have the same area.

    Uniform points on the sphere land in each cell in proportion to its area, so the
    occupancy histogram should be flat to within counting noise. Returns
    (expected_per_cell, max_relative_deviation); the deviation is dominated by sqrt(n)
    noise, so compare it against `3 / sqrt(expected)` rather than against zero.
    """
    rng = np.random.default_rng(seed)
    z = rng.uniform(-1.0, 1.0, samples)
    lon = rng.uniform(-180.0, 180.0, samples)
    lat = np.degrees(np.arcsin(z))
    counts = np.bincount(grid.cell_of(lon, lat), minlength=grid.n_cells)
    expected = samples / grid.n_cells
    return expected, float(np.max(np.abs(counts - expected)) / expected)


def equal_degree_bands(step=5.0):
    """Latitude bands of equal DEGREES, south to north -- [{'from', 'to'}, ...].

    Equal degrees, not equal area, and deliberately: this feeds an axis a reader reads as
    "latitude". Equal-area bands (equal in sin) would remove the cos(latitude) sampling
    bias but put a nonlinear axis in front of them, with the tropics filling most of the
    height. The bias is real -- a 60-degree band covers half the surface of an equatorial
    one -- so band totals are not area-normalised and should not be read as densities.
    """
    edges = np.arange(-90.0, 90.0 + step / 2, step)
    return [{"from": float(edges[i]), "to": float(edges[i + 1])}
            for i in range(len(edges) - 1)]


def _pole_angle_to_matrix(pole_lon, pole_lat, angle_deg):
    """Rodrigues rotation matrix for a finite rotation given as pole + angle."""
    k = np.array([
        math.cos(math.radians(pole_lat)) * math.cos(math.radians(pole_lon)),
        math.cos(math.radians(pole_lat)) * math.sin(math.radians(pole_lon)),
        math.sin(math.radians(pole_lat)),
    ])
    t = math.radians(angle_deg)
    c, s = math.cos(t), math.sin(t)
    kx = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    return np.eye(3) * c + s * kx + (1.0 - c) * np.outer(k, k)


def reconstruct(home_xyz, plate_index, plate_rotations, time_index):
    """Rotate present-day unit vectors to one time. Returns (n, 3).

    @param home_xyz        (n, 3) present-day unit vectors
    @param plate_index     (n,) index into `plate_rotations`' key order
    @param plate_rotations list of per-plate [[pole_lon, pole_lat, angle], ...] series,
                           one entry per time
    @param time_index      which sample to use
    """
    out = np.empty_like(home_xyz)
    for p, series in enumerate(plate_rotations):
        sel = plate_index == p
        if not sel.any():
            continue
        m = _pole_angle_to_matrix(*series[time_index])
        out[sel] = home_xyz[sel] @ m.T
    return out


def live_mask(records, time, lifespan="range"):
    """Which records are drawable at `time` -- the exact rule `PointLayer.isLive()` applies.

    Duplicated here rather than approximated, because an aggregate that counted points the
    map does not draw (or vice versa) would disagree with the globe beside it with nothing
    on screen to show it. The plate-validity half is `_plateValidAt`: a point is never live
    older than its assigned static polygon's own begin age, and a point with no polygon at
    all (plate 0) is valid only at the present day.
    """
    n = len(records)
    out = np.zeros(n, dtype=bool)
    for i, r in enumerate(records):
        begin = r.get("plate_begin_age")
        if begin is not None:
            if time > begin:
                continue
        elif r.get("plate_id") == 0 and time > 0:
            continue

        if lifespan == "always":
            out[i] = True
        elif lifespan == "range":
            frm = r.get("from", r.get("age"))
            to = r.get("to", 0) or 0
            out[i] = True if frm is None else (time <= frm and time >= to)
        elif lifespan == "since":
            age = r.get("age")
            out[i] = age is None or time <= age
        else:
            raise ValueError(f"unsupported lifespan {lifespan!r}")
    return out


def _prepare(records, rotations):
    """Present-day unit vectors, plate index, and the plate rotation series in that order."""
    lon = np.array([r["lon"] for r in records], dtype=float)
    lat = np.array([r["lat"] for r in records], dtype=float)
    clat = np.cos(np.radians(lat))
    home = np.stack([clat * np.cos(np.radians(lon)),
                     clat * np.sin(np.radians(lon)),
                     np.sin(np.radians(lat))], axis=1)

    keys = sorted(rotations.keys(), key=lambda k: int(k))
    order = {k: i for i, k in enumerate(keys)}
    missing = {str(r["plate_id"]) for r in records} - set(order)
    if missing:
        raise ValueError(f"no rotation series for plate(s) {sorted(missing)}")
    index = np.array([order[str(r["plate_id"])] for r in records], dtype=np.int64)
    return home, index, [rotations[k] for k in keys]


def _grouping_codes(grouping, n):
    """Per-record category index for one grouping, -1 where uncategorised."""
    order = grouping["category_order"]
    lookup = {c: i for i, c in enumerate(order)}
    values = grouping["values"]
    if len(values) != n:
        raise ValueError("grouping 'values' must be parallel to records")
    return np.array([lookup.get(v, -1) for v in values], dtype=np.int64), order


def _declared(grouping):
    """The parts of a grouping that travel to the client (no per-record values)."""
    return {
        "label": grouping.get("label"),
        "category_order": grouping["category_order"],
        "categories": grouping.get("categories", {}),
    }


def build_aggregates(records, rotations, times, groupings, grid=None,
                     lifespan="range", richness_field=None, model_name=None,
                     meta=None, default_grouping=None):
    """One glyph's worth of summary per occupied cell per time.

    @param records         [{lon, lat, plate_id, plate_begin_age, from, to, ...}]
    @param rotations       {plate_id_str: [[pole_lon, pole_lat, angle], ...per time]} --
                           the same block `build_points` writes, so map and summary agree
    @param times           sample times in Ma, parallel to each rotation series
    @param groupings       {name: {label, category_order, categories, values}} where
                           `values` is a per-record category key (or None)
    @param richness_field  record key whose DISTINCT count per cell is reported alongside
                           the raw count -- e.g. a genus name. Skipped when None.
    """
    grid = grid or EqualAreaGrid()
    home, plate_index, series = _prepare(records, rotations)
    n = len(records)

    coded = {name: _grouping_codes(g, n) for name, g in groupings.items()}
    richness_values = (
        [r.get(richness_field) for r in records] if richness_field else None
    )

    out_groupings = {
        name: {**_declared(g), "frames": {}} for name, g in groupings.items()
    }

    for ti, time in enumerate(times):
        live = live_mask(records, time, lifespan)
        xyz = reconstruct(home, plate_index, series, ti)
        lat = np.degrees(np.arcsin(np.clip(xyz[:, 2], -1.0, 1.0)))
        lon = np.degrees(np.arctan2(xyz[:, 1], xyz[:, 0]))
        cell = grid.cell_of(lon, lat)

        for name, (codes, order) in coded.items():
            sel = live & (codes >= 0)
            if not sel.any():
                out_groupings[name]["frames"][_key(time)] = {
                    "cells": [], "counts": [], "richness": []}
                continue

            cells = cell[sel]
            cats = codes[sel]
            occupied, inverse = np.unique(cells, return_inverse=True)
            counts = np.zeros((len(occupied), len(order)), dtype=np.int64)
            np.add.at(counts, (inverse, cats), 1)

            frame = {
                "cells": occupied.tolist(),
                "counts": counts.tolist(),
            }
            if richness_values is not None:
                seen = [set() for _ in occupied]
                idx = np.flatnonzero(sel)
                for slot, rec in zip(inverse, idx):
                    v = richness_values[rec]
                    if v is not None:
                        seen[slot].add(v)
                frame["richness"] = [len(s) for s in seen]
            out_groupings[name]["frames"][_key(time)] = frame

    return {
        "model": model_name or "unknown",
        "grid": _grid_meta(grid),
        "times": [float(t) for t in times],
        "cell_centres": grid.centres(),
        # Only present for a grid that implements it (HealpixGrid) -- optional, same
        # duck-typed spirit as `describe()`. `null` for EqualAreaGrid: nothing here
        # currently renders equal-area-ring cells as filled pixels, so there was
        # nothing to justify writing an equivalent rectangle-corners method for it yet.
        "cell_bounds": grid.bounds() if hasattr(grid, "bounds") else None,
        "default_grouping": default_grouping or next(iter(groupings)),
        "groupings": out_groupings,
        "lifespan": lifespan,
        "richness_field": richness_field,
        "meta": meta or {},
    }


def _grid_meta(grid):
    """The exported `grid` metadata block -- `kind` and construction params first (so
    `EqualAreaGrid`'s `rings`/`lon_cells` and `HealpixGrid`'s `nside`/`order` read the same
    as before this was generalised), `cells`/`cell_area_sr` last since every grid has them.
    Grid classes are duck-typed against `describe()`/`cell_of()`/`centres()`/`n_cells`/
    `cell_area_sr` -- nothing here or in `build_aggregates`/`build_density` needs to know
    which one it was given."""
    return {
        "kind": grid.kind,
        **grid.describe(),
        "cells": grid.n_cells,
        "cell_area_sr": round(grid.cell_area_sr, 10),
    }


def grid_geometry(grid):
    """The (potentially large) coordinate arrays a grid's cells need to render as filled
    pixels: every cell's centre and quad corners. Split out from `build_density()`'s own
    payload so a caller can write ONE shared file per (kind, resolution) and point many
    datasets/bandwidths at it (`build_density(..., geometry_ref=...)`) instead of each
    duplicating a copy -- measured 2026-09-24: at nside=512, `cell_bounds` alone is ~245MB
    of JSON text, the majority of a fine HealpixGrid density export's size, and identical
    for every dataset that happens to share that nside."""
    return {
        "kind": grid.kind,
        **grid.describe(),
        "cells": grid.n_cells,
        "cell_centres": grid.centres(),
        "cell_bounds": grid.bounds() if hasattr(grid, "bounds") else None,
    }


def _insert_name_in_ref(ref, name):
    """"density_bw0.5.bin.gz" + "realm" -> "density_bw0.5.realm.bin.gz" -- gives a
    multi-grouping dataset (e.g. panama's origin/basin/realm) a distinct filename per
    grouping. Splits on the LITERAL ".bin" marker, not the first '.' -- a bandwidth
    like 0.5 puts a decimal point in the base name itself, so splitting on the first
    '.' produced "density_bw0.origin.5.bin.gz" (caught by actually running this
    against panama's real bandwidth-keyed filenames, not a synthetic fixture without a
    fractional bandwidth in its name)."""
    idx = ref.rindex(".bin")
    return f"{ref[:idx]}.{name}{ref[idx:]}"


def _pack_frames_binary(packed):
    """One grouping's per-frame (cells, density) arrays, in TIME order, as a flat binary
    blob -- `js/aggregate.js`'s `fetchMaybeGzippedBinary()` reads this directly into
    Uint32Array/Float32Array views, no JSON number parsing (a large fine-grid density
    payload's dominant cost -- measured 2026-09-24: 227,292-sample igneous rocks at
    nside=512 over 361 frames costs 494MB as JSON text, mostly this) and no per-element
    Python `round()` loop building it either.

    Layout (little-endian): magic 'DNSF' (4 bytes), version (uint32=1), n_frames
    (uint32); then per frame, in the SAME order `times` lists them: cell_count (uint32),
    cells (uint32 * cell_count), density (float32 * cell_count). See SCHEMA.md.
    """
    import struct
    out = bytearray(b"DNSF")
    out += struct.pack("<II", 1, len(packed))
    for cells, density in packed:
        out += struct.pack("<I", len(cells))
        out += np.asarray(cells, dtype="<u4").tobytes()
        out += np.asarray(density, dtype="<f4").tobytes()
    return bytes(out)


def _bin_nside_for_bandwidth(bandwidth_deg, cells_per_bandwidth=32):
    """Smallest power-of-two HEALPix nside for pre-binning `build_density()`'s source
    points, well finer than any grid a caller would choose to actually DISPLAY at this
    bandwidth (that one wants ~4 cells per bandwidth, not ~32 -- see the prep scripts'
    own `_nside_for_bandwidth()`; this is a private, internal-only resolution knob, not
    part of the payload).

    HEALPix cell spacing is ~58.6/nside degrees, so this finds the smallest nside whose
    spacing is at most bandwidth_deg/cells_per_bandwidth. Measured 2026-09-24 against
    2,002,734 real PBDB occurrences (whole-database fossils, 0-540 Ma) at bw=1.0/0.5:
    cells_per_bandwidth=32 gave 0.38-0.45% mean relative error on cells above 0.1% of
    peak density, for a 32-37x reduction in per-frame KDE runtime -- finer bins bought
    only marginally better accuracy for a real cost (cells_per_bandwidth=16 measured
    0.79-0.91% error at 42-45x; =4, matching the *display* grid itself, measured
    3.5-3.8% error, judged too coarse to use as the computation grid even though it's
    fine for what gets drawn).
    """
    nside = 1
    while 58.6 / nside > bandwidth_deg / cells_per_bandwidth:
        nside *= 2
    return nside


def build_density(records, rotations, times, grid, groupings, bandwidth_deg=2.0,
                  lifespan="range", model_name=None, meta=None, default_grouping=None,
                  eps=1e-3, geometry_ref=None, frames_ref=None):
    """A continuous spherical KDE, evaluated at `grid`'s cell centres, per grouping per time.

    Same payload shape as `build_aggregates()` (`groupings[name].frames[time]`) so
    `AggregateLayer` needs no new parsing to load it -- only a new draw mode, since a frame
    here carries `density` (float, one per listed cell) instead of `counts`/`richness`.
    `grid` supplies evaluation POINTS only (`.centres()`, plus `.bounds()` where the grid
    has it, for true-pixel rendering -- see `cell_bounds` below); its `cell_of`/`cell_area_sr`
    are not used, so passing an `EqualAreaGrid` here works but is pointless -- a `HealpixGrid`
    gives a more even point spacing for the same reason it does in `build_aggregates()`, AND
    is the only grid here with cell boundaries to render as actual pixels.

    @param bandwidth_deg  angular standard deviation of the von Mises-Fisher kernel, in
                          degrees. Default 2.0 (~222 km, FWHM ~520 km) resolves clustering
                          at roughly 500 km scale or finer -- narrower than that and the grid
                          itself becomes the limit (see `HealpixGrid`'s own `nside`, which
                          the caller must choose finely enough to actually resolve a kernel
                          this narrow: cell spacing should be well under one bandwidth, not
                          merely under the old default's). A prep-time constant, not a
                          runtime slider: re-evaluating a KDE per frame in the browser is not
                          on the table, and a bandwidth chosen per-view would make the same
                          dataset tell two different stories depending on who is looking.
    @param eps            a cell is dropped from a frame once its density falls below
                          `eps` of that grouping's `density_ref` (see below), rather than
                          shipping a long tail of near-zero values.
    @param geometry_ref   when given, `cell_centres`/`cell_bounds` are OMITTED from this
                          payload and `grid.geometry_ref` is set to this string instead --
                          the caller has already written (or will write) a shared
                          `grid_geometry()` file at that location for the viewer to fetch
                          once and reuse across every dataset/bandwidth at this grid's
                          resolution, rather than each payload embedding its own copy of
                          the same coordinates. `None` (the default) keeps the old
                          self-contained shape, unchanged, for callers that have not
                          adopted the shared-file convention.
    @param frames_ref     when given, every grouping's `frames` (per-time cells/density,
                          normally a JSON dict of JSON arrays) is instead packed via
                          `_pack_frames_binary()`; the caller writes the bytes found at
                          each grouping's `frames_bin` key (removed from the
                          JSON-serialisable payload, since raw bytes are not JSON) to
                          its own `frames_ref` path. A SINGLE grouping's `frames_ref` is
                          this string unchanged; MULTIPLE groupings (e.g. panama's
                          origin/basin/realm) each get a distinct name, this string with
                          the grouping's own name inserted before the first '.'
                          (`_insert_name_in_ref()`) -- caught by running this against a
                          real multi-grouping dataset, which otherwise silently pointed
                          every grouping at whichever one's bytes got written last.
                          `None` (the default) keeps the old embedded-JSON `frames`
                          shape.

    This is SAMPLED density, not abundance -- ADR-0035's discipline. Ship the discrete
    `build_aggregates()` counts alongside every density payload as the honest companion
    ("how much was collected here", not "how much was really there"); this function does
    not compute that half.
    """
    from astropy_healpix import healpy as hp
    from scipy.spatial import cKDTree

    grid = grid or HealpixGrid()
    home, plate_index, series = _prepare(records, rotations)
    n = len(records)
    coded = {name: _grouping_codes(g, n) for name, g in groupings.items()}

    centres = np.array(grid.centres())
    clon = np.radians(centres[:, 0])
    clat = np.radians(centres[:, 1])
    cxyz = np.stack([
        np.cos(clat) * np.cos(clon),
        np.cos(clat) * np.sin(clon),
        np.sin(clat),
    ], axis=1)
    # Built ONCE: the grid never changes across times/groupings, so re-building a tree
    # over it inside the loop below would pay its construction cost (seconds, at a
    # million-plus-cell grid) again on every one of potentially hundreds of iterations
    # for no reason -- only the (usually far smaller, and per-iteration-varying) point
    # side needs a fresh tree each time.
    cell_tree = cKDTree(cxyz)

    # kappa ~= 1/sigma**2 is the standard small-angle correspondence between a von
    # Mises-Fisher concentration and an angular standard deviation -- exact as sigma -> 0,
    # and close enough at the tens-of-degrees bandwidths a map actually uses.
    sigma = math.radians(bandwidth_deg)
    kappa = 1.0 / (sigma * sigma)
    # exp(kappa) / sinh(kappa) blows up in floating point for the kappa a tight bandwidth
    # implies, so the normalising constant is written in the numerically stable form that
    # cancels it: C(kappa) = kappa / (2*pi*(1 - exp(-2*kappa))), paired below with
    # exp(kappa*(cos_theta - 1)) rather than exp(kappa*cos_theta). Each point's kernel then
    # integrates to exactly 1 steradian over the whole sphere, so summed density reads in
    # "points per steradian" regardless of bandwidth.
    norm = kappa / (2.0 * math.pi * (1.0 - math.exp(-2.0 * kappa)))
    # Truncate where the kernel is indistinguishable from zero at this bandwidth. Chord
    # distance for an angular separation theta on a unit sphere is 2*sin(theta/2).
    chord_radius = 2.0 * math.sin(min(4.0 * sigma, math.pi) / 2.0)
    # Source points are pre-binned into a HEALPix grid far finer than the kernel before
    # the pair search below -- see the loop body for why, and _bin_nside_for_bandwidth's
    # docstring for the measurement behind cells_per_bandwidth=32.
    bin_nside = _bin_nside_for_bandwidth(bandwidth_deg)

    out_groupings = {name: {**_declared(g), "frames": {}} for name, g in groupings.items()}
    raw = {name: {} for name in groupings}
    series_max = {name: 0.0 for name in groupings}

    for ti, time in enumerate(times):
        live = live_mask(records, time, lifespan)
        xyz = reconstruct(home, plate_index, series, ti)

        for name, (codes, _order) in coded.items():
            sel = live & (codes >= 0)
            density = np.zeros(len(centres))
            if sel.any():
                pts = xyz[sel]
                # Collapse selected points into HEALPix bins far finer than the kernel
                # before the pair search, weighting each occupied bin by how many points
                # landed in it. The search cost below tracks DISTINCT source positions,
                # not raw record count, and real point data clusters heavily (repeated
                # localities, rounded coordinates) -- measured 2026-09-24 against
                # 2,002,734 real PBDB occurrences (whole-database fossils, 0-540 Ma):
                # 96-98% collapsed into under 90K distinct bins at this resolution, for a
                # 32-45x reduction in per-frame KDE runtime (from ~200s to ~2-5s at the
                # scale that job runs at) at 0.4-0.9% mean relative error on cells above
                # 0.1% of peak density -- a bin this much finer than the bandwidth loses
                # far less than the kernel's own 4-sigma truncation already does.
                # vec2pix/pix2vec work directly on unit vectors, no lon/lat round trip.
                bin_pix = hp.vec2pix(bin_nside, pts[:, 0], pts[:, 1], pts[:, 2], nest=False)
                occ, counts = np.unique(bin_pix, return_counts=True)
                bx, by, bz = hp.pix2vec(bin_nside, occ, nest=False)
                bin_xyz = np.stack([bx, by, bz], axis=1)
                # No Python-level loop over either side, cells or bins: `sparse_distance_matrix`
                # between the cell tree and the bin tree returns every (cell, bin) pair within
                # chord_radius as flat arrays in one C-level call -- no ragged per-cell/
                # per-bin Python loop at all -- and the kernel is then one vectorised
                # expression over all pairs at once, scattered into `density` by
                # `np.bincount` (a cell can appear more than once, once per nearby bin,
                # and needs every one summed in, not just the last write -- bincount's
                # `weights` sums duplicates, it does not overwrite them).
                pairs = cell_tree.sparse_distance_matrix(
                    cKDTree(bin_xyz), max_distance=chord_radius, output_type="coo_matrix")
                if pairs.nnz:
                    # Chord distance c = 2*sin(theta/2) on a unit sphere, so
                    # cos(theta) = 1 - c**2/2 -- the law of cosines applied to the
                    # isosceles triangle two unit vectors and their chord make.
                    # Equivalent to the direct dot product bin_xyz[j] @ cxyz[i] the
                    # earlier per-cell loop used, just read off the tree's own
                    # distances instead of recomputing it. Each bin's contribution is
                    # scaled by how many points it stands in for.
                    cos_theta = 1.0 - 0.5 * pairs.data * pairs.data
                    contrib = norm * np.exp(kappa * (cos_theta - 1.0)) * counts[pairs.col]
                    density = np.bincount(pairs.row, weights=contrib, minlength=len(centres))
            raw[name][_key(time)] = density
            if density.size:
                series_max[name] = max(series_max[name], float(density.max()))

    for name in groupings:
        # Normalised against the max over the WHOLE series, not per-frame -- a per-frame
        # normalisation would rescale the colour ramp at every step, making an animation
        # imply the same peak density at every time even when abundance genuinely changed.
        ref = series_max[name] or 1.0
        threshold = eps * ref
        if frames_ref:
            packed = []
            for density in raw[name].values():
                keep = np.flatnonzero(density >= threshold)
                packed.append((keep, density[keep]))
            del out_groupings[name]["frames"]
            out_groupings[name]["frames_bin"] = _pack_frames_binary(packed)
            # A single shared filename is only safe for a single grouping -- more than
            # one (e.g. panama's origin/basin/realm) would otherwise all claim the SAME
            # frames_ref while each writing DIFFERENT bytes there, so the JSON `groupings`
            # dict would end up pointing every grouping at whichever one wrote last.
            # Caught by actually running this against panama's real 3-grouping config,
            # not by the single-grouping tests written before that. Distinguished by
            # inserting the grouping's own name before the first '.' when there is more
            # than one, so a single-grouping dataset's filename is unchanged.
            out_groupings[name]["frames_ref"] = (
                frames_ref if len(groupings) == 1
                else _insert_name_in_ref(frames_ref, name)
            )
        else:
            for key, density in raw[name].items():
                keep = np.flatnonzero(density >= threshold)
                out_groupings[name]["frames"][key] = {
                    "cells": keep.tolist(),
                    "density": [round(float(d), 8) for d in density[keep]],
                }
        out_groupings[name]["density_ref"] = ref

    grid_block = _grid_meta(grid)
    payload = {
        "model": model_name or "unknown",
        "grid": grid_block,
        "times": [float(t) for t in times],
        "default_grouping": default_grouping or next(iter(groupings)),
        "groupings": out_groupings,
        "lifespan": lifespan,
        "bandwidth_deg": bandwidth_deg,
        "density_units": "points per steradian",
        "meta": meta or {},
    }
    if geometry_ref:
        grid_block["geometry_ref"] = geometry_ref
    else:
        payload["cell_centres"] = grid.centres()
        # Only present for a grid that implements it (HealpixGrid) -- optional, same
        # duck-typed spirit as `describe()`. `null` for EqualAreaGrid: nothing here
        # currently renders equal-area-ring cells as filled pixels, so there was
        # nothing to justify writing an equivalent rectangle-corners method for it yet.
        payload["cell_bounds"] = grid.bounds() if hasattr(grid, "bounds") else None
    return payload


def build_latitude(records, rotations, bin_ages, bins, groupings, bands=None,
                   bin_of=None, model_name=None, meta=None, default_grouping=None):
    """Counts per (age bin, latitude band, category) -- the latitude-against-age matrix.

    Age bins are STRATIGRAPHIC intervals with their own uneven widths (ICS stages run from
    under 1 Myr to 21.6), not the uniform sampling step the map uses. That distinction is
    the caller's to make: `bins` carries the intervals, `bin_ages` the age each one is
    reconstructed at, and `bin_of` says which bin each record belongs to.

    @param bin_ages  age in Ma to reconstruct at, one per bin -- normally the bin midpoint
    @param bins      [{'name', 'from', 'to'}] with from >= to, parallel to `bin_ages`
    @param bin_of    per-record bin index, or -1 for a record that resolves to no single
                     bin. Records are counted in exactly one bin, never smeared across
                     several: a record whose age range spans two stages does not constrain
                     either, and splitting it would invent precision.
    """
    bands = bands or equal_degree_bands()
    home, plate_index, series = _prepare(records, rotations)
    n = len(records)
    if bin_of is None:
        raise ValueError("bin_of is required -- which bin each record belongs to is a "
                         "domain decision, not something this module can infer")
    bin_of = np.asarray(bin_of, dtype=np.int64)

    edges = np.array([b["from"] for b in bands] + [bands[-1]["to"]], dtype=float)
    coded = {name: _grouping_codes(g, n) for name, g in groupings.items()}

    out = {}
    for name, (codes, order) in coded.items():
        matrix = np.zeros((len(bins), len(bands), len(order)), dtype=np.int64)
        out[name] = {**_declared(groupings[name]), "counts": matrix}

    for bi, age in enumerate(bin_ages):
        sel_bin = bin_of == bi
        if not sel_bin.any():
            continue
        # Reconstruct the whole set at this bin's age and then select -- one matrix per
        # plate either way, and it keeps the code identical to build_aggregates'.
        xyz = reconstruct(home, plate_index, series, bi)
        lat = np.degrees(np.arcsin(np.clip(xyz[:, 2], -1.0, 1.0)))
        band = np.clip(np.searchsorted(edges, lat, side="right") - 1, 0, len(bands) - 1)

        for name, (codes, order) in coded.items():
            sel = sel_bin & (codes >= 0)
            if not sel.any():
                continue
            np.add.at(out[name]["counts"], (bi, band[sel], codes[sel]), 1)

    for name in out:
        out[name]["counts"] = out[name]["counts"].tolist()

    return {
        "model": model_name or "unknown",
        "bands": bands,
        "bins": [{"name": b.get("name"), "from": float(b["from"]), "to": float(b["to"])}
                 for b in bins],
        "bin_ages": [float(a) for a in bin_ages],
        "default_grouping": default_grouping or next(iter(groupings)),
        "groupings": out,
        "meta": meta or {},
    }


def _key(time):
    """Frame keys are stringified numbers; keep 12.0 and 12 from becoming two keys."""
    t = float(time)
    return str(int(t)) if t.is_integer() else str(t)
