/* ============================================================
   facts.js — 수급 데이터 → **평문 사실**
   ------------------------------------------------------------
   이 파일은 화면을 그리지 않는다. "이 종목에 무슨 일이 있었나"를
   한국어 문장으로 만들 수 있는 재료만 뽑는다.

   왜 따로 두는가:
     세 안(A/B/C)이 **같은 사실**을 서로 다른 방식으로 보여줘야
     비교가 성립한다. 안마다 문장을 따로 지으면 무엇이 더 잘
     읽히는지가 아니라 어느 문장이 더 잘 쓰였는지를 재게 된다.

   가장 중요한 발견 (2026-08-21 실측):
     `vacancyZone: '빈집'` 한 단어가 **서로 다른 네 상황**을 덮고 있다.
       LG생활건강  5일 +39억  / 20일 +681억  → 담다가 속도가 1/4 로 죽음
       아모레퍼시픽 5일 +113억 / 20일 +553억  → 담는 중, 속도만 조금 둔화
       실리콘투    5일 -55억  / 20일 +236억  → 담다가 최근 돌아섬
       SK스퀘어    5일 -736억 / 20일 -1,367억 → 계속 파는 중, 오히려 가속
     같은 태그를 달고 있으니 사용자는 "빈집"이 무슨 뜻인지 영영 못 배운다.
     실제로 LG생활건강은 외인·기관이 **순매수 중**인데 화면엔 '빈집' 이라
     붙어 있어, 지금 UI 는 그 모순을 해명하는 문단을 따로 달고 있다.
     → 용어를 설명하지 말고, **상황을 그대로 말한다.**
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
    var oscAll = c.supplyOscHistory || [];
    var oscBy = {};
    oscAll.forEach(function (o) { if (o && o.date) oscBy[o.date] = o.osc; });

    var rows = raw.map(function (r) {
      var o = oscBy[r.date];
      return { date: r.date, v: r.instAmount || 0, osc: (o == null ? null : o) };
    });
    var max = Math.max.apply(null, rows.map(function (r) { return Math.abs(r.v); }).concat([1]));

    // 선의 세로 스케일은 **그 종목의 전체 이력** 기준이다. 보이는 10일만으로
    // 재면 거의 안 움직인 종목도 요동치는 것처럼 그려진다.
    var span = 0;
    oscAll.forEach(function (o) {
      if (o && o.osc != null) span = Math.max(span, Math.abs(o.osc));
    });
    var drawn = rows.filter(function (r) { return r.osc != null; }).length;
    return { rows: rows, max: max, oscSpan: span || 1e-6, oscDrawn: drawn };
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

  global.Facts = {
    facts: facts, headline: headline, whyNow: whyNow, bars: bars,
    money: money, eok: eok, CASES: CASES
  };
})(window);
