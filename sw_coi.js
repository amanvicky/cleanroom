/* The in-tab VM's multi-core enabler.

   Browsers only hand out SharedArrayBuffer (Web Workers sharing memory —
   Pyodide's pthread pool, numpy's OpenBLAS threads) to pages that are
   cross-origin isolated, which requires COOP/COEP response headers.
   GitHub Pages cannot set response headers — so this service worker adds
   them to this page's own responses. Nothing is cached or proxied: the
   request goes to the network as usual, the response just comes back
   wearing the two headers the browser demands.

   Scope: same-origin responses under this worker's scope only
   (the cleanroom page files). Cross-origin fetches (the Pyodide CDN,
   the GitHub API) pass through untouched — they already serve CORS
   headers, which satisfies COEP's requirement. */

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", () => self.clients.claim());

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  if (!req.url.startsWith(self.registration.scope)) return;
  event.respondWith((async () => {
    try {
      const res = await fetch(req);
      if (!res || res.status !== 200 || res.type !== "basic") return res;
      const headers = new Headers(res.headers);
      headers.set("Cross-Origin-Opener-Policy", "same-origin");
      // credentialless, not require-corp: require-corp would block the
      // this-PC mode's app-screen iframe (a real server you serve can't be
      // asked to send CORP headers). credentialless still grants
      // SharedArrayBuffer isolation in Chrome/Firefox; browsers without it
      // simply fall back to the honest single-thread path.
      headers.set("Cross-Origin-Embedder-Policy", "credentialless");
      headers.set("Cross-Origin-Resource-Policy", "same-origin");
      return new Response(await res.arrayBuffer(),
                          { status: res.status, statusText: res.statusText, headers });
    } catch (e) {
      return fetch(req);
    }
  })());
});
