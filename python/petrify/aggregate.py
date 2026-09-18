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
traded for exact equality of area, and for an exporter with no HEALPix dependency
available. A HEALPix grid would improve the aspect ratio and needs no renderer change --
only a different implementation of `cell_of`/`centres` here.

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
    "build_aggregates",
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
        "grid": {
            "kind": grid.kind,
            "rings": grid.rings,
            "lon_cells": grid.lon_cells,
            "cells": grid.n_cells,
            "cell_area_sr": round(grid.cell_area_sr, 10),
        },
        "times": [float(t) for t in times],
        "cell_centres": grid.centres(),
        "default_grouping": default_grouping or next(iter(groupings)),
        "groupings": out_groupings,
        "lifespan": lifespan,
        "richness_field": richness_field,
        "meta": meta or {},
    }


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
