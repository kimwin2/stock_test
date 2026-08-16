/* ============================================================
   detail.js — 종목 상세 시트
   ------------------------------------------------------------
   순서가 곧 사용자가 묻는 순서다:
     1) 얼마인가        가격 · 등락 · 업종
     2) 어떻게 생겼나   일봉 + 수급 오실레이터
     3) 왜 뽑혔나       포착 경로 (이게 이 제품의 신뢰 근거)
     4) 수급이 언제 돌았나  10일 히트맵
     5) 숫자            거래대금 배율 · 외인지분 · 이평
   3번을 4·5번 아래로 내리면 "AI 가 뽑았다" 는 주장으로만 남는다.
   ============================================================ */
(function (global) {
  'use strict';

  var C = global.Core, E = C.esc;

  function open(code, name) {
    C.flow().then(function (f) {
      var hit = C.findStock(f, code);
      if (!hit) {
        global.UI.sheet(name || String(code), global.UI.empty({
          title: '분석 대상에 없는 종목입니다',
          desc: '코스피·코스닥 시가총액 상위 종목만 분석합니다.'
        }));
        return;
      }
      render(hit.rec, hit, f);
    }).catch(function (e) {
      global.UI.sheet(name || String(code), global.UI.errorState(e.message));
    });
  }

  function render(c, hit, f) {
    var watched = C.watchHas(c.code);
    var ret5 = c.ret5d;
    var ss = C.supplyState(c);
    var px = c.close != null ? c.close : c.lastClose;
    var kinds = hit.kinds || [hit.kind];
    var has = function (k) { return kinds.indexOf(k) >= 0; };

    var tags = [];
    if (has('cand')) tags.push('<span class="tag tag-up">오늘 후보</span>');
    if (has('overflow')) tags.push('<span class="tag tag-warn">업종 상한</span>');
    if (has('exit')) tags.push('<span class="tag tag-down">이탈 신호</span>');
    if (has('value') && !has('cand')) tags.push('<span class="tag">거래대금 상위</span>');
    if (c.vacancyZone) tags.push('<span class="tag ' + (c.vacancyZone === '빈집' ? 'tag-down' : '') + '">' + E(c.vacancyZone) + '</span>');
    // 이탈 신호는 10일선을 내줬다는 뜻이다. 같은 카드에 '추세 생존'을 나란히
    // 걸면 정면으로 모순돼 보인다 — 더 구체적인 판정인 이탈 쪽을 남긴다.
    if (c.aboveMA10 && !has('exit')) tags.push('<span class="tag tag-ok">추세 생존</span>');
    if (c.newHigh50d) tags.push('<span class="tag tag-up">50일 신고가</span>');

    var html =
      '<div class="d-top">' +
        (px != null ? '<span class="d-px num">' + C.num(px) + '</span>' : '<span class="d-px">—</span>') +
        (ret5 != null ? '<span class="d-chg num ' + C.dirClass(ret5) + '">5일 ' + C.pct(ret5, 1) + '</span>' : '') +
      '</div>' +
      '<div class="d-sub">' +
        '<span class="tag">' + E(c.sector || hit.sector || '기타') + '</span>' +
        '<span class="tag">' + E(c.market || '') + ' ' + E(c.code) + '</span>' +
        tags.join('') +
      '</div>' +

      '<div class="d-acts">' +
        '<button class="btn btn-watch' + (watched ? ' is-on' : ' btn-primary') + '" type="button" id="d-watch" ' +
          'aria-pressed="' + watched + '">' +
          starSvg() + '<span>' + (watched ? '관심 담김' : '관심 담기') + '</span>' +
        '</button>' +
        '<button class="btn btn-ghost" type="button" id="d-full">' +
          '<svg viewBox="0 0 24 24"><path d="M4 19V9M9.3 19V5M14.7 19v-7M20 19V8"/></svg><span>차트 크게</span>' +
        '</button>' +
      '</div>' +

      chartSection(c) +
      pathSection(c, f) +
      gaugeSection(c) +
      heatSection(c, ss) +
      numbersSection(c);

    global.UI.sheet(c.name || hit.name, html, {
      mounted: function (body) {
        var box = body.querySelector('#d-chart');
        if (box) global.Viz.mount(box, function (w) { return global.Viz.priceOsc(c, w); });

        var wb = body.querySelector('#d-watch');
        if (wb) wb.addEventListener('click', function () {
          var on = C.watchToggle(c.code, c.name);
          wb.classList.toggle('btn-primary', !on);
          wb.classList.toggle('is-on', on);
          wb.setAttribute('aria-pressed', String(on));
          wb.querySelector('span').textContent = on ? '관심 담김' : '관심 담기';
          global.UI.toast(on ? '관심에 담았습니다' : '관심에서 뺐습니다');
          global.App.refreshWatchDot();
        });

        var fb = body.querySelector('#d-full');
        if (fb) fb.addEventListener('click', function () { global.UI.openFull(c.code, c.name); });
      }
    });
  }

  function starSvg() {
    return '<svg viewBox="0 0 24 24"><path d="M12 4.8l2.3 4.7 5.2.8-3.75 3.65.9 5.15L12 16.65 7.35 19.1l.9-5.15L4.5 10.3l5.2-.8z"/></svg>';
  }

  /* 2) 차트 */
  function chartSection(c) {
    var has = (c.priceHistory60d && c.priceHistory60d.length >= 2) || (c.ohlc60d && c.ohlc60d.length >= 2);
    if (!has) {
      return '<div class="d-sec"><h4>흐름</h4>' +
        '<div class="chart-empty">이 종목은 60일 시계열을 계산하지 않았습니다.<br>“차트 크게”로 실시간 캔들을 볼 수 있습니다.</div></div>';
    }
    return '<div class="d-sec">' +
      '<h4>흐름 <span style="text-transform:none;letter-spacing:0;font-weight:600">· 60거래일</span>' + global.UI.info('osc') + '</h4>' +
      '<div class="chart-box"><div id="d-chart"></div></div>' +
      '<div class="legend">' +
        '<span><i style="background:var(--c-ma5)"></i>5일선</span>' +
        '<span><i style="background:var(--c-ma20)"></i>20일선</span>' +
        '<span><i style="background:var(--up)"></i>수급 유입</span>' +
        '<span><i style="background:var(--down)"></i>수급 빈집</span>' +
      '</div></div>';
  }

  /* 3) 포착 경로 — 왜 이 종목이 여기 있나 */
  function pathSection(c, f) {
    var labels = (f && f.leadingSectorLabels) || [];
    var rank = labels.indexOf(c.sector);
    var steps = [];

    if (rank >= 0) {
      var src = ((f && f.leadingSectorSources) || {})[c.sector] || {};
      if (src.via === 'etf' && src.etf) {
        steps.push([src.etf + ' 강세', '시장 대비 강도 ' + src.rsNorm]);
      } else if (src.via === 'flow' && src.strength != null) {
        steps.push(['외인·기관 자금 유입', '시총 대비 강도 ' + src.strength]);
      } else if (src.via === 'turnover' && src.sharePct != null) {
        steps.push(['거래대금 쏠림', '시장 거래대금의 ' + src.sharePct + '%']);
      } else {
        steps.push(['주도 업종 판정', '세 축 중 하나에서 통과']);
      }
      steps.push([c.sector + ' 업종', '오늘 주도 ' + (rank + 1) + '위']);
    } else if (c.sector) {
      steps.push([c.sector + ' 업종', '오늘 주도 업종은 아님']);
    }

    var eh = c.etfHoldings;
    if (eh && eh.top && eh.top.length) {
      var more = eh.count > 1 ? ' 외 ' + (eh.count - 1) + '개' : '';
      steps.push([eh.top[0].etf + more + '이 편입', '합계 ' + eh.totalWeight + '%' +
        (eh.leadingCount ? ' · 주도 ETF 포함' : '')]);
    } else if (eh === null || (eh && !eh.count)) {
      steps.push(['테마 ETF 미편입', '수급 근거만으로 올라온 종목']);
    }

    var last = [];
    if (c.oscPercentile != null) last.push('빈집 하위 ' + Math.round(c.oscPercentile) + '%');
    if (c.aboveMA10) last.push('10일선 위');
    if (c.flowScore != null) last.push('점수 ' + c.flowScore);
    if (last.length) steps.push([c.name, last.join(' · ')]);

    if (!steps.length) return '';

    return '<div class="d-sec"><h4>왜 이 종목인가' + global.UI.info('leading') + '</h4>' +
      '<div class="path">' + steps.map(function (s, i) {
        return '<div class="path-step">' +
          '<span class="path-rail"><i class="path-dot"></i><i class="path-line"></i></span>' +
          '<span class="path-txt"><b>' + E(s[0]) + '</b><span>' + E(s[1]) + '</span></span></div>';
      }).join('') + '</div>' +
      reasonsLine(c) + '</div>';
  }

  // 점수 근거를 사람 말로 바꾼다. 백엔드 문자열은 로그용 표기라
  // "↑10MA" · "50d 신고가" · "정배열(10>20>50)" 처럼 그대로 두면 읽히지 않는다.
  var REASON_WORDS = [
    [/^↑\s*(\d+)MA$/, '$1일선 위'],
    [/^(\d+)d 신고가$/, '$1일 신고가'],
    [/^정배열\(([^)]*)\)$/, '이평선 정배열'],
    [/^(\d+)MA 우상향$/, '$1일선 우상향'],
    [/^매수권 진입$/, '눌림 매수권'],
    [/^거래대금 강도 하단.*$/, '거래대금 강도 바닥권'],
    [/^매도 (\d+)일 연속$/, '$1일 연속 순매도'],
    [/^(\d+)d 추세 ([+-][\d.]+%)$/, '$1일 추세 $2'],
    [/^(\d+)d ([+-][\d.]+%)$/, '$1일 등락 $2'],
    [/^주도섹터 (\d+)위\(([^)]*)\)$/, '$2 주도 $1위'],
    [/^외인 5d 매수 #\d+ \(([^)]*)\)$/, '외국인 5일 순매수 $1']
  ];
  function humanize(t) {
    for (var i = 0; i < REASON_WORDS.length; i++) {
      if (REASON_WORDS[i][0].test(t)) return t.replace(REASON_WORDS[i][0], REASON_WORDS[i][1]);
    }
    return t;
  }

  function reasonsLine(c) {
    var rs = (c.flowReasons || [])
      .filter(function (r) { return r && !/osc=|pct=|ratio=/.test(r); })
      .map(function (r) {
        return {
          text: humanize(r.replace(/\s*[+-]\d+(\.\d+)?$/, '').replace(/^★\s*/, '').trim()),
          pts: Math.abs(parseFloat((r.match(/([+-]\d+(?:\.\d+)?)\s*$/) || [])[1] || 0))
        };
      })
      .filter(function (x) { return x.text; })
      .sort(function (a, b) { return b.pts - a.pts; })
      .slice(0, 3);
    if (!rs.length) return '';
    return '<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px">' +
      rs.map(function (x) { return '<span class="tag">' + E(x.text) + '</span>'; }).join('') + '</div>';
  }

  /* 수급 게이지 5단 */
  function gaugeSection(c) {
    if (c.vacancyZone == null && c.oscPercentile == null) return '';
    var idx = C.supplyLevel(c.vacancyZone, c.oscPercentile);
    var lv = C.SUPPLY_LEVELS[idx];
    var amt = c.institutionNet5d;
    return '<div class="d-sec"><h4>수급 상태' + global.UI.info('vacancy') + '</h4>' +
      '<div class="gauge5"><div class="g5-track">' +
      C.SUPPLY_LEVELS.map(function (s, i) {
        return '<span class="g5-seg on-' + i + (i === idx ? ' is-on' : '') + '">' + E(s.label) + '</span>';
      }).join('') +
      '</div><p class="g5-cap"><b>' + E(lv.label) + '</b> — ' + E(lv.desc) +
      (amt != null ? ' · 외인+기관 5일 <b class="num ' + C.dirClass(amt) + '">' + C.won(amt) + '</b>' : '') +
      '</p>' +
      // 순매수인데 '빈집' 이면 정면으로 모순돼 보인다. 빈집은 부호가 아니라
      // 감속이라는 걸 이 자리에서 한 줄로 밝혀야 오해가 안 남는다.
      ((amt != null && amt > 0 && c.vacancyZone === '빈집')
        ? '<p class="g5-cap" style="color:var(--ink-4)">순매수인데 빈집인 이유 — 빈집은 사고 파는 <b>부호</b>가 아니라 ' +
          '<b>사들이는 속도</b>를 봅니다. 5일 순매수가 플러스여도 20일 페이스보다 느려졌으면 빈집입니다.</p>'
        : '') +
      '</div></div>';
  }

  /* 4) 10일 수급 히트맵 */
  function heatSection(c, ss) {
    var days = c.dailyFlow10d || [];
    if (!days.length) return '';
    var maxAbs = Math.max.apply(null, days.map(function (d) {
      return Math.max(Math.abs(d.foreigner || 0), Math.abs(d.organ || 0));
    }).concat([1]));

    function cells(key) {
      return days.map(function (d, i) {
        var v = d[key] || 0;
        var a = Math.min(1, Math.abs(v) / maxAbs);
        var col = v === 0 ? 'var(--surface-3)'
          : (v > 0 ? 'color-mix(in srgb, var(--up) ' + Math.round(18 + a * 82) + '%, transparent)'
                   : 'color-mix(in srgb, var(--down) ' + Math.round(18 + a * 82) + '%, transparent)');
        // style 을 두 번 쓰면 뒤쪽이 무시된다 — 전환일 테두리가 안 그려지던 원인.
        // 한 속성으로 합친다.
        var css = 'background:' + col +
          ((ss && ss.turnIdx === i) ? ';box-shadow:inset 0 0 0 2px var(--ink)' : '');
        return '<i class="hm-cell" style="' + css + '" title="' +
               E(String(d.date || '')) + ' ' + E(C.won(v)) + '"></i>';
      }).join('');
    }

    return '<div class="d-sec"><h4>수급이 언제 돌았나 <span style="text-transform:none;letter-spacing:0;font-weight:600">· 최근 10거래일</span></h4>' +
      '<div class="hm">' +
        '<div class="hm-line"><span class="hm-lab">외국인</span><span class="hm-cells">' + cells('foreigner') + '</span></div>' +
        '<div class="hm-line"><span class="hm-lab">기관</span><span class="hm-cells">' + cells('organ') + '</span></div>' +
        '<div class="hm-line"><span class="hm-lab"></span><span class="hm-days">' +
          days.map(function (d) {
            var m = /(\d{2})-(\d{2})$/.exec(String(d.date || ''));
            return '<span class="hm-day">' + (m ? (+m[2]) : '') + '</span>';
          }).join('') + '</span></div>' +
        '<p class="hm-legend">붉을수록 순매수 · 푸를수록 순매도' +
          (ss && ss.turnIdx >= 0 ? ' · 테두리 = 순매수로 돌아선 날' : '') +
          ' · 지금은 <b>' + E(ss.label) + '</b></p>' +
      '</div></div>';
  }

  /* 5) 숫자 */
  function numbersSection(c) {
    var items = [];
    var bz = c.buyZone || {};
    if (bz.todayPullbackPct != null)
      items.push(['고가 대비', '<span class="' + C.dirClass(bz.todayPullbackPct) + '">' + C.pct(bz.todayPullbackPct, 1) + '</span>']);
    if (c.tradingValueRatio != null)
      items.push(['거래대금 <small>20일 대비</small>', c.tradingValueRatio.toFixed(2) + '배']);
    if (c.foreignerHoldRatio) items.push(['외인 지분', E(c.foreignerHoldRatio)]);
    if (c.marketCap) items.push(['시가총액', C.won(c.marketCap)]);
    if (c.ret20d != null)
      items.push(['20일 등락', '<span class="' + C.dirClass(c.ret20d) + '">' + C.pct(c.ret20d, 1) + '</span>']);
    if (c.ma10 != null) items.push(['10일선', C.num(Math.round(c.ma10))]);
    if (c.ma20 != null) items.push(['20일선', C.num(Math.round(c.ma20))]);
    if (c.max250d != null) items.push(['250일 최고', C.num(Math.round(c.max250d))]);
    if (c.drawdownFromHighPct != null)
      items.push(['최근 고점 대비', '<span class="down">' + C.pct(c.drawdownFromHighPct, 1) + '</span>']);
    if (c.recentHigh != null) items.push(['최근 고점', C.num(Math.round(c.recentHigh))]);
    if (c.ti != null) items.push(['거래대금 강도', c.ti + ' <small>' + E(c.zone || '') + '</small>']);
    if (!items.length) return '';
    return '<div class="d-sec"><h4>숫자</h4><div class="kv">' +
      items.map(function (kv) {
        return '<div><dt>' + kv[0] + '</dt><dd class="num">' + kv[1] + '</dd></div>';
      }).join('') + '</div></div>';
  }

  global.Detail = { open: open };
})(window);
