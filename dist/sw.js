'use strict';
const VERSION = '7895e0f7d914a1cd';
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
