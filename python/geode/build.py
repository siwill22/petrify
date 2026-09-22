"""
Turning layer specs into exported data, through the cache.

Everything here needs pygplates and gprm. Nothing here runs twice for the same
inputs. Those two sentences together are the whole design: the scientific stack is
required to *make* a viewer and not to *look at* one, and the seam between those is
the cache (cache.py).
"""

import os
import sys

from .cache import Cache, key_for


def _petrify():
    """Import petrify, adding the sibling package directory if need be.

    `geode` ships beside `petrify` in the same repository, so a user who put
    the repo on their path once should not have to do it again per package.

    The repository a reader clones is *also* named `petrify`, so running the
    notebook from the directory that `git clone` created leaves an empty
    `petrify/` sitting in the current directory. Python's import system finds
    that directory before it ever reaches pip's editable-install machinery
    and happily builds an empty namespace package out of it -- `import
    petrify` succeeds, but the result has no `export_series` and no
    `__file__`. Checking for the attribute, not just catching ImportError, is
    what tells the real package apart from that empty stand-in.
    """
    try:
        import petrify
        if not hasattr(petrify, "export_series"):
            raise ImportError("shadowed by an unrelated 'petrify' on sys.path")
    except ImportError:
        sys.modules.pop("petrify", None)
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import petrify
    return petrify


class Builder:
    """Builds and caches the data a View needs. One per export."""

    def __init__(self, view, cache=None, quiet=False):
        self.view = view
        self.cache = cache or Cache()
        self.quiet = quiet
        self._model = None
        self._series_key = None

    def log(self, message):
        if not self.quiet:
            print(message)

    @property
    def model(self):
        """The loaded reconstruction model, fetched once per export.

        Deliberately lazy: a rebuild where every layer is a cache hit never touches
        gprm at all, which is what makes re-exporting after a colour change fast.
        """
        if self._model is None:
            self.log("loading {}".format(self.view.reconstruction))
            self._model = _petrify().load_model(self.view.reconstruction)
        return self._model

    def _base(self):
        return {"model": self.view.reconstruction, "start": self.view.start,
                "end": self.view.end, "step": self.view.step}

    # -- boundaries and velocities ------------------------------------------

    def series(self, tessellate=0.5, healpix_n=8, delta_time=1.0):
        """Boundary frames and velocity frames, in ONE cache entry.

        They are exported together because they are computed together: resolving the
        topologies at a timestep is the expensive part, and both outputs fall out of
        it. Keying them separately would mean resolving every timestep twice.
        """
        params = dict(self._base(), tessellate=tessellate, healpix_n=healpix_n,
                      delta_time=delta_time)
        key = key_for("series", params)
        self._series_key = key

        if self.cache.hit(key):
            self.log("  series: cached ({:.0f} MB)".format(self.cache.size(key) / 1e6))
            return self.cache.path(key)

        self.log("  series: building {} frames, {}-{} Ma".format(
            (self.view.end - self.view.start) // self.view.step + 1,
            self.view.start, self.view.end))
        out = self.cache.begin(key)
        _petrify().export_series(
            model_name=self.view.reconstruction,
            start=self.view.start, end=self.view.end, step=self.view.step,
            tessellate=tessellate, healpix_n=healpix_n, delta_time=delta_time,
            out_dir=out, quiet=self.quiet)
        self.cache.finish(key, params)
        return out

    # -- polygons ------------------------------------------------------------

    def polygons(self, which="continents", tolerance=0.02):
        params = dict(self._base(), which=which, tolerance=tolerance)
        key = key_for("polygons", params)

        if self.cache.hit(key):
            self.log("  {}: cached".format(which))
            return self.cache.path(key)

        self.log("  {}: building".format(which))
        out = self.cache.begin(key)
        _petrify().export_polygons(
            model_name=self.view.reconstruction, model=self.model,
            start=self.view.start, end=self.view.end, step=self.view.step,
            which=which, tolerance=tolerance, out_dir=out, quiet=self.quiet)
        self.cache.finish(key, params)
        return out

    # -- points --------------------------------------------------------------

    def points(self, spec):
        """Reconstruct one point layer.

        The cache key covers the reconstruction parameters AND the data itself --
        the exact lon/lat/age values and the set of carried fields. Change a filter
        upstream and this correctly misses; change a colour and it correctly hits.
        """
        frame = spec["_frame"]
        fields = spec["_fields"]
        plate_id_field = spec.get("_plate_id_field")
        fingerprint = _frame_fingerprint(frame, fields, spec["_has_age"], plate_id_field)
        params = dict(self._base(), fields=[f[0] for f in fields],
                      data=fingerprint, rows=len(frame))
        key = key_for("points", params)

        if self.cache.hit(key):
            self.log("  points: cached ({} rows)".format(len(frame)))
            return self.cache.path(key)

        self.log("  points: assigning plates to {} rows".format(len(frame)))
        out = self.cache.begin(key)
        _petrify().export_points(
            frame, model_name=self.view.reconstruction, model=self.model,
            start=self.view.start, end=self.view.end, step=self.view.step,
            # `rotations` ships present-day geometry plus a rotation series per
            # plate; `trajectory` ships a position per point per time. With points
            # in the thousands on a few hundred plates, rotations is smaller by
            # orders of magnitude. The crossover is a property of the data, not a
            # choice a caller should have to make, so it is not exposed.
            transport="rotations",
            fields=fields, categories=spec["_categories"],
            meta=spec["_meta"] or None, out_dir=out, quiet=self.quiet,
            plate_id_field=plate_id_field)
        self.cache.finish(key, params)
        return out

    # -- derived series ------------------------------------------------------

    def boundary_length(self):
        """Ridge/transform/subduction length per timestep, from the frames.

        Reads whatever `series()` already built rather than re-resolving anything,
        so this is cheap and cannot disagree with the boundaries actually drawn.
        """
        if self._series_key is None:
            raise RuntimeError(
                "timeseries('boundary_length') needs boundaries() on the same view")
        data_dir = self.cache.path(self._series_key)
        path = os.path.join(data_dir, "boundary_length.csv")
        if not os.path.exists(path):
            self.log("  boundary_length: measuring frames")
            _petrify().boundary_length_series(data_dir)
        return path


def _frame_fingerprint(frame, fields, has_age, plate_id_field=None):
    """A hash of the values that determine the reconstruction, not of the object.

    pandas hashing rather than anything bespoke: it is stable across processes and
    handles the mixed dtypes a real compilation carries. Only the columns that reach
    the export are included, so adding an unused column to the frame is not a cache
    miss.
    """
    import pandas as pd

    columns = ["Longitude", "Latitude"] + (["Age"] if has_age else []) \
        + [c for _, c in fields if c in frame.columns] \
        + ([plate_id_field] if plate_id_field else [])
    subset = frame[[c for c in dict.fromkeys(columns) if c in frame.columns]]
    return str(pd.util.hash_pandas_object(subset.astype(str), index=False).sum())
