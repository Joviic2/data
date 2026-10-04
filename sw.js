const C="ela-v1";
self.addEventListener("install",e=>{e.waitUntil(caches.open(C).then(c=>c.addAll(["./","index.html","manifest.webmanifest"]).catch(()=>{})).then(()=>self.skipWaiting()))});
self.addEventListener("activate",e=>e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!=C).map(x=>caches.delete(x)))).then(()=>self.clients.claim())));
self.addEventListener("fetch",e=>{if(e.request.method!="GET")return;const u=new URL(e.request.url);
 if(u.origin!=location.origin&&!u.hostname.endsWith("githubusercontent.com"))return;const k=u.origin+u.pathname;
 e.respondWith(fetch(e.request).then(r=>{const cp=r.clone();caches.open(C).then(c=>c.put(k,cp));return r}).catch(()=>caches.match(k)))});
