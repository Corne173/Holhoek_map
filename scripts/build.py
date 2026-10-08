#!/usr/bin/env python3
"""Build a self-contained HTML guide and an optional hosted offline worker."""
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'


def safe_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


def build():
    data = json.loads((ROOT / 'data/holhoek.geojson').read_text())
    # Keep non-hiking source features in the source file, out of the shared guide.
    data['features'] = [f for f in data['features'] if f['properties']['category'] != 'other']
    photo_count = 0
    for feature in data['features']:
        photo = feature['properties'].get('photo')
        if not photo:
            continue
        path = (ROOT / photo).resolve()
        if not path.is_relative_to(ROOT) or path.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}:
            raise ValueError('Waypoint photo must be a local JPG, PNG, or WebP inside the repository.')
        mime = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}[path.suffix.lower()]
        feature['properties']['photo'] = f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode()
        photo_count += 1
    imagery = None
    image_meta = ROOT / 'assets/satellite.json'
    if image_meta.exists():
        imagery = json.loads(image_meta.read_text())
        image_path = ROOT / 'assets/satellite.jpg'
        image_bytes = image_path.read_bytes()
        if imagery.get('imageSha256') and hashlib.sha256(image_bytes).hexdigest() != imagery['imageSha256']:
            raise ValueError('Satellite image checksum does not match its metadata. Re-fetch imagery.')
        imagery['dataUrl'] = 'data:image/jpeg;base64,' + base64.b64encode(image_bytes).decode()
    replacements = {
        '__LEAFLET_CSS__': (ROOT / 'vendor/leaflet/leaflet.css').read_text(),
        '__APP_CSS__': (ROOT / 'src/app.css').read_text(),
        '__MAP_DATA__': safe_json(data),
        '__IMAGERY_DATA__': safe_json(imagery),
        '__LEAFLET_JS__': (ROOT / 'vendor/leaflet/leaflet.js').read_text().split('//# sourceMappingURL=')[0],
        '__APP_JS__': (ROOT / 'src/app.js').read_text(),
    }
    page = (ROOT / 'src/page.html').read_text()
    for token, value in replacements.items():
        if token not in page:
            raise ValueError(f'Missing template token: {token}')
        page = page.replace(token, value)
    license_notice = (ROOT / 'vendor/leaflet/LICENSE').read_text()
    page = page.replace('</head>', '<!-- Leaflet license notice:\n' + license_notice + '\n-->\n</head>')
    version = hashlib.sha256(page.encode()).hexdigest()[:16]
    page = page.replace('__BUILD_VERSION__', version)
    DIST.mkdir(exist_ok=True)
    for filename in ['index.html', 'Holhoek-offline.html']:
        (DIST / filename).write_text(page)
    worker = """'use strict';
const VERSION = '__VERSION__';
const CACHE = 'holhoek-guide-' + VERSION;
const SHELL = new URL('./index.html', self.location).href;
self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    const response = await fetch(new Request(SHELL, {cache: 'reload'}));
    if (!response.ok || !response.headers.get('content-type')?.includes('text/html')) throw new Error('Map download failed');
    const html = await response.clone().text();
    if (!html.includes(VERSION)) throw new Error('Map version does not match');
    await cache.put(SHELL, response);
    await self.skipWaiting();
  })());
});
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) if (name.startsWith('holhoek-guide-') && name !== CACHE) await caches.delete(name);
    await self.clients.claim();
  })());
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET' || event.request.mode !== 'navigate') return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || !url.href.startsWith(self.registration.scope)) return;
  event.respondWith((async () => {
    const saved = await (await caches.open(CACHE)).match(SHELL);
    if (saved) return saved;
    try { const response = await fetch(event.request); if (response.ok) return response; } catch {}
    return new Response('Offline copy unavailable. Reconnect and save the map again.', {status:503});
  })());
});
self.addEventListener('message', event => {
  if (event.data?.type === 'OFFLINE_STATUS' && event.ports[0]) event.waitUntil((async () => {
    const ready = Boolean(await (await caches.open(CACHE)).match(SHELL));
    event.ports[0].postMessage({ready, version:VERSION});
  })());
});
""".replace('__VERSION__', version)
    (DIST / 'sw.js').write_text(worker)
    print(f'Built dist/Holhoek-offline.html and hosted index.html ({len(page.encode()) / 1024:.0f} KB each)')
    print(f'{len(data["features"])} hiking features; {photo_count} photos; satellite imagery: {bool(imagery)}; version {version}')


if __name__ == '__main__':
    build()
