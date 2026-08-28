"""
deep-time-map: export plate reconstructions as web-ready JSON.

    from deep_time_map import export_series
    export_series(model_name="Merdith2021", start=0, end=250, out_dir="data")

or from the command line:

    python -m deep_time_map.export --model Merdith2021 --end 250 --out data
    python -m deep_time_map.verify --data data --time 100
"""

from .boundaries import build_frame
from .export import export_points, export_series, load_model
from .points import build_points, points_from_dataframe
from .velocities import build_velocities, healpix_domain
from .verify import verify

__all__ = [
    "build_frame",
    "build_points",
    "build_velocities",
    "export_points",
    "export_series",
    "healpix_domain",
    "load_model",
    "points_from_dataframe",
    "verify",
]

__version__ = "0.1.0"
