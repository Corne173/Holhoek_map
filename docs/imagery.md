# Preparing satellite imagery for offline use

The supplied KMZ contains routes and waypoints, not satellite imagery. The offline
map can include a small, embedded satellite image once one has been retrieved.
The included crop was acquired on **20 May 2026**, from scene
`S2B_35JNJ_20260520_0_L2A`. It is a historical image, not a live satellite feed.

## Recommended open source

Copernicus Sentinel-2 Level 2A provides true-colour imagery at **10 metres per
pixel**. This is suitable for landscape context: ridges, fields, dams and larger
vegetation patterns. It will not reliably reveal a hiking path, small junction,
or individual obstacle. Enlarging the image does not add detail. Higher-resolution
aerial imagery can replace it if its terms permit offline redistribution.

The [AWS Open Data registry](https://registry.opendata.aws/sentinel-2-l2a-cogs/)
declares Sentinel data access free, full and open, and identifies the public
`sentinel-cogs` bucket as not requiring requester-pays authentication. Its
[source metadata](https://github.com/awslabs/open-data-registry/blob/main/datasets/sentinel-2-l2a-cogs.yaml)
and [Element 84 collection documentation](https://github.com/Element84/earth-search/blob/main/docs/collections/sentinel-2-l2a.md)
describe the dataset. Preserve the attribution and the
[Copernicus Sentinel legal notice](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice)
when sharing modified imagery. No Google or commercial map tiles are downloaded
by this helper.

## Fetch a small crop

Run from the repository root with Python 3.10 or newer:

```sh
python -m venv .venv-imagery
.venv-imagery/bin/python -m pip install -r requirements-imagery.txt
.venv-imagery/bin/python scripts/fetch_imagery.py
```

The helper uses a fixed default date range, 1 January 2019 through 8 October 2026,
and a padded rectangle covering the supplied Holhoek routes. It searches up to
100 low-cloud scenes and inspects at most eight fully covering candidates. It
uses Sentinel's scene classification to reject any locally classified clouds,
shadows, snow, unclassified pixels, or missing coverage. A low scene-wide cloud
percentage alone does not establish that Holhoek is clear. Classification is
imperfect: **visually inspect the result before distributing the map**.

The script reads only overlapping cloud-optimized GeoTIFF blocks, then reprojects
the crop to a north-up Web Mercator rectangle, 1024 pixels wide. No account,
credential, or API key is required. TLS verification stays enabled. Network
errors stop the operation without repeatedly retrying blocked requests.

Successful output:

- `assets/satellite.jpg`: the complete cropped RGB image.
- `assets/satellite.json`: geographic bounds in `[[south, west], [north, east]]`
  order, acquisition timestamp, scene ID, attribution, source URL, native
  resolution, image dimensions, projection, and image checksum.

Both outputs are prepared before the final files are installed. Existing assets
are preserved unless `--replace` is supplied. The map build can embed these two
files; hikers then need no network connection to see the satellite layer.

For reproducible selection, use the scene ID printed by a successful fetch:

```sh
.venv-imagery/bin/python scripts/fetch_imagery.py --scene-id SCENE_ID --replace
```

For the included image, replace `SCENE_ID` with
`S2B_35JNJ_20260520_0_L2A` and use the default bounding box.

Use `--after YYYY-MM-DD`, `--before YYYY-MM-DD`, `--max-scenes 16`, or
`--bbox WEST SOUTH EAST NORTH` to change the search. Keep the same bounds when
reproducing a crop. An exact scene ID overrides the search date/cloud filter.
The helper intentionally limits crops to a small area and accepts assets only
from the documented public Sentinel COG bucket.

## Network availability and validation status

The helper needs these two hosts during preparation:

```text
earth-search.aws.element84.com
sentinel-cogs.s3.us-west-2.amazonaws.com
```

Initial requests received `HTTP/1.1 403 Forbidden` during proxy CONNECT. A later
request succeeded in this session. If this error
returns in a future environment, add both hosts to its domain allowlist. No
credentials are required and none should be supplied in chat.

Validation completed on 8 October 2026:

- Actual STAC lookup, remote COG reads, and JPEG generation succeeded. The first
  candidate was rejected for locally classified cloud/shadow pixels; the second
  was rejected for incomplete coverage; the third supplied the included image.
- The completed 1024 × 1071 image was visually inspected for obvious cloud or
  missing-data regions.
- Synthetic UTM rasters verified reprojection into the exact Web Mercator
  rectangle, RGB preservation, geographic metadata, and output checksums.
- Synthetic cloud and partial-coverage cases were rejected.
