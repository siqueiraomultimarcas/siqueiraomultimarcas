const CACHE = 'siqueirao-v1';

// Recursos estáticos que ficam em cache
const PRECACHE = [
    '/static/logo_nova.png',
    'https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css',
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE)
            .then(cache => cache.addAll(PRECACHE).catch(() => {}))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(
                keys.filter(k => k !== CACHE).map(k => caches.delete(k))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    const url = event.request.url;

    // API: sempre rede (dados em tempo real, sem cache)
    if (url.includes('/api/') || url.includes('/login') || url.includes('/logout')) {
        event.respondWith(fetch(event.request));
        return;
    }

    // Páginas HTML: network-first (sempre dados frescos)
    if (event.request.mode === 'navigate') {
        event.respondWith(
            fetch(event.request)
                .then(resp => {
                    const clone = resp.clone();
                    caches.open(CACHE).then(c => c.put(event.request, clone));
                    return resp;
                })
                .catch(() => caches.match(event.request))
        );
        return;
    }

    // Estáticos (imagens, fontes, CSS externo): cache-first
    event.respondWith(
        caches.match(event.request).then(cached => {
            if (cached) return cached;
            return fetch(event.request).then(resp => {
                if (resp && resp.status === 200 && resp.type !== 'opaque') {
                    const clone = resp.clone();
                    caches.open(CACHE).then(c => c.put(event.request, clone));
                }
                return resp;
            }).catch(() => cached);
        })
    );
});
