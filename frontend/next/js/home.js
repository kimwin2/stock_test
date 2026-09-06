/* ============================================================
   home.js — '오늘' 화면
   ------------------------------------------------------------
   한 화면에 결론은 하나. 히어로가 "오늘 볼 종목 N개" 를 말하고,
   그 아래는 전부 그 숫자를 믿어도 되는지에 대한 근거다.

   순서: 결론 → 장 상태 → 주도 업종 → 종목 → 근거(깔때기) →
         재료 → 점검 → 어제와 변화
   ============================================================ */
(function (global) {
  'use strict';

  var C = global.Core, E = C.esc, UI = global.UI;

  /* 쏠림 밴드 — 절대 임계값이 아니라 자기 이력 백분위로 판정한다.
     우리 지수 스케일은 참고 자료와 다르므로 숫자를 옮겨 적으면 틀린다.
     방향이 직관과 반대라(높을수록 어려움) 화면에 방향을 명시한다. */
  var CROWD_BANDS = [
    { from: 90, label: '극단 쏠림', tone: 'up',   desc: '소수 업종 독주. 순환매(되돌림) 출현 구간' },
    { from: 70, label: '어려운 장', tone: 'warn', desc: '일부 업종만 상승. 종목 압축 필요' },
    { from: 30, label: '보통',      tone: 'flat', desc: '주도 업종 위주 흐름' },
    { from: 0,  label: '편한 장',   tone: 'ok',   desc: '업종 간 편차 축소. 종목 선별 용이' }
  ];
  function crowdBand(p) {
    for (var i = 0; i < CROWD_BANDS.length; i++) if (p >= CROWD_BANDS[i].from) return CROWD_BANDS[i];
    return CROWD_BANDS[CROWD_BANDS.length - 1];
  }
  function pctRank(vals, v) {
    if (!vals.length) return null;
    var below = vals.filter(function (x) { return x <= v; }).length;
    return Math.round(below / vals.length * 100);
  }

  /* 시장 구간 색 — 백엔드 zone(오실레이터 기준)을 그대로 쓴다.
     F&G '레벨' 과 '방향' 은 다른 축이라 섞으면 정반대 결론이 나온다. */
  var ZONE_TONE = { '과열': 'up', '강세': 'warn', '중립': 'flat', '약세': 'down', '공포': 'down' };
  function toneVar(t) {
    return t === 'up' ? 'var(--up)' : t === 'down' ? 'var(--down)'
         : t === 'warn' ? 'var(--warn)' : t === 'ok' ? 'var(--ok)' : 'var(--ink-2)';
  }

  function view(root) {
    root.innerHTML = UI.skeleton();
    C.flow()
      .then(function (f) { paint(root, f); })
      .catch(function (e) {
        root.innerHTML = UI.errorState(e.message, function () { C.reload(); view(root); });
      });
  }

  function paint(root, f) {
    var cands = f.buyCandidates || [];
    var b = f.briefing || {};
    var k = (f.marketSentiment || {}).kospi || {};
    var cash = f.cashRecommendation || {};
    var cr = f.crowding || {};
    var hist = (cr.history || []).filter(function (h) { return h && h.crowding != null; })
                                 .map(function (h) { return h.crowding; });
    var cp = hist.length >= 20 ? pctRank(hist, hist[hist.length - 1]) : null;
    var band = cp != null ? crowdBand(cp) : null;
    var zone = k.zone || '중립';

    root.innerHTML =
      staleNotice(f) +
      '<div class="stack">' +

      /* ── 히어로 ── */
      '<section class="hero">' +
        '<div class="hero-date">' + E(C.dateLine(f.updatedAt)) + '</div>' +
        '<h1 class="hero-line">' + E(b.headline || '시장 요약 준비 중') + '</h1>' +
        '<div class="hero-answer">' +
          '<span class="ha-n num" style="color:' + (cands.length ? 'var(--up)' : 'var(--ink-3)') + '">' + cands.length + '</span>' +
          '<span class="ha-lab"><b>오늘 볼 종목</b>' +
            '<span>수급 빠짐 · 추세 생존 · 주도 업종, 3조건 통과</span></span>' +
          (cands.length ? '<button class="btn btn-primary" type="button" data-go="stocks">보기</button>' : '') +
        '</div>' +
      '</section>' +

      /* ── 장 상태 3칸 ── */
      '<div class="state3">' +
        cell('psy', '심리', (k.fearGreed != null ? k.fearGreed.toFixed(0) : '—'), zone,
             ZONE_TONE[zone] || 'flat', 'KOSPI 공포·탐욕') +
        // 쏠림은 '낮을수록 편한 장'이라 방향을 잘못 적으면 정반대로 읽힌다.
        // "상위 99%" 라고 쓰면 아주 높아 보이는데 실제로는 1년 중 가장 낮은 축이다.
        cell('crowd', '난이도', (cr.latest != null ? cr.latest.toFixed(0) : '—'),
             (band ? band.label : (cr.signal || '—')), (band ? band.tone : 'flat'),
             cp != null ? '1년 중 ' + (cp <= 50 ? '하위 ' + Math.max(1, cp) : '상위 ' + Math.max(1, 100 - cp)) + '%'
                        : '업종 쏠림') +
        cell('cash', '현금', (cash.cashPct != null ? cash.cashPct + '%' : '—'),
             cash.level || '—', 'flat', '참고 비중') +
      '</div>' +

      /* ── 주도 업종 ── */
      sectorsCard(f) +

      /* ── 오늘 볼 종목 ── */
      candCard(f, cands) +

      /* ── 어떻게 좁혔나 ── */
      funnelCard(f) +

      /* ── 오늘의 재료 ── */
      '<div id="home-themes"></div>' +

      /* ── 점검 ── */
      exitCard(f) +

      /* ── 어제와 변화 ── */
      changesCard(f) +

      /* ── 서술 요약 ── */
      briefCard(b) +

      '<p style="font-size:.75rem;color:var(--ink-4);text-align:center;padding:8px 12px 0">' +
        E(b.disclaimer || '데이터 요약 화면. 투자 권유 아님.') +
      '</p>' +
      '</div>';

    bind(root, f, { zone: zone, band: band, cp: cp, cash: cash, k: k, cr: cr });
    themes(root, f);
  }

  /* ── 조각들 ─────────────────────────────────────────────── */

  function staleNotice(f) {
    var ic = '<svg viewBox="0 0 24 24"><path d="M12 3.8 21 19H3z"/><path d="M12 10v4M12 16.6h.01"/></svg>';
    if (C.isCached('flow')) {
      return '<div class="notice" style="margin-bottom:12px">' + ic +
        '<span><b>오프라인 · 저장본 표시</b> · ' + E(C.stamp(f.updatedAt)) + ' 기준. 연결되면 자동 갱신</span></div>';
    }
    var mins = (Date.now() - new Date(f.updatedAt).getTime()) / 60000;
    if (!(mins > 24 * 60)) return '';
    return '<div class="notice" style="margin-bottom:12px">' + ic +
      '<span>' + E(C.ago(f.updatedAt)) + ' 데이터 · 평일 장중 갱신</span></div>';
  }

  // 13px 구간 라벨은 옅은 톤 위에서 강조색 그대로면 대비가 모자란다(주황 3.5:1).
  function toneInk(t) { return (!t || t === 'flat') ? 'var(--ink-3)' : 'var(--' + t + '-ink)'; }
  function cell(key, k, v, z, tone, d) {
    return '<button class="st-cell" type="button" data-cell="' + key + '">' +
      '<span class="k">' + E(k) + '</span>' +
      '<span class="v" style="color:' + toneVar(tone) + '">' + E(v) + '</span>' +
      '<span class="z" style="color:' + toneInk(tone) + '">' + E(z) + '</span>' +
      '<span class="d">' + E(d) + '</span></button>';
  }

  /* 주도 업종.
     7개를 전부 큰 행으로 깔면 406px 을 먹어서 정작 결론인 '오늘 볼 종목'이
     첫 화면 밖(898px)으로 밀린다. 실측하고 나서야 보였다.
     상위 3개만 근거와 함께 행으로 세우고 나머지는 칩으로 접는다 —
     점수 로직도 1·2·3위에만 가산점(+40/+32/+24)을 주므로 자의적인 컷이 아니다. */
  var SECTOR_ROWS = 3;
  function whyOf(src, s) {
    var v = src[s] || {};
    if (v.via === 'etf' && v.etf) return v.etf + ' 강도 ' + v.rsNorm;
    if (v.via === 'flow' && v.strength != null) return '외인·기관 유입 ' + v.strength;
    if (v.via === 'turnover' && v.sharePct != null) return '거래대금 ' + v.sharePct + '%';
    return '';
  }

  function sectorsCard(f) {
    var labels = f.leadingSectorLabels || [];
    if (!labels.length) return '';
    var src = f.leadingSectorSources || {};
    var head = labels.slice(0, SECTOR_ROWS), rest = labels.slice(SECTOR_ROWS);

    return '<section class="card">' +
      '<div class="card-h"><h3>주도 업종</h3>' + UI.info('leading') +
        '<span class="spacer"></span><span class="cnt">' + labels.length + '개</span></div>' +
      '<div class="sector-list" style="margin-top:12px">' +
      head.map(function (s, i) {
        return '<button class="sector-row' + (i === 0 ? ' top1' : '') + '" type="button" data-sector="' + E(s) + '">' +
          '<span class="rk">' + (i + 1) + '</span>' +
          '<span class="nm">' + E(s) + '</span>' +
          '<span class="why">' + E(whyOf(src, s)) + '</span>' +
          '<span class="go"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M9 5l7 7-7 7"/></svg></span>' +
          '</button>';
      }).join('') + '</div>' +
      (rest.length
        ? '<div class="chiprow" style="padding:10px 16px 12px;margin:0">' +
          rest.map(function (s, i) {
            var w = whyOf(src, s);
            return '<button class="chip" type="button" data-sector="' + E(s) + '"' +
              (w ? ' title="' + E(w) + '"' : '') + '>' +
              '<span style="color:var(--ink-4);font-weight:800">' + (SECTOR_ROWS + i + 1) + '</span>' + E(s) + '</button>';
          }).join('') + '</div>'
        : '') +
      '</section>';
  }

  var TOP_N = 5;
  function candCard(f, cands) {
    if (!cands.length) {
      return '<section class="card card-pad">' + UI.empty({
        title: '조건 통과 종목 없음',
        desc: '수급 빠짐 · 추세 생존 · 주도 업종 세 조건을 모두 충족한 종목이 없음. 억지로 채우지 않음.'
      }) + '</section>';
    }
    return '<section class="card">' +
      '<div class="card-h"><h3>오늘 볼 종목</h3>' + UI.info('vacancy') +
        '<span class="spacer"></span><span class="cnt">' + cands.length + '개</span></div>' +
      '<div class="rows" style="margin-top:12px">' +
        cands.slice(0, TOP_N).map(function (c, i) { return row(c, i); }).join('') +
      '</div>' +
      (cands.length > TOP_N
        ? '<button class="btn btn-block" type="button" data-go="stocks" style="border-radius:0;border-top:1px solid var(--line)">' +
          '나머지 ' + (cands.length - TOP_N) + '종목 보기</button>'
        : '') +
      '</section>';
  }

  function row(c, i) {
    var ss = C.supplyState(c);
    var d = c.oscPercentile;
    return '<button class="row" type="button" data-code="' + E(c.code) + '" data-name="' + E(c.name) + '">' +
      '<span class="r-rk num">' + (i + 1) + '</span>' +
      '<span class="r-name"><b>' + E(c.name) + '</b></span>' +
      '<span class="r-meta">' + E(c.sector || '-') +
        (d != null ? '<span class="depth" title="수급 위치 · 막대가 짧을수록 많이 빠짐"><i><b style="width:' +
          Math.max(6, 100 - d) + '%"></b></i>하위 ' + Math.round(d) + '%</span>' : '') +
        '<span class="tag">' + E(ss.label) + '</span>' +
      '</span>' +
      '<span class="r-price"><b class="num">' + C.num(c.close) + '</b>' +
        (c.ret5d != null ? '<em class="num ' + C.dirClass(c.ret5d) + '">' + C.pct(c.ret5d, 1) + '</em>' : '') +
      '</span></button>';
  }

  function funnelCard(f) {
    var st = f.candidateFilterStats || {};
    var uni = f.universeSize || (f.universeMetadata || []).length;
    var fin = (f.buyCandidates || []).length;
    if (!fin || !st.beforeFilter) return '';
    var steps = [
      ['분석 종목', uni, '코스피·코스닥 시총 상위'],
      ['주도 업종 소속', st.beforeFilter, '자금 유입 업종'],
      ['수급 빠짐', Math.max(fin, st.beforeFilter - (st.droppedByVacancy || 0)), '외인·기관 매수 둔화 종목'],
      ['추세 생존 + 업종 배분', fin, '10일선 위 · 업종당 상한 적용']
    ];
    var max = Math.max.apply(null, steps.map(function (s) { return s[1]; }).concat([1]));
    var dropped = [];
    if (st.droppedByTrend) dropped.push('추세 이탈 ' + st.droppedByTrend);
    if (st.droppedByVacancy) dropped.push('수급 유입 중 ' + st.droppedByVacancy);
    if (st.droppedByScore) dropped.push('점수 미달 ' + st.droppedByScore);
    if (st.droppedByConcentration) dropped.push('업종 상한 ' + st.droppedByConcentration);

    return '<details class="fold"><summary>선정 과정<span class="cnt">' + fin + '개</span></summary>' +
      '<div class="fold-body"><div class="funnel">' +
      steps.map(function (s) {
        return '<div class="fn-row"><div class="fn-top"><b>' + E(s[0]) + '</b><span class="num">' +
          C.num(s[1]) + '</span></div>' +
          '<div class="fn-track"><div class="fn-bar" style="width:' + Math.max(8, s[1] / max * 100).toFixed(1) + '%"></div></div>' +
          '<div class="fn-note">' + E(s[2]) + '</div></div>';
      }).join('') +
      '</div>' +
      (dropped.length ? '<p class="fn-note" style="margin-top:12px">제외: ' + E(dropped.join(' · ')) + '</p>' : '') +
      (st.scoreCutoffRelaxed ? '<p class="fn-note">후보 부족으로 점수 기준만 완화. 추세·수급 조건은 유지.</p>' : '') +
      '</div></details>';
  }

  function exitCard(f) {
    var ex = (f.exitSignals || []).slice().sort(function (a, b) {
      return (a.drawdownFromHighPct || 0) - (b.drawdownFromHighPct || 0);
    });
    if (!ex.length) return '';
    return '<details class="fold"><summary>보유 점검 · 이탈 신호<span class="cnt">' + ex.length + '건</span></summary>' +
      '<div class="fold-body flush">' +
      '<p style="padding:12px 16px 4px;font-size:.8125rem;color:var(--ink-3)">' +
        '고점 대비 하락 후 10일선 이탈 종목. 매도 신호 아님.' + UI.info('exit') + '</p>' +
      '<div class="rows">' + ex.slice(0, 12).map(function (e) {
        return '<button class="row" type="button" data-code="' + E(e.code) + '" data-name="' + E(e.name) + '">' +
          '<span class="r-rk"></span>' +
          '<span class="r-name"><b>' + E(e.name) + '</b></span>' +
          '<span class="r-meta">' + E(e.sector || '-') + ' · 10일선 ' + C.num(Math.round(e.ma10 || 0)) + '</span>' +
          '<span class="r-price"><b class="num">' + C.num(e.lastClose) + '</b>' +
          '<em class="num down">고점 ' + C.pct(e.drawdownFromHighPct, 1) + '</em></span></button>';
      }).join('') + '</div></div></details>';
  }

  function changesCard(f) {
    var ch = f.changes;
    if (!ch || !ch.available) return '';
    var items = [];
    var nm = function (a) { return (a || []).map(function (x) { return E(x.name || x); }).join(', '); };
    if ((ch.candidatesEntered || []).length) items.push(['새로 진입', nm(ch.candidatesEntered), 'up']);
    if ((ch.candidatesLeft || []).length) items.push(['목록에서 빠짐', nm(ch.candidatesLeft), 'down']);
    if ((ch.sectorsEntered || []).length) items.push(['주도 업종 진입', nm(ch.sectorsEntered), 'up']);
    if ((ch.sectorsLeft || []).length) items.push(['주도 업종 이탈', nm(ch.sectorsLeft), 'down']);
    if ((ch.newExitSignals || []).length) items.push(['새 이탈 신호', nm(ch.newExitSignals), 'down']);
    if (!items.length) return '';
    return '<section class="card"><div class="card-h"><h3>직전 대비 변화</h3>' +
      '<span class="spacer"></span><span class="cnt">' + items.length + '건</span></div>' +
      '<div class="card-pad" style="padding-top:10px;display:flex;flex-direction:column;gap:10px">' +
      items.map(function (it) {
        return '<div style="display:flex;gap:10px;align-items:flex-start">' +
          '<span class="tag tag-' + it[2] + '" style="flex:none">' + E(it[0]) + '</span>' +
          '<span style="font-size:.875rem;color:var(--ink-2)">' + it[1] + '</span></div>';
      }).join('') + '</div></section>';
  }

  function briefCard(b) {
    var secs = (b.sections || []).filter(function (s) { return s && s.body; });
    if (!secs.length) return '';
    return '<details class="fold"><summary>오늘 시장 서술<span class="cnt">' + secs.length + '단락</span></summary>' +
      '<div class="fold-body">' + secs.map(function (s) {
        return '<div style="margin-top:14px"><b style="font-size:.9375rem">' + E(s.title) + '</b>' +
          '<p style="font-size:.9375rem;color:var(--ink-2);margin-top:3px">' + E(s.body) + '</p></div>';
      }).join('') +
      (b.generatedAt ? '<p class="fn-note" style="margin-top:16px">작성 ' + E(C.stamp(b.generatedAt)) +
        (b.llmError ? ' · 규칙 기반 대체' : '') + '</p>' : '') +
      '</div></details>';
  }

  /* 재료 — 테마 데이터는 따로 받는다. 이것 때문에 화면 전체가 늦어지면
     안 되므로 먼저 그리고 도착하면 끼워 넣는다. */
  function themes(root, f) {
    var slot = root.querySelector('#home-themes');
    if (!slot) return;
    C.theme().then(function (td) {
      var ts = (td.themes || []).slice(0, 3);
      if (!ts.length) return;
      var candNames = {};
      (f.buyCandidates || []).forEach(function (c) { candNames[String(c.name).replace(/\s+/g, '')] = 1; });
      var overlap = 0;
      var rows = ts.map(function (t) {
        var stocks = (t.stocks || []).slice(0, 4);
        var chips = stocks.map(function (s) {
          var on = candNames[String(s.name).replace(/\s+/g, '')];
          if (on) overlap++;
          return '<span class="tag' + (on ? ' tag-star' : '') + '">' + E(s.name) +
            (s.changeRate != null ? ' ' + C.pct(s.changeRate, 1) : '') + '</span>';
        }).join('');
        return '<button class="row-block" type="button" data-theme-name="' + E(t.themeName) + '">' +
          '<div style="display:flex;align-items:center;gap:8px">' +
            '<b style="font-size:.9375rem">' + E(t.themeName) + '</b>' +
            '<span class="tag">' + E(t.totalVolume || '') + '</span></div>' +
          '<div style="display:flex;flex-wrap:wrap;gap:5px;margin-top:7px">' + chips + '</div>' +
          '</button>';
      }).join('');
      slot.innerHTML = '<section class="card">' +
        '<div class="card-h"><h3>오늘 움직인 재료</h3>' + UI.info('theme') +
          '<span class="spacer"></span><span class="cnt">' +
          (overlap ? '수급 겹침 ' + overlap : '겹침 없음') + '</span></div>' +
        '<div class="rows" style="margin-top:12px">' + rows + '</div>' +
        '<p style="padding:10px 16px 14px;font-size:.8125rem;color:var(--ink-3)">' +
          (overlap ? '노란 표시 = 재료 보유 + 수급 빠짐 종목'
                   : '재료 보유 종목과 수급 빠짐 종목 겹침 없음. 급등 종목은 대개 수급 유입 완료 상태.') +
        '</p></section>';
    }).catch(function () { /* 재료가 없어도 오늘 화면은 성립한다 */ });
  }

  /* ── 이벤트 ─────────────────────────────────────────────── */
  function bind(root, f, s) {
    root.addEventListener('click', function (e) {
      var go = e.target.closest('[data-go]');
      if (go) { global.App.go(go.dataset.go); return; }

      var sec = e.target.closest('[data-sector]');
      if (sec) { global.App.go('stocks', { sector: sec.dataset.sector }); return; }

      var th = e.target.closest('[data-theme-name]');
      if (th) { global.App.go('themes', { focus: th.dataset.themeName }); return; }

      var cell = e.target.closest('[data-cell]');
      if (cell) { openCell(cell.dataset.cell, f, s); return; }

      var st = e.target.closest('[data-code]');
      if (st) { global.Detail.open(st.dataset.code, st.dataset.name); }
    });
  }

  function openCell(key, f, s) {
    if (key === 'psy') {
      var k = s.k;
      var q = (f.marketSentiment || {}).kosdaq || {};
      UI.sheet('시장 심리',
        '<p style="font-size:.9375rem;color:var(--ink-2)">' +
          '공포·탐욕 지수 = 현재 <b>장세 배경</b>. 매수 신호 아님. ' +
          '구간(과열/강세/중립/약세/공포)은 <b>지수 방향</b> 기준, 숫자는 <b>수준</b>. 두 축은 별개.</p>' +
        '<div class="kv" style="margin-top:14px">' +
          '<div><dt>KOSPI</dt><dd class="num">' + (k.fearGreed != null ? k.fearGreed.toFixed(1) : '-') + ' · ' + E(k.zone || '-') + '</dd></div>' +
          '<div><dt>KOSDAQ</dt><dd class="num">' + (q.fearGreed != null ? q.fearGreed.toFixed(1) : '-') + ' · ' + E(q.zone || '-') + '</dd></div>' +
          '<div><dt>KOSPI 종가</dt><dd class="num">' + C.num(k.close) + '</dd></div>' +
          '<div><dt>KOSDAQ 종가</dt><dd class="num">' + C.num(q.close) + '</dd></div>' +
        '</div>' +
        '<div class="d-sec"><h4>KOSPI · 최근 흐름</h4><div class="chart-box"><div id="psy-chart"></div></div>' +
        '<div class="legend"><span><i style="background:var(--c-ma5)"></i>5일선</span>' +
        '<span><i style="background:var(--c-ma20)"></i>20일선</span>' +
        '<span><i style="background:var(--c-osc)"></i>공포·탐욕</span></div></div>',
        { mounted: function (body) {
            var el = body.querySelector('#psy-chart');
            if (el) global.Viz.mount(el, function (w) { return global.Viz.indexChart(k.history || [], w); });
          } });
      return;
    }

    if (key === 'crowd') {
      var band = s.band, cp = s.cp;
      UI.sheet('장 난이도 (쏠림)',
        '<p style="font-size:.9375rem;color:var(--ink-2)">' +
          '주도 업종과 후행 업종의 수익률 격차. <b>높을수록 소수 업종 독주</b>, 종목 선별 어려움.</p>' +
        (band ? '<div class="notice" style="margin-top:14px;background:var(--surface-2);color:var(--ink-2)">' +
          '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 8v4.5M12 16h.01"/></svg>' +
          '<span><b>' + E(band.label) + '</b> · ' + E(band.desc) +
          (cp != null ? ' · 1년 이력 ' +
            (cp <= 50 ? '하위 ' + Math.max(1, cp) : '상위 ' + Math.max(1, 100 - cp)) + '%' : '') +
          '</span></div>' : '') +
        '<div class="d-sec"><h4>쏠림 추이</h4><div class="chart-box"><div id="cr-chart"></div></div></div>' +
        leadersLag(s.cr),
        { mounted: function (body) {
            var el = body.querySelector('#cr-chart');
            if (el) global.Viz.mount(el, function (w) { return global.Viz.crowding(s.cr.history || [], w); });
          } });
      return;
    }

    if (key === 'cash') {
      var cash = s.cash;
      UI.sheet('현금 비중',
        '<p style="font-size:.9375rem;color:var(--ink-2)">' +
          '공포·탐욕 지수와 쏠림 신호로 산출한 <b>참고용</b> 비중. 매매 지시 아님. ' +
          '과열+쏠림 → 상향, 공포+분산 → 하향.</p>' +
        '<div class="kv" style="margin-top:14px">' +
          '<div><dt>권고 현금</dt><dd class="num">' + (cash.cashPct != null ? cash.cashPct + '%' : '-') + '</dd></div>' +
          '<div><dt>구간</dt><dd>' + E(cash.level || '-') + '</dd></div>' +
          '<div><dt>공포·탐욕</dt><dd class="num">' + (cash.fearGreed != null ? cash.fearGreed.toFixed(1) : '-') + '</dd></div>' +
          '<div><dt>쏠림 신호</dt><dd>' + E(cash.crowdingSignal || '-') + '</dd></div>' +
        '</div>');
    }
  }

  function leadersLag(cr) {
    var L = (cr.leaders || []).slice(0, 5), G = (cr.laggards || []).slice(0, 5);
    if (!L.length && !G.length) return '';
    var list = function (arr, title, cls) {
      return '<div style="flex:1;min-width:0"><h4 style="font-size:.75rem;color:var(--ink-4);font-weight:800;margin-bottom:6px">' +
        title + '</h4>' + arr.map(function (x) {
          return '<div style="display:flex;justify-content:space-between;gap:8px;padding:5px 0;font-size:.875rem">' +
            '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + E(x.name) + '</span>' +
            '<b class="num ' + cls + '">' + C.pct(x.ret6m, 0) + '</b></div>';
        }).join('') + '</div>';
    };
    return '<div class="d-sec"><h4>6개월 수익률 양 끝</h4>' +
      '<div style="display:flex;gap:18px">' + list(L, '앞선 ETF', 'up') + list(G, '뒤처진 ETF', 'down') + '</div></div>';
  }

  global.HomeView = { view: view };
})(window);
