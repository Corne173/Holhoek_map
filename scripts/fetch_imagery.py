#!/usr/bin/env python3
"""Download a small, attributed Sentinel-2 crop for the offline Holhoek map.

Optional preparation step; the distributed HTML needs no Python or network.
See docs/imagery.md for data rights, limitations, and installation instructions.
"""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
API = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-l2a"
ASSET_HOST = "sentinel-cogs.s3.us-west-2.amazonaws.com"
# Padding around both supplied routes. west, south, east, north (WGS84).
DEFAULT_BOUNDS = (27.995, -28.457, 28.070, -28.388)


class UnsuitableScene(Exception):
    """A valid scene does not provide clear, complete local coverage."""


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene-id", help="Use this exact STAC scene ID for reproducibility")
    parser.add_argument("--after", default="2019-01-01", type=date.fromisoformat)
    parser.add_argument("--before", default="2026-10-08", type=date.fromisoformat)
    parser.add_argument("--bbox", nargs=4, type=float, default=DEFAULT_BOUNDS,
                        metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    parser.add_argument("--max-scenes", type=int, default=8,
                        help="Maximum candidate crops to inspect (default: 8)")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "assets")
    parser.add_argument("--replace", action="store_true", help="Replace an existing satellite image and metadata")
    args = parser.parse_args()
    west, south, east, north = args.bbox
    if not all(math.isfinite(v) for v in args.bbox):
        parser.error("Bounding coordinates must be finite")
    if not (-180 <= west < east <= 180 and -85 < south < north < 85):
        parser.error("Expected valid west < east and south < north coordinates")
    if east - west > 0.25 or north - south > 0.25:
        parser.error("This helper supports small crops only (at most 0.25 degrees per side)")
    if args.after > args.before or not 1 <= args.max_scenes <= 100:
        parser.error("Check date order and use --max-scenes between 1 and 100")
    return args


def search(args):
    query = {
        "collections": COLLECTION,
        "bbox": ",".join(map(str, args.bbox)),
        "limit": "100",
        "sortby": "+properties.eo:cloud_cover",
    }
    if args.scene_id:
        query["ids"] = args.scene_id
    else:
        query["datetime"] = f"{args.after}T00:00:00Z/{args.before}T23:59:59Z"
        query["query"] = json.dumps({"eo:cloud_cover": {"lt": 10}})
    request = Request(API + "?" + urlencode(query), headers={"User-Agent": "Holhoek-offline-map/1.0"})
    with urlopen(request, timeout=30) as response:
        payload = response.read(16 * 1024 * 1024 + 1)
    if len(payload) > 16 * 1024 * 1024:
        raise RuntimeError("STAC response exceeded the expected size")
    result = json.loads(payload)
    if not isinstance(result.get("features"), list):
        raise RuntimeError("STAC response has no feature list")
    return result["features"]


def asset_url(item, key):
    href = item.get("assets", {}).get(key, {}).get("href", "")
    parsed = urlparse(href)
    if parsed.scheme != "https" or parsed.netloc != ASSET_HOST:
        raise UnsuitableScene(f"No public {key!r} COG in the documented Sentinel bucket")
    return href


def target_grid(bounds, width=1024):
    from rasterio.transform import from_bounds
    from rasterio.warp import transform_bounds

    mercator = transform_bounds("EPSG:4326", "EPSG:3857", *bounds)
    west, south, east, north = mercator
    height = max(1, round(width * (north - south) / (east - west)))
    if height > 4096:
        raise ValueError("Crop aspect ratio is too tall")
    return from_bounds(*mercator, width, height), width, height


def read_crop(source, bounds, target_transform, width, height, bands, resampling):
    """Read overlapping COG blocks, then warp that window to exact map bounds."""
    import numpy as np
    from rasterio.warp import reproject, transform_bounds
    from rasterio.windows import Window, from_bounds
    from rasterio.enums import Resampling

    source_bounds = transform_bounds("EPSG:4326", source.crs, *bounds, densify_pts=21)
    float_window = from_bounds(*source_bounds, transform=source.transform)
    left, top = math.floor(float_window.col_off), math.floor(float_window.row_off)
    right = math.ceil(float_window.col_off + float_window.width)
    bottom = math.ceil(float_window.row_off + float_window.height)
    window = Window(left, top, right - left, bottom - top)
    if window.width * window.height > 16_000_000:
        raise ValueError("Source window is unexpectedly large; refusing full-scene download")
    data = source.read(bands, window=window, boundless=True, fill_value=0)
    mask = source.dataset_mask(window=window, boundless=True)
    output = np.zeros((len(bands), height, width), dtype=data.dtype)
    coverage = np.zeros((height, width), dtype="uint8")
    common = dict(src_transform=source.window_transform(window), src_crs=source.crs,
                  dst_transform=target_transform, dst_crs="EPSG:3857", dst_nodata=0)
    reproject(mask, coverage, resampling=Resampling.nearest, **common)
    if not np.all(coverage):
        raise UnsuitableScene("Scene does not completely cover the requested crop")
    for index in range(len(bands)):
        reproject(data[index], output[index], resampling=resampling, **common)
    return output


