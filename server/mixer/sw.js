// Минимальный service worker микшера: нужен только для установки страницы как PWA. Ничего не кэширует,
// поэтому страница всегда свежая. Версия интерфейса — в index.html (UI_VERSION), показывается во вкладке «Ещё».
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
