// --- v4: BUMPED ALL CACHE NAMES TO FORCE A FULL UPDATE ---
const CACHE_NAME = 'static-cache-v4';
const DATA_CACHE_NAME = 'video-cache-v4';
const API_CACHE_NAME = 'api-cache-v4';
const CDN_CACHE_NAME = 'cdn-cache-v4'; // (NEW) Cache for CDN assets

// --- (CRITICAL FIX) ---
// ONLY cache local app shell files.
// Caching external CDNs in the install step is unreliable and can
// cause the entire service worker to fail to install.
const FILES_TO_CACHE = [
    '/',
    '/static/js/app.js',
    '/static/css/style.css'
    // All other CDN assets (Tailwind, Alpine, Fonts) will be cached on-demand.
];

self.addEventListener('install', (evt) => {
    console.log('[SW v4] Install');
    evt.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('[SW v4] Pre-caching static assets');
            return cache.addAll(FILES_TO_CACHE);
        })
    );
    self.skipWaiting();
});

self.addEventListener('activate', (evt) => {
    console.log('[SW v4] Activate');
    // Clean up old caches (v1, v2, v3)
    evt.waitUntil(
        caches.keys().then((keyList) => {
            return Promise.all(keyList.map((key) => {
                if (![CACHE_NAME, DATA_CACHE_NAME, API_CACHE_NAME, CDN_CACHE_NAME].includes(key)) {
                    console.log('[SW v4] Removing old cache', key);
                    return caches.delete(key);
                }
            }));
        })
    );
    self.clients.claim();
});

// (NEW) Stale-While-Revalidate strategy for CDNs (Tailwind, Alpine, Fonts)
// This serves the file from cache *first* (fast), then re-fetches it in the
// background to keep it up-to-date.
function staleWhileRevalidate(evt, cacheName) {
    evt.respondWith(
        caches.open(cacheName).then((cache) => {
            return cache.match(evt.request).then((cachedResponse) => {
                const fetchPromise = fetch(evt.request).then((networkResponse) => {
                    cache.put(evt.request, networkResponse.clone());
                    return networkResponse;
                });
                // Return cached response immediately if available, otherwise wait for network
                return cachedResponse || fetchPromise;
            });
        })
    );
}

self.addEventListener('fetch', (evt) => {
    const url = new URL(evt.request.url);

    // (NEW) Handle CDNs
    if (url.origin === 'https://cdn.tailwindcss.com' ||
        url.origin === 'https://cdn.jsdelivr.net' ||
        url.origin === 'https://fonts.googleapis.com' ||
        url.origin === 'https://fonts.gstatic.com') {
        staleWhileRevalidate(evt, CDN_CACHE_NAME);
        return;
    }

    // "Network First" strategy for API data
    if (url.pathname === '/api/data') {
        evt.respondWith(
            caches.open(API_CACHE_NAME).then((cache) => {
                return fetch(evt.request)
                    .then((response) => {
                        if (response.ok) {
                            cache.put(evt.request, response.clone());
                        }
                        return response;
                    })
                    .catch((err) => {
                        console.log('[SW v4] Network failed for /api/data, serving from cache.');
                        return cache.match(evt.request);
                    });
            })
        );
        return;
    }

    // "Cache First" strategy for videos and images
    if (url.pathname.startsWith('/api/video/') || url.pathname.startsWith('/api/thumbnail/')) {
        evt.respondWith(
            caches.open(DATA_CACHE_NAME).then(async (cache) => {
                const response = await cache.match(evt.request);
                return response || fetch(evt.request);
            })
        );
        return;
    }

    // "Cache First" for all other static app shell files
    evt.respondWith(
        caches.match(evt.request).then((response) => {
            return response || fetch(evt.request);
        })
    );
});