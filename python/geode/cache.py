"""
The heavy/light seam.

Turning a DataFrame into `points.json` needs plate assignment against static
polygons, which needs pygplates and gprm. Reconstructing boundaries at 1 Myr steps
over a billion years needs the same stack and the better part of an hour. If either
of those ran every time someone re-evaluated a notebook cell, the "view" half of the
notebook would not be a view half at all, and the "fork this viewer and change its
colours" story would be false.

So every export is content-addressed on the parameters that determine it, and lands
in a cache directory. The key deliberately includes everything the output depends on
and nothing else -- change the colour of a symbol and you hit the cache; change the
reconstruction model and you do not, because you cannot.

That draws the honest boundary. Someone who re-runs the view block against an
exported artifact can change colours, grouping, sizes, hover fields and Theme
without the scientific stack installed. They cannot change the model or extend the
time range, which is correct: those are data decisions, not display ones.
"""

import hashlib
import json
import os
import shutil


def cache_root():
    """Where cached exports live. `GEODE_CACHE` overrides, for CI and for tests."""
    return os.environ.get(
        "GEODE_CACHE",
        os.path.join(os.path.expanduser("~"), ".cache", "geode"))


def key_for(kind, params):
    """A stable hash of an export's inputs.

    `json.dumps(sort_keys=True)` rather than `hash()`: the key has to survive across
    processes, and Python's string hash is salted per-run by default.
    """
    blob = json.dumps({"kind": kind, **params}, sort_keys=True, default=str)
    digest = hashlib.sha256(blob.encode()).hexdigest()[:16]
    return "{}-{}".format(kind, digest)


class Cache:
    """A directory of completed exports, one subdirectory per key.

    Completion is marked by a `.done` file written last. A run interrupted halfway
    leaves the directory without one and is treated as a miss, so a half-written
    1001-frame export is never served as if it were whole.
    """

    def __init__(self, root=None):
        self.root = root or cache_root()

    def path(self, key):
        return os.path.join(self.root, key)

    def hit(self, key):
        return os.path.exists(os.path.join(self.path(key), ".done"))

    def begin(self, key):
        """Clear and create the directory for a fresh build. Returns its path."""
        target = self.path(key)
        if os.path.exists(target):
            shutil.rmtree(target)
        os.makedirs(target)
        return target

    def finish(self, key, manifest):
        """Mark a build complete, recording what it was so the cache is inspectable."""
        with open(os.path.join(self.path(key), ".done"), "w") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True, default=str)

    def manifest(self, key):
        with open(os.path.join(self.path(key), ".done")) as fh:
            return json.load(fh)

    def size(self, key):
        total = 0
        for dirpath, _, names in os.walk(self.path(key)):
            for name in names:
                total += os.path.getsize(os.path.join(dirpath, name))
        return total

    def adopt(self, key, source_dir, manifest):
        """Register an existing directory of exported files as a cache entry.

        For data that was produced by an identical earlier export outside this API --
        the same model, range, step and tolerances, just run from a hand-written
        script. Re-deriving it would take an hour and produce the same bytes.

        Deliberately a copy rather than a symlink: a cache entry that can be
        invalidated by someone tidying up an unrelated directory is worse than no
        cache. `manifest` should record where it came from, so the provenance of a
        cache hit is never a mystery.
        """
        target = self.begin(key)
        for name in sorted(os.listdir(source_dir)):
            src = os.path.join(source_dir, name)
            dst = os.path.join(target, name)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        self.finish(key, manifest)
        return target
