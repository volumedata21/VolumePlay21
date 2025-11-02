const CACHE_NAME = 'static-cache-v1';
const DATA_CACHE_NAME = 'video-cache-v1';

// (NEW) Give the API data its own cache name
const API_CACHE_NAME = 'api-cache-v1';

const FILES_TO_CACHE = [
    '/',
    '/static/js/app.js',
    '/static/css/style.css',
    'https://cdn.tailwindcss.com?plugins=forms,line-clamp,typography,aspect-ratio',
    'https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js',
    'https://fonts.googleapis.com/icon?family=Material+Icons'
];

self.addEventListener('install', (evt) => {
    console.log('[SW] Install');
    evt.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('[SW] Pre-caching static assets');
            return cache.addAll(FILES_TO_CACHE);
        })
    );
    self.skipWaiting();
});

self.addEventListener('activate', (evt) => {
    console.log('[SW] Activate');
    // Clean up old caches
    evt.waitUntil(
        caches.keys().then((keyList) => {
            return Promise.all(keyList.map((key) => {
                // (NEW) Add API_CACHE_NAME to the list of caches to keep
                if (key !== CACHE_NAME && key !== DATA_CACHE_NAME && key !== API_CACHE_NAME) {
                    console.log('[SW] Removing old cache', key);
                    return caches.delete(key);
                }
            }));
        })
    );
    self.clients.claim();
});

self.addEventListener('fetch', (evt) => {
    // (NEW) "Network First" strategy for API data
    // Try network, cache the result, but fall back to cache if network fails.
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
                        console.log('[SW] Network failed for /api/data, serving from cache.');
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