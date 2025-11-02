const STATIC_CACHE_NAME = 'app-shell-cache-v1';
const VIDEO_CACHE_NAME = 'video-cache-v1';

// All the files your app needs to load its basic shell
const APP_SHELL_URLS = [
  '/', // This caches index.html
  '/static/js/app.js',
  '/static/css/style.css',
  'https://cdn.tailwindcss.com?plugins=forms,line-clamp,typography,aspect-ratio',
  'https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js',
  'https://fonts.googleapis.com/icon?family=Material+Icons'
];

// 1. Install the App Shell
self.addEventListener('install', event => {
  console.log('[SW] Install');
  event.waitUntil(
    caches.open(STATIC_CACHE_NAME).then(cache => {
      console.log('[SW] Caching App Shell');
      return cache.addAll(APP_SHELL_URLS);
    })
  );
});

// 2. Clean up old caches
self.addEventListener('activate', event => {
  console.log('[SW] Activate');
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          // Delete all caches that aren't our current static or video caches
          if (cacheName !== STATIC_CACHE_NAME && cacheName !== VIDEO_CACHE_NAME) {
            console.log('[SW] Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
  return self.clients.claim();
});

// 3. Serve from cache when offline (Cache-First strategy)
self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request).then(cachedResponse => {
      // If the file is in any cache, return it
      if (cachedResponse) {
        return cachedResponse;
      }
      
      // If not in cache, try to fetch it from the network
      return fetch(event.request).then(networkResponse => {
          // We don't cache API calls or other dynamic content here,
          // only explicit video downloads (see app.js)
          return networkResponse;
      }).catch(error => {
          console.log('[SW] Fetch failed:', error);
          // You could return a specific "offline.html" page here if you wanted
      });
    })
  );
});
