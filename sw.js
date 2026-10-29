/* سرویس‌ورکر سرخط‌زن — کش استاتیک برای کار آفلاین؛ داده زنده هرگز کش نمی‌شود */
const CACHE = "sarkhat-v3";
const ASSETS = ["./", "./index.html", "./app.css", "./app.js", "./btc.js", "./manifest.webmanifest", "./icons/icon-192.png", "./icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // داده زنده (API سرور یا دیتای ابری): هرگز کش نشود
  if (url.pathname.startsWith("/api/") || url.pathname.includes("/data/")) return;
  // استاتیک: اول کش، بعد شبکه
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).then((resp) => {
      const copy = resp.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
      return resp;
    }).catch(() => caches.match("./index.html")))
  );
});
