// Минимальный service worker микшера: нужен только для установки страницы как PWA. Ничего не кэширует.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
