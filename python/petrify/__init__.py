"""
petrify: export plate reconstructions as web-ready JSON.

    from petrify import export_series
    export_series(model_name="Merdith2021", start=0, end=250, out_dir="data")

or from the command line:

    python -m petrify.export --model Merdith2021 --end 250 --out data
    python -m petrify.verify --data data --time 100
    python -m petrify.timeseries --data data
"""

from .aggregate import (
    EqualAreaGrid,
    build_aggregates,
    build_latitude,
    equal_degree_bands,
    verify_equal_area,
)
from .boundaries import build_frame
from .export import export_points, export_polygons, export_series, load_model
from .points import build_points, points_from_dataframe, rotation_block
from .polygons import build_polygons
from .timeseries import boundary_length_series, by_boundary_type, length_series
from .velocities import build_velocities, healpix_domain
from .verify import verify

__all__ = [
    "EqualAreaGrid",
    "boundary_length_series",
    "build_aggregates",
    "build_latitude",
    "build_frame",
    "build_points",
    "build_velocities",
    "build_polygons",
    "by_boundary_type",
    "equal_degree_bands",
    "export_points",
    "export_polygons",
    "export_series",
    "healpix_domain",
    "length_series",
    "load_model",
    "points_from_dataframe",
    "rotation_block",
    "verify",
    "verify_equal_area",
]

__version__ = "0.13.0"
