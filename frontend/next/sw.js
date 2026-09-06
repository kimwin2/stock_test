/* ============================================================
   sw.js — 오프라인 대비
   ------------------------------------------------------------
   원칙: 온라인이면 항상 네트워크(최신). 캐시는 네트워크가 죽었을 때만
   꺼낸다. 캐시 우선으로 두면 배포 뒤에도 옛 화면이 며칠 남는다.
   데이터(S3 JSON)는 마지막으로 성공한 응답을 보관해 지하철·장외에서도
   직전 장의 화면이 열리게 한다. 캐시에서 꺼낸 응답에는
   X-Served-From: cache 를 붙여 화면이 '저장본' 임을 밝힌다.
   ============================================================ */
var VERSION = 'next-v1';
var SHELL = [
  './', './index.html', './app.css', './manifest.webmanifest',
  './js/core.js', './js/viz.js', './js/supply.js', './js/ui.js', './js/detail.js',
  './js/home.js', './js/stocks.js', './js/themes.js', './js/watch.js', './js/app.js',
  './assets/favicon.svg', './assets/icon-192.png', './assets/icon-512.png'
];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(VERSION)
      .then(function (c) { return c.addAll(SHELL); })
      .catch(function () { /* 일부 실패해도 설치는 진행. 런타임 캐시가 채운다 */ })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== VERSION; })
        .map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

// 캐시 키에서 t=… 같은 캐시버스터를 뗀다. 안 떼면 요청마다 새 키가 생겨
// 절대 매칭이 안 되고 캐시만 불어난다.
function keyOf(req) {
  var u = new URL(req.url);
  u.search = '';
  u.hash = '';
  return u.toString();
}

function isData(url) { return /\.s3\.[^/]+\.amazonaws\.com\//.test(url) || /\/(dashboard_data|flow_dashboard)\.json$/.test(url.split('?')[0]); }
function isShell(url) { return url.indexOf(self.registration.scope) === 0; }
function isFont(url) { return url.indexOf('https://cdn.jsdelivr.net/') === 0; }

function markCached(res) {
  if (!res || res.type === 'opaque') return res;
  var h = new Headers(res.headers);
  h.set('X-Served-From', 'cache');
  return res.blob().then(function (b) {
    return new Response(b, { status: res.status, statusText: res.statusText, headers: h });
  });
}

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = req.url;

  // 서체·CSS 는 버전이 URL 에 박혀 있어 캐시 우선이 안전하다.
  if (isFont(url)) {
    e.respondWith(caches.open(VERSION).then(function (c) {
      return c.match(req).then(function (hit) {
        if (hit) return hit;
        return fetch(req).then(function (res) {
          if (res && res.ok) c.put(req, res.clone());
          return res;
        });
      });
    }));
    return;
  }

  if (!isData(url) && !isShell(url)) return;

  e.respondWith(
    fetch(req).then(function (res) {
      if (res && res.ok && res.type !== 'opaque') {
        var copy = res.clone();
        e.waitUntil(caches.open(VERSION).then(function (c) { return c.put(new Request(keyOf(req)), copy); }));
      }
      return res;
    }).catch(function () {
      return caches.open(VERSION).then(function (c) {
        return c.match(keyOf(req)).then(function (hit) {
          if (!hit && req.mode === 'navigate') return c.match('./index.html');
          return hit;
        });
      }).then(function (hit) {
        if (!hit) return new Response('', { status: 504, statusText: 'offline' });
        return isData(url) ? markCached(hit) : hit;
      });
    })
  );
});
