"""
The `View` -- nine verbs that turn a DataFrame into a reconstructed globe.

The design constraint is stated rather than implied: if this grows past about a
dozen verbs, the property that makes the whole thing worth having -- that reading
the generated code teaches you the API -- breaks, and it has failed regardless of
how useful the extras are.

Two further rules the implementation keeps:

* **Every verb logs its own call.** Not for replay, but because `export()` then
  emits a canonical, ordered, minimal View Script that provably reproduces the view.
  That is immune to out-of-order notebook execution and works identically whether
  the calls came from a notebook, a script, or a chat session.

* **Nothing heavy happens in a verb.** `points()` assigns plates and `boundaries()`
  reconstructs topologies, but both go through the cache (see cache.py), so
  re-running the view block to change a colour costs nothing.
"""

import inspect
import os
import re

DEFAULT_THEME = "abyssal"

# Credits for the reconstruction models this can be built against. The exported
# record keeps the model NAME; this only decides how the page spells it out, so a
# page's attribution follows its data rather than having to be remembered.
MODEL_CREDITS = {
    "Merdith2021": "Merdith et al. (2021)",
    "Muller2019": "Müller et al. (2019)",
    "Muller2022": "Müller et al. (2022)",
    "Matthews2016": "Matthews et al. (2016)",
    "Cao2024": "Cao et al. (2024)",
    "Scotese2016": "Scotese & Wright (2018)",
    "Seton2012": "Seton et al. (2012)",
    "Zahirovic2022": "Zahirovic et al. (2022)",
    "TorsvikCocks2017": "Torsvik & Cocks (2017)",
}


# ---- Display Rules ---------------------------------------------------------
#
# Two, both derived from pages that exist. A third ships when a real page needs
# one; every rule justified by a page that exists is the discipline that stops this
# becoming a DSL with bad syntax.

def constant():
    """Colour depends only on which group a point is in. The default."""
    return {"type": "constant"}


def age_window(window, fade=0.07):
    """Bright within `window` Myr of a point's own age, faint outside it.

    A function of (point, current time), re-evaluated on every scrub -- which is
    exactly why it cannot be a Python callback and has to be a rule the browser
    understands. `fade` is the alpha of the faint variant: the same hue, not a
    separately chosen colour, so the two read as one category in two states.
    """
    return {"type": "age_window", "window": float(window), "fade": float(fade)}


# ---- column names ----------------------------------------------------------

def payload_key(column):
    """A DataFrame column name as a JSON payload key.

    Callers name columns; the payload key is an implementation detail they should
    never have to type. Leading digits are prefixed because a key like `2_error`
    reads as a number in every consumer that tries.
    """
    key = re.sub(r"[^0-9a-zA-Z]+", "_", str(column)).strip("_").lower()
    if not key:
        raise ValueError("column {!r} has no usable name".format(column))
    return "n" + key if key[0].isdigit() else key


def _auto_label(column):
    text = re.sub(r"[_/]+", " ", str(column)).strip()
    return text[:1].upper() + text[1:]


def _caller_name(obj, default):
    """The variable the caller bound `obj` to, for the View Script.

    A DataFrame cannot be inlined into generated code, so the script has to refer to
    it by name; guessing "df" when the notebook called it `kept` would make the
    script look like a sketch rather than the thing that ran. Two frames up because
    this is called from inside a verb.
    """
    frame = inspect.currentframe()
    try:
        outer = frame.f_back.f_back
        if outer is None:
            return default
        for name, value in outer.f_locals.items():
            if value is obj and not name.startswith("_"):
                return name
    finally:
        del frame
    return default


# Coordinate columns are found rather than demanded. Every compilation spells them
# differently and none of the spellings are interesting; making the caller state
# which one theirs uses would be the first line of boilerplate back.
LON_NAMES = ("lon", "longitude", "long", "x", "lng")
LAT_NAMES = ("lat", "latitude", "y")


def _find_column(columns, candidates, what):
    lookup = {str(c).strip().lower(): c for c in columns}
    for name in candidates:
        if name in lookup:
            return lookup[name]
    if what is None:
        return None
    raise KeyError("no {} column found (tried {}); pass one explicitly".format(
        what, ", ".join(candidates)))


def _unless(value, default):
    """`value`, or None when it is the default -- see View._record()."""
    return None if value == default else value


