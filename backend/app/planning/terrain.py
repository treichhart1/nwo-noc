"""Offline raster adapters. No GDAL virtual/network paths or whole-raster reads."""

from collections import OrderedDict
from pathlib import Path
import hashlib, math, os
import numpy as np
import rasterio
from rasterio.windows import Window
from rasterio.warp import transform_bounds
from pyproj import CRS, Transformer

FACTORS = {"metres": 1.0, "international_feet": 0.3048, "us_survey_feet": 1200 / 3937}
MAX_PIXELS = 100_000_000
MAX_TILES = 20000
ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_CACHEMAX": 16,
    "PROJ_NETWORK": "OFF",
    "GDAL_NUM_THREADS": "1",
}
os.environ.setdefault("PROJ_NETWORK", "OFF")


def root():
    from app.core.config import settings

    p = Path(settings.planning_storage_root)
    if p.is_symlink():
        raise ValueError("Unsafe terrain storage")
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    return p.resolve()


def local(key):
    if (
        not key
        or Path(key).name != key
        or not all(c.isalnum() or c in "-." for c in key)
    ):
        raise ValueError("Invalid private terrain key")
    p = root() / key
    if p.is_symlink() or not p.is_file():
        raise ValueError("Terrain file unavailable")
    return p


def checksum(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def crs_check(crs):
    c = CRS.from_user_input(crs)
    if not (c.is_projected or c.is_geographic) or len(c.axis_info) < 2:
        raise ValueError("Horizontal CRS required")
    return c


def validate_raster(path, expected):
    with rasterio.Env(**ENV), rasterio.open(path, driver="GTiff") as src:
        if (
            src.driver != "GTiff"
            or src.count != 1
            or src.width < 2
            or src.height < 2
            or src.width * src.height > MAX_PIXELS
        ):
            raise ValueError("Single-band bounded GeoTIFF required")
        crs = crs_check(src.crs)
        if src.dtypes[0] not in {
            "float32",
            "float64",
            "int16",
            "int32",
            "uint16",
            "uint8",
        }:
            raise ValueError("Unsupported raster elevation type")
        t = src.transform
        if abs(t.b) > 1e-10 or abs(t.d) > 1e-10 or t.a <= 0 or t.e >= 0:
            raise ValueError("North-up georeferenced raster required")
        b = list(transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21))
        if not all(math.isfinite(x) for x in b) or not (
            -180 <= b[0] < b[2] <= 180 and -85 <= b[1] < b[3] <= 85
        ):
            raise ValueError("Invalid transformed terrain bounds")
        tolerance = 0.0001
        if any(abs(a - z) > tolerance for a, z in zip(b, expected)):
            raise ValueError("Terrain bounds do not match declared coverage")
        tiles = []
        for row in range(0, src.height, 512):
            for col in range(0, src.width, 512):
                if len(tiles) >= MAX_TILES:
                    raise ValueError("Terrain tile index limit exceeded")
                win = Window(
                    col, row, min(512, src.width - col), min(512, src.height - row)
                )
                tb = transform_bounds(
                    src.crs,
                    "EPSG:4326",
                    *rasterio.windows.bounds(win, t),
                    densify_pts=5,
                )
                tiles.append(
                    {
                        "column": col // 512,
                        "row": row // 512,
                        "west": tb[0],
                        "south": tb[1],
                        "east": tb[2],
                        "north": tb[3],
                    }
                )
        return {
            "crs": crs.to_string(),
            "width": src.width,
            "height": src.height,
            "pixel_resolution_native": list(src.res),
            "horizontal_units": [a.unit_name for a in crs.axis_info[:2]],
            "bbox": b,
            "nodata": str(src.nodata),
            "tiles": tiles,
            "sampling": "nearest source pixel; nodata stays missing",
            "source_tags": {k: str(v)[:500] for k, v in list(src.tags().items())[:20]},
        }


