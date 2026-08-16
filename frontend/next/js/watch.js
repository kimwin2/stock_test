/* ============================================================
   watch.js — '관심' 화면
   ------------------------------------------------------------
   왜 새로 두는가: 지금까지는 "오늘 뽑힌 20개" 를 매일 새로 읽어야 했고,
   어제 본 종목을 다시 찾을 방법이 없었다. 서버 없이 localStorage 로
   되는 기능인데 매일 여는 이유를 만든다.

   담아둔 종목이 오늘 다시 후보에 들었는지(★재진입)를 표시하는 게 핵심이다.
   그게 없으면 그냥 이름 목록이라 다시 안 열게 된다.
   ============================================================ */
(function (global) {
  'use strict';

  var C = global.Core, E = C.esc, UI = global.UI;

  function view(root) {
    var saved = C.watchAll();
    if (!saved.length) {
      root.innerHTML = UI.empty({
        title: '담아둔 종목이 없습니다',
        desc: '종목을 눌러 상세를 열고 <b>관심 담기</b>를 누르면 여기에 모입니다.<br>' +
              '다음 날 그 종목이 후보에 다시 들면 표시해 드립니다.',
        icon: '<path d="M12 4.8l2.3 4.7 5.2.8-3.75 3.65.9 5.15L12 16.65 7.35 19.1l.9-5.15L4.5 10.3l5.2-.8z"/>',
        action: { label: '오늘 볼 종목 보기', attr: 'data-go="stocks"' }
      });
      root.addEventListener('click', onClick);
      return;
    }

    root.innerHTML = UI.skeleton('rows');
    C.flow()
      .then(function (f) { paint(root, f, saved); })
      .catch(function (e) {
        // flow 를 못 받아도 담아둔 목록 자체는 보여준다.
        paint(root, null, saved, e.message);
      });
  }

  function paint(root, f, saved, err) {
    var rows = saved.map(function (w) {
      var hit = f ? C.findStock(f, w.code) : null;
      var c = hit ? hit.rec : {};
      var isCand = hit && hit.kind === 'cand';
      var isExit = hit && hit.kind === 'exit';
      var ss = (c && c.dailyFlow10d) ? C.supplyState(c) : null;

      return '<div class="row row-x" role="button" tabindex="0" data-code="' + E(w.code) +
        '" data-name="' + E(w.name || (c && c.name) || '') + '">' +
        '<span class="r-name"><b>' + E(w.name || (c && c.name) || w.code) + '</b>' +
          (isCand ? '<span class="tag tag-up">오늘 후보</span>' : '') +
          (isExit ? '<span class="tag tag-down">이탈</span>' : '') + '</span>' +
        '<span class="r-meta">' + E((c && c.sector) || (hit && hit.sector) || '—') +
          (ss ? ' · ' + E(ss.label) : '') +
          ' · ' + E(day(w.at)) + ' 담음</span>' +
        '<span class="r-price">' +
          (c && c.close != null ? '<b class="num">' + C.num(c.close) + '</b>' : '<b style="color:var(--ink-4)">—</b>') +
          (c && c.ret5d != null ? '<em class="num ' + C.dirClass(c.ret5d) + '">' + C.pct(c.ret5d, 1) + '</em>' : '') +
        '</span>' +
        '<button class="wx" type="button" data-remove="' + E(w.code) + '" aria-label="' +
          E(w.name || w.code) + ' 관심에서 빼기">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
          '<path d="M6 6l12 12M18 6L6 18"/></svg></button>' +
        '</div>';
    }).join('');

    var again = f ? saved.filter(function (w) {
      var h = C.findStock(f, w.code);
      return h && h.kind === 'cand';
    }).length : 0;

    root.innerHTML =
      (err ? '<div class="notice" style="margin-bottom:12px">' +
        '<svg viewBox="0 0 24 24"><path d="M12 3.8 21 19H3z"/><path d="M12 10v4M12 16.6h.01"/></svg>' +
        '<span>오늘 데이터를 못 받아 시세·상태를 채우지 못했습니다. (' + E(err) + ')</span></div>' : '') +
      '<div class="sec-h"><h2>담아둔 종목</h2>' +
        '<span class="sec-side">' + saved.length + '개' +
        (again ? ' · 오늘 후보 재진입 ' + again : '') + '</span></div>' +
      '<section class="card"><div class="rows">' + rows + '</div></section>' +
      '<button class="btn btn-ghost btn-block" type="button" id="w-clear" style="margin-top:12px">전체 비우기</button>';

    root.addEventListener('click', onClick);
    // 행이 <div role="button"> 이라 키보드 활성화를 직접 붙여야 한다.
    // tabindex 만 주고 끝내면 포커스는 가는데 눌리지 않는 함정이 된다.
    root.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var row = e.target.closest('.row-x');
      if (!row || e.target.closest('[data-remove]')) return;
      e.preventDefault();
      global.Detail.open(row.dataset.code, row.dataset.name);
    });
    var cl = root.querySelector('#w-clear');
    if (cl) cl.addEventListener('click', function () {
      confirmClear(saved.length);
    });
  }

  function onClick(e) {
    var go = e.target.closest('[data-go]');
    if (go) { global.App.go(go.dataset.go); return; }

    var rm = e.target.closest('[data-remove]');
    if (rm) {
      e.stopPropagation();
      var code = rm.dataset.remove;
      var name = (rm.closest('[data-code]') || {}).dataset ? rm.closest('[data-code]').dataset.name : '';
      C.watchToggle(code, name);
      UI.toast('관심에서 뺐습니다');
      global.App.refreshWatchDot();
      global.App.go('watch');
      return;
    }

    var row = e.target.closest('[data-code]');
    if (row) global.Detail.open(row.dataset.code, row.dataset.name);
  }

  // 전체 삭제는 되돌릴 수 없다. 한 번 묻는다.
  function confirmClear(n) {
    UI.sheet('관심 종목 전체 비우기',
      '<p style="font-size:.9375rem;color:var(--ink-2)">담아둔 <b>' + n + '개</b>를 모두 지웁니다. ' +
      '이 기기에만 저장된 목록이라 되돌릴 수 없습니다.</p>' +
      '<div class="d-acts" style="margin-top:18px">' +
        '<button class="btn" type="button" data-sheet-close>취소</button>' +
        '<button class="btn btn-primary" type="button" id="w-yes" style="background:var(--up);color:#fff">전체 삭제</button>' +
      '</div>',
      { mounted: function (body) {
          body.querySelector('#w-yes').addEventListener('click', function () {
            C.watchAll().forEach(function (w) { C.watchToggle(w.code); });
            UI.closeSheet();
            UI.toast('전체 삭제했습니다');
            global.App.refreshWatchDot();
            global.App.go('watch');
          });
        } });
  }

  function day(ts) {
    if (!ts) return '-';
    var d = new Date(ts);
    return (d.getMonth() + 1) + '/' + d.getDate();
  }

  global.WatchView = { view: view };
})(window);