def crop_scene(item, bounds):
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    from PIL import Image

    visual, classification = asset_url(item, "visual"), asset_url(item, "scl")
    transform, width, height = target_grid(bounds)
    # HTTPS verification remains enabled. No credentials, S3 listing, or full downloads.
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_TIMEOUT="30",
                      GDAL_HTTP_MAX_RETRY="0", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        with rasterio.open("/vsicurl/" + classification) as source:
            classes = read_crop(source, bounds, transform, width, height, [1], Resampling.nearest)[0]
        # SCL: nodata, defective, cloud shadow, unclassified, cloud, cirrus, snow.
        unsuitable = np.isin(classes, [0, 1, 3, 7, 8, 9, 10, 11])
        if np.any(unsuitable):
            raise UnsuitableScene(f"Local crop includes {100 * unsuitable.mean():.2f}% cloud/shadow/snow/unclassified pixels")
        with rasterio.open("/vsicurl/" + visual) as source:
            if source.count < 3 or any(dtype != "uint8" for dtype in source.dtypes[:3]):
                raise UnsuitableScene("Expected an 8-bit three-band true-colour visual asset")
            rgb = read_crop(source, bounds, transform, width, height, [1, 2, 3], Resampling.bilinear)
    image = Image.fromarray(np.moveaxis(rgb, 0, 2))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90, optimize=True)
    acquired = item["properties"]["datetime"]
    west, south, east, north = bounds
    metadata = {
        "bounds": [[south, west], [north, east]],
        "attribution": f"Contains modified Copernicus Sentinel data {acquired[:4]}. Source: ESA / Copernicus; COGs: Element 84 / AWS Open Data.",
        "acquired": acquired,
        "sceneId": item["id"],
        "resolutionMeters": 10,
        "sourceUrl": visual,
        "projection": "EPSG:3857",
        "width": width,
        "height": height,
        "imageSha256": hashlib.sha256(buffer.getvalue()).hexdigest(),
        "licenseUrl": "https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice",
    }
    return buffer.getvalue(), metadata


def save_crop(directory, image, metadata):
    # Download/validate everything before writing either final artifact.
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".satellite-", dir=directory) as staging:
        staging = Path(staging)
        (staging / "satellite.jpg").write_bytes(image)
        (staging / "satellite.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        (staging / "satellite.jpg").replace(directory / "satellite.jpg")
        (staging / "satellite.json").replace(directory / "satellite.json")


def main():
    args = arguments()
    if not args.replace and any((args.output_dir / name).exists() for name in ("satellite.jpg", "satellite.json")):
        raise RuntimeError("Existing satellite assets preserved. Use --replace to regenerate them.")
    for package in ("rasterio", "PIL", "numpy"):
        try:
            __import__(package)
        except ImportError as error:
            raise RuntimeError("Install optional dependencies: python -m pip install -r requirements-imagery.txt") from error
    items = search(args)
    if not items:
        raise RuntimeError("No matching scenes found. Check the dates, bounding box, or scene ID.")
    attempts = 0
    west, south, east, north = args.bbox
    for item in items:
        scene_bounds = item.get("bbox", [])
        if len(scene_bounds) != 4 or not (scene_bounds[0] <= west and scene_bounds[1] <= south
                                        and scene_bounds[2] >= east and scene_bounds[3] >= north):
            continue
        attempts += 1
        print(f"Inspecting {item['id']} ({attempts}/{args.max_scenes})", file=sys.stderr)
        try:
            image, metadata = crop_scene(item, args.bbox)
        except UnsuitableScene as error:
            print(f"Skipped: {error}", file=sys.stderr)
        else:
            save_crop(args.output_dir, image, metadata)
            print(f"Saved {args.output_dir / 'satellite.jpg'} and satellite.json")
            print(f"Reproduce with --scene-id {metadata['sceneId']} (and the same --bbox)")
            print("Visually inspect the crop before distributing it; automated cloud classification is imperfect.")
            return
        if attempts >= args.max_scenes:
            break
    raise RuntimeError("No fully covering, locally clear scene was found in the inspected candidates. Try another date range or a specific --scene-id. No imagery was written.")


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError) as error:
        sys.exit(f"Satellite retrieval blocked or unavailable: {error}. Required hosts: earth-search.aws.element84.com and {ASSET_HOST}. No new imagery was written.")
    except Exception as error:
        sys.exit(f"Satellite preparation failed: {error}")
