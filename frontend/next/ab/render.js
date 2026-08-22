/* ============================================================
   render.js — 같은 사실을 세 가지 방식으로 그린다.
   ------------------------------------------------------------
   A 문장형 : 결론을 한국어 문장으로 먼저 말한다
   B 그림형 : 일별 수급 막대가 주인공, 말은 캡션 한 줄
   C 단계형 : 고정된 3단계 모형에서 지금 어디인지 짚어준다

   공통 규칙 — **화면에 '빈집' 이라는 말을 한 번도 쓰지 않는다.**
   용어를 설명하는 대신 상황을 그대로 말한다.
   ============================================================ */
(function (global) {
  'use strict';
  var F = global.Facts;
  var ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
  function E(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) { return ESC[m]; }); }
  function num(v) { return v == null ? '—' : Math.round(v).toLocaleString('ko-KR'); }

  function dirClass(v) { return v == null || v === 0 ? 'flat' : (v > 0 ? 'up' : 'down'); }

  /* 카드 머리 — 세 안 공통. 종목·업종·현재가는 어느 안에서든 필요하다. */
  function head(c) {
    var ph = c.priceHistory60d || [];
    var last = ph[ph.length - 1], prev = ph[ph.length - 2];
    var pct = (last != null && prev) ? (last / prev - 1) * 100 : null;
    return '<div class="hd"><b>' + E(c.name) + '</b>' +
      (c.sector ? '<span class="sec">' + E(c.sector) + '</span>' : '') +
      '<span class="px ' + dirClass(pct) + '">' + num(last) + '원' +
      (pct != null ? ' <small>' + (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%</small>' : '') +
      '</span></div>';
  }

  /* 접어둔 숫자 근거 — 세 안 공통. 궁금한 사람만 연다. */
  function detail(c) {
    var f = F.facts(c);
    var p = f.pace || {};
    return '<details class="more"><summary>숫자로 보기</summary><dl class="kv">' +
      '<dt>최근 5일 외인·기관</dt><dd class="' + dirClass(f.n5) + '">' + (f.n5 >= 0 ? '+' : '−') + F.money(Math.abs(f.n5)) + '</dd>' +
      '<dt>지난 20일 외인·기관</dt><dd class="' + dirClass(f.n20) + '">' + (f.n20 >= 0 ? '+' : '−') + F.money(Math.abs(f.n20)) + '</dd>' +
      '<dt>하루 평균 (최근 5일)</dt><dd class="' + dirClass(p.p5) + '">' + (p.p5 >= 0 ? '+' : '−') + F.money(Math.abs(p.p5)) + '</dd>' +
      '<dt>하루 평균 (지난 20일)</dt><dd class="' + dirClass(p.p20) + '">' + (p.p20 >= 0 ? '+' : '−') + F.money(Math.abs(p.p20)) + '</dd>' +
      '<dt>10일선</dt><dd>' + (f.trendAlive ? '위' : '아래') + (f.maRising ? ' · 우상향' : '') + '</dd>' +
      '</dl></details>';
  }

  /* ── A안: 문장형 ──────────────────────────────────────── */
  function cardA(c) {
    var why = F.whyNow(c);
    return '<section class="card">' + head(c) +
      '<p class="why">' + E(F.headline(c)) + '</p>' +
      '<div class="chips">' + why.map(function (w) { return '<span class="chip">' + E(w) + '</span>'; }).join('') + '</div>' +
      detail(c) + '</section>';
  }

  /* ── B안: 그림형 ──────────────────────────────────────── */
  function cardB(c) {
    var b = F.bars(c), f = F.facts(c);
    var cols = b.rows.map(function (r) {
      // 선형 스케일을 유지한다. 첫날이 크면 나머지가 납작해지는데, 그게 바로
      // "그날 몰렸고 그 뒤로 안 담았다" 는 사실이다 — 로그·제곱근으로 펴면
      // 보이기는 좋아져도 '감속' 의 크기를 왜곡한다. 이 그림의 존재 이유가
      // 감속을 보여주는 것이라 여기서 정직함을 포기하면 안 된다.
      var h = Math.max(2, Math.round(Math.abs(r.v) / b.max * 44));
      var up = r.v >= 0;
      return '<div class="bcol" title="' + E(r.date) + ' ' + (up ? '+' : '−') + F.money(Math.abs(r.v)) + '">' +
        '<i class="' + (up ? 'up' : 'dn') + '" style="height:' + h + 'px"></i></div>';
    }).join('');
    var first = b.rows[0], lastR = b.rows[b.rows.length - 1];
    return '<section class="card">' + head(c) +
      '<div class="bars">' + cols + '</div>' +
      '<div class="bax"><span>' + E((first || {}).date || '').slice(5) + '</span>' +
      '<span>외인·기관 일별 순매수 (위 빨강 = 사들임)</span>' +
      '<span>' + E((lastR || {}).date || '').slice(5) + '</span></div>' +
      '<p class="blab"><b>' + E((f.situation || {}).title || '') + '</b> · ' + E(F.headline(c)) + '</p>' +
      detail(c) + '</section>';
  }

  /* ── C안: 단계형 ──────────────────────────────────────────
     3단계는 **종목마다 바뀌지 않는다.** 전략 자체가 이 순서이고,
     고정돼 있어야 사람이 한 번 배워서 계속 쓴다. */
  // 라벨은 **네 상황 모두에 참**이어야 한다.
  // 처음엔 '큰손이 빠진다' 로 썼는데, LG생활건강은 그 시점에 외인·기관이
  // 순매수 중이라 1단계 문구가 그 카드의 사실과 정면으로 어긋났다.
  // 한 단어가 네 상황을 덮어서 생긴 원래 문제를 라벨에서 되풀이한 것이다.
  // '몰렸다 / 잠잠하다 / 다시 몰린다' 는 매수 감속·매도 지속 어느 쪽에도
  // 거짓이 되지 않는다.
  var STEPS = [
    { t: '큰손이 몰렸다' },
    { t: '지금은 잠잠하다' },
    { t: '다시 몰린다' }
  ];
  function cardC(c) {
    var f = F.facts(c);
    // 지금 위치: 되돌아오기 시작했으면 3단계, 아니면 2단계.
    var at = (f.situation && f.situation.key === 'returning') ? 2 : 1;
    var rail = STEPS.map(function (s, i) {
      var cls = i < at ? 'done' : (i === at ? 'on' : '');
      return '<div class="step ' + cls + '"><div class="dot"></div><div class="bar"></div>' +
        '<div class="t">' + (i + 1) + '. ' + E(s.t) + '</div></div>';
    }).join('');
    return '<section class="card">' + head(c) +
      '<div class="rail">' + rail + '</div>' +
      '<div class="now">' + E(F.headline(c)) + '</div>' +
      '<div class="chips">' + F.whyNow(c).map(function (w) { return '<span class="chip">' + E(w) + '</span>'; }).join('') + '</div>' +
      detail(c) + '</section>';
  }

  var RENDER = { a: cardA, b: cardB, c: cardC };

  function boot(which) {
    var S3 = 'https://stock-dashboard-data.s3.ap-northeast-2.amazonaws.com';
    var host = global.location.hostname || '';
    var prod = host.indexOf('github.io') >= 0 || host.indexOf('stock') >= 0;
    var url = prod ? S3 + '/flow_dashboard.json' : '../../flow_dashboard.json';
    var host_el = document.getElementById('list');
    fetch(url + '?t=' + Date.now())
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (d) {
        var list = d.buyCandidates || [];
        if (!list.length) { host_el.innerHTML = '<div class="err">오늘은 조건을 넘긴 종목이 없습니다.</div>'; return; }
        host_el.innerHTML = list.map(RENDER[which]).join('');
      })
      .catch(function (e) {
        host_el.innerHTML = '<div class="err">데이터를 불러오지 못했습니다.<br><small>' + E(e.message) + '</small></div>';
      });
  }

  global.AB = { boot: boot };
})(window);
