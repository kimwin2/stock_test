/* ============================================================
   app.js — 라우팅 · 테마 · 부팅
   ============================================================ */
(function (global) {
  'use strict';

  var C = global.Core;

  var ROUTES = {
    home:   { title: '오늘의 장', view: function (r, o) { global.HomeView.view(r, o); } },
    stocks: { title: '종목',       view: function (r, o) { global.StocksView.view(r, o); } },
    themes: { title: '테마',       view: function (r, o) { global.ThemesView.view(r, o); } },
    watch:  { title: '관심',       view: function (r, o) { global.WatchView.view(r, o); } }
  };

  var current = null;
  var scrollMemo = {};
  var viewEl, titleEl;

  function go(route, opts) {
    if (!ROUTES[route]) route = 'home';
    if (current) scrollMemo[current] = global.scrollY;

    current = route;
    document.querySelectorAll('.tabbtn').forEach(function (b) {
      var on = b.dataset.route === route;
      b.classList.toggle('is-on', on);
      b.setAttribute('aria-current', on ? 'page' : 'false');
    });
    titleEl.textContent = ROUTES[route].title;

    // 그림 조각은 화면마다 새로 등록된다. 지난 화면 것을 남겨두면
    // 리사이즈 때마다 사라진 DOM 을 계속 다시 그리려 든다.
    global.Viz.clearMounts();

    // 화면마다 **새 컨테이너**를 만들어 넘긴다.
    // #view 하나를 계속 재활용하면서 각 화면이 거기에 click 리스너를 붙이면,
    // 탭을 오갈 때마다 리스너가 쌓여 한 번의 탭이 여러 번 처리된다.
    // 컨테이너를 갈아 끼우면 리스너가 DOM 과 함께 사라진다.
    viewEl.innerHTML = '';
    var host = document.createElement('div');
    viewEl.appendChild(host);
    ROUTES[route].view(host, opts || {});

    // 탭으로 이동하면 위에서 시작. 뒤로가기 성격의 복귀만 위치를 되살린다.
    if (opts && opts.restore && scrollMemo[route] != null) {
      var y = scrollMemo[route];
      requestAnimationFrame(function () { global.scrollTo(0, y); });
    } else {
      global.scrollTo(0, 0);
    }

    try {
      // 탭 이동은 히스토리 항목을 늘리지 않는다 — 뒤로가기는 '덮개 닫기'로
      // 예약돼 있다(ui.js). state 를 그대로 넘겨야 열려 있는 덮개의 깊이가
      // 지워지지 않는다.
      var q = route === 'home' ? '#/' : '#/' + route;
      if (global.location.hash !== q) history.replaceState(history.state, '', q);
    } catch (e) {}
  }

  /* ── 테마(밝게/어둡게) ─────────────────────────────────── */
  var TKEY = 'next.theme';
  function applyTheme(v) {
    if (v === 'light' || v === 'dark') document.documentElement.setAttribute('data-theme', v);
    else document.documentElement.removeAttribute('data-theme');
    global.Viz.dropTok();
    global.Viz.redrawAll(true);
  }
  function currentTheme() {
    var v = document.documentElement.getAttribute('data-theme');
    if (v) return v;
    return (global.matchMedia && global.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }
  function toggleTheme() {
    var next = currentTheme() === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem(TKEY, next); } catch (e) {}
    applyTheme(next);
    global.UI.toast(next === 'dark' ? '어둡게' : '밝게');
  }

  /* ── 관심 배지 ─────────────────────────────────────────── */
  function refreshWatchDot() {
    var dot = document.getElementById('watch-dot');
    if (!dot) return;
    if (C.watchAll().length) dot.removeAttribute('hidden');
    else dot.setAttribute('hidden', '');
  }

  /* ── 갱신 시각 · 다시 받기 ─────────────────────────────── */
  function refreshStamp() {
    var btn = document.getElementById('tb-stamp');
    var txt = document.getElementById('tb-stamp-txt');
    if (!btn || !txt) return;
    C.flow().then(function (f) {
      txt.textContent = C.ago(f.updatedAt);
      btn.title = '데이터 기준 ' + new Date(f.updatedAt).toLocaleString('ko-KR') + ' · 눌러서 다시 받기';
    }).catch(function () { txt.textContent = '실패'; btn.title = '눌러서 다시 받기'; });
  }

  var reloading = false;
  function reloadData() {
    if (reloading) return;
    reloading = true;
    var btn = document.getElementById('tb-stamp');
    if (btn) btn.classList.add('is-spin');
    C.reload();
    var here = current;
    go(here, { restore: true });    // 새 데이터로 다시 그리되 보던 위치는 지킨다
    C.flow().then(function () { global.UI.toast('갱신 완료'); })
      .catch(function () { global.UI.toast('갱신 실패'); })
      .then(function () {
        reloading = false;
        if (btn) btn.classList.remove('is-spin');
        refreshStamp();
      });
  }

  /* ── 부팅 ──────────────────────────────────────────────── */
  function boot() {
    viewEl = document.getElementById('view');
    titleEl = document.getElementById('tb-title');

    try {
      var saved = localStorage.getItem(TKEY);
      if (saved) applyTheme(saved);
    } catch (e) {}

    document.getElementById('btn-theme').addEventListener('click', toggleTheme);
    document.getElementById('btn-search').addEventListener('click', function () { global.UI.openSearch(); });
    document.getElementById('btn-home').addEventListener('click', function () { go('home'); });
    document.getElementById('tb-stamp').addEventListener('click', reloadData);

    document.getElementById('tabbar').addEventListener('click', function (e) {
      var b = e.target.closest('[data-route]');
      if (!b) return;
      if (b.dataset.route === current) { global.scrollTo({ top: 0, behavior: 'smooth' }); return; }
      go(b.dataset.route);
    });

    global.addEventListener('hashchange', function () {
      var r = (global.location.hash || '').replace(/^#\/?/, '') || 'home';
      if (r !== current) go(r, { restore: true });
    });

    refreshWatchDot();
    refreshStamp();

    var start = (global.location.hash || '').replace(/^#\/?/, '') || 'home';
    go(ROUTES[start] ? start : 'home');

    // 갱신 시각은 흘러간다. 1분마다 문구만 다시 쓴다(재요청 아님).
    setInterval(refreshStamp, 60000);
  }

  global.App = { go: go, refreshWatchDot: refreshWatchDot, refreshStamp: refreshStamp, reload: reloadData };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})(window);
