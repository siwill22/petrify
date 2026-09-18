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
                 start_time=None, title=None, subtitle=None, ocean=True):
        start, end, step = times
        self.reconstruction = reconstruction
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
        self.title = title or "{} reconstruction".format(reconstruction)
        self.subtitle = subtitle
        self.theme_id = DEFAULT_THEME
        self.caption_text = None
        self.play_rate = None

        self._layers = []
        self._charts = []
        self._log = []
        self._libraries = []
        self._notebook = None
        self._notebook_requirements = None

        self._record("globe", reconstruction=reconstruction,
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

    def notebook(self, path, requirements=None):
        """Bundle the author's own notebook or script beside the View Script.

        `requirements` is optional plain-language setup steps for reproducing the
        analysis (a conda environment, a package that is not on PyPI, an expected
        runtime) -- shown in the provenance drawer under "Running the notebook
        yourself". Only the author knows what their own analysis needs, so this is
        opt-in data rather than something the host could guess at. Leaving it out
        means the drawer simply omits that section rather than showing a guess.
        """
        self._notebook = os.path.abspath(path)
        self._notebook_requirements = list(requirements) if requirements else None
        return self

    # -- the standard layers ------------------------------------------------

    def continents(self, which="continents", tolerance=0.02, alpha=0.62,
                   line_width=0.7):
        """Reconstructed continent polygons.

        `tolerance` is a simplification in degrees; the default is about one device
        pixel at 4x zoom on a globe of this size, so the saving is invisible.
        """
        self._layers.append({"kind": "continents", "which": which,
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
               colours=None, lifespan="since", highlight=None, size=3.4,
               keyline_alpha=0.55, hover=(), label=None, footer=None,
               style_js=None, caption=None, source=None, doi=None,
               legend_title=None, name=None):
        """Put a DataFrame of located, dated things on the globe.

        `age`, `lon`, `lat`, `group` and everything in `hover` are COLUMN NAMES. The
        payload keys they become are an implementation detail; nothing a caller
        writes ever refers to them.

        `hover` may be a list of columns (labels derived from the names) or a dict
        of column -> label, or column -> {label, unit, error, errorUnit} where a
        measurement and its uncertainty belong on one line.

        `style_js` is the escape hatch, and a supported path rather than a failure:
        a JS module whose default export is `(point, category, api) => {fill, ...}`.
        Pages whose symbols are genuinely bespoke -- pie glyphs, say -- belong there
        rather than in a Display Rule nobody else could use.
        """
        columns = set(df.columns)

        def need(col, what):
            if col not in columns:
                raise KeyError("no column {!r} for {} (have: {})".format(
                    col, what, ", ".join(sorted(columns)[:12])))

        if age is not None:
            need(age, "age")
        lon = lon or _find_column(columns, LON_NAMES, "longitude")
        lat = lat or _find_column(columns, LAT_NAMES, "latitude")
        need(lon, "longitude")
        need(lat, "latitude")
        if group is not None:
            need(group, "group")

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
        # rename is internal; the caller's frame is untouched.
        prepared = df.rename(columns={lon: "Longitude", lat: "Latitude"})
        if age is not None:
            prepared = prepared.rename(columns={age: "Age"})

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
            "_meta": {k: v for k, v in
                      {"source": source, "doi": doi, "caption": caption}.items()
                      if v},
        }
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
                     lon=_unless(lon, _find_column(columns, LON_NAMES, None)),
                     lat=_unless(lat, _find_column(columns, LAT_NAMES, None)),
                     group=group, labels=labels, colours=colours,
                     lifespan=None if lifespan == "since" else lifespan,
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
