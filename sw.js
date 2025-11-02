// --- v2: Incremented all cache names to force a full update ---
const CACHE_NAME = 'static-cache-v2';
const DATA_CACHE_NAME = 'video-cache-v2';
const API_CACHE_NAME = 'api-cache-v2';

const FILES_TO_CACHE = [
    '/',
    '/static/js/app.js',
    '/static/css/style.css',
    'https://cdn.tailwindcss.com?plugins=forms,line-clamp,typography,aspect-ratio',
    'https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js',
    'https://fonts.googleapis.com/icon?family=Material+Icons'
];

self.addEventListener('install', (evt) => {
    console.log('[SW v2] Install');
    evt.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('[SW v2] Pre-caching static assets');
            // addAll() is atomic - if one file fails, the whole install fails.
            return cache.addAll(FILES_TO_CACHE);
        })
    );
    self.skipWaiting();
});

self.addEventListener('activate', (evt) => {
    console.log('[SW v2] Activate');
    // Clean up old v1 caches
    evt.waitUntil(
        caches.keys().then((keyList) => {
            return Promise.all(keyList.map((key) => {
                if (key !== CACHE_NAME && key !== DATA_CACHE_NAME && key !== API_CACHE_NAME) {
                    console.log('[SW v2] Removing old cache', key);
                    return caches.delete(key);
                }
            }));
        })
    );
    self.clients.claim();
});

self.addEventListener('fetch', (evt) => {
    // "Network First" strategy for API data
    if (evt.request.url.includes('/api/data')) {
        evt.respondWith(
            caches.open(API_CACHE_NAME).then((cache) => {
                return fetch(evt.request)
                    .then((response) => {
                        // If we get a good response, clone it and cache it.
                        if (response.ok) {
                            cache.put(evt.request, response.clone());
                        }
                        return response;
                    })
                    .catch((err) => {
                        // Network failed, serve from cache
                        console.log('[SW v2] Network failed for /api/data, serving from cache.');
                        return cache.match(evt.request);
                    });
            })
        );
        return;
    }

    // "Cache First" strategy for videos and images
    if (evt.request.url.includes('/api/video/') || evt.request.url.includes('/api/thumbnail/')) {
        evt.respondWith(
            caches.open(DATA_CACHE_NAME).then(async (cache) => {
                const response = await cache.match(evt.request);
                // If found in cache, return it. Otherwise, fetch from network.
                return response || fetch(evt.request);
            })
        );
        return;
    }

    // "Cache First" for all other static app shell files
    evt.respondWith(
        caches.match(evt.request).then((response) => {
            // If found in cache, return it. Otherwise, fetch from network.
            return response || fetch(evt.request);
        })
    );
});