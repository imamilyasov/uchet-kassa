/* Сервис-воркер: кэширует оболочку приложения, чтобы оно открывалось без интернета. */
const CACHE_NAME = "bazar-shell-v2";
const SHELL_URLS = ["/", "/login", "/manifest.json"];

/* Сохраняем в кэш только настоящую страницу приложения,
   а не заглушки хостинга вида «502 Bad Gateway» или «Service Unavailable». */
async function putIfRealPage(cache, request, response) {
  try {
    if (!response || !response.ok) return false;
    const type = response.headers.get("content-type") || "";
    if (!type.includes("text/html")) return false;
    const text = await response.clone().text();
    if (!text.includes("Базар") && !text.includes("bazar")) return false;
    const headers = new Headers(response.headers);
    await cache.put(
      request,
      new Response(text, { status: response.status, statusText: response.statusText, headers })
    );
    return true;
  } catch (err) {
    return false;
  }
}

async function cacheShell() {
  const cache = await caches.open(CACHE_NAME);
  await Promise.all(
    SHELL_URLS.map(async (url) => {
      try {
        const response = await fetch(url, { cache: "reload" });
        if (url === "/" || url === "/login") {
          await putIfRealPage(cache, url, response);
        } else if (response.ok) {
          await cache.put(url, response);
        }
      } catch (err) {
        /* нет сети — оставляем что есть */
      }
    })
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(cacheShell().then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;

  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // API всегда идёт в сеть — офлайн-данные хранит и синхронизирует сама страница.
  if (url.pathname.startsWith("/reports") || url.pathname.startsWith("/auth/")) return;

  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const response = await fetch(request);
          if (response && response.ok) {
            const cache = await caches.open(CACHE_NAME);
            await putIfRealPage(cache, request, response);
            return response;
          }
          // Сервер ответил ошибкой — показываем сохранённую копию, если она есть.
          const cached = (await caches.match(request)) || (await caches.match("/")) || (await caches.match("/login"));
          return cached || response;
        } catch (err) {
          const cached = (await caches.match(request)) || (await caches.match("/")) || (await caches.match("/login"));
          if (cached) return cached;
          return new Response("Нет интернета. Откройте приложение, когда появится связь.", {
            status: 503,
            headers: { "Content-Type": "text/plain; charset=utf-8" }
          });
        }
      })()
    );
    return;
  }

  event.respondWith(
    (async () => {
      const cached = await caches.match(request);
      if (cached) return cached;
      try {
        const response = await fetch(request);
        if (response && response.ok) {
          const cache = await caches.open(CACHE_NAME);
          cache.put(request, response.clone()).catch(() => {});
        }
        return response;
      } catch (err) {
        return new Response("Нет интернета", {
          status: 503,
          headers: { "Content-Type": "text/plain; charset=utf-8" }
        });
      }
    })()
  );
});

// Позволяет странице попросить воркер обновить кэш сразу после входа.
self.addEventListener("message", (event) => {
  if (event.data === "refresh-shell") {
    event.waitUntil(cacheShell());
  }
});
