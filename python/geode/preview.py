"""
`View.show()` -- the inline preview.

A full export is large (tens of MB for a 1 Myr series over a billion years), so
`show()` and `export()` cannot be the same artifact rendered two ways. They are
different artifacts, and the honest consequence is that **what you see is not
byte-identical to what you ship**.

So the preview says so, on the figure. That label is load-bearing, not decoration:
a coarse preview that looked like the final thing would send people to
export-and-refresh, which is the workflow this exists to replace.

What a preview reduces:

* the time step, to whatever gives about `frames` steps across the range -- enough
  to judge colour, grouping and symbol size, which is what iteration is for;
* the point count, by an even sample, which preserves the spatial and temporal
  distribution rather than taking the first N rows.

What it does NOT reduce: the layers, the Theme, the display rules or the controls.
A preview that dropped those would not be previewing the thing you are making.
"""

import os
import shutil

PREVIEW_DIR = "_geode_preview"


def show(view, frames=40, max_points=4000, height=560, directory=None):
    """Render a coarse preview of this view inline in a notebook.

    Returns an IPython IFrame when one is available, and otherwise the path it
    wrote -- so this is still useful from a plain script.
    """
    import copy

    out_dir = os.path.abspath(directory or PREVIEW_DIR)
    coarse = _coarsen(view, frames=frames, max_points=max_points, copier=copy)

    from .artifact import export
    export(coarse, out_dir, quiet=True)
    _label(out_dir, coarse.step, len(coarse._layers))

    try:
        from IPython.display import IFrame
    except ImportError:
        print("preview written to {} (no IPython available to display it)"
              .format(out_dir))
        return out_dir

    # A relative src, so the notebook server serves it; an absolute file:// path
    # would be blocked by the same-origin policy the moment the page fetched
    # view.json.
    rel = os.path.relpath(out_dir, os.getcwd())
    return IFrame(src="{}/index.html".format(rel), width="100%", height=height)


def _coarsen(view, frames, max_points, copier):
    """A shallow clone of the view with a bigger step and fewer points."""
    coarse = copier.copy(view)
    span = view.end - view.start
    coarse.step = max(view.step, int(round(span / max(frames, 2))) or view.step)
    coarse.title = view.title
    coarse._charts = list(view._charts)
    coarse._libraries = list(view._libraries)
    # The preview is not the record; bundling a View Script for a thing nobody will
    # keep would put a misleading `export()` line in front of the reader.
    coarse._notebook = None

    layers = []
    for spec in view._layers:
        if spec["kind"] != "points":
            layers.append(spec)
            continue
        spec = dict(spec)
        frame = spec["_frame"]
        if len(frame) > max_points:
            # Every nth row rather than the head: a compilation is usually ordered
            # by source or by region, so the first N rows are one continent.
            stride = len(frame) // max_points + 1
            spec["_frame"] = frame.iloc[::stride]
        layers.append(spec)
    coarse._layers = layers
    return coarse


def _label(out_dir, step, _n_layers):
    """Stamp the preview so it can never be mistaken for the exported viewer."""
    index = os.path.join(out_dir, "index.html")
    with open(index) as fh:
        html = fh.read()

    banner = (
        '<div style="position:fixed;left:0;right:0;bottom:0;z-index:99;'
        'padding:.3rem .8rem;font:600 11px/1.4 ui-sans-serif,system-ui;'
        'letter-spacing:.1em;text-transform:uppercase;text-align:center;'
        'background:#c8a415;color:#1a1400">'
        'Preview &middot; {} Ma steps, sampled points &middot; '
        'export() ships the full record</div>'
    ).format(step)

    with open(index, "w") as fh:
        fh.write(html.replace("</body>", banner + "\n</body>"))


def clear(directory=None):
    """Remove a preview directory."""
    target = os.path.abspath(directory or PREVIEW_DIR)
    if os.path.exists(target):
        shutil.rmtree(target)
