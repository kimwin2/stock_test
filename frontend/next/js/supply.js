/* ============================================================
   supply.js — 수급을 **평문**으로 말한다 (next 공용)
   ------------------------------------------------------------
   왜 이 모듈이 따로 있나:
     한 화면에 수급을 말하는 그림이 셋이었고(60일 오실레이터 / 5단
     게이지 / 10일 히트맵) 셋이 서로 다른 언어를 썼다. 사용자는
     "이 셋이 같은 얘긴가" 부터 풀어야 했다.
     → 화면에 수급 그림은 **하나만** 둔다. 문장도 한 곳에서만 만든다.
       화면마다 따로 지으면 같은 종목이 화면마다 다르게 말한다.

   화면 규칙 — **'빈집' 이라는 말을 쓰지 않는다.**
     그 한 단어가 서로 다른 네 상황을 덮고 있어서(2026-08-22 실측)
     어떤 설명을 붙여도 학습이 안 됐다. 용어를 설명하는 대신 상황을
     그대로 말한다.

   오실레이터는 지우지 않는다. 시스템이 실제로 후보를 거르는 값이라
   근거로 남아야 한다 — 다만 기본 화면이 아니라 '숫자로 보기' 안이다.
   ============================================================ */
(function (global) {
  'use strict';


  var EOK = 1e8;

  function eok(v) {
    if (v == null || isNaN(v)) return null;
    return v / EOK;
  }

  /* 억 단위 사람 표기 — 1,367억 / 1.4조 */
  function money(v) {
    var e = eok(v);
    if (e == null) return '—';
    var a = Math.abs(e);
    if (a >= 10000) return (e / 10000).toFixed(1).replace(/\.0$/, '') + '조';
    return Math.round(e).toLocaleString('ko-KR') + '억';
  }

  /* 조사 — 받침이 있으면 '으로', 없으면 '로'. money() 는 '억'(받침 ㄱ) 또는
     '조'(받침 없음) 로 끝난다. 붙여 쓰지 않으면 "787억 으로" 처럼 어색해진다. */
  function euro(word) {
    var last = String(word).slice(-1);
    return word + (last === '조' ? '로' : '으로');
  }

  /* ── 상황 판정 ────────────────────────────────────────────
     5일 순매수와 20일 순매수의 **부호 조합**으로 나눈다.
     이 네 가지는 트레이더에게 뜻이 완전히 다르고, 지금은 전부
     '빈집' 한 단어로 뭉쳐 있다. */
  var CASES = {
    slowing:  { key: 'slowing',  title: '담다가 쉬는 중' },
    turning:  { key: 'turning',  title: '담다가 돌아섰다' },
    selling:  { key: 'selling',  title: '계속 파는 중' },
    returning:{ key: 'returning',title: '팔다가 돌아왔다' }
  };

  function classify(n5, n20) {
    if (n5 == null || n20 == null) return null;
    if (n20 > 0 && n5 >= 0) return CASES.slowing;
    if (n20 > 0 && n5 < 0)  return CASES.turning;
    if (n20 <= 0 && n5 < 0) return CASES.selling;
    return CASES.returning;                      // 20일 순매도인데 5일 순매수
  }

  /* 하루 평균 페이스 비교 — "속도"를 말할 수 있게 한다.
     누적 금액끼리 비교하면 5일 vs 20일이라 당연히 20일이 크다.
     사람에게 뜻이 있는 건 **하루 평균**이다. */
  function pace(n5, n20) {
    if (n5 == null || n20 == null) return null;
    var p5 = n5 / 5, p20 = n20 / 20;
    return { p5: p5, p20: p20, ratio: p20 !== 0 ? p5 / p20 : null };
  }

  /* ── 결론 한 문장 ─────────────────────────────────────────
     '빈집' 이라는 말을 쓰지 않는다. 상황을 그대로 말한다. */
  function headline(c) {
    var f = facts(c);
    if (!f.situation) return '수급 데이터가 아직 부족합니다.';
    var p = f.pace, pct;
    switch (f.situation.key) {
      case 'slowing':
        // **그림과 같은 양을 말한다.** 예전엔 '5일 페이스 ÷ 20일 페이스'(아모레
        // 81%)를 썼는데, 그림은 '5일 합계 꼭지 → 현재'(644억 → 113억, 17%)를
        // 보여준다. 둘 다 참이지만 재는 대상이 달라서 나란히 놓으면 서로
        // 어긋나 보인다 — 숫자를 꼼꼼히 보는 사람일수록 먼저 걸린다.
        // 그림에서 눈으로 확인되는 쪽을 문장으로 쓴다. 20일 페이스 비교는
        // '숫자로 보기' 에 그대로 남아 있다.
        var b = bars(c), cum = b.rows.filter(function (r) { return r.cum5 != null; }).map(function (r) { return r.cum5; });
        if (cum.length >= 2) {
          var peak = cum.reduce(function (a, v) { return Math.abs(v) > Math.abs(a) ? v : a; }, cum[0]);
          var now = cum[cum.length - 1];
          if (Math.abs(peak) > Math.abs(now) * 1.3) {
            return '외인·기관 5일 합계가 ' + money(peak) + '에서 ' + euro(money(now)) +
              ' 줄었습니다. 그날그날은 사고 있지만 들어오는 힘이 빠지는 중입니다.';
          }
        }
        pct = (p && p.ratio != null) ? Math.round(p.ratio * 100) : null;
        return pct != null && pct < 90
          ? '외인·기관이 계속 담고 있지만, 최근 5일 사들이는 속도가 지난 20일 평균의 ' + pct + '% 로 내려왔습니다.'
          : '외인·기관이 담는 중이지만 속도가 예전만 못합니다.';
      case 'turning':
        return '지난 20일은 ' + money(f.n20) + ' 담았는데, 최근 5일은 ' + money(Math.abs(f.n5)) + ' 팔았습니다. 방향이 바뀌었습니다.';
      case 'selling':
        return '최근 5일 동안 ' + money(Math.abs(f.n5)) + ' 팔았습니다'
          + (p && p.ratio != null && p.ratio > 1.2
              ? ' — 20일 평균보다 ' + p.ratio.toFixed(1) + '배 빠른 속도입니다.'
              : '.');
      case 'returning':
        return '지난 20일은 ' + money(Math.abs(f.n20)) + ' 팔았는데, 최근 5일은 ' + money(f.n5) + ' 다시 담았습니다.';
    }
    return '';
  }

  /* 이 종목이 후보에 오른 이유 — 화면 어디서도 용어를 안 쓴다.
     "왜 지금 이 종목인가"에 직접 답한다. */
  function whyNow(c) {
    var f = facts(c);
    var parts = [];
    if (f.situation) {
      parts.push(f.situation.key === 'selling' || f.situation.key === 'turning'
        ? '큰손이 아직 자리를 비워두고 있습니다'
        : '큰손이 아직 자리를 다 채우지 않았습니다');
    }
    if (c.aboveMA10) parts.push('주가는 10일선 위에서 버티는 중입니다');
    if (c.sector) parts.push('오늘 시장이 미는 업종(' + c.sector + ')입니다');
    return parts;
  }

  /* 일별 막대 + 수급 속도 — '감속' 은 글이 아니라 그림이다.
     막대(instAmount) = 그날 사고판 **금액**.
     선(supplyOscHistory.osc) = 사들이는 **속도**. 둘은 다른 양이지만
     0 의 뜻이 같아서(위=붙는 중, 아래=식는 중) 0선을 공유할 수 있다.
     같이 그리면 "막대가 작아지니 선이 내려간다" 가 눈에 보인다 —
     이 지표가 왜 감속을 재는지를 말이 아니라 그림이 설명한다.

     두 배열은 **날짜를 키로** 맞춘다. 실측으로는 10/10 이 겹치지만
     서로 다른 fetch 에서 오므로 길이가 같다고 같은 날이 아니다
     (2026-08-20 에 가격↔오실레이터가 정확히 그렇게 어긋났다). */
  function bars(c) {
    var raw = c.dailyFlow10d || [];

    // 시총은 날짜마다 다르다. ratio 는 그날 시총으로 나눈 값이라, 되돌릴 때도
    // 그날 시총을 써야 한다 (현재 시총으로 일괄 곱하면 과거일수록 어긋난다).
    var capBy = {};
    var dh = c.dateHistory60d || [], ch = c.capHistory60d || [];
    for (var i = 0; i < dh.length && i < ch.length; i++) capBy[dh[i]] = ch[i];

    var oscBy = {};
    (c.supplyOscHistory || []).forEach(function (o) {
      if (o && o.date) oscBy[o.date] = o;
    });

    var rows = raw.map(function (r) {
      var o = oscBy[r.date];
      var cap = capBy[r.date] || c.marketCap || 0;
      // 5일 누적 = ratio × 그날 시총. 백엔드가 오실레이터를 만들 때 쓴 바로 그 양.
      var cum = (o && o.ratio != null && cap) ? o.ratio * cap : null;
      return { date: r.date, v: r.instAmount || 0, cum5: cum, osc: (o && o.osc != null) ? o.osc : null };
    });

    // 막대(일별)와 선(5일 누적)은 **같은 단위(원)** 라 한 축을 공유한다.
    // 실측 9종목에서 누적 최대가 일별 최대의 0.8~2.6배라 막대가 죽지 않는다.
    // 무단위 오실레이터를 얹던 예전 방식은 축이 둘이라, 그날 빨간 막대와
    // 큰 음수 선이 같이 보여도 왜 그런지 그림이 설명하지 못했다.
    var max = 1;
    rows.forEach(function (r) {
      max = Math.max(max, Math.abs(r.v), r.cum5 == null ? 0 : Math.abs(r.cum5));
    });
    var drawn = rows.filter(function (r) { return r.cum5 != null; }).length;
    return { rows: rows, max: max, cumDrawn: drawn };
  }

  function facts(c) {
    var n5 = c.institutionNet5d, n20 = c.institutionNet20d;
    return {
      n5: n5, n20: n20,
      situation: classify(n5, n20),
      pace: pace(n5, n20),
      depth: c.oscPercentile,          // 0 에 가까울수록 '자리가 비어 있다'
      sellStreak: c.currentVacancyDays || 0,
      trendAlive: !!c.aboveMA10,
      maRising: !!c.ma10Rising
    };
  }

  /* ── B안 차트 — 막대(그날) + 선(최근 5일 합계) ─────────────
     같은 단위(원)라 한 축을 공유한다. 무단위 오실레이터를 얹던 방식은
     축이 둘이라 "그날은 빨간 막대인데 선은 왜 큰 음수냐" 를 설명하지
     못했다 (2026-08-22). 5일 합계를 그대로 그리면 그 간극이 보인다. */
  var BW = 320, BH = 124, PADX = 4, PADT = 16, PADB = 20;
  var CUM = '#B4560F';
  var ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (m) { return ESC[m]; }); }

  function chart(c) {
    var b = bars(c), n = b.rows.length;
    if (!n) return '';
    // 0선은 **데이터 범위에 맞춰** 놓는다. 늘 한가운데 두면 전부 순매수인
    // 종목은 아래 절반이 통째로 비어, 모바일에서 그림이 절반 크기가 된다.
    // 0 의 위치는 선으로 표시되므로 위아래 비대칭이어도 오해가 없다.
    var plotH = BH - PADT - PADB;
    var vmax = 0, vmin = 0;
    b.rows.forEach(function (r) {
      [r.v, r.cum5].forEach(function (v) {
        if (v == null) return;
        if (v > vmax) vmax = v;
        if (v < vmin) vmin = v;
      });
    });
    var pad = (vmax - vmin) * 0.08 || 1;
    vmax += pad; vmin -= (vmin < 0 ? pad : 0);
    var span = (vmax - vmin) || 1;
    var zeroY = PADT + plotH * (vmax / span);
    var step = (BW - PADX * 2) / n, bw = Math.max(4, step * 0.56);
    var cx = function (i) { return PADX + step * (i + 0.5); };
    var y = function (v) { return zeroY - (v / span) * plotH; };

    var barSvg = b.rows.map(function (r, i) {
      var h = Math.max(1.5, Math.abs(r.v) / span * plotH), up = r.v >= 0;
      return '<rect x="' + (cx(i) - bw / 2).toFixed(1) + '" y="' + (up ? zeroY - h : zeroY).toFixed(1) +
        '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="1.5" fill="' +
        (up ? 'var(--up)' : 'var(--down)') + '" opacity="0.8"><title>' + esc(r.date) + ' ' +
        (up ? '+' : '−') + money(Math.abs(r.v)) + '</title></rect>';
    }).join('');

    // 값이 없는 날은 잇지 않는다 (0 으로 메우면 없는 관측이 생긴다).
    var pts = b.rows.map(function (r, i) {
      return r.cum5 == null ? null : cx(i).toFixed(1) + ',' + y(r.cum5).toFixed(1);
    });
    var segs = [], cur = [];
    pts.forEach(function (p) { if (p) cur.push(p); else if (cur.length) { segs.push(cur); cur = []; } });
    if (cur.length) segs.push(cur);
    var line = segs.filter(function (g) { return g.length > 1; }).map(function (g) {
      return '<polyline points="' + g.join(' ') + '" fill="none" stroke="' + CUM +
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
      var lv = b.rows[lastIdx].cum5, FLOOR = BH - PADB - 3, CEIL = PADT + 10;
      var ly = y(lv) + (lv >= 0 ? -9 : 15);
      if (ly > FLOOR) ly = y(lv) - 9;
      if (ly < CEIL) ly = y(lv) + 15;
      marks += '<circle cx="' + cx(lastIdx).toFixed(1) + '" cy="' + y(lv).toFixed(1) + '" r="3.2" fill="' + CUM + '"/>' +
        '<text x="' + (BW - PADX) + '" y="' + ly.toFixed(1) + '" text-anchor="end" font-size="10.5" font-weight="800" fill="' + CUM +
        '" paint-order="stroke" stroke="var(--surface)" stroke-width="3" stroke-linejoin="round">' + esc(money(lv)) + '</text>';
      if (peakIdx >= 0 && peakIdx !== lastIdx && Math.abs(peak) > Math.abs(lv) * 1.5) {
        var pv = b.rows[peakIdx].cum5, py = y(pv) - 7;
        if (py < 10) py = y(pv) + 13;
        marks += '<text x="' + cx(peakIdx).toFixed(1) + '" y="' + py.toFixed(1) +
          '" text-anchor="middle" font-size="10" font-weight="700" fill="#8A7350"' +
          ' paint-order="stroke" stroke="var(--surface)" stroke-width="3" stroke-linejoin="round">' + esc(money(pv)) + '</text>';
      }
    }
    var d0 = (b.rows[0] || {}).date || '', d1 = (b.rows[n - 1] || {}).date || '';
    var axis = '<text x="' + PADX + '" y="' + (BH - 6) + '" font-size="10" fill="var(--ink-4)">' + esc(d0.slice(5)) + '</text>' +
      '<text x="' + (BW - PADX) + '" y="' + (BH - 6) + '" font-size="10" fill="var(--ink-4)" text-anchor="end">' + esc(d1.slice(5)) + '</text>';

    return '<svg class="sup-chart" viewBox="0 0 ' + BW + ' ' + BH + '" xmlns="http://www.w3.org/2000/svg" role="img" ' +
      'aria-label="외인·기관 일별 순매수와 최근 5일 합계">' +
      '<line x1="' + PADX + '" y1="' + zeroY + '" x2="' + (BW - PADX) + '" y2="' + zeroY + '" stroke="var(--line)" stroke-width="1"/>' +
      barSvg + line + marks + axis + '</svg>' +
      '<p class="sup-legend"><i class="sw up"></i>산 날 <i class="sw dn"></i>판 날 ' +
      '<i class="sw ln"></i>최근 5일 합계' +
      '<span class="hint">그날 사도, 5일 합계가 줄면 힘이 빠지는 중입니다</span></p>';
  }

  global.Supply = {
    facts: facts, headline: headline, whyNow: whyNow, bars: bars,
    money: money, chart: chart, CASES: CASES
  };
})(window);
