# Holhoek offline trail guide

A mobile-friendly satellite map built from `Holhoek.kmz`. The shared map is a
single self-contained HTML file: imagery, trail geometry, waypoints, controls,
styles and the Leaflet library are embedded. It makes no runtime requests for
map tiles, scripts, fonts or waypoint data.

## Share the map

**`dist/Holhoek-offline.html`** is the downloadable guide (about 650 KB). Share
this file and have hikers download it before leaving reception. It opens in a
browser with zoom, pan, two trail toggles, searchable numbered waypoints, and
photo placeholders. Mobile file viewers vary; check it in the intended phone
browser before a hike.

For a hosted copy, deploy the contents of `dist/` to a static **HTTPS** host.
`index.html` and `sw.js` must stay beside one another; `Holhoek-offline.html`
provides the download link. On the device, choose **Save for offline** before
leaving reception. The app verifies that its complete current version was
cached. Reopening uses the cached map immediately, even with poor reception.
The explicit refresh action checks for a new worker/map. After a newer version
is saved, reopening the page loads it.

The included GitHub Actions workflow builds and publishes `dist/` after a push
to `main`. In this repository's **Settings → Pages**, the build source must be
**GitHub Actions**. Once deployment succeeds, the shared map is available at
`https://corne173.github.io/Holhoek_map/`. Hikers should open that page with
reception and choose **Save for offline**, then check it in airplane mode.

The service worker cache can be cleared by the browser or user. Keep the HTML
download as a backup and test the hosted page in airplane mode before hiking.
The first visit still requires reception; a link cannot download itself offline.

## Included data

- Day 1: approximately **10.7 km**, waypoint numbers 1–15.
- Day 2: approximately **6.6 km**, waypoint numbers 16–19.
- Optional water points, service roads and the separately labelled route.
- Lodge, main entrance, fossil and dinosaur-footprint markers.
- A **10 m/pixel Sentinel-2** image acquired **20 May 2026**, covering both
  trails and their surroundings. This gives landscape context; it does not
  resolve individual hiking paths. Zooming does not add photographic detail.
- No waypoint photos yet. The UI shows explicit placeholders.

The original 57 KMZ features are preserved in `data/holhoek.geojson`. The shared
guide embeds 44 hiking-related features, leaving residences, hunting-related
features and other miscellaneous source features out of the display/export.
Names and geometry remain as supplied, including the original spellings.

Distances are calculated from the drawn routes, not surveyed walking distances.
All source altitude values are zero, so no elevation gain/profile is reported.
The Day 1 endpoints are about 60 metres apart; no joining segment was invented.
Water availability/quality and the accessibility of the wheelchair-labelled
route are unverified. This is a map guide, not turn-by-turn navigation.

## Location

The location button uses the device's browser geolocation API with permission.
GPS can work without mobile reception. Location is most reliable in the HTTPS
hosted/cached version because phone browsers can restrict local HTML files.
The app shows a position and accuracy circle, warns about old/out-of-area fixes,
and stops tracking when the button is pressed again or the page is left.
Locations stay on the device; no position is sent to a server.

Location handling was tested with simulated positions. Physical GPS acquisition,
real trail navigation, and file opening on iOS/Android still need device testing.

## Develop and rebuild

Python 3.10+ is sufficient. Dependencies and imagery are already included;
building needs no network or package installation. Use this existing checkout.

```sh
cd /workspace/Holhoek_map
python3 scripts/build.py
python3 -m http.server 8000 --bind 127.0.0.1 --directory dist
```

Edit `src/page.html`, `src/app.css`, and `src/app.js`, then rebuild. The build
generates identical `dist/index.html` and `dist/Holhoek-offline.html`, plus a
versioned offline service worker. Use an HTTPS host for phone location and
offline browser storage in a shared deployment.

The optional imagery fetcher and its dependencies are described in
[`docs/imagery.md`](docs/imagery.md). The included image has already been fetched,
reprojected and visually checked. Build-time fetching is unnecessary.

## Add photos and descriptions

1. Put reasonably compressed JPG, PNG or WebP photos under `assets/photos/`.
2. Find the matching feature by `properties.id` or `properties.name` in
   `data/holhoek.geojson`.
3. Set `properties.photo` to its repository-relative path, for example
   `assets/photos/waterfall.jpg`, and add plain text to `properties.description`.
4. Run `python3 scripts/build.py`. The photo is embedded for offline access.

The importer can regenerate the source data with
`python3 scripts/import_kmz.py`. **This replaces derived GeoJSON, including
manual photo/description edits**, so preserve those edits before importing a
new KMZ. The build does not re-import or modify source data.

## Validate

The current development environment provides Playwright Python and Chromium:

```sh
python3 tests/smoke.py
```

The test starts its own local server, exercises Chromium, and exits nonzero on
failure. On a different machine, install Playwright Python and Chromium first;
the script discovers `chromium` on PATH. Results/screenshots go to the ignored
`test-results/` directory.

Validated scenarios:

1. Complete HTML rendered with networking disabled: satellite image, drag/zoom,
   trail filtering, waypoint search/details and optional layers, with zero HTTP
   requests and no JavaScript errors.
2. A 375×667 phone viewport: drawer controls, visible selected pin above its
   details, and no horizontal page overflow.
3. Simulated GPS success, accuracy, out-of-area position, tracking stop and
   permission denial.
4. Hosted offline saving, followed by reload and reopening in a new tab with
   networking disabled; imagery and waypoint interaction still work.
5. Failed offline save: no false success indication.

This environment's managed Chromium blocks `file://` navigation. The standalone
test therefore loads the actual HTML contents directly into an offline browser;
it does not claim to validate local file opening on a real phone.

## Sources and notices

Route source: supplied `data/Holhoek.kmz`. Imagery: **Contains modified Copernicus
Sentinel data 2026**; ESA/Copernicus, via Element 84/AWS Open Data. Acquisition,
bounds, scene ID and checksum are recorded in `assets/satellite.json`; source
terms and reproduction steps are linked in `docs/imagery.md`.

Leaflet 1.9.4 is vendored under its BSD-2-Clause license. The npm package SHA-512
integrity was verified; provenance is in `vendor/leaflet/source.json`. Its full
license is in `vendor/leaflet/LICENSE` and is embedded in each shared HTML file.
