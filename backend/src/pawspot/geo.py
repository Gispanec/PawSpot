import math
from dataclasses import dataclass

GEO_POLICY_VERSION = 1


@dataclass(frozen=True)
class PublicLocation:
    latitude: float
    longitude: float
    cell_key: str
    policy_version: int = GEO_POLICY_VERSION


def public_location(latitude: float, longitude: float, radius_m: int) -> PublicLocation:
    """Квантует точку в фиксированную глобальную сетку политики v1."""
    if not math.isfinite(latitude) or not -90 <= latitude <= 90:
        raise ValueError("latitude must be finite and between -90 and 90")
    if not math.isfinite(longitude) or not -180 <= longitude <= 180:
        raise ValueError("longitude must be finite and between -180 and 180")
    if not 50 <= radius_m <= 2000:
        raise ValueError("radius_m must be between 50 and 2000")

    # -180 и +180 обозначают один меридиан и должны попадать в одну ячейку.
    if longitude == 180:
        longitude = -180
    step = math.sqrt(2) * radius_m / 112_000
    row = min(math.floor((latitude + 90) / step), math.ceil(180 / step) - 1)
    col = min(math.floor((longitude + 180) / step), math.ceil(360 / step) - 1)
    center_lat = min(90.0, -90 + (row + 0.5) * step)
    center_lon = min(180.0, -180 + (col + 0.5) * step)
    return PublicLocation(
        latitude=center_lat,
        longitude=center_lon,
        cell_key=f"v1:{radius_m}:{row}:{col}",
    )


def point_wkt(latitude: float, longitude: float) -> str:
    if not math.isfinite(latitude) or not -90 <= latitude <= 90:
        raise ValueError("invalid latitude")
    if not math.isfinite(longitude) or not -180 <= longitude <= 180:
        raise ValueError("invalid longitude")
    return f"SRID=4326;POINT({longitude} {latitude})"
