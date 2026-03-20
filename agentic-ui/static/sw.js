// Service Worker for Agentic UI — minimal PWA implementation
// Cache strategy: static assets cache-first, HTML network-first, API network-only

const CACHE_NAME = 'v14-static';
const CRITICAL_ASSETS = [
  '/',
  '/static/app.js?v=14',
  '/static/style.css?v=14',
];

// Install: pre-cache critical assets
self.addEventListener('install', event => {
  console.log('[SW] Installing, pre-caching critical assets...');
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(CRITICAL_ASSETS).catch(err => {
        console.warn('[SW] Some assets failed to cache (may be offline)', err);
        // Don't fail install if cache fails — allow graceful degradation
      });
    })
  );
});

// Activate: remove old caches
self.addEventListener('activate', event => {
  console.log('[SW] Activating, cleaning old caches...');
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames
          .filter(name => name !== CACHE_NAME)
          .map(name => {
            console.log('[SW] Deleting old cache:', name);
            return caches.delete(name);
          })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch: routing logic
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Skip non-GET requests
  if (event.request.method !== 'GET') {
    return;
  }

  // API calls: network-only (no offline support for messages)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(event.request).catch(() => {
        // Return 503 so client knows API is unavailable
        return new Response('API unavailable offline', { status: 503 });
      })
    );
    return;
  }

  // Static assets (versioned): cache-first, update in background
  if (url.pathname.startsWith('/static/') && url.search.includes('v=')) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) {
          return cached;
        }
        return fetch(event.request).then(response => {
          if (!response || response.status !== 200) {
            return response;
          }
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then(cache => {
            cache.put(event.request, responseClone);
          });
          return response;
        });
      })
    );
    return;
  }

  // HTML (index): network-first (always check for updates)
  if (url.pathname === '/' || url.pathname.endsWith('.html')) {
    event.respondWith(
      fetch(event.request).then(response => {
        if (!response || response.status !== 200) {
          return response;
        }
        const responseClone = response.clone();
        caches.open(CACHE_NAME).then(cache => {
          cache.put(event.request, responseClone);
        });
        return response;
      }).catch(() => {
        return caches.match(event.request).then(cached => {
          return cached || new Response('Offline, unable to load page', { status: 503 });
        });
      })
    );
    return;
  }

  // Google Fonts: cache-first
  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) {
          return cached;
        }
        return fetch(event.request).then(response => {
          if (!response || response.status !== 200) {
            return response;
          }
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then(cache => {
            cache.put(event.request, responseClone);
          });
          return response;
        }).catch(() => {
          // No fallback for fonts — system fonts will render
          return new Response('Font unavailable', { status: 503 });
        });
      })
    );
    return;
  }

  // Everything else: network-first
  event.respondWith(
    fetch(event.request).then(response => {
      if (!response || response.status !== 200) {
        return response;
      }
      const responseClone = response.clone();
      caches.open(CACHE_NAME).then(cache => {
        cache.put(event.request, responseClone);
      });
      return response;
    }).catch(() => {
      return caches.match(event.request);
    })
  );
});
