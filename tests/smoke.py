#!/usr/bin/env python3
"""Real-browser smoke checks for the self-contained file and hosted offline copy.

Requires Playwright Python and Chromium; starts and shuts down its own local server.
"""
import functools
import http.server
import json
from pathlib import Path
import shutil
import threading

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'test-results'


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def main():
    RESULTS.mkdir(exist_ok=True)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(ROOT / 'dist')))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    passed = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=shutil.which('chromium'), headless=True, args=['--no-sandbox'])
            # Render the complete artifact with no connection. This managed browser
            # blocks file:// navigation by policy; set_content exercises the same
            # inline HTML/CSS/JS without claiming phone file-opening was tested.
            context = browser.new_context(offline=True, viewport={'width': 1440, 'height': 940}, reduced_motion='reduce')
            page = context.new_page()
            errors, network_requests = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: network_requests.append(request.url) if request.url.startswith(('http:', 'https:')) else None)
            page.set_content((ROOT / 'dist/Holhoek-offline.html').read_text(), wait_until='load')
            expect(page.locator('.trail-pin')).to_have_count(19)
            expect(page.locator('.waypoint-item')).to_have_count(19)
            expect(page.locator('#basemap-label')).to_have_text('Offline satellite map')
            assert page.locator('img.leaflet-image-layer').evaluate('(img) => img.complete && img.naturalWidth === 1024')
            before_zoom = page.locator('img.leaflet-image-layer').bounding_box()['width']
            page.get_by_role('button', name='Zoom in', exact=True).click()
            assert page.locator('img.leaflet-image-layer').bounding_box()['width'] > before_zoom
            image_before = page.locator('img.leaflet-image-layer').bounding_box()
            page.mouse.move(1040, 720); page.mouse.down(); page.mouse.move(1100, 670, steps=8); page.mouse.up()
            assert page.locator('img.leaflet-image-layer').bounding_box() != image_before
            page.locator('#toggle-day1').click()
            expect(page.locator('.trail-pin')).to_have_count(4)
            expect(page.locator('.waypoint-item')).to_have_count(4)
            page.locator('#toggle-day2').click()
            expect(page.locator('.trail-pin')).to_have_count(0)
            expect(page.locator('.empty-search')).to_contain_text('Turn on a trail')
            page.locator('#fit-all').click()
            expect(page.locator('.trail-pin')).to_have_count(19)
            page.locator('#search').fill('Waterfall')
            expect(page.locator('.waypoint-item')).to_have_count(3)
            page.get_by_role('button', name='4. Waterfall, Day 1', exact=True).click()
            expect(page.locator('#detail')).to_be_visible()
            expect(page.locator('#detail-name')).to_have_text('Waterfall')
            expect(page.locator('#detail-photo')).to_contain_text('Photo coming soon')
            expect(page.locator('#detail-coordinates')).to_contain_text('28.42246')
            page.keyboard.press('Escape')
            expect(page.locator('#detail')).to_be_hidden()
            page.locator('#search').fill('')
            page.locator('#layers-button').click()
            page.locator('#layer-water').check()
            expect(page.locator('.facility-symbol.water')).to_have_count(10)
            page.locator('#layer-water').uncheck()
            expect(page.locator('.facility-symbol.water')).to_have_count(0)
            page.locator('#layer-roads').check()
            page.locator('#layer-roads').uncheck()
            page.locator('#layers-button').click()
            page.locator('#fit-map').click()
            page.screenshot(path=str(RESULTS / 'desktop.png'))
            assert not network_requests, network_requests
            assert not errors, errors
            passed.append('Standalone offline HTML: satellite rendering, pan/zoom, route filters, search, waypoints, optional layers; zero HTTP requests or JS errors')

            # Check phone layout and that the selected pin remains above the sheet.
            page.set_viewport_size({'width': 375, 'height': 667})
            page.locator('#fit-map').click()
            expect(page.locator('#waypoint-toggle')).to_have_attribute('aria-expanded', 'false')
            page.locator('#waypoint-toggle').click()
            page.locator('#search').fill('Waterfall')
            page.get_by_role('button', name='4. Waterfall, Day 1', exact=True).click()
            expect(page.locator('#waypoint-content')).to_be_hidden()
            pin = page.locator('.trail-pin.is-selected').bounding_box()
            sheet = page.locator('#detail').bounding_box()
            assert pin['y'] + pin['height'] <= sheet['y'] + 1, {'pin': pin, 'sheet': sheet}
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(RESULTS / 'mobile-waypoint.png'))
            page.locator('#close-detail').click()
            page.locator('#fit-map').click()
            page.screenshot(path=str(RESULTS / 'mobile.png'))
            passed.append('375×667 phone: responsive drawer, usable waypoint sheet, selected pin visible, no horizontal overflow')
            context.close()

            # Check GPS states with simulated browser positions, never actual tracking.
            context = browser.new_context(geolocation={'latitude': -28.422463, 'longitude': 28.027694, 'accuracy': 12}, permissions=['geolocation'], reduced_motion='reduce')
            page = context.new_page(); page.goto(base)
            page.locator('#locate').click()
            expect(page.locator('#location-status')).to_contain_text('Accuracy ±12 m')
            expect(page.locator('.location-marker')).to_have_count(1)
            context.set_geolocation({'latitude': -26.2, 'longitude': 28.0, 'accuracy': 30})
            expect(page.locator('#location-status')).to_contain_text('outside this map area')
            page.locator('#locate').click()
            expect(page.locator('.location-marker')).to_have_count(0)
            expect(page.locator('#location-status')).to_be_hidden()
            context.close()
            context = browser.new_context()
            context.add_init_script("Object.defineProperty(navigator, 'geolocation', {value:{watchPosition(success, error){setTimeout(()=>error({code:1}),0);return 1;},clearWatch(){}}});")
            page = context.new_page(); page.goto(base); page.locator('#locate').click()
            expect(page.locator('#location-status')).to_contain_text('Location access was denied')
            assert 'active' not in (page.locator('#locate').get_attribute('class') or '')
            context.close()
            passed.append('Simulated GPS: accuracy displayed, outside-map location handled, tracking stops, permission denial explained')

            # Save while online, then reopen with all networking disabled.
            context = browser.new_context(reduced_motion='reduce')
            page = context.new_page(); page.goto(base)
            page.locator('#offline-button').click(); page.locator('#save-offline').click()
            expect(page.locator('#offline-status')).to_contain_text('This map is saved in this browser', timeout=30000)
            page.locator('#close-help').click()
            context.set_offline(True)
            page.reload(wait_until='load')
            expect(page.locator('.trail-pin')).to_have_count(19)
            expect(page.locator('#offline-label')).to_have_text('SAVED OFFLINE')
            assert page.locator('img.leaflet-image-layer').evaluate('(img) => img.complete && img.naturalWidth > 0')
            page.close()
            page = context.new_page(); page.goto(base + '/index.html')
            expect(page.locator('.trail-pin')).to_have_count(19)
            page.get_by_role('button', name='Open 17. Wonderland', exact=True).click()
            expect(page.locator('#detail-name')).to_have_text('Wonderland')
            context.close()
            passed.append('Hosted offline save: cache verified, offline reload and new tab succeed with satellite and clickable pins')

            # A missing service worker must never produce a false saved indicator.
            context = browser.new_context()
            context.route('**/sw.js', lambda route: route.fulfill(status=404, body='Not found'))
            page = context.new_page(); page.goto(base)
            page.locator('#offline-button').click(); page.locator('#save-offline').click()
            expect(page.locator('#offline-status')).to_contain_text('Could not save offline', timeout=30000)
            expect(page.locator('#offline-label')).not_to_have_text('SAVED OFFLINE')
            context.close()
            passed.append('Offline save failure: missing worker reports failure and never claims readiness')
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    (RESULTS / 'smoke-results.json').write_text(json.dumps({'passed': passed, 'failed': []}, indent=2) + '\n')
    for item in passed:
        print('PASS:', item)
    print(f'{len(passed)} browser scenarios passed.')


if __name__ == '__main__':
    main()
