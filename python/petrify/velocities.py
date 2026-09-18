"""
Plate velocity fields on a HEALPix domain.

HEALPix, not a lon/lat grid: an equal-angle grid bunches points towards the poles, which
on a globe means a dense smear over the pole and a sparse equator. HEALPix cells are
equal-area, so the arrows come out evenly spaced on the sphere.

Velocity is a stage rotation evaluated at a fixed point, so the domain points are the
same lon/lat at every timestep -- they are a sampling grid, not something being
reconstructed. That is why the coordinates are written once in the file header and only
the components vary per frame, which is what lets a whole 250 Myr series fit in a couple
of megabytes.

Units are km/Myr, numerically equal to mm/yr. Divide by 10 for cm/yr.
"""

from gprm.GPlatesReconstructionModel import PointDistributionOnSphere

UNITS = "km/Myr (numerically equal to mm/yr; divide by 10 for cm/yr)"

DOMAIN_NOTE = (
    "Domain points are a fixed HEALPix sampling grid, identical at every time -- they "
    "are not reconstructed. Each frame holds east and north components at those "
    "points. Zero means the point fell outside every resolved topology."
)


def healpix_domain(n):
    """A HEALPix point distribution with 12*n^2 points."""
    return PointDistributionOnSphere(distribution_type="healpix", N=n)


def build_velocities(snapshot, domain_feature, delta_time=1.0, decimals=1):
    """(east, north) velocity in km/Myr at each domain point.

    Points falling outside every resolved topology come back as (0, 0); the renderer
    skips them rather than drawing a zero-length arrow.
    """
    field = snapshot.velocity_field(
        velocity_domain_features=[domain_feature],
        delta_time=delta_time)
    return (
        [round(v, decimals) for v in field.velocity_east],
        [round(v, decimals) for v in field.velocity_north],
    )


def velocity_payload(domain, frames, model_name, healpix_n, delta_time):
    """Assemble the single-file velocity series."""
    return {
        "model": model_name,
        "units": UNITS,
        "delta_time": delta_time,
        "healpix_n": healpix_n,
        "note": DOMAIN_NOTE,
        "lon": [round(v, 4) for v in domain.longitude.tolist()],
        "lat": [round(v, 4) for v in domain.latitude.tolist()],
        "frames": frames,
    }