def las_to_raster(path, output, kind, expected):
    """Bounded classified-point binning, not interpolation or a vegetation classifier."""
    import laspy

    with laspy.open(path) as src:
        h = src.header
        if h.point_count > 2_000_000:
            raise ValueError("LAS/LAZ limit is two million points; pre-tile offline")
        crs = crs_check(h.parse_crs())
        if not crs.is_projected:
            raise ValueError("LAS processing requires projected horizontal CRS")
        bounds = transform_bounds(
            crs, "EPSG:4326", h.mins[0], h.mins[1], h.maxs[0], h.maxs[1], densify_pts=21
        )
        if any(abs(a - z) > 0.0001 for a, z in zip(bounds, expected)):
            raise ValueError("LAS bounding box mismatch")
        # Explicit one-native-metre cells; horizontal axis must be metre based.
        if any(abs(a.unit_conversion_factor - 1) > 1e-9 for a in crs.axis_info[:2]):
            raise ValueError("LAS adapter requires metre horizontal coordinates")
        w = max(2, int(math.ceil(h.maxs[0] - h.mins[0])) + 1)
        ht = max(2, int(math.ceil(h.maxs[1] - h.mins[1])) + 1)
        if w * ht > 1_000_000:
            raise ValueError("LAS rasterization cell budget exceeded; pre-tile offline")
        values = np.full(
            (ht, w), np.inf if kind == "ground" else -np.inf, dtype=np.float32
        )
        accepted = 0
        for points in src.chunk_iterator(50000):
            mask = (
                np.asarray(points.classification) == 2
                if kind == "ground"
                else ~np.isin(np.asarray(points.classification), [7, 18])
            )
            x = np.asarray(points.x)[mask]
            y = np.asarray(points.y)[mask]
            z = np.asarray(points.z)[mask]
            good = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
            x = x[good]
            y = y[good]
            z = z[good]
            col = np.clip((x - h.mins[0]).astype(int), 0, w - 1)
            row = np.clip((h.maxs[1] - y).astype(int), 0, ht - 1)
            (np.minimum.at if kind == "ground" else np.maximum.at)(
                values, (row, col), z
            )
            accepted += len(z)
        if not accepted:
            raise ValueError("No usable classified ground/surface points")
        values[~np.isfinite(values)] = -9999
        transform = rasterio.transform.from_origin(h.mins[0], h.maxs[1], 1, 1)
        with rasterio.open(
            output,
            "w",
            driver="GTiff",
            width=w,
            height=ht,
            count=1,
            dtype="float32",
            crs=crs,
            transform=transform,
            nodata=-9999,
            compress="deflate",
            tiled=True,
        ) as dst:
            dst.write(values, 1)
        return {
            "las_version": str(h.version),
            "point_format": h.point_format.id,
            "point_count": h.point_count,
            "accepted_points": accepted,
            "method": "1m classified-ground minimum / non-noise surface maximum; empty cells remain nodata",
            "source_bounds_wgs84": list(bounds),
        }


class Sampler:
    def __init__(self, datasets):
        self.sources = []
        self.cache = OrderedDict()
        self.env = rasterio.Env(**ENV)
        self.env.__enter__()
        try:
            for d in datasets:
                path = local(d["raster_key"])
                if checksum(path) != d["raster_sha256"]:
                    raise ValueError("Terrain checksum mismatch")
                src = rasterio.open(path, driver="GTiff")
                self.sources.append(
                    (d, src, Transformer.from_crs("EPSG:4326", src.crs, always_xy=True))
                )
        except Exception:
            self.close()
            raise

    def close(self):
        for _, src, _ in self.sources:
            src.close()
        self.sources = []
        self.env.__exit__(None, None, None)

    def value(self, kind, lon, lat):
        for d, src, tr in self.sources:
            if d["kind"] != kind:
                continue
            x, y = tr.transform(lon, lat)
            row, col = src.index(x, y)
            if not (0 <= row < src.height and 0 <= col < src.width):
                continue
            key = (d["id"], row // 128, col // 128)
            if key not in self.cache:
                self.cache[key] = src.read(
                    1,
                    window=Window(
                        col // 128 * 128,
                        row // 128 * 128,
                        min(128, src.width - col // 128 * 128),
                        min(128, src.height - row // 128 * 128),
                    ),
                    masked=True,
                )
                if len(self.cache) > 64:
                    self.cache.popitem(last=False)
            block = self.cache[key]
            self.cache.move_to_end(key)
            v = block[row % 128, col % 128]
            if np.ma.is_masked(v) or not math.isfinite(float(v)):
                continue
            v = float(v) * FACTORS[d["units"]]
            if not -500 <= v <= 10000:
                continue
            return v, d["id"]
        return None, None