class View:
    """A reconstructed globe under construction. Build with `geode.globe()`."""

    def __init__(self, reconstruction="Merdith2021", times=(0, 250, 1),
                 projection="orthographic", centre=(0, 0), zoom=1.0,
                 start_time=None, title=None, subtitle=None, ocean=True,
                 anchor_plate=0, anchor_plates=None, partition_polygons=None):
        start, end, step = times
        # A single model, unchanged, or a list of models sharing one view -- the
        # live reconstruction-model toggle a page can offer (see `explorer.js`'s
        # model-switch panel). `self.reconstruction` stays the first/primary model
        # so every existing single-model read site (Builder.model, MODEL_CREDITS,
        # recipe["credit"], script.py) needs no changes at all.
        self.reconstructions = ([reconstruction] if isinstance(reconstruction, str)
                                else list(reconstruction))
        self.reconstruction = self.reconstructions[0]
        # Per-model overrides, keyed by reconstruction name. `anchor_plate` is the
        # single-model shorthand (applies to the primary model only, unless the
        # caller names it again in `anchor_plates`); petrify's own export functions
        # already take an `anchor_plate` parameter -- this just finally exposes it
        # above Builder, which always used the implicit default 0 before.
        self.anchor_plates = dict(anchor_plates or {})
        self.anchor_plates.setdefault(self.reconstruction, anchor_plate)
        # Point-in-polygon partitioning source per model: 'static' (default) or
        # 'continents', for a model (e.g. Muller2022) whose ReconstructionModel has
        # continent polygons but no static polygons.
        self.partition_polygons = dict(partition_polygons or {})
        self.start = int(start)
        self.end = int(end)
        self.step = int(step)
        self.projection = projection
        self.centre = tuple(centre)
        self.zoom = float(zoom)
        # Neither endpoint is a good opening view: near the present the globe is a
        # solid pile of everything that ever happened, and near the far end it is
        # almost empty. A third of the way in shows an accumulated backdrop plus a
        # legible band of whatever is currently active.
        self.start_time = (float(start_time) if start_time is not None
                           else round(self.start + (self.end - self.start) * 0.3))
        self.title = title or "{} reconstruction".format(self.reconstruction)
        self.subtitle = subtitle
        self.theme_id = DEFAULT_THEME
        self.caption_text = None
        self.play_rate = None

        self._layers = []
        self._background = None
        self._charts = []
        self._distance_heatmap = None
        self._log = []
        self._libraries = []
        self._notebook = None
        self._notebook_requirements = None
        self._notebook_steps = None
        self._notebook_figures = None

        self._record("globe",
                     reconstruction=(self.reconstruction if len(self.reconstructions) == 1
                                     else self.reconstructions),
                     anchor_plate=(_unless(self.anchor_plates.get(self.reconstruction, 0), 0)
                                   if len(self.reconstructions) == 1 else None),
                     anchor_plates=(self.anchor_plates
                                    if len(self.reconstructions) > 1 else None),
                     partition_polygons=self.partition_polygons or None,
                     times=(self.start, self.end, self.step),
                     projection=_unless(projection, "orthographic"),
                     centre=_unless(self.centre, (0, 0)),
                     zoom=_unless(self.zoom, 1.0),
                     start_time=self.start_time, title=title, subtitle=subtitle,
                     receiver=None)

        if ocean:
            # Drawn first, under everything. Without it a globe with no
            # paleogeography raster has no body at all and the vectors float on the
            # page background. Not a verb: nobody wants to think about it, and
            # making it one would spend a tenth of the API's budget on a disc.
            self._layers.append({"kind": "ocean"})

    # -- provenance ---------------------------------------------------------

    def _record(self, verb, receiver="v", **kwargs):
        """Log a call, dropping arguments that were left at their default.

        "Minimal" in the View Script's contract is not decoration: a script that
        spells out every default reads as machine output, and the property that
        makes the whole exercise worth it -- that reading the generated code teaches
        you the API -- depends on it reading like code someone wrote.
        """
        self._log.append({"verb": verb, "receiver": receiver,
                          "kwargs": {k: v for k, v in kwargs.items()
                                     if k != "receiver" and v is not None}})

    def cites(self, call, package, version=None, citation=None):
        """Name an analysis library this view's data came from.

        The provenance drawer shows the View Script and, optionally, the author's
        notebook. It cannot show `gprm` -- that is a package, not a snippet. A panel
        claiming to show "the code" while silently omitting the thing that did the
        science implies an audit trail it does not have, so the record names and
        pins the call instead.
        """
        self._libraries.append({"call": call, "package": package,
                                "version": version, "citation": citation})
        return self

    def notebook(self, path, requirements=None, steps=None, figures=None):
        """Bundle the author's own notebook or script beside the View Script.

        `requirements` is optional plain-language setup steps for reproducing the
        analysis (a conda environment, a package that is not on PyPI, an expected
        runtime) -- shown in the provenance drawer under "Running the notebook
        yourself". Only the author knows what their own analysis needs, so this is
        opt-in data rather than something the host could guess at. Leaving it out
        means the drawer simply omits that section rather than showing a guess.

        `steps` is an optional, author-written, one-line-per-step account of what
        the analysis block actually does -- fetch, filter, classify, whatever it
        really is. This can never be generated FROM the notebook: the data block is
        arbitrary code `View` never sees, so only the author can state honestly
        what it does. Leaving it out means the drawer omits that section too,
        rather than guessing at a summary it cannot verify.

        `figures` is an optional list of `{"path": ..., "caption": ...}` dicts --
        static images bundled alongside the notebook as supporting evidence for
        `steps`. Each is copied into the export as-is; nothing about a figure's
        content is checked against the view, so a caption that comes from a
        different dataset or a separate script than this notebook's own steps
        must say so plainly, the same way `cites()` names analysis it cannot show
        directly.
        """
        self._notebook = os.path.abspath(path)
        self._notebook_requirements = list(requirements) if requirements else None
        self._notebook_steps = list(steps) if steps else None
        self._notebook_figures = list(figures) if figures else None
        return self

    # -- the standard layers ------------------------------------------------

    def background(self, image, label=None, legend_title=None, source=None,
                   doi=None, caption=None):
        """A static raster painted under every other layer, never reconstructed.

        Unlike `.points()`/`.boundaries()`, nothing here is derived from a
        DataFrame or run through pygplates -- `image` is a path to a pre-rendered,
        already colour-mapped PNG. This verb does no numeric colour-mapping of its
        own: bake whatever colour ramp the raster needs into the image itself
        before calling this (a `Variable`'s ramp is a scientific decision a Theme
        must never touch -- the same rule that keeps a scientific colormap outside
        `.theme()`'s reach applies here).

        Replaces the default ocean disc rather than sitting alongside it -- both
        are answers to the same "what gives the globe a body" question, and a
        raster big enough to cover the sphere makes the flat fallback pointless
        underneath it.
        """
        self._layers = [lyr for lyr in self._layers if lyr["kind"] != "ocean"]
        self._background = {
            "kind": "raster",
            "_image_path": os.path.abspath(image),
            "label": label,
            "legend": {"title": legend_title} if legend_title else None,
            # Not `_meta`: unlike `.points()`, there is no petrify export call here
            # to consume an underscore-prefixed key before `_public()` strips it --
            # these have to survive as ordinary fields to reach `recipe["background"]`
            # at all.
            "source": source,
            "doi": doi,
            "caption": caption,
        }
        self._background = {k: v for k, v in self._background.items() if v is not None}
        self._record("background", image=image, label=label,
                     legend_title=legend_title, source=source, doi=doi,
                     caption=caption)
        return self

    def continents(self, which="continents", tolerance=0.02, alpha=0.62,
                   line_width=0.7):
        """Reconstructed continent polygons.

        `tolerance` is a simplification in degrees; the default is about one device
        pixel at 4x zoom on a globe of this size, so the saving is invisible.
        """
        self._layers.append({"kind": "continents", "id": which, "which": which,
                             "tolerance": tolerance, "fillAlpha": alpha,
                             "lineWidth": line_width})
        self._record("continents", which=_unless(which, "continents"),
                     tolerance=_unless(tolerance, 0.02))
        return self

    def boundaries(self, tessellate=0.5, legend=True):
        """Resolved plate boundaries, one frame per timestep.

        Frames are shown with hard cuts, not interpolated: resolved topologies change
        discontinuously, so a tween between two of them draws boundaries that never
        existed.
        """
        self._layers.append({"kind": "boundaries", "tessellate": tessellate,
                             "legend": legend})
        self._record("boundaries", tessellate=_unless(tessellate, 0.5))
        return self

    def velocities(self, healpix_n=8, delta_time=1.0, scale_bar=True,
                   label="Plate velocity"):
        """Plate velocity arrows on a fixed HEALPix sampling grid.

        Equal-area by construction, which matters: a regular lon/lat grid would
        crowd arrows at the poles and claim a density that is an artifact of the
        graticule rather than of the plate motions.
        """
        self._layers.append({"kind": "velocities", "healpixN": healpix_n,
                             "deltaTime": delta_time, "scaleBar": scale_bar,
                             "label": label})
        self._record("velocities", healpix_n=_unless(healpix_n, 8),
                     scale_bar=_unless(scale_bar, True))
        return self

    # -- the main verb ------------------------------------------------------

    def points(self, df, age=None, lon=None, lat=None, group=None, labels=None,
               colours=None, lifespan="since", window=None, highlight=None, size=3.4,
               keyline_alpha=0.55, keyline_width=None, hover=(), label=None, footer=None,
               style_js=None, caption=None, source=None, doi=None,
               legend_title=None, name=None, plate_id=None, symbol=None,
               star_points=None):
        """Put a DataFrame of located, dated things on the globe.

        `age`, `lon`, `lat`, `group` and everything in `hover` are COLUMN NAMES. The
        payload keys they become are an implementation detail; nothing a caller
        writes ever refers to them.

        `hover` may be a list of columns (labels derived from the names) or a dict
        of column -> label, or column -> {label, unit, error, errorUnit} where a
        measurement and its uncertainty belong on one line.

        `window`, with `lifespan="range"`, is a Myr width around `age` that a point
        stays visible. A single number is Myr *before* `age` only -- e.g. a preview
        marker that should appear only in the run-up to some later event, not
        persist after it (that is what `lifespan="since"` is for). A `(before,
        after)` pair instead brackets `age` on both sides -- e.g. a brief, symmetric
        pulse for the event itself, visible from `age + before` to `age - after`.
        Internally this is `from = age + before, to = age - after` (a bare number
        means `after = 0`), the `from`/`to` pair the renderer's `range` lifespan
        already reads -- computed here from the single `age` column already being
        carried, so the caller never has to add their own `from`/`to` columns. A
        global width, not per-row: if a dataset already encodes its own per-feature
        window (e.g. a `valid_time` range baked into a source file), derive one
        `age` column from it (its midpoint, usually) before calling this, rather
        than trying to carry that per-row width through.

        `plate_id`, a column name, trusts an existing plate assignment instead of
        the default (point-in-polygon testing against the Reconstruction Model's
        static polygons). Some compilations ship a plate id reflecting real domain
        knowledge partitioning cannot recover -- oceanic crust especially, which a
        model's static polygons may not cover at all, silently pinning such a point
        at its present-day position forever. Only rows where this column is
        non-null are overridden; everything else still gets a plate id from
        partitioning as usual. An explicit `0` is itself a real override -- the
        anchor plate, i.e. "hold this point at its literal position forever" -- not
        a way of saying "no override", which is what a null/NaN cell means instead.
        That is how a marker fixed in the absolute/mantle frame (never reconstructed
        by any plate motion) is asked for: a constant plate-id column of zeros.

        On a view built on more than one reconstruction model, `plate_id` may
        instead be `{model_name: column_or_None}` -- a compilation's plate ids are
        usually native to the one model they were built/QA'd against, and applying
        them under a DIFFERENT model's rotation file is wrong, not just imprecise
        (one model's plate numbering fed to another model's Euler poles). Name only
        the model(s) the column is valid for; any other model in the view falls back
        to ordinary partitioning, exactly as if `plate_id` had been left `None` for
        it. A plain column name (today's behaviour) still applies to every model.

        `lon`/`lat` may likewise be `{model_name: column}` dicts (both, naming every
        model in the view) -- for positions that are themselves model-dependent,
        e.g. an eruption-age paleoposition held fixed in the mantle frame, which each
        model places differently.

        `symbol` is the whole layer's marker shape (`'circle'` the default,
        `'square'`, `'diamond'`, `'triangle'`, `'triangle-down'`, `'hexagon'`,
        `'cross'`, `'star'`) -- a per-layer choice, not per-category; a page
        needing different shapes per group is exactly what `style_js` is for.
        `star_points` is `symbol='star'`'s own point count (default 5); ignored
        for every other symbol. `keyline_width`, like `keyline_alpha`, is a
        layer-wide pen weight for the outline every symbol is stroked with
        (default 0.6) -- thicker for a mark that needs to read as bold, not
        just brightly coloured.

        `style_js` is the escape hatch, and a supported path rather than a failure:
        a JS module whose default export is `(point, category, api) => {fill, ...}`.
        Pages whose symbols are genuinely bespoke -- pie glyphs, say -- belong there
        rather than in a Display Rule nobody else could use.
        """
        if window is not None and lifespan != "range":
            raise ValueError(
                "window= only means something with lifespan='range' "
                "(got lifespan={!r})".format(lifespan))
        if lifespan == "range" and window is not None and age is None:
            raise KeyError("window= needs an age= column to measure the range from")
        if isinstance(window, (tuple, list)):
            if len(window) != 2:
                raise ValueError(
                    "window=(before, after) needs exactly two values, got {!r}".format(
                        window))
            window_before, window_after = float(window[0]), float(window[1])
        else:
            window_before = float(window) if window is not None else None
            window_after = 0.0
        columns = set(df.columns)

        def need(col, what):
            if col not in columns:
                raise KeyError("no column {!r} for {} (have: {})".format(
                    col, what, ", ".join(sorted(columns)[:12])))

        if age is not None:
            need(age, "age")
        positions = None
        if isinstance(lon, dict) or isinstance(lat, dict):
            if not (isinstance(lon, dict) and isinstance(lat, dict)) \
                    or set(lon) != set(lat):
                raise ValueError("lon= and lat= must both be {model: column} dicts "
                                 "with the same models, or both plain column names")
            missing = [m for m in self.reconstructions if m not in lon]
            if missing:
                raise KeyError("lon=/lat= name no column for model(s) {}".format(
                    ", ".join(missing)))
            for m in lon:
                need(lon[m], "longitude ({})".format(m))
                need(lat[m], "latitude ({})".format(m))
            positions = {m: (lon[m], lat[m]) for m in lon}
            lon, lat = positions[self.reconstruction]
        lon = lon or _find_column(columns, LON_NAMES, "longitude")
        lat = lat or _find_column(columns, LAT_NAMES, "latitude")
        need(lon, "longitude")
        need(lat, "latitude")
        if group is not None:
            need(group, "group")
        if isinstance(plate_id, dict):
            for col in plate_id.values():
                if col is not None:
                    need(col, "plate_id")
        elif plate_id is not None:
            need(plate_id, "plate_id")

        # The age, longitude and latitude columns are written by the exporter under
        # fixed payload keys of its own. Carrying them AGAIN as ordinary fields is
        # not merely redundant: the export reads fields off the RENAMED frame, where
        # the caller's own column name no longer exists, so the duplicate lands as
        # null and shadows the real value. Cost one silently empty Age row in every
        # popup before the browser check caught it.
        reserved = {age, lon, lat} - {None}
        hover_spec, hover_cols = _hover_spec(hover, columns, age_column=age)
        extra = []
        for col in ([group] if group else []) + list(hover_cols) \
                + ([label] if label else []) + ([footer] if footer else []):
            if col and col not in extra:
                need(col, "points")
                if col not in reserved:
                    extra.append(col)

        fields = [(payload_key(c), c) for c in extra]
        # points_from_dataframe reads fixed 'Longitude'/'Latitude'/'Age' columns. The
        # rename is internal; the caller's frame is untouched (this operates on
        # `prepared`, never `df` itself).
        #
        # A source frame that already carries a column under one of those THREE
        # exact names -- distinct from the column being renamed into it, e.g. a
        # gprm loader's own 'Longitude'/'Latitude' alongside a caller-computed
        # 'fixed_lon'/'fixed_lat' picked via lon=/lat= instead -- must be dropped
        # first. Otherwise the rename produces two columns sharing one name, and
        # every later `row[lon_field]` lookup in points_from_dataframe returns a
        # Series, not a scalar: silently wrong at best, a bare TypeError at worst.
        prepared = df
        renames = {lon: "Longitude", lat: "Latitude"}
        if age is not None:
            renames[age] = "Age"
        collisions = [target for src, target in renames.items()
                     if src != target and target in prepared.columns]
        if collisions:
            prepared = prepared.drop(columns=collisions)
        prepared = prepared.rename(columns=renames)

        if lifespan == "range" and window is not None:
            # `from`/`to` are what the renderer's `range` lifespan actually reads
            # (points.js: `to <= time <= from`) -- carried through as ordinary named
            # fields, same mechanism as `hover`/`group`, just under the two literal
            # keys the renderer already knows. No petrify change needed below this.
            prepared = prepared.copy()
            prepared["_geode_range_from"] = prepared["Age"] + window_before
            prepared["_geode_range_to"] = prepared["Age"] - window_after
            fields = fields + [("from", "_geode_range_from"), ("to", "_geode_range_to")]

        categories = None
        if group is not None:
            gkey = payload_key(group)
            seen = [g for g in prepared[group].dropna().unique()]
            categories = {str(g): {"label": (labels or {}).get(g, str(g))}
                          for g in seen}
            # PointLayer groups on `type`, so the group column is carried under that
            # key and not its own. Anything else referring to the group -- the popup
            # eyebrow below -- must therefore say `type` too.
            fields = [("type", group)] + [f for f in fields if f[0] != gkey]

        spec = {
            "kind": "points",
            "id": "points{}".format(sum(1 for lyr in self._layers
                                        if lyr["kind"] == "points") or ""),
            "lifespan": lifespan,
            "size": size,
            "keylineAlpha": keyline_alpha,
            "rule": highlight or constant(),
            "colours": {str(k): v for k, v in (colours or {}).items()},
            "legend": {"title": legend_title or "Samples", "counts": True},
            "_fields": fields,
            "_categories": categories,
            "_frame": prepared,
            "_has_age": age is not None,
            "_plate_id_field": plate_id,
            "_positions": positions,
            "_meta": {k: v for k, v in
                      {"source": source, "doi": doi, "caption": caption}.items()
                      if v},
        }
        if symbol:
            spec["symbol"] = symbol
        if star_points:
            spec["starPoints"] = star_points
        if keyline_width is not None:
            spec["keylineWidth"] = keyline_width
        if style_js:
            spec["styleJs"] = os.path.basename(style_js)
            spec["_style_js_path"] = os.path.abspath(style_js)
        if hover_spec or label or footer:
            spec["hover"] = {
                "rows": hover_spec,
                "title": [payload_key(label)] if label else [],
                "titleFallback": "Unnamed",
                "eyebrow": "type" if group else None,
                "footer": payload_key(footer) if footer else None,
            }
            spec["hover"] = {k: v for k, v in spec["hover"].items() if v}

        self._layers.append(spec)
        self._record("points", frame=name or _caller_name(df, "df"), age=age,
                     lon=({m: p[0] for m, p in positions.items()} if positions
                          else _unless(lon, _find_column(columns, LON_NAMES, None))),
                     lat=({m: p[1] for m, p in positions.items()} if positions
                          else _unless(lat, _find_column(columns, LAT_NAMES, None))),
                     group=group, labels=labels, colours=colours,
                     lifespan=None if lifespan == "since" else lifespan,
                     window=window, plate_id=plate_id, symbol=symbol,
                     star_points=star_points, keyline_width=keyline_width,
                     highlight=highlight, size=None if size == 3.4 else size,
                     hover=hover or None, label=label, footer=footer,
                     style_js=style_js, source=source, doi=doi, caption=caption,
                     legend_title=legend_title)
        return self

    # -- chart --------------------------------------------------------------

    def timeseries(self, data, series, time=None, mode="shade", step=False):
        """A chart under the slider, sharing the map's clock.

        `data` is a DataFrame, a path to a CSV, or the string `"boundary_length"`,
        which is computed from the boundary frames this view already exports.

        `step=True` writes each row at both ends of its interval, which draws a
        stepped histogram out of a plain line renderer -- the alternative being a
        bar mode in the chart library for the sake of one page.
        """
        self._charts.append({"data": data, "series": series, "time": time,
                             "step": step})
        self.chart_mode = mode
        self._record("timeseries",
                     data=data if isinstance(data, str) else _caller_name(data, "df"),
                     series=series, time=time,
                     mode=None if mode == "shade" else mode,
                     step=step or None)
        return self

    def distance_heatmap(self, samples, baseline, distance_label="Distance",
                         categories=None, note=None):
        """A time/distance heatmap under the slider, with a quantile-shift bar above it.

        Unlike every other verb, this does NOT compute anything -- it packages numbers
        the caller already has. `.points()` and `.boundaries()` can do their own
        (cached) reconstruction because pygplates and a rotation model are enough; this
        chart's numbers come from a proximity analysis (see `gprm.utils.molchan`) that
        is expensive, exploratory, and often keyed to a dataset the view's own `.points()`
        never touches. Teaching this verb to run that analysis itself would be real new
        machinery, not justified until a second consumer needs it (see Geode's ADR-0052).

        `samples` is a DataFrame with one row per real sample: a `time` column (the
        reconstruction time bucket, matching `baseline`'s own), an `age` column (the
        sample's own continuous age -- the heatmap's x axis), a `distance` column, and
        one column per key in `categories` (e.g. `rockType`, `setting`) -- whatever a
        reader can toggle. Every other column is dropped.

        `baseline` is a DataFrame with one row per reconstruction time: a `time` column
        and decile columns `d10` .. `d90` -- NOT raw random-point distances. The random
        baseline does not depend on which samples are toggled on, so its deciles are
        computed once here rather than shipping the raw points (tens of thousands of
        rows per time step) for the browser to redo the same arithmetic on every toggle.

        `categories` is `{field: {"label": ..., "options": [...]}}`, one entry per
        toggleable column in `samples` -- it tells the host what buttons to draw and
        what each option is called; it does not change how the data is read.

        `note`, shown beside the chart (not just in the provenance drawer): state
        plainly if `samples` comes from a different dataset than the view's own
        `.points()` -- this chart is primary UI, not an aside, so the caveat has to be
        visible without opening anything.
        """
        self._distance_heatmap = {
            "samples": samples, "baseline": baseline,
            "distance_label": distance_label,
            "categories": categories or {}, "note": note,
        }
        self._record("distance_heatmap",
                     samples=_caller_name(samples, "samples"),
                     baseline=_caller_name(baseline, "baseline"),
                     distance_label=_unless(distance_label, "Distance"),
                     categories=categories, note=note)
        return self

    # -- presentation -------------------------------------------------------

    def theme(self, theme_id):
        """A named Theme: a coherent set of colours, line weights and decoration
        sizes for the map's furniture. Never for the data -- see Geode's ADR-0038."""
        self.theme_id = theme_id
        self._record("theme", theme_id=theme_id)
        return self

    def caption(self, text):
        self.caption_text = text
        self._record("caption", text=text)
        return self

    def playback(self, myr_per_second):
        """How fast the play button runs. Defaults to crossing the whole range in
        about 40 seconds, so a long record does not take minutes to watch once."""
        self.play_rate = float(myr_per_second)
        self._record("playback", myr_per_second=myr_per_second)
        return self

    # -- output -------------------------------------------------------------

    def show(self, **kwargs):
        from .preview import show
        return show(self, **kwargs)

    def export(self, out_dir, **kwargs):
        from .artifact import export
        return export(self, out_dir, **kwargs)

    # -- internals ----------------------------------------------------------

    @property
    def times(self):
        return (self.start, self.end, self.step)

    @property
    def model_credit(self):
        return MODEL_CREDITS.get(self.reconstruction, self.reconstruction)

    def default_play_rate(self):
        if self.play_rate is not None:
            return self.play_rate
        return max(1.0, round((self.end - self.start) / 40.0))


def _hover_spec(hover, columns, age_column=None):
    """Normalise the accepted `hover` forms into recipe rows.

    `age_column` is remapped to the payload's own `age` key rather than to a key
    derived from its name: the exporter always writes the age there, whatever the
    caller's column was called.
    """
    rows = []
    used = []

    def key_of(column):
        return "age" if column == age_column else payload_key(column)

    def add(column, meta):
        if column not in columns:
            raise KeyError("no column {!r} for hover".format(column))
        used.append(column)
        row = {"key": key_of(column),
               "label": meta.get("label") or _auto_label(column)}
        for name in ("unit", "errorUnit"):
            if meta.get(name):
                row[name] = meta[name]
        if meta.get("numeric") is False:
            row["numeric"] = False
        if meta.get("error"):
            if meta["error"] not in columns:
                raise KeyError("no column {!r} for hover error".format(meta["error"]))
            used.append(meta["error"])
            row["error"] = key_of(meta["error"])
        rows.append(row)

    if isinstance(hover, dict):
        for column, meta in hover.items():
            add(column, meta if isinstance(meta, dict) else {"label": meta})
    else:
        for column in hover or ():
            add(column, {})

    return rows, used
