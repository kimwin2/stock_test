/* ============================================================
   themes.js — '테마' 화면
   ------------------------------------------------------------
   이 탭은 "오늘 뭐가 왜 올랐나" 만 말한다. 수급 빈집 판정은 하지 않는다.
   두 탭이 같은 종목을 다른 기준으로 말하면 사용자는 어느 쪽을 믿을지
   알 수 없다. 빈집은 오늘·종목 탭의 언어다.

   지도(트리맵)를 목록보다 위에 두는 이유: 테마명과 종목명을 글로
   나열하면 "왜 이것들이 한 덩어리인지" 가 안 보인다. 군집은 원래
   그림으로 증명한다.
   ============================================================ */
(function (global) {
  'use strict';

  var C = global.Core, E = C.esc, UI = global.UI;

  function view(root, opts) {
    opts = opts || {};
    root.innerHTML = UI.skeleton();
    C.theme()
      .then(function (td) { paint(root, td, opts); })
      .catch(function (e) {
        root.innerHTML = UI.errorState(e.message, function () { C.reload(); view(root, opts); });
      });
  }

  function paint(root, td, opts) {
    var themes = td.themes || [];
    if (!themes.length) {
      root.innerHTML = UI.empty({
        title: '오늘 테마 없음',
        desc: '뉴스·시그널·급등 클러스터 모두 조건 미충족'
      });
      return;
    }

    root.innerHTML =
      session(td) + stale(td) +
      '<div class="stack">' +
        '<section class="card">' +
          '<div class="card-h"><h3>오늘의 테마 지도</h3>' + UI.info('theme') + '</div>' +
          '<p style="padding:4px 16px 10px;font-size:.75rem;color:var(--ink-4)">' +
            '칸 크기 ≈ 거래대금(제곱근 보정) · 색 = 등락률 · 탭하면 종목 상세</p>' +
          '<div class="tmap" id="tmap" style="padding:0 10px 12px"></div>' +
        '</section>' +
        themes.map(card).join('') +
      '</div>';

    global.Viz.mount(document.getElementById('tmap'), function (w) {
      return global.Viz.treemap(themes, w);
    });

    root.addEventListener('click', function (e) {
      var tile = e.target.closest('.tmap-tile');
      if (tile) {
        var code = tile.getAttribute('data-code');
        if (code) global.Detail.open(code, tile.getAttribute('data-name'));
        return;
      }
      var st = e.target.closest('[data-code]');
      if (st && !e.target.closest('a')) global.Detail.open(st.dataset.code, st.dataset.name);
    });
    if (opts.focus) {
      var target = root.querySelector('[data-theme-card="' + cssEsc(opts.focus) + '"]');
      if (target) setTimeout(function () { target.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 80);
    }
  }

  function cssEsc(s) { return String(s).replace(/["\\]/g, '\\$&'); }

  // 장 시작 전(08:00~09:00)에는 정규장이 아직 안 열렸다. 시세의 성격이 다르므로
  // 그 사실을 화면에 밝힌다 — 숫자만 보여주고 어느 장인지 안 말하면 거짓말이 된다.
  function session(td) {
    var q = td.quoteSession;
    if (!q || !q.isPreMarket) return '';
    var pre = q.premarketQuotes || 0;
    return '<div class="notice" style="margin-bottom:12px;background:var(--surface-2);color:var(--ink-2)">' +
      '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 1.8"/></svg>' +
      '<span><b>장 시작 전</b> · ' +
      (pre ? '넥스트레이드 프리마켓 체결가 기준 ' + pre + '종목. 09:00 정규장 개장 후 재계산'
           : '체결 없음, 등락률 미산출. 09:00 개장 후 갱신') +
      '</span></div>';
  }

  // 테마 분석이 실패한 회차에는 서버가 기존 themes 를 그대로 두고 updatedAt 만
  // 갱신한다. 그 사실을 드러내지 않으면 몇 달 지난 테마를 최신으로 오인한다.
  function stale(td) {
    if (!td.themesError) return '';
    return '<div class="notice" style="margin-bottom:12px">' +
      '<svg viewBox="0 0 24 24"><path d="M12 3.8 21 19H3z"/><path d="M12 10v4M12 16.6h.01"/></svg>' +
      '<span><b>테마 갱신 중단</b> · ' + E(td.themesError) +
      (td.themesGeneratedAt ? ' · 마지막 성공 ' + E(C.stamp(td.themesGeneratedAt)) : '') + '</span></div>';
  }

  function card(t, i) {
    var stocks = (t.stocks || []).filter(function (s) {
      return !(s.price === 0 && s.changeRate === 0);
    });
    var link = t.headlineUrl || (t.headlineLink && t.headlineLink.url) ||
               (Array.isArray(t.headlineLinks) && t.headlineLinks[0] && t.headlineLinks[0].url) || '';
    var lead = stocks.reduce(function (a, b) {
      return (a && a.changeRate >= b.changeRate) ? a : b;
    }, null);

    return '<section class="card" data-theme-card="' + E(t.themeName) + '">' +
      '<div class="card-h">' +
        '<h3>' + E(t.themeName) + '</h3>' +
        '<span class="spacer"></span>' +
        (i === 0 ? '<span class="tag tag-up">오늘 1위</span>' : '') +
        '<span class="cnt">' + E(t.totalVolume || '') + '</span>' +
      '</div>' +
      (lead && lead.changeRate != null
        ? '<p style="padding:6px 16px 0;font-size:.8125rem;color:var(--ink-3)">대장 ' + E(lead.name) +
          ' <b class="num ' + C.dirClass(lead.changeRate) + '">' + C.pct(lead.changeRate, 1) + '</b>' +
          ' · ' + stocks.length + '종목</p>'
        : '') +
      (t.headline
        ? (link
            ? '<a class="theme-card-news" href="' + E(link) + '" target="_blank" rel="noopener noreferrer" style="margin-top:10px">' +
              '<u>' + E(t.headline) + '</u> ↗</a>'
            : '<p class="theme-card-news" style="margin-top:10px">' + E(t.headline) + '</p>')
        : '') +
      (stocks.length
        ? '<div class="rows" style="margin-top:1px">' + stocks.map(stockRow).join('') + '</div>'
        : '') +
      '</section>';
  }

  function stockRow(s) {
    var cls = C.dirClass(s.changeRate);
    return '<button class="trow" type="button" data-code="' + E(s.code) + '" data-name="' + E(s.name) + '">' +
      '<span class="tn"><b>' + E(s.name) + '</b>' +
        (s.isTop ? '<span class="tag tag-up">대장</span>' : '') + '</span>' +
      '<span class="tv">' + C.num(s.price) + '원' + (s.volume ? ' · 거래대금 ' + E(s.volume) : '') + '</span>' +
      '<span class="tc ' + cls + '">' + C.pct(s.changeRate, 2) + '</span>' +
      '</button>';
  }

  global.ThemesView = { view: view };
})(window);
