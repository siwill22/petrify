"""
Resolve topological plate boundaries and export them as GeoJSON.

One file per timestep. Resolved topologies change discontinuously -- sub-segments appear,
vanish and split at triple junctions -- so there is no meaningful way to interpolate
between two frames, and no fixed geometry to rotate the way coastlines can be. Discrete
snapshots are the honest representation.

Subduction polarity is carried through as a property. Following gpml:subductionPolarity,
the value names the side of the line, in vertex order, on which the OVERRIDING plate
lies. The renderer draws the triangles on that side.
"""

import math

import pygplates

from gprm.utils.geometry import find_overriding_and_subducting_plates

# gpml feature type -> the class the renderer styles on. Everything not named here
# (InferredPaleoBoundary, FractureZone, TerraneBoundary, SlabEdge, ...) is 'other'.
BOUNDARY_TYPES = {
    "gpml:SubductionZone": "subduction",
    "gpml:MidOceanRidge": "ridge",
    "gpml:Transform": "transform",
}

POLARITY_CONVENTION = (
    "gpml:subductionPolarity. 'Left'/'Right' names the side of the line, in vertex "
    "order, on which the OVERRIDING plate lies. Triangles are drawn on that side."
)

ANTIMERIDIAN_NOTE = (
    "Lines are NOT split at +/-180 deg. The renderer works in 3D unit vectors where "
    "the dateline is not special; splitting would only add spurious breaks. Do not "
    "treat this as RFC 7946 GIS-ready 2D data."
)


def plate_id_of(resolved_topology):
    return resolved_topology.get_resolved_feature().get_reconstruction_plate_id()


def subduction_plates(shared_sub_segment, time):
    """(overriding_plate_id, subducting_plate_id), or (None, None) if undetermined.

    find_overriding_and_subducting_plates() needs exactly two sharing topologies and a
    known polarity. Where a trench is shared by only one topology -- model edges, or a
    boundary not fully closed at this time -- it returns None and reports why on stderr.
    That is informative, not fatal: the segment is still drawn, just without plate
    attribution.
    """
    result = find_overriding_and_subducting_plates(shared_sub_segment, time)
    if result is None:
        return None, None
    overriding, subducting, _polarity = result
    return plate_id_of(overriding), plate_id_of(subducting)


def linestring_coords(geometry, tessellate_degrees, decimals):
    """Tessellated [lon, lat] pairs for a PolylineOnSphere/PolygonOnSphere.

    Boundary vertices can be degrees apart, and the arc between them is a great circle.
    Densifying here, where pygplates can do it exactly, avoids the browser having to
    approximate it -- straight lon/lat interpolation would cut the corner, worst near
    the poles.
    """
    tessellated = geometry.to_tessellated(math.radians(tessellate_degrees))
    return [
        [round(lon, decimals), round(lat, decimals)]
        for lat, lon in tessellated.to_lat_lon_list()
    ]


def build_frame(snapshot, time, model_name, anchor_plate,
                tessellate_degrees=0.5, decimals=3):
    """GeoJSON FeatureCollection of every boundary sub-segment at one time.

    Returns (payload, counts, polarities).
    """
    features = []
    counts = {}
    polarities = {}

    for section in snapshot.resolved_topological_sections:
        for sub_segment in section.get_shared_sub_segments():
            feature = sub_segment.get_feature()
            feature_type = str(feature.get_feature_type())
            boundary_type = BOUNDARY_TYPES.get(feature_type, "other")

            geometry = sub_segment.get_resolved_geometry()
            if geometry is None or len(geometry.get_points()) < 2:
                continue

            polarity = None
            overriding_id = None
            subducting_id = None
            if boundary_type == "subduction":
                polarity = feature.get_enumeration(
                    pygplates.PropertyName.gpml_subduction_polarity)
                polarity = polarity or "Unknown"
                if polarity in ("Left", "Right"):
                    overriding_id, subducting_id = subduction_plates(sub_segment, time)
                polarities[polarity] = polarities.get(polarity, 0) + 1

            counts[boundary_type] = counts.get(boundary_type, 0) + 1

            features.append({
                "type": "Feature",
                "properties": {
                    "boundary_type": boundary_type,
                    "feature_type": feature_type,
                    "polarity": polarity,
                    "overriding_plate_id": overriding_id,
                    "subducting_plate_id": subducting_id,
                    "plate_ids": sorted(
                        plate_id_of(t)
                        for t in sub_segment.get_sharing_resolved_topologies()
                    ),
                    "name": feature.get_name(),
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": linestring_coords(
                        geometry, tessellate_degrees, decimals),
                },
            })

    payload = {
        "type": "FeatureCollection",
        "meta": {
            "model": model_name,
            "reconstruction_time_ma": time,
            "anchor_plate_id": anchor_plate,
            "pygplates_version": str(pygplates.Version.get_imported_version()),
            "tessellate_degrees": tessellate_degrees,
            "polarity_convention": POLARITY_CONVENTION,
            "antimeridian": ANTIMERIDIAN_NOTE,
        },
        "features": features,
    }
    return payload, counts, polarities
