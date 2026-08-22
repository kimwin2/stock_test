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

  /* ── B안: 그림형 ──────────────────────────────────────────
     막대 = **그날** 산/판 돈, 선 = **최근 5일 합계**.
     같은 단위(원)라 한 축을 공유한다 — 눈으로 바로 견줄 수 있다.

     처음엔 선을 무단위 오실레이터로 그렸는데, "그날은 빨간 막대인데 선은
     왜 큰 음수냐" 는 질문을 막지 못했다. 답은 **막대와 선이 다른 질문에
     답하기 때문**이다 — 아모레퍼시픽은 8/6·8/7 의 244억·178억이 5일 창
     **밖으로 빠져나가면서** 누적이 644억 → 113억으로 무너졌고, 최근의
     작은 빨간 막대들은 그 자리를 못 메운다.
     5일 합계를 그대로 그리면 그 사실이 그림에 보인다. 축이 둘일 때는
     아무리 라벨을 잘 달아도 안 보였다. */
  var BW = 320, BH = 124, BPADX = 4, BPADT = 16, BPADB = 20;   // BPADT: 꼭지 라벨 자리
  var CUM_COLOR = '#B4560F';

  function cardB(c) {
    var b = F.bars(c), f = F.facts(c);
    var n = b.rows.length;
    if (!n) return '<section class="card">' + head(c) + '<p class="blab">수급 데이터가 없습니다.</p></section>';

    var plotH = BH - BPADT - BPADB;
    var zeroY = BPADT + plotH / 2;
    var half = plotH / 2 - 4;
    var step = (BW - BPADX * 2) / n;
    var bw = Math.max(4, step * 0.56);
    var cx = function (i) { return BPADX + step * (i + 0.5); };
    var y = function (v) { return zeroY - (v / b.max) * half; };

    var barSvg = b.rows.map(function (r, i) {
      var h = Math.max(1.5, Math.abs(r.v) / b.max * half);
      var up = r.v >= 0;
      return '<rect x="' + (cx(i) - bw / 2).toFixed(1) + '" y="' + (up ? zeroY - h : zeroY).toFixed(1) +
        '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="1.5" fill="' +
        (up ? 'var(--up)' : 'var(--down)') + '" opacity="0.8"><title>' +
        E(r.date) + ' ' + (up ? '+' : '−') + F.money(Math.abs(r.v)) + '</title></rect>';
    }).join('');

    // 값이 없는 날은 잇지 않는다 (0 으로 메우면 없는 관측이 생긴다).
    var pts = b.rows.map(function (r, i) {
      return r.cum5 == null ? null : cx(i).toFixed(1) + ',' + y(r.cum5).toFixed(1);
    });
    var segs = [], cur = [];
    pts.forEach(function (p) { if (p) cur.push(p); else if (cur.length) { segs.push(cur); cur = []; } });
    if (cur.length) segs.push(cur);
    var lineSvg = segs.filter(function (g) { return g.length > 1; }).map(function (g) {
      return '<polyline points="' + g.join(' ') + '" fill="none" stroke="' + CUM_COLOR +
        '" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>';
    }).join('');

    var lastIdx = -1, peakIdx = -1, peak = -Infinity;
    for (var i = 0; i < n; i++) {
      if (b.rows[i].cum5 == null) continue;
      lastIdx = i;
      if (Math.abs(b.rows[i].cum5) > peak) { peak = Math.abs(b.rows[i].cum5); peakIdx = i; }
    }
    var marks = '';
    if (lastIdx >= 0) {
      var lv = b.rows[lastIdx].cum5;
      var FLOOR = BH - BPADB - 3, CEIL = BPADT + 10;
      var ly = y(lv) + (lv >= 0 ? -9 : 15);
      if (ly > FLOOR) ly = y(lv) - 9;
      if (ly < CEIL) ly = y(lv) + 15;
      marks += '<circle cx="' + cx(lastIdx).toFixed(1) + '" cy="' + y(lv).toFixed(1) + '" r="3.2" fill="' + CUM_COLOR + '"/>' +
        '<text x="' + (BW - BPADX) + '" y="' + ly.toFixed(1) + '" text-anchor="end" font-size="10.5" font-weight="800" fill="' + CUM_COLOR +
        '" paint-order="stroke" stroke="#fff" stroke-width="3" stroke-linejoin="round">' + E(F.money(lv)) + '</text>';
      // 꼭지도 같이 찍는다 — "얼마에서 얼마로 줄었나" 가 이 그림의 전부다.
      if (peakIdx >= 0 && peakIdx !== lastIdx && Math.abs(peak) > Math.abs(lv) * 1.5) {
        var pv = b.rows[peakIdx].cum5;
        // 꼭지가 천장에 붙으면 라벨이 잘린다. 위가 좁으면 아래로 내린다.
        var py = y(pv) - 7;
        if (py < 10) py = y(pv) + 13;
        marks += '<text x="' + cx(peakIdx).toFixed(1) + '" y="' + py.toFixed(1) +
          '" text-anchor="middle" font-size="10" font-weight="700" fill="#8A7350"' +
          ' paint-order="stroke" stroke="#fff" stroke-width="3" stroke-linejoin="round">' + E(F.money(pv)) + '</text>';
      }
    }

    var d0 = (b.rows[0] || {}).date || '', d1 = (b.rows[n - 1] || {}).date || '';
    var axis =
      '<text x="' + BPADX + '" y="' + (BH - 6) + '" font-size="10" fill="var(--ink3)">' + E(d0.slice(5)) + '</text>' +
      '<text x="' + (BW - BPADX) + '" y="' + (BH - 6) + '" font-size="10" fill="var(--ink3)" text-anchor="end">' + E(d1.slice(5)) + '</text>';

    return '<section class="card">' + head(c) +
      '<svg class="bchart" viewBox="0 0 ' + BW + ' ' + BH + '" xmlns="http://www.w3.org/2000/svg" role="img" ' +
      'aria-label="외인·기관 일별 순매수와 최근 5일 합계">' +
        '<line x1="' + BPADX + '" y1="' + zeroY + '" x2="' + (BW - BPADX) + '" y2="' + zeroY +
        '" stroke="#CDD2D9" stroke-width="1"/>' +
        barSvg + lineSvg + marks + axis +
      '</svg>' +
      '<p class="legend"><i class="sw up"></i>산 날 <i class="sw dn"></i>판 날 ' +
      '<i class="sw ln"></i>최근 5일 합계' +
      '<span class="hint">— 그날 사도, 5일 합계가 줄면 힘이 빠지는 중입니다</span></p>' +
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
