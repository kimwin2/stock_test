/* ============================================================
   ui.js — 시트 · 토스트 · 검색 · 전체화면 차트 · 빈 상태
   ------------------------------------------------------------
   덮개(overlay)는 셋 다 같은 규칙을 따른다:
     · 열면 body 스크롤 잠금, 닫으면 해제
     · Esc 로 닫힘
     · 닫을 때 열기 전 포커스로 되돌림
     · 겹쳐 열려도 가장 위 것부터 닫힘
   이 규칙을 컴포넌트마다 따로 쓰면 하나는 반드시 빠뜨린다.
   ============================================================ */
(function (global) {
  'use strict';

  var E = global.Core.esc;
  var stack = [];                 // 열린 덮개들 [{el, restore, onClose}]

  function lock() { document.body.classList.add('is-locked'); }
  function unlock() { if (!stack.length) document.body.classList.remove('is-locked'); }

  function open(el, opts) {
    opts = opts || {};
    var restore = document.activeElement;
    el.removeAttribute('hidden');
    stack.push({ el: el, restore: restore, onClose: opts.onClose });
    lock();
    // 첫 포커스 — 스크린리더/키보드 사용자가 덮개 안에서 시작하게 한다
    var first = el.querySelector('[data-autofocus]') || el.querySelector('button, input, [tabindex]');
    if (first) setTimeout(function () { try { first.focus({ preventScroll: true }); } catch (e) {} }, 40);
  }

  function closeTop() {
    var top = stack.pop();
    if (!top) return false;
    top.el.setAttribute('hidden', '');
    unlock();
    if (top.onClose) { try { top.onClose(); } catch (e) {} }
    if (top.restore && document.contains(top.restore)) {
      try { top.restore.focus({ preventScroll: true }); } catch (e) {}
    }
    return true;
  }

  function closeEl(el) {
    var i = stack.findIndex(function (s) { return s.el === el; });
    if (i < 0) { el.setAttribute('hidden', ''); return; }
    // 위에 쌓인 것부터 순서대로 닫는다
    while (stack.length > i) closeTop();
  }

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || !stack.length) return;
    e.preventDefault();
    // 시트가 겹쳐 있으면 Esc 는 '뒤로', 다 비면 그때 닫는다.
    var top = stack[stack.length - 1];
    if (sheetEl && top.el === sheetEl && sheetStack.length) { popSheet(); return; }
    closeTop();
  });

  /* ── 바텀시트 ─────────────────────────────────────────────
     시트 안에서 ⓘ 를 누르면 **새 시트가 겹쳐 열린다.** 예전처럼 내용만
     갈아 끼우면, 용어 설명을 닫는 순간 보고 있던 종목 상세까지 사라진다.
     한 단계 되돌아가는 게 사용자가 기대하는 동작이다. */
  var sheetEl, sheetBody, sheetTitle, sheetBack;
  var sheetStack = [];            // [{title, html, scroll, mounted}]

  function sheetRefs() {
    sheetEl = sheetEl || document.getElementById('sheet');
    sheetBody = sheetBody || document.getElementById('sheet-body');
    sheetTitle = sheetTitle || document.getElementById('sheet-title');
    if (!sheetBack) {
      sheetBack = document.createElement('button');
      sheetBack.type = 'button';
      sheetBack.className = 'sheet-back';
      sheetBack.setAttribute('aria-label', '이전으로');
      sheetBack.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>';
      sheetBack.hidden = true;
      sheetBack.addEventListener('click', popSheet);
      sheetTitle.parentNode.insertBefore(sheetBack, sheetTitle);
    }
  }

  function paintSheet(title, html, mounted) {
    sheetTitle.textContent = title || '';
    sheetBody.innerHTML = html || '';
    sheetBody.scrollTop = 0;
    if (mounted) mounted(sheetBody);
    sheetBack.hidden = sheetStack.length === 0;
  }

  function sheet(title, html, opts) {
    opts = opts || {};
    sheetRefs();
    var alreadyOpen = !sheetEl.hasAttribute('hidden');
    if (alreadyOpen) {
      sheetStack.push({
        title: sheetTitle.textContent,
        html: sheetBody.innerHTML,
        scroll: sheetBody.scrollTop,
        mounted: sheetEl._mounted
      });
    }
    sheetEl._mounted = opts.mounted || null;
    paintSheet(title, html, opts.mounted);
    if (!alreadyOpen) {
      open(sheetEl, { onClose: function () {
        sheetStack.length = 0;
        sheetBack.hidden = true;
        sheetEl._mounted = null;
        if (opts.onClose) opts.onClose();
      } });
    }
    return sheetBody;
  }

  // 한 단계 뒤로. 남은 게 없으면 시트를 닫는다.
  function popSheet() {
    if (!sheetStack.length) { closeSheet(); return; }
    var prev = sheetStack.pop();
    sheetEl._mounted = prev.mounted;
    paintSheet(prev.title, prev.html, prev.mounted);
    sheetBody.scrollTop = prev.scroll || 0;
  }

  function closeSheet() {
    if (!sheetEl) return;
    sheetStack.length = 0;
    closeEl(sheetEl);
  }

  /* ── 용어 시트 ──────────────────────────────────────────── */
  function glossary(key) {
    var g = global.Core.GLOSSARY[key];
    if (!g) return;
    var stacked = sheetEl && !sheetEl.hasAttribute('hidden');
    sheet(g.t,
      '<dl class="gloss">' +
      '<dd>' + g.d + '</dd>' +
      '<div class="g-how"><b>어떻게 읽나</b><br>' + g.how + '</div>' +
      '</dl>' +
      '<button class="btn btn-block" style="margin-top:18px" type="button" ' +
        (stacked ? 'data-sheet-back' : 'data-sheet-close') + '>' +
        (stacked ? '돌아가기' : '닫기') + '</button>');
  }

  // 본문에 붙이는 ⓘ 마크업. 누르면 위 시트가 열린다.
  function info(key) {
    var g = global.Core.GLOSSARY[key];
    if (!g) return '';
    return '<button class="info" type="button" data-gloss="' + E(key) + '" aria-label="' + E(g.t) + ' 설명">i</button>';
  }

  /* ── 토스트 ─────────────────────────────────────────────── */
  var toastEl, toastT;
  function toast(msg) {
    toastEl = toastEl || document.getElementById('toast');
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.removeAttribute('hidden');
    clearTimeout(toastT);
    toastT = setTimeout(function () { toastEl.setAttribute('hidden', ''); }, 1900);
  }

  /* ── 빈 상태 / 에러 / 스켈레톤 ──────────────────────────── */
  function empty(o) {
    o = o || {};
    var icon = o.icon || '<circle cx="12" cy="12" r="9"/><path d="M12 8v4.5M12 16h.01"/>';
    return '<div class="empty">' +
      '<div class="e-ic"><svg viewBox="0 0 24 24">' + icon + '</svg></div>' +
      '<b>' + E(o.title || '표시할 내용이 없습니다') + '</b>' +
      (o.desc ? '<p>' + o.desc + '</p>' : '') +
      (o.action ? '<button class="btn" type="button" ' + o.action.attr + '>' + E(o.action.label) + '</button>' : '') +
      '</div>';
  }

  function errorState(msg, retryFn) {
    var id = 'rt' + Math.random().toString(36).slice(2, 8);
    setTimeout(function () {
      var b = document.getElementById(id);
      if (b && retryFn) b.addEventListener('click', retryFn);
    }, 0);
    return '<div class="empty">' +
      '<div class="e-ic"><svg viewBox="0 0 24 24"><path d="M12 3.8 21 19H3z"/><path d="M12 10v4M12 16.6h.01"/></svg></div>' +
      '<b>데이터를 불러오지 못했습니다</b>' +
      '<p>' + E(msg || '') + '</p>' +
      '<button class="btn btn-primary" type="button" id="' + id + '">다시 시도</button>' +
      '</div>';
  }

  function skeleton(kind) {
    if (kind === 'rows') {
      var r = '';
      for (var i = 0; i < 6; i++) {
        r += '<div class="row" style="pointer-events:none">' +
             '<span class="r-rk"><span class="sk sk-line" style="width:14px"></span></span>' +
             '<span class="r-name"><span class="sk sk-line" style="width:' + (90 + (i % 3) * 26) + 'px"></span></span>' +
             '<span class="r-meta"><span class="sk sk-line" style="width:' + (120 + (i % 2) * 40) + 'px;height:11px"></span></span>' +
             '<span class="r-price"><span class="sk sk-line" style="width:56px"></span></span>' +
             '</div>';
      }
      return '<div class="card"><div class="rows">' + r + '</div></div>';
    }
    return '<div class="stack">' +
      '<div class="sk sk-card" style="height:150px"></div>' +
      '<div class="sk sk-card" style="height:112px"></div>' +
      '<div class="sk sk-card" style="height:190px"></div>' +
      '</div>';
  }

  /* ── 전체화면 검색 ──────────────────────────────────────── */
  var searchEl, searchInput, searchBody, searchClear, searchIdx = null;

  function initSearch() {
    searchEl = document.getElementById('search');
    searchInput = document.getElementById('search-input');
    searchBody = document.getElementById('search-body');
    searchClear = document.getElementById('search-clear');

    document.getElementById('search-back').addEventListener('click', function () { closeEl(searchEl); });
    searchClear.addEventListener('click', function () {
      searchInput.value = '';
      searchClear.setAttribute('hidden', '');
      renderSearch('');
      searchInput.focus();
    });
    searchInput.addEventListener('input', function () {
      var q = searchInput.value.trim();
      if (q) searchClear.removeAttribute('hidden'); else searchClear.setAttribute('hidden', '');
      renderSearch(q);
    });
    searchBody.addEventListener('click', function (e) {
      var row = e.target.closest('[data-code]');
      if (!row) return;
      closeEl(searchEl);
      global.Detail.open(row.dataset.code, row.dataset.name);
    });
  }

  function openSearch() {
    if (!searchEl) initSearch();
    searchInput.value = '';
    searchClear.setAttribute('hidden', '');
    renderSearch('');
    open(searchEl);
    setTimeout(function () { searchInput.focus(); }, 60);
    global.Core.flow().then(function (f) {
      searchIdx = global.Core.stockIndex(f);
      if (!searchInput.value) renderSearch('');
    }).catch(function () {});
  }

  function renderSearch(q) {
    if (!searchIdx) { searchBody.innerHTML = skeleton('rows'); return; }
    if (!q) {
      // 빈 상태를 "검색어를 입력하세요" 로 두면 화면이 죽는다.
      // 오늘 후보를 바로 보여주면 검색이 곧 목록 진입점이 된다.
      var top = searchIdx.filter(function (x) { return x.kind === 'cand'; }).slice(0, 12);
      searchBody.innerHTML = '<div class="sec-h"><h2>오늘 볼 종목</h2><span class="sec-side">' + top.length + '개</span></div>' +
        '<div class="card"><div class="rows">' + top.map(searchRow).join('') + '</div></div>';
      return;
    }
    var nq = q.replace(/\s+/g, '').toLowerCase();
    var hit = searchIdx.filter(function (x) {
      return String(x.name).replace(/\s+/g, '').toLowerCase().indexOf(nq) >= 0 || x.code.indexOf(nq) >= 0;
    }).slice(0, 30);
    if (!hit.length) {
      searchBody.innerHTML = empty({
        title: '"' + E(q) + '" 검색 결과 없음',
        desc: '분석 대상은 코스피·코스닥 시가총액 상위 종목입니다. 그 밖의 종목은 목록에 없습니다.'
      });
      return;
    }
    searchBody.innerHTML = '<div class="card"><div class="rows">' + hit.map(searchRow).join('') + '</div></div>';
  }

  var KIND_TAG = {
    cand: '<span class="tag tag-up">오늘 후보</span>',
    value: '<span class="tag">거래대금 상위</span>',
    overflow: '<span class="tag tag-warn">섹터 상한</span>',
    exit: '<span class="tag tag-down">이탈 신호</span>',
    ti: '<span class="tag">거래대금 강도</span>',
    universe: ''
  };

  function searchRow(x) {
    var c = x.rec || {};
    var ret = c.ret5d;
    return '<button class="row" type="button" data-code="' + E(x.code) + '" data-name="' + E(x.name) + '">' +
      '<span class="r-rk"></span>' +
      '<span class="r-name"><b>' + E(x.name) + '</b>' + (KIND_TAG[x.kind] || '') + '</span>' +
      '<span class="r-meta">' + E(x.sector || '-') + ' · ' + E(x.code) + '</span>' +
      '<span class="r-price">' + (c.close != null ? '<b class="num">' + global.Core.num(c.close) + '</b>' : '') +
      (ret != null ? '<em class="num ' + global.Core.dirClass(ret) + '">5일 ' + global.Core.pct(ret, 1) + '</em>' : '') +
      '</span></button>';
  }

  /* ── 전체화면 캔들 차트 ─────────────────────────────────── */
  var TF = [
    { key: 'minute', label: '3분', count: 400 },
    { key: 'day', label: '일', count: 80 },
    { key: 'week', label: '주', count: 80 },
    { key: 'month', label: '월', count: 60 }
  ];
  var fullEl, fullBody, fullState = { code: null, name: null, tf: 'day', cache: {}, token: 0 };

  function initFull() {
    fullEl = document.getElementById('fullchart');
    fullBody = document.getElementById('full-body');
    document.getElementById('full-back').addEventListener('click', function () { closeEl(fullEl); });
    var seg = document.getElementById('full-tf');
    seg.innerHTML = TF.map(function (t) {
      return '<button type="button" data-tf="' + t.key + '"' + (t.key === 'day' ? ' class="is-on"' : '') + '>' + t.label + '</button>';
    }).join('');
    seg.addEventListener('click', function (e) {
      var b = e.target.closest('[data-tf]');
      if (!b) return;
      seg.querySelectorAll('button').forEach(function (x) { x.classList.toggle('is-on', x === b); });
      fullState.tf = b.dataset.tf;
      loadFull();
    });
  }

  function openFull(code, name) {
    if (!fullEl) initFull();
    fullState.code = String(code);
    fullState.name = name || '';
    fullState.tf = 'day';
    fullState.cache = {};
    document.getElementById('full-name').textContent = name || code;
    document.getElementById('full-code').textContent = code;
    document.querySelectorAll('#full-tf button').forEach(function (x) {
      x.classList.toggle('is-on', x.dataset.tf === 'day');
    });
    open(fullEl);
    loadFull();
  }

  function apiUrl() {
    if (global.STOCK_CHART_API_URL) return global.STOCK_CHART_API_URL;
    var h = global.location.hostname;
    var prod = h.indexOf('github.io') >= 0 || h.indexOf('stock') >= 0;
    return prod ? null : 'http://127.0.0.1:8081/chart';
  }

  // 네이버는 1분봉만 준다 → 프론트에서 3분봉으로 묶는다 (기존 화면과 같은 규칙)
  function agg3(cs) {
    var out = [], b = null, k = 0;
    for (var i = 0; i < cs.length; i++) {
      var c = cs[i];
      if (k === 0) { b = { t: c.t, o: c.o, h: c.h, l: c.l, c: c.c, v: c.v || 0 }; }
      else {
        if (c.h != null && (b.h == null || c.h > b.h)) b.h = c.h;
        if (c.l != null && (b.l == null || c.l < b.l)) b.l = c.l;
        b.c = c.c; b.v += c.v || 0;
      }
      k++;
      if (k === 3) { out.push(b); k = 0; }
    }
    if (k > 0 && b) out.push(b);
    return out;
  }

  function loadFull() {
    var key = fullState.code + ':' + fullState.tf;
    if (fullState.cache[key]) return paintFull(fullState.cache[key]);
    var url = apiUrl();
    if (!url) {
      fullBody.innerHTML = empty({
        title: '차트 서버가 연결되어 있지 않습니다',
        desc: 'index.html 의 <code>window.STOCK_CHART_API_URL</code> 을 확인해 주세요.'
      });
      return;
    }
    var myToken = ++fullState.token;
    fullBody.innerHTML = '<div class="sk" style="height:60vh;border-radius:12px"></div>';
    var tf = TF.find(function (t) { return t.key === fullState.tf; }) || TF[1];
    var qs = new URLSearchParams({ code: fullState.code, timeframe: tf.key, count: String(tf.count) });
    fetch(url + '?' + qs, { mode: 'cors' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (j) {
        if (myToken !== fullState.token) return;
        if (tf.key === 'minute' && Array.isArray(j.candles)) j.candles = agg3(j.candles);
        fullState.cache[key] = j;
        paintFull(j);
      })
      .catch(function (err) {
        if (myToken !== fullState.token) return;
        fullBody.innerHTML = errorState(err.message, loadFull);
      });
  }

  function paintFull(data) {
    var cs = data.candles || [];
    if (!cs.length) { fullBody.innerHTML = empty({ title: '이 기간의 캔들이 없습니다' }); return; }
    var last = cs[cs.length - 1], first = cs[0];
    var chg = first && first.c ? (last.c - first.c) / first.c * 100 : null;
    var isMin = fullState.tf === 'minute';
    var labelAt = function (c) {
      var t = String(c.t || '');
      if (isMin) return t.length >= 12 ? t.slice(8, 10) + ':' + t.slice(10, 12) : t;
      if (t.length >= 8) return (+t.slice(4, 6)) + '/' + (+t.slice(6, 8));
      return t;
    };
    fullBody.innerHTML =
      '<div class="card card-pad" style="margin-bottom:12px">' +
        '<div style="display:flex;align-items:baseline;gap:10px">' +
          '<span class="d-px num">' + global.Core.num(last.c) + '</span>' +
          '<span class="d-chg num ' + global.Core.dirClass(chg) + '">' + global.Core.pct(chg) + '</span>' +
          '<span style="margin-left:auto;font-size:.8125rem;color:var(--ink-4)">' + cs.length + '개 구간</span>' +
        '</div>' +
      '</div>' +
      '<div class="card card-pad"><div id="full-canvas"></div>' +
      '<div class="legend"><span><i style="background:var(--c-ma5)"></i>5</span>' +
      '<span><i style="background:var(--c-ma20)"></i>20</span>' +
      '<span><i style="background:var(--up)"></i>양봉</span>' +
      '<span><i style="background:var(--down)"></i>음봉</span></div></div>';
    global.Viz.mount(document.getElementById('full-canvas'), function (w) {
      return global.Viz.candles(cs, w, { labelAt: labelAt, height: Math.round(Math.min(560, Math.max(300, global.innerHeight * 0.55))) });
    });
  }

  /* ── 전역 위임: ⓘ · 시트 닫기 ──────────────────────────── */
  document.addEventListener('click', function (e) {
    var g = e.target.closest('[data-gloss]');
    if (g) { e.preventDefault(); e.stopPropagation(); glossary(g.dataset.gloss); return; }
    if (e.target.closest('[data-sheet-back]')) { e.preventDefault(); popSheet(); return; }
    if (e.target.closest('[data-sheet-close]')) { e.preventDefault(); closeSheet(); }
  });

  global.UI = {
    sheet: sheet, closeSheet: closeSheet, glossary: glossary, info: info,
    toast: toast, empty: empty, errorState: errorState, skeleton: skeleton,
    openSearch: openSearch, openFull: openFull, closeTop: closeTop
  };
})(window);
