/* ============================================================
   stocks.js — '종목' 화면
   ------------------------------------------------------------
   기존 화면은 이 내용을 '정밀 분석 펼치기' <details> 안에 숨겨 뒀다.
   숨긴 게 아니라 자리를 못 찾은 것이다 — 후보·거래대금·이탈은
   성격이 다른 세 목록이지 '고급 기능'이 아니다. 세그먼트로 나란히
   세우면 셋 다 한 번의 탭으로 닿는다.
   ============================================================ */
(function (global) {
  'use strict';

  var C = global.Core, E = C.esc, UI = global.UI;

  var state = { seg: 'cand', sector: null, sort: 'rank' };

  var SEGS = [
    { key: 'cand',  label: '후보' },
    { key: 'value', label: '거래대금' },
    { key: 'exit',  label: '이탈' }
  ];
  var SORTS = [
    { key: 'rank',  label: '기본순' },
    { key: 'depth', label: '큰손 많이 빠진 순' },
    { key: 'ret',   label: '5일 등락순' }
  ];

  function view(root, opts) {
    opts = opts || {};
    state.sector = opts.sector || null;
    if (opts.sector) state.seg = 'cand';
    root.innerHTML = UI.skeleton('rows');
    C.flow()
      .then(function (f) { paint(root, f); })
      .catch(function (e) {
        root.innerHTML = UI.errorState(e.message, function () { C.reload(); view(root, opts); });
      });
  }

  function paint(root, f) {
    root.innerHTML =
      '<div class="seg" id="st-seg" role="group" aria-label="목록 종류">' +
        SEGS.map(function (s) {
          var n = count(f, s.key);
          return '<button type="button" data-seg="' + s.key + '"' +
            (s.key === state.seg ? ' class="is-on" aria-pressed="true"' : ' aria-pressed="false"') + '>' +
            s.label + (n ? ' <span class="num" style="opacity:.6">' + n + '</span>' : '') + '</button>';
        }).join('') +
      '</div>' +
      '<div id="st-body" style="margin-top:12px"></div>';

    root.querySelector('#st-seg').addEventListener('click', function (e) {
      var b = e.target.closest('[data-seg]');
      if (!b || b.dataset.seg === state.seg) return;
      state.seg = b.dataset.seg;
      state.sector = null;
      root.querySelectorAll('#st-seg button').forEach(function (x) {
        var on = x.dataset.seg === state.seg;
        x.classList.toggle('is-on', on);
        x.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      body(root, f);
      root.scrollIntoView({ block: 'start' });
    });

    root.addEventListener('click', function (e) {
      var ch = e.target.closest('[data-sector-filter]');
      if (ch) {
        var v = ch.dataset.sectorFilter;
        state.sector = v === '__all' ? null : v;
        body(root, f);
        return;
      }
      var so = e.target.closest('[data-sort]');
      if (so) { state.sort = so.dataset.sort; body(root, f); return; }
      var st = e.target.closest('[data-code]');
      if (st) global.Detail.open(st.dataset.code, st.dataset.name);
    });

    body(root, f);
  }

  function count(f, seg) {
    if (seg === 'cand') return (f.buyCandidates || []).length;
    if (seg === 'value') return (f.leadingValueTop || []).length;
    if (seg === 'exit') return (f.exitSignals || []).length;
    return 0;
  }

  function body(root, f) {
    var el = root.querySelector('#st-body');
    if (state.seg === 'cand') el.innerHTML = candView(f);
    else if (state.seg === 'value') el.innerHTML = valueView(f);
    else el.innerHTML = exitView(f);
  }

  /* ── 후보 ───────────────────────────────────────────────── */
  function candView(f) {
    var all = f.buyCandidates || [];
    if (!all.length) {
      return UI.empty({
        title: '조건 통과 종목 없음',
        desc: '수급 빠짐 · 추세 생존 · 주도 업종 세 조건을 모두 충족한 종목이 없음. 억지로 채우지 않음.'
      });
    }

    var sectors = [];
    all.forEach(function (c) { if (c.sector && sectors.indexOf(c.sector) < 0) sectors.push(c.sector); });

    var list = all.filter(function (c) { return !state.sector || c.sector === state.sector; });
    list = sortList(list);

    var head =
      '<div class="chiprow">' +
        '<button class="chip' + (!state.sector ? ' is-on' : '') + '" type="button" aria-pressed="' +
          (!state.sector) + '" data-sector-filter="__all">전체 <em>' + all.length + '</em></button>' +
        sectors.map(function (s) {
          var n = all.filter(function (c) { return c.sector === s; }).length;
          return '<button class="chip' + (state.sector === s ? ' is-on' : '') + '" type="button" aria-pressed="' +
            (state.sector === s) + '" data-sector-filter="' + E(s) + '">' + E(s) + ' <em>' + n + '</em></button>';
        }).join('') +
      '</div>' +
      '<div class="chiprow" style="padding-top:0">' +
        SORTS.map(function (s) {
          return '<button class="chip' + (state.sort === s.key ? ' is-on' : '') + '" type="button" aria-pressed="' +
            (state.sort === s.key) + '" data-sort="' + s.key + '">' + s.label + '</button>';
        }).join('') +
      '</div>';

    var note = '<p style="font-size:.8125rem;color:var(--ink-3);padding:2px 2px 10px">' +
      '외인·기관 <b>매수 둔화</b> + 10일선 위 추세 유지 + 오늘 주도 업종 소속 종목.' +
      UI.info('vacancy') + '</p>';

    return head + note +
      '<section class="card"><div class="rows">' +
        (list.length ? list.map(function (c, i) { return row(c, i); }).join('')
                     : '<div style="background:var(--surface)">' + UI.empty({ title: '해당 업종 후보 없음' }) + '</div>') +
      '</div></section>' +
      overflowFold(f);
  }

  function sortList(list) {
    var a = list.slice();
    if (state.sort === 'depth') {
      a.sort(function (x, y) {
        var xa = x.oscPercentile == null ? 999 : x.oscPercentile;
        var ya = y.oscPercentile == null ? 999 : y.oscPercentile;
        return xa - ya;
      });
    } else if (state.sort === 'ret') {
      a.sort(function (x, y) { return (y.ret5d || -999) - (x.ret5d || -999); });
    }
    return a;
  }

  function row(c, i) {
    var ss = C.supplyState(c);
    var d = c.oscPercentile;
    return '<button class="row" type="button" data-code="' + E(c.code) + '" data-name="' + E(c.name) + '">' +
      '<span class="r-rk num">' + (i + 1) + '</span>' +
      '<span class="r-name"><b>' + E(c.name) + '</b>' +
        (c.newHigh50d ? '<span class="tag tag-up">신고가</span>' : '') + '</span>' +
      '<span class="r-meta">' + E(c.sector || '-') +
        (d != null ? '<span class="depth"><i><b style="width:' + Math.max(6, 100 - d) + '%"></b></i>하위 ' +
          Math.round(d) + '%</span>' : '') +
        '<span class="tag">' + E(ss.label) + '</span></span>' +
      '<span class="r-price"><b class="num">' + C.num(c.close) + '</b>' +
        (c.ret5d != null ? '<em class="num ' + C.dirClass(c.ret5d) + '">' + C.pct(c.ret5d, 1) + '</em>' : '') +
      '</span></button>';
  }

  /* 섹터 상한에 걸린 종목 — 숨기면 "우리가 못 봤다"로 오해된다.
     목록을 좁게 유지하는 규율은 지키되, 잘린 사실은 드러낸다. */
  function overflowFold(f) {
    var ov = f.overflowCandidates || [];
    if (!ov.length) return '';
    var bySec = {};
    ov.forEach(function (o) { (bySec[o.sector || '기타'] = bySec[o.sector || '기타'] || []).push(o); });
    return '<details class="fold" style="margin-top:12px">' +
      '<summary>업종 상한 제외 종목<span class="cnt">' + ov.length + '개</span></summary>' +
      '<div class="fold-body flush">' +
      '<p style="padding:12px 16px 4px;font-size:.8125rem;color:var(--ink-3)">' +
        '조건 통과했으나 업종별 상한 초과로 제외. 제외 종목이 많은 업종 = 그만큼 강한 업종.' +
        UI.info('overflow') + '</p>' +
      Object.keys(bySec).map(function (sec) {
        return '<div style="padding:10px 16px;border-top:1px solid var(--line)">' +
          '<b style="font-size:.875rem">' + E(sec) + ' <span class="num" style="color:var(--ink-4)">' + bySec[sec].length + '</span></b>' +
          '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">' +
          bySec[sec].map(function (o) {
            return '<button class="tag tag-tap" type="button" ' +
              'data-code="' + E(o.code) + '" data-name="' + E(o.name) + '">' + E(o.name) +
              (o.ret5d != null ? ' <b class="' + C.dirClass(o.ret5d) + '">' + C.pct(o.ret5d, 1) + '</b>' : '') +
              '</button>';
          }).join('') + '</div></div>';
      }).join('') + '</div></details>';
  }

  /* ── 거래대금 상위 ──────────────────────────────────────── */
  function valueView(f) {
    var list = f.leadingValueTop || [];
    if (!list.length) return UI.empty({ title: '거래대금 상위 목록 없음' });
    return '<p style="font-size:.8125rem;color:var(--ink-3);padding:2px 2px 10px">' +
      '주도 업종 내 <b>외인·기관 순매수 상위</b> 종목. 수급 빠짐 여부와 무관한 거래대금 순, ' +
      '수급 유입 완료 종목 포함.</p>' +
      '<section class="card"><div class="rows">' +
      list.map(function (c, i) {
        var amt = c.institutionNet5d;
        return '<button class="row" type="button" data-code="' + E(c.code) + '" data-name="' + E(c.name) + '">' +
          '<span class="r-rk num">' + (i + 1) + '</span>' +
          '<span class="r-name"><b>' + E(c.name) + '</b>' +
            (c.vacancyZone === '빈집' ? '<span class="tag tag-down">큰손 빠짐</span>' : '') + '</span>' +
          '<span class="r-meta">' + E(c.sector || '-') +
            (c.tradingValue5dAvg != null ? ' · 하루 ' + C.won(c.tradingValue5dAvg) : '') + '</span>' +
          '<span class="r-price"><b class="num">' + C.num(c.close) + '</b>' +
            (amt != null ? '<em class="num ' + C.dirClass(amt) + '">외+기 ' + C.won(amt) + '</em>' : '') +
          '</span></button>';
      }).join('') + '</div></section>' + tiFold(f);
  }

  /* 거래대금 강도 — 보조 지표라 접어 둔다 */
  function tiFold(f) {
    var ti = f.tradingIntensity || [];
    if (!ti.length) return '';
    return '<details class="fold" style="margin-top:12px">' +
      '<summary>거래대금 강도<span class="cnt">' + ti.length + '종목</span></summary>' +
      '<div class="fold-body flush">' +
      '<p style="padding:12px 16px 4px;font-size:.8125rem;color:var(--ink-3)">' +
        '과거 대비 거래대금 수준(0~100). 바닥권 상승 구간 관심, 과열권은 자금 유입 완료.' + UI.info('ti') + '</p>' +
      '<div class="rows">' + ti.map(function (t) {
        var col = t.ti >= 80 ? 'var(--up)' : t.ti >= 60 ? 'var(--warn)' : t.ti >= 20 ? 'var(--ink-2)' : 'var(--down)';
        // 스파크라인은 글자가 없어서 배율이 변해도 뭉개지지 않는다. 고정 폭으로 그린다.
        var sp = global.Viz.spark(t.tiHistory || [], 78, { height: 26, color: col, ref: 50 });
        return '<button class="row" type="button" data-code="' + E(t.code) + '" data-name="' + E(t.name) + '">' +
          '<span class="r-rk"></span>' +
          '<span class="r-name"><b>' + E(t.name) + '</b></span>' +
          '<span class="r-meta">' + E(t.sector || '-') + ' · ' + E(t.zone || '') + ' · ' + C.num(t.close) + '원' +
            (sp ? '<span class="sparkwrap" title="거래대금 강도 60일 추이 (점선 = 50)">' + sp + '</span>' : '') + '</span>' +
          '<span class="r-price"><b class="num" style="color:' + col + '">' + t.ti + '</b>' +
          '<em style="color:var(--ink-4)">강도</em></span></button>';
      }).join('') + '</div></div></details>';
  }

  /* ── 이탈 ───────────────────────────────────────────────── */
  function exitView(f) {
    var ex = (f.exitSignals || []).slice().sort(function (a, b) {
      return (a.drawdownFromHighPct || 0) - (b.drawdownFromHighPct || 0);
    });
    if (!ex.length) {
      return UI.empty({
        title: '이탈 신호 없음',
        desc: '후보 종목 중 10일선 이탈 종목 없음',
        icon: '<path d="M20 6 9 17l-5-5"/>'
      });
    }
    return '<p style="font-size:.8125rem;color:var(--ink-3);padding:2px 2px 10px">' +
      '고점 대비 하락 후 10일 이동평균선 이탈 종목. 추세 이탈 <b>사실</b> 표시, 매도 신호 아님.' +
      UI.info('exit') + '</p>' +
      '<section class="card"><div class="rows">' + ex.map(function (e) {
        return '<button class="row" type="button" data-code="' + E(e.code) + '" data-name="' + E(e.name) + '">' +
          '<span class="r-rk"></span>' +
          '<span class="r-name"><b>' + E(e.name) + '</b></span>' +
          '<span class="r-meta">' + E(e.sector || '-') + ' · 10일선 ' + C.num(Math.round(e.ma10 || 0)) +
            ' · 최근 고점 ' + C.num(Math.round(e.recentHigh || 0)) + '</span>' +
          '<span class="r-price"><b class="num">' + C.num(e.lastClose) + '</b>' +
          '<em class="num down">' + C.pct(e.drawdownFromHighPct, 1) + '</em></span></button>';
      }).join('') + '</div></section>';
  }

  global.StocksView = { view: view, state: state };
})(window);
