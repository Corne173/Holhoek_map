'use strict';
(() => {
  const data = JSON.parse(document.getElementById('map-data').textContent);
  const imagery = JSON.parse(document.getElementById('imagery-data').textContent);
  const BUILD_VERSION = '__BUILD_VERSION__';
  const $ = id => document.getElementById(id);
  const colors = { day1: '#b96935', day2: '#276d69' };
  const routes = data.features.filter(f => f.properties.category === 'main-trail');
  const waypoints = data.features.filter(f => f.properties.category === 'trail-waypoint')
    .sort((a, b) => a.properties.waypointNumber - b.properties.waypointNumber);
  const routeVisible = { day1: true, day2: true };
  const byId = new Map(data.features.map(f => [f.properties.id, f]));
  const markers = new Map();
  const routeLayers = { day1: L.layerGroup(), day2: L.layerGroup() };
  const extras = { water: L.layerGroup(), landmarks: L.layerGroup(), roads: L.layerGroup(), secondary: L.layerGroup() };
  const allBounds = L.geoJSON({ type: 'FeatureCollection', features: routes }).getBounds();
  const extent = L.latLngBounds([-28.447, 28.005], [-28.398, 28.060]);
  let selectedId = null;
  let selectedTrigger = null;
  let toastTimer;
  let watchId = null;
  let locationMarker = null;
  let accuracyCircle = null;
  let gpsTimer = null;
  let lastLocationTime = null;
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

  const map = L.map('map', { zoomControl: false, minZoom: 13, maxZoom: 19, zoomSnap: 0.25, zoomDelta: 0.5,
    maxBounds: extent.pad(1.3), maxBoundsViscosity: 0.75, zoomAnimation: !reducedMotion });
  L.control.zoom({ position: 'topright' }).addTo(map);
  L.control.scale({ imperial: false, maxWidth: 110 }).addTo(map);
  map.attributionControl.setPrefix(false);
  map.attributionControl.addAttribution('Routes: Holhoek KMZ · Leaflet');

  // A coordinate grid is used when no licensed, georeferenced image is bundled.
  // It deliberately does not depict invented terrain, buildings, or paths.
  const CoordinateGrid = L.GridLayer.extend({
    createTile(coords) {
      const tile = document.createElement('canvas');
      const size = this.getTileSize();
      tile.width = size.x * 2;
      tile.height = size.y * 2;
      const ctx = tile.getContext('2d');
      ctx.scale(2, 2);
      ctx.fillStyle = '#e9eddf';
      ctx.fillRect(0, 0, size.x, size.y);
      ctx.strokeStyle = '#d9e0ce';
      ctx.lineWidth = 0.6;
      const tl = map.unproject(L.point(coords.x * size.x, coords.y * size.y), coords.z);
      const br = map.unproject(L.point((coords.x + 1) * size.x, (coords.y + 1) * size.y), coords.z);
      const step = coords.z <= 14 ? 0.005 : coords.z <= 16 ? 0.001 : 0.00025;
      for (let lon = Math.ceil(tl.lng / step) * step; lon < br.lng; lon += step) {
        const x = map.project([tl.lat, lon], coords.z).x - coords.x * size.x;
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, size.y); ctx.stroke();
      }
      for (let lat = Math.ceil(br.lat / step) * step; lat < tl.lat; lat += step) {
        const y = map.project([lat, tl.lng], coords.z).y - coords.y * size.y;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(size.x, y); ctx.stroke();
      }
      return tile;
    }
  });
  new CoordinateGrid({ attribution: null }).addTo(map);
  if (imagery && imagery.dataUrl) {
    L.imageOverlay(imagery.dataUrl, imagery.bounds, { opacity: 1 }).addTo(map);
    map.attributionControl.addAttribution(`Contains modified Copernicus Sentinel data ${imagery.acquired.slice(0, 4)}`);
    $('basemap-label').textContent = 'Offline satellite map';
    $('map-note-copy').textContent = 'Satellite image, trails and pins are included in this file.';
    $('imagery-help-title').textContent = 'Satellite imagery, included';
    $('imagery-help').textContent = `Satellite image acquired ${imagery.acquired.slice(0, 10)}; ${imagery.resolutionMeters} metres per pixel. It shows the landscape, with limited detail at close zoom. Photos can be added to waypoints later. Distances are approximate lengths of the drawn routes.`;
  }

  function latLng(feature) {
    const c = feature.geometry.coordinates;
    return L.latLng(c[1], c[0]);
  }
  function displayName(feature) { return feature.properties.name.replace(/^\d+\.\s*/, ''); }
  function toast(message) {
    clearTimeout(toastTimer);
    $('toast').textContent = message; $('toast').hidden = false;
    toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4000);
  }
  function fitTrails() {
    closeDetail(false);
    const visible = routes.filter(f => routeVisible[f.properties.trail]);
    const bounds = visible.length ? L.geoJSON({ type: 'FeatureCollection', features: visible }).getBounds() : allBounds;
    map.fitBounds(bounds, { paddingTopLeft: [38, 100], paddingBottomRight: [75, 80], maxZoom: 15, animate: false });
  }
  for (const route of routes) {
    const p = route.properties;
    $( `distance-${p.trail}`).textContent = p.distanceKm.toFixed(1);
    L.geoJSON(route, { style: { color: '#faf7ed', opacity: 0.92, weight: 7, lineCap: 'round' }, interactive: false }).addTo(routeLayers[p.trail]);
    const line = L.geoJSON(route, { style: { color: colors[p.trail], weight: 3.5, opacity: 1, lineCap: 'round' } });
    const label = document.createElement('span');
    label.textContent = `${p.name} · ${p.distanceKm.toFixed(1)} km`;
    line.bindTooltip(label, { sticky: true });
    line.addTo(routeLayers[p.trail]);
  }

  function addPoint(feature, group, kind) {
    const p = feature.properties;
    const isNumbered = p.category === 'trail-waypoint';
    const symbol = kind === 'water' ? '<svg viewBox="0 0 24 24" width="17" height="17" fill="none"><path d="M12 3c-2 4-6 7-6 11a6 6 0 0 0 12 0c0-4-4-7-6-11Z" stroke="currentColor" stroke-width="1.5"/></svg>' : kind === 'landmark' ? '◇' : p.name === 'Lodge' ? '<svg viewBox="0 0 24 24" width="17" height="17" fill="none"><path d="m3 11 9-8 9 8M6 9v12h12V9m-8 12v-7h4v7" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>' : '<svg viewBox="0 0 24 24" width="17" height="17" fill="none"><path d="M5 21V4h14v17M9 12h6m-3-3 3 3-3 3" stroke="currentColor" stroke-width="1.5"/></svg>';
    const html = isNumbered
      ? `<span class="pin-number ${p.trail === 'day2' ? 'day-two' : ''}">${Number(p.waypointNumber)}</span>`
      : `<span class="facility-symbol ${kind === 'water' ? 'water' : ''}">${symbol}</span>`;
    const marker = L.marker(latLng(feature), {
      icon: L.divIcon({ className: isNumbered ? 'trail-pin' : 'facility-pin', html, iconSize: [29, 29], iconAnchor: [14.5, 14.5] }),
      title: p.name, alt: p.name, riseOnHover: true,
      zIndexOffset: isNumbered ? 500 : 0,
    });
    marker.on('click', () => openDetail(feature, marker.getElement()));
    marker.on('add', () => {
      const el = marker.getElement();
      if (el) {
        el.setAttribute('aria-label', `Open ${p.name}`);
        el.setAttribute('role', 'button');
        el.classList.toggle('is-selected', selectedId === p.id);
      }
    });
    if (p.category === 'facility') {
      const label = document.createElement('span'); label.textContent = p.name;
      marker.bindTooltip(label, { permanent: true, direction: 'bottom', offset: [0, 14], className: 'facility-label' });
    }
    marker.addTo(group); markers.set(p.id, marker);
  }
  waypoints.forEach(f => addPoint(f, routeLayers[f.properties.trail], 'waypoint'));
  data.features.forEach(feature => {
    const category = feature.properties.category;
    if (category === 'facility') addPoint(feature, map, 'facility');
    if (category === 'water') addPoint(feature, extras.water, 'water');
    if (category === 'landmark') addPoint(feature, extras.landmarks, 'landmark');
    if (category === 'service-road') L.geoJSON(feature, { style: { color: '#8c9681', weight: 2, dashArray: '5 7', opacity: 0.8 }, interactive: false }).addTo(extras.roads);
    if (category === 'secondary-trail') {
      const layer = L.geoJSON(feature, { style: { color: '#8173a3', weight: 3, dashArray: '6 6' } });
      const label = document.createElement('span'); label.textContent = `${feature.properties.name} (KMZ label; accessibility unverified)`;
      layer.bindTooltip(label).addTo(extras.secondary);
    }
  });
  routeLayers.day1.addTo(map); routeLayers.day2.addTo(map); extras.landmarks.addTo(map);

  function renderList() {
    const term = $('search').value.trim().toLowerCase();
    const filtered = waypoints.filter(f => routeVisible[f.properties.trail] && f.properties.name.toLowerCase().includes(term));
    $('waypoint-list').replaceChildren(); $('waypoint-count').textContent = filtered.length;
    if (!filtered.length) {
      const empty = document.createElement('p'); empty.className = 'empty-search';
      empty.textContent = term ? 'No waypoints match your search.' : 'Turn on a trail to see its waypoints.';
      $('waypoint-list').append(empty);
    }
    filtered.forEach(feature => {
      const p = feature.properties;
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'waypoint-item';
      button.classList.toggle('selected', selectedId === p.id);
      button.dataset.id = p.id; button.setAttribute('aria-label', `${p.name}, ${p.trail === 'day1' ? 'Day 1' : 'Day 2'}`);
      const number = document.createElement('span'); number.className = `waypoint-number ${p.trail === 'day2' ? 'day-two' : ''}`; number.textContent = p.waypointNumber;
      const text = document.createElement('span'); text.className = 'waypoint-item-text';
      const name = document.createElement('strong'); name.textContent = displayName(feature);
      const category = document.createElement('small'); category.textContent = `${p.trail === 'day1' ? 'Day 1' : 'Day 2'} · Waypoint ${p.waypointNumber}`;
      text.append(name, category);
      const arrow = document.createElement('span'); arrow.className = 'waypoint-arrow'; arrow.innerHTML = '<svg viewBox="0 0 16 16" width="12" height="12" fill="none"><path d="M4 12 12 4M4 4h8v8" stroke="currentColor"/></svg>'; arrow.setAttribute('aria-hidden', 'true');
      button.append(number, text, arrow);
      button.addEventListener('click', () => {
        if (matchMedia('(max-width: 700px)').matches) setDrawer(false);
        openDetail(feature, button);
      });
      $('waypoint-list').append(button);
    });
  }

  function openDetail(feature, trigger) {
    selectedId = feature.properties.id; selectedTrigger = trigger;
    const p = feature.properties;
    $('detail-category').textContent = p.trail ? `${p.trail === 'day1' ? 'Day 1' : 'Day 2'} / Waypoint ${p.waypointNumber}` : ({ facility: 'Around Holhoek', water: 'Water point / From your KMZ', landmark: 'Along the trail' }[p.category] || 'From your KMZ');
    $('detail-name').textContent = displayName(feature);
    $('detail-description').textContent = p.description || (p.category === 'water' ? 'Marked in the source map. Availability and drinking-water quality have not been verified.' : 'Marked in your Holhoek trail map. A photo and description can be added here.');
    const c = feature.geometry.coordinates;
    $('detail-coordinates').textContent = `${Math.abs(c[1]).toFixed(5)}° ${c[1] < 0 ? 'S' : 'N'} · ${Math.abs(c[0]).toFixed(5)}° ${c[0] < 0 ? 'W' : 'E'}`;
    $('detail-photo').replaceChildren();
    if (p.photo && /^data:image\/(png|jpeg|webp);base64,/.test(p.photo)) {
      const img = document.createElement('img'); img.src = p.photo; img.alt = p.name;
      $('detail-photo').append(img);
    } else {
      $('detail-photo').innerHTML = '<svg viewBox="0 0 48 48" fill="none" aria-hidden="true"><rect x="5" y="9" width="38" height="30" rx="3" stroke="currentColor" stroke-width="1.2"/><path d="m7 35 12-14 10 10 5-6 8 10" stroke="currentColor" stroke-width="1.2"/><circle cx="33" cy="18" r="3" stroke="currentColor" stroke-width="1.2"/></svg><span>Photo coming soon</span>';
    }
    $('detail').hidden = false; document.querySelector('.map-area').classList.add('has-detail');
    for (const [id, marker] of markers) marker.getElement()?.classList.toggle('is-selected', id === selectedId);
    renderList();
    // Put the selected marker above the detail sheet, without changing source geometry.
    map.invalidateSize({ pan: false });
    const zoom = Math.max(map.getZoom(), 16);
    const point = map.project(latLng(feature), zoom);
    const size = map.getSize();
    const mobile = matchMedia('(max-width: 700px)').matches;
    const sheetTop = $('detail').getBoundingClientRect().top - $('map').getBoundingClientRect().top;
    const target = mobile ? L.point(size.x * 0.43, Math.max(78, sheetTop / 2 + 20)) : L.point(size.x * 0.65, size.y * 0.43);
    map.setView(map.unproject(point.add(size.divideBy(2)).subtract(target), zoom), zoom, { animate: !reducedMotion });
    $('close-detail').focus({ preventScroll: true });
  }
  function closeDetail(restoreFocus = true) {
    $('detail').hidden = true; document.querySelector('.map-area').classList.remove('has-detail');
    for (const marker of markers.values()) marker.getElement()?.classList.remove('is-selected');
    const previousId = selectedId; selectedId = null; renderList();
    if (restoreFocus) {
      const listButton = [...$('waypoint-list').querySelectorAll('button')].find(el => el.dataset.id === previousId);
      const target = selectedTrigger?.isConnected ? selectedTrigger : listButton;
      if (target && target.getClientRects().length) target.focus({ preventScroll: true });
      else $('waypoint-toggle').focus({ preventScroll: true });
    }
  }
  $('close-detail').addEventListener('click', () => closeDetail());
  $('detail-zoom').addEventListener('click', () => {
    if (!selectedId) return;
    const point = latLng(byId.get(selectedId)); closeDetail(false);
    map.setView(point, 18, { animate: !reducedMotion });
  });
  $('copy-coordinates').addEventListener('click', async () => {
    if (!selectedId) return;
    const c = byId.get(selectedId).geometry.coordinates;
    const text = `${c[1].toFixed(6)}, ${c[0].toFixed(6)}`;
    try {
      if (!navigator.clipboard) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(text); toast('Coordinates copied');
    } catch {
      const range = document.createRange(); range.selectNodeContents($('detail-coordinates'));
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      toast('Coordinates selected — use your device’s Copy command.');
    }
  });
  for (const trail of ['day1', 'day2']) {
    $(`toggle-${trail}`).addEventListener('click', () => {
      routeVisible[trail] = !routeVisible[trail];
      $(`toggle-${trail}`).setAttribute('aria-pressed', String(routeVisible[trail]));
      if (routeVisible[trail]) routeLayers[trail].addTo(map); else map.removeLayer(routeLayers[trail]);
      if (selectedId && byId.get(selectedId).properties.trail === trail && !routeVisible[trail]) closeDetail(false);
      renderList();
    });
  }
  $('search').addEventListener('input', renderList);
  $('fit-all').addEventListener('click', () => {
    for (const trail of ['day1', 'day2']) {
      routeVisible[trail] = true; routeLayers[trail].addTo(map); $(`toggle-${trail}`).setAttribute('aria-pressed', 'true');
    }
    $('search').value = ''; renderList(); fitTrails();
  });
  $('fit-map').addEventListener('click', fitTrails);
  $('dismiss-note').addEventListener('click', () => { $('map-note').hidden = true; });
  $('layers-button').addEventListener('click', () => {
    const show = $('layers-panel').hidden;
    $('layers-panel').hidden = !show; $('layers-button').setAttribute('aria-expanded', String(show));
  });
  for (const key of Object.keys(extras)) {
    $(`layer-${key}`).addEventListener('change', event => {
      if (event.target.checked) extras[key].addTo(map); else map.removeLayer(extras[key]);
    });
  }
  function setDrawer(open) {
    document.querySelector('.waypoint-section').classList.toggle('open', open);
    $('waypoint-toggle').setAttribute('aria-expanded', String(open));
    requestAnimationFrame(() => map.invalidateSize({ pan: false }));
  }
  $('waypoint-toggle').addEventListener('click', () => {
    if (matchMedia('(max-width: 700px)').matches) setDrawer($('waypoint-toggle').getAttribute('aria-expanded') !== 'true');
  });
  const phoneLayout = matchMedia('(max-width: 700px)');
  function syncDrawerState() {
    $('waypoint-toggle').setAttribute('aria-expanded', String(!phoneLayout.matches || document.querySelector('.waypoint-section').classList.contains('open')));
  }
  phoneLayout.addEventListener('change', syncDrawerState);
  syncDrawerState();

  function stopLocation() {
    if (watchId !== null) navigator.geolocation.clearWatch(watchId);
    watchId = null; clearInterval(gpsTimer); gpsTimer = null; lastLocationTime = null;
    if (locationMarker) { map.removeLayer(locationMarker); locationMarker = null; }
    if (accuracyCircle) { map.removeLayer(accuracyCircle); accuracyCircle = null; }
    $('locate').classList.remove('active'); $('locate').setAttribute('aria-label', 'Show my location');
  }
  $('locate').addEventListener('click', () => {
    if (watchId !== null) { stopLocation(); $('location-status').hidden = true; return; }
    $('location-status').hidden = false;
    if (!navigator.geolocation || !window.isSecureContext) {
      $('location-status').textContent = 'This browser cannot share location here. Use the HTTPS hosted map for location access. The downloaded trails still work offline.';
      return;
    }
    $('location-status').textContent = 'Finding your GPS position… Allow location access if prompted.';
    $('locate').classList.add('active'); $('locate').setAttribute('aria-label', 'Stop showing my location');
    let firstFix = true;
    let latestAccuracy = null;
    let outside = false;
    function showFixStatus() {
      if (!lastLocationTime) return;
      const age = Math.max(0, Math.floor((Date.now() - lastLocationTime) / 1000));
      $('location-status').textContent = `${outside ? 'Your position is outside this map area. ' : 'Your GPS position. '}Accuracy ±${Math.round(latestAccuracy)} m. ${age >= 30 ? `Last fix ${age} seconds ago; it may be out of date.` : 'Updated just now.'} Tap the location button to stop.`;
    }
    watchId = navigator.geolocation.watchPosition(position => {
      const { latitude, longitude, accuracy } = position.coords;
      if (![latitude, longitude, accuracy].every(Number.isFinite) || accuracy < 0) return;
      const point = L.latLng(latitude, longitude);
      outside = !extent.contains(point); lastLocationTime = position.timestamp; latestAccuracy = accuracy;
      if (!locationMarker) {
        locationMarker = L.marker(point, { icon: L.divIcon({ className: 'location-marker', html: '<span class="location-dot"></span>', iconSize: [18, 18], iconAnchor: [9, 9] }), title: 'Your GPS position', zIndexOffset: 2000 }).addTo(map);
        accuracyCircle = L.circle(point, { radius: accuracy, color: '#3c84c8', weight: 1, fillColor: '#3c84c8', fillOpacity: 0.1, interactive: false }).addTo(map);
      } else { locationMarker.setLatLng(point); accuracyCircle.setLatLng(point).setRadius(accuracy); }
      if (firstFix && !outside) map.setView(point, Math.max(map.getZoom(), 16), { animate: !reducedMotion });
      firstFix = false; showFixStatus();
      if (!gpsTimer) gpsTimer = setInterval(showFixStatus, 10000);
    }, error => {
      const hadFix = lastLocationTime !== null;
      if (error.code === 1) {
        stopLocation(); $('location-status').textContent = 'Location access was denied. Enable it in this browser’s site settings to show your position.';
      } else {
        $('location-status').textContent = hadFix ? 'No fresh GPS fix. The last shown position may be out of date; still trying. Tap the location button to stop.' : 'No GPS fix yet. Move into open sky and keep location enabled. Still trying; tap the location button to stop.';
      }
    }, { enableHighAccuracy: true, maximumAge: 0, timeout: 20000 });
  });
  window.addEventListener('pagehide', stopLocation);

  const isHosted = location.protocol === 'https:' || (['localhost', '127.0.0.1'].includes(location.hostname) && location.protocol === 'http:');
  const canCache = isHosted && 'serviceWorker' in navigator;
  $('download-guide').hidden = location.protocol === 'file:';
  function openHelp() { $('help-dialog').showModal(); }
  $('help-button').addEventListener('click', openHelp);
  $('offline-button').addEventListener('click', openHelp);
  $('close-help').addEventListener('click', () => $('help-dialog').close());
  $('help-dialog').addEventListener('click', event => { if (event.target === $('help-dialog')) {
    const rect = $('help-dialog').getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $('help-dialog').close();
  } });
  if (!canCache) {
    $('save-offline').hidden = true;
    $('offline-status').textContent = location.protocol === 'file:' ? 'This downloaded file already includes everything shown on the map.' : 'Download the HTML file to use it offline, or open an HTTPS hosted copy to save it in this browser.';
  } else $('offline-label').textContent = 'SAVE OFFLINE';

  function workerStatus(worker) {
    return new Promise((resolve, reject) => {
      const channel = new MessageChannel();
      const timer = setTimeout(() => { channel.port1.close(); reject(new Error('Offline verification timed out.')); }, 8000);
      channel.port1.onmessage = event => { clearTimeout(timer); channel.port1.close(); resolve(event.data); };
      worker.postMessage({ type: 'OFFLINE_STATUS' }, [channel.port2]);
    });
  }
  function showSaved() {
    $('offline-label').textContent = 'SAVED OFFLINE'; $('offline-button').classList.add('saved');
    $('offline-status').textContent = 'This map is saved in this browser. Reopen this same page in airplane mode before your hike to check it. Browser storage can be cleared; keep the HTML download as a backup.';
    $('save-offline').textContent = 'Refresh offline copy';
  }
  $('save-offline').addEventListener('click', async () => {
    $('save-offline').disabled = true; $('offline-status').textContent = 'Saving this map on your device…';
    try {
      const registration = await navigator.serviceWorker.register('./sw.js');
      await registration.update();
      const pending = registration.installing || registration.waiting;
      if (pending && pending.state !== 'activated') {
        await new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error('Saving timed out. Check your connection and try again.')), 20000);
          const check = () => {
            if (pending.state === 'activated') { clearTimeout(timer); pending.removeEventListener('statechange', check); resolve(); }
            else if (pending.state === 'redundant') { clearTimeout(timer); pending.removeEventListener('statechange', check); reject(new Error('The offline map could not be saved.')); }
          };
          pending.addEventListener('statechange', check); check();
        });
      }
      const active = registration.active || (await navigator.serviceWorker.ready).active;
      const status = await workerStatus(active);
      if (!status.ready || status.version !== BUILD_VERSION) throw new Error('The saved copy is not the current map. Reconnect and try again.');
      showSaved();
      // Persistence is a browser preference, not a guarantee that storage will survive.
      if (navigator.storage?.persist) navigator.storage.persist().catch(() => {});
    } catch (error) {
      $('offline-status').textContent = `Could not save offline: ${error.message} Keep a downloaded HTML copy instead.`;
    } finally { $('save-offline').disabled = false; }
  });
  if (canCache) navigator.serviceWorker.getRegistration('./').then(async registration => {
    if (!registration?.active) return;
    const status = await workerStatus(registration.active);
    if (status.ready && status.version === BUILD_VERSION) showSaved();
  }).catch(() => {});
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      if (!$('detail').hidden) closeDetail();
      $('layers-panel').hidden = true; $('layers-button').setAttribute('aria-expanded', 'false');
    }
  });
  renderList(); fitTrails();
  new ResizeObserver(() => map.invalidateSize({ pan: false })).observe($('map'));
})();
