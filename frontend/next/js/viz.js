/* ============================================================
   viz.js — SVG 그림 조각
   ------------------------------------------------------------
   기존 화면이 반복해서 부딪힌 문제 하나를 구조로 없앤다.

   기존: viewBox 폭을 화면 폭에 따라 320 또는 760 중에서 고른다.
         → 실제 표시 폭이 그 사이(폴드 펼침, 태블릿 세로)면 SVG 가
           0.72~1.7배로 늘어나고 축·라벨 글자가 같이 늘어난다.
           "8.6px 로 적었는데 6px 로 보인다" 가 여기서 나왔다.

   여기: **실제 픽셀 폭을 재서 그 폭으로 viewBox 를 잡는다.** 배율이
         항상 1이라 font-size 로 적은 숫자가 그대로 그 크기로 보인다.
         Viz.mount() 가 측정·삽입·리사이즈 재렌더를 다 맡는다.
   ============================================================ */
(function (global) {
  'use strict';

  var E = global.Core.esc;

  /* ── 색 토큰 — CSS 변수를 읽는다(다크모드 대응) ───────────── */
  var tokCache = null;
  function tok() {
    if (tokCache) return tokCache;
    var cs = getComputedStyle(document.documentElement);
    var g = function (n, fb) { return (cs.getPropertyValue(n) || '').trim() || fb; };
    tokCache = {
      up: g('--up', '#E02B2B'), down: g('--down', '#1763D4'), flat: g('--flat', '#8A9099'),
      price: g('--c-price', '#1F2933'), ma5: g('--c-ma5', '#8E5BE8'), ma20: g('--c-ma20', '#EE9A1E'),
      osc: g('--c-osc', '#B4791E'), grid: g('--c-grid', '#E5E8EC'), axis: g('--c-axis', '#8B929C'),
      surface: g('--surface', '#fff'), surface2: g('--surface-2', '#F7F8FA'),
      surface3: g('--surface-3', '#EDEFF3'), ink: g('--ink', '#14171C'), ink3: g('--ink-3', '#6B7280')
    };
    return tokCache;
  }
  function dropTok() { tokCache = null; }

  /* ── mount — 측정 후 렌더, 폭이 바뀌면 다시 그린다 ────────── */
  var mounted = [];
  function mount(el, build) {
    if (!el) return;
    var entry = { el: el, build: build, w: 0 };
    mounted.push(entry);
    draw(entry);
  }
  function draw(entry) {
    var w = Math.round(entry.el.clientWidth || entry.el.getBoundingClientRect().width);
    if (!(w > 0)) w = 320;                    // 숨겨진 컨테이너 — 나중에 리사이즈로 잡힌다
    if (Math.abs(w - entry.w) < 8) return;
    entry.w = w;
    entry.el.innerHTML = entry.build(w) || '';
  }
  function redrawAll(force) {
    for (var i = mounted.length - 1; i >= 0; i--) {
      var e = mounted[i];
      if (!document.body.contains(e.el)) { mounted.splice(i, 1); continue; }
      if (force) e.w = 0;
      draw(e);
    }
  }
  function clearMounts() { mounted.length = 0; }

  var rt = null;
  global.addEventListener('resize', function () {
    clearTimeout(rt);
    rt = setTimeout(function () { redrawAll(false); }, 140);
  });

  /* ── 계산 도우미 ────────────────────────────────────────── */
  function sma(arr, p) {
    var out = new Array(arr.length).fill(null);
    for (var i = p - 1; i < arr.length; i++) {
      var s = 0, ok = 0;
      for (var j = i - p + 1; j <= i; j++) {
        var v = arr[j];
        if (v != null && !isNaN(v)) { s += v; ok++; }
      }
      if (ok === p) out[i] = s / p;
    }
    return out;
  }
  function fin(a) { return a.filter(function (v) { return v != null && !isNaN(v); }); }
  function f1(n) { return Number(n).toFixed(1); }
  function mdY(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(s || ''));
    return m ? (+m[2]) + '/' + (+m[3]) : String(s || '');
  }

  /* ============================================================
     종목 차트 — 위: 일봉+거래량+MA / 아래: 수급 오실레이터
     ------------------------------------------------------------
     아래창을 '백분위 음영 + 선' 에서 **0선 기준 막대**로 바꿨다.
     이 값은 MACD 히스토그램이다. 히스토그램을 선으로 그리면 0선을
     넘나드는 순간(= 빈집에 들어가고 나오는 순간)이 안 보인다.
     막대면 파란 구간이 곧 빈집이라 설명 없이 읽힌다.
     ============================================================ */
  function priceOsc(c, W, opts) {
    opts = opts || {};
    var T = tok();
    var price = (c.priceHistory60d && c.priceHistory60d.length >= 2) ? c.priceHistory60d : null;
    var ohlc = (c.ohlc60d && c.ohlc60d.length >= 2) ? c.ohlc60d : null;
    var oscSrc = c.supplyOscHistory || [];
    var dates = c.dateHistory60d || oscSrc.map(function (o) { return o && o.date; });
    if (!price && !ohlc) return emptyBox('차트 데이터 없음');

    var closes = price || ohlc.map(function (d) { return d.c; });
    var n = closes.length;
    if (ohlc && ohlc.length !== n) ohlc = ohlc.slice(-n);
    var osc = oscSrc.slice(-n).map(function (o) { return (o && o.osc != null) ? o.osc : null; });
    while (osc.length < n) osc.unshift(null);
    // opts.noOsc — 수급 창을 끈다. 종목 상세는 수급을 '큰손 움직임' 섹션이
    // 전담하므로, 여기서 또 그리면 한 화면에 수급 그림이 둘이 된다.
    var hasOsc = !opts.noOsc && fin(osc).length >= 2;

    var H = opts.height || Math.round(Math.max(180, Math.min(320, W * 0.64)));
    var padL = 6, padR = 58, padT = 16, padB = 16;
    // 아래창 제목("수급 오실레이터…")이 들어갈 자리. 좁게 두면 위창의
    // 거래량 막대와 글자가 겹친다.
    var gap = 20;
    var body = H - padT - padB;
    var priceH = Math.round(body * (hasOsc ? 0.63 : 1));
    var oscTop = padT + priceH + gap;
    var oscH = hasOsc ? (padT + body - oscTop) : 0;
    var x0 = padL, x1 = W - padR;
    var X = function (i) { return x0 + (n <= 1 ? 0 : (i / (n - 1)) * (x1 - x0)); };

    var ma5 = sma(closes, 5), ma20 = sma(closes, 20);
    var all = fin(closes).concat(fin(ma5)).concat(fin(ma20));
    if (ohlc) ohlc.forEach(function (d) { if (d) { all.push(d.h, d.l); } });
    var pMin = Math.min.apply(null, all), pMax = Math.max.apply(null, all);
    var padP = (pMax - pMin) * 0.09 || 1;
    pMin -= padP; pMax += padP;
    var yP = function (v) { return padT + (1 - (v - pMin) / (pMax - pMin)) * priceH; };

    var s = '';

    // 거래량 (가격창 하단 22%)
    if (ohlc) {
      var bw = Math.max(1.4, Math.min(9, (x1 - x0) / n * 0.62));
      var vMax = Math.max.apply(null, ohlc.map(function (d) { return (d && d.v) || 0; }).concat([1]));
      var vBase = padT + priceH, vH = priceH * 0.2;
      s += ohlc.map(function (d, i) {
        if (!d || !d.v) return '';
        var h = (d.v / vMax) * vH;
        return '<rect x="' + f1(X(i) - bw / 2) + '" y="' + f1(vBase - h) + '" width="' + f1(bw) +
               '" height="' + f1(h) + '" fill="' + (d.c >= d.o ? T.up : T.down) + '" opacity=".18"/>';
      }).join('');
    }

    // MA
    var poly = function (arr, color, w) {
      var pts = arr.map(function (v, i) { return v == null ? null : f1(X(i)) + ',' + f1(yP(v)); })
                   .filter(Boolean).join(' ');
      return pts ? '<polyline points="' + pts + '" fill="none" stroke="' + color +
                   '" stroke-width="' + w + '" stroke-linejoin="round" stroke-linecap="round"/>' : '';
    };
    s += poly(ma20, T.ma20, 1.2);
    s += poly(ma5, T.ma5, 1.2);

    // 캔들 (없으면 종가 선)
    if (ohlc) {
      var bw2 = Math.max(1.4, Math.min(9, (x1 - x0) / n * 0.62));
      s += ohlc.map(function (d, i) {
        if (!d) return '';
        var up = d.c >= d.o, col = up ? T.up : T.down, cx = X(i);
        var yo = yP(d.o), yc = yP(d.c);
        var top = Math.min(yo, yc), bh = Math.max(0.9, Math.abs(yc - yo));
        return '<line x1="' + f1(cx) + '" y1="' + f1(yP(d.h)) + '" x2="' + f1(cx) + '" y2="' + f1(yP(d.l)) +
               '" stroke="' + col + '" stroke-width="1"/>' +
               '<rect x="' + f1(cx - bw2 / 2) + '" y="' + f1(top) + '" width="' + f1(bw2) + '" height="' + f1(bh) +
               '" fill="' + (up ? col : T.surface) + '" stroke="' + col + '" stroke-width="1"/>';
      }).join('');
    } else {
      s += poly(closes, T.price, 1.9);
    }

    // 현재가 pill — HTS 관례. 우측 축에 색 태그 + 점선 가이드
    var lastC = fin(closes).slice(-1)[0];
    var firstC = fin(closes)[0];
    var dirCol = lastC >= firstC ? T.up : T.down;
    var ly = yP(lastC), lab = Number(lastC).toLocaleString('ko-KR');
    var tw = Math.min(padR - 6, Math.max(34, lab.length * 7.2 + 10)), th = 17;
    s += '<line x1="' + x0 + '" y1="' + f1(ly) + '" x2="' + f1(x1) + '" y2="' + f1(ly) +
         '" stroke="' + dirCol + '" stroke-width=".8" stroke-dasharray="3 2" opacity=".45"/>' +
         '<rect x="' + f1(x1 + 3) + '" y="' + f1(ly - th / 2) + '" width="' + tw + '" height="' + th +
         '" rx="3" fill="' + dirCol + '"/>' +
         '<text x="' + f1(x1 + 3 + tw / 2) + '" y="' + f1(ly + 4.2) +
         '" font-size="11" font-weight="800" fill="#fff" text-anchor="middle">' + E(lab) + '</text>';

    s += '<text x="' + x0 + '" y="' + (padT - 5) + '" font-size="11" font-weight="700" fill="' + T.axis + '">' +
         (ohlc ? '일봉 · 거래량' : '주가') + '</text>';

    // ── 아래창: 수급 오실레이터 히스토그램 ──
    if (hasOsc) {
      var ov = osc.map(function (v) { return v == null ? 0 : v; });
      var oMax = Math.max.apply(null, ov.map(Math.abs).concat([1e-9]));
      var zeroY = oscTop + oscH / 2;
      var yO = function (v) { return zeroY - (v / oMax) * (oscH / 2 - 3); };
      var obw = Math.max(1.2, (x1 - x0) / n * 0.7);
      s += '<rect x="' + x0 + '" y="' + f1(oscTop) + '" width="' + f1(x1 - x0) + '" height="' + f1(oscH) +
           '" fill="' + T.surface3 + '" opacity=".5" rx="3"/>';
      s += ov.map(function (v, i) {
        if (!v) return '';
        var y = yO(v), h = Math.max(0.8, Math.abs(zeroY - y));
        return '<rect x="' + f1(X(i) - obw / 2) + '" y="' + f1(Math.min(y, zeroY)) + '" width="' + f1(obw) +
               '" height="' + f1(h) + '" fill="' + (v >= 0 ? T.up : T.down) + '" opacity=".78"/>';
      }).join('');
      s += '<line x1="' + x0 + '" y1="' + f1(zeroY) + '" x2="' + f1(x1) + '" y2="' + f1(zeroY) +
           '" stroke="' + T.axis + '" stroke-width=".9" opacity=".6"/>';
      s += '<text x="' + x0 + '" y="' + f1(oscTop - 4) + '" font-size="11" font-weight="700" fill="' + T.osc +
           '">수급 오실레이터<tspan fill="' + T.axis + '" font-weight="600"> · 0 아래 = 큰손이 빠지는 중</tspan></text>';

      // 끝점 라벨은 백분위로. 원값(1e-4 수준)을 %로 찍으면 0 과 구별이 안 된다.
      var pctv = c.oscPercentile;
      if (pctv != null) {
        var pl = '하위 ' + Math.round(pctv) + '%';
        s += '<text x="' + f1(x1 - 3) + '" y="' + f1(oscTop + 13) + '" font-size="11.5" font-weight="800" fill="' +
             (ov[n - 1] < 0 ? T.down : T.up) + '" text-anchor="end">' + E(pl) + '</text>';
      }
    }

    // X축 날짜 3틱
    if (dates && dates.length >= 2) {
      var last = n - 1;
      [[0, 'start'], [Math.round(last / 2), 'middle'], [last, 'end']].forEach(function (t) {
        var d = dates[dates.length - n + t[0]] || dates[t[0]];
        if (!d) return;
        s += '<text x="' + f1(X(t[0])) + '" y="' + (H - 3) + '" font-size="11" fill="' + T.axis +
             '" text-anchor="' + t[1] + '">' + E(mdY(d)) + '</text>';
      });
    }

    return svg(W, H, s);
  }

  /* ============================================================
     지수 차트 — 일봉 + 공포·탐욕 오실레이터
     ============================================================ */
  function indexChart(history, W, opts) {
    opts = opts || {};
    var T = tok();
    if (!history || history.length < 5) return emptyBox('데이터 부족');
    var h = history.slice(-Math.min(history.length, opts.points || 120));
    var closes = h.map(function (p) { return p.close; });
    var ohlc = h.map(function (p) { return p.ohlc || null; });
    var hasC = ohlc.filter(Boolean).length >= Math.floor(h.length * 0.6);
    var fgv = h.map(function (p) { return p.fearGreed != null ? p.fearGreed : null; });
    var hasFg = fin(fgv).length >= 2;

    var n = h.length;
    var H = opts.height || Math.round(Math.max(150, Math.min(280, W * 0.52)));
    var padL = 6, padR = 54, padT = 16, padB = 16, gap = 10;
    var body = H - padT - padB;
    var priceH = Math.round(body * (hasFg ? 0.64 : 1));
    var fgTop = padT + priceH + gap;
    var fgH = hasFg ? (padT + body - fgTop) : 0;
    var x0 = padL, x1 = W - padR;
    var X = function (i) { return x0 + (n <= 1 ? 0 : (i / (n - 1)) * (x1 - x0)); };

    var ma5 = sma(closes, 5), ma20 = sma(closes, 20);
    var all = fin(closes).concat(fin(ma5)).concat(fin(ma20));
    if (hasC) ohlc.forEach(function (d) { if (d) all.push(d.h, d.l); });
    var pMin = Math.min.apply(null, all), pMax = Math.max.apply(null, all);
    var pp = (pMax - pMin) * 0.08 || 1; pMin -= pp; pMax += pp;
    var yP = function (v) { return padT + (1 - (v - pMin) / (pMax - pMin)) * priceH; };
    var s = '';

    var poly = function (arr, color, w) {
      var pts = arr.map(function (v, i) { return v == null ? null : f1(X(i)) + ',' + f1(yP(v)); })
                   .filter(Boolean).join(' ');
      return pts ? '<polyline points="' + pts + '" fill="none" stroke="' + color + '" stroke-width="' + w +
                   '" stroke-linejoin="round" stroke-linecap="round"/>' : '';
    };

    if (hasC) {
      var bw = Math.max(1.2, Math.min(7, (x1 - x0) / n * 0.6));
      s += ohlc.map(function (d, i) {
        if (!d) return '';
        var up = d.c >= d.o, col = up ? T.up : T.down, cx = X(i);
        var yo = yP(d.o), yc = yP(d.c);
        return '<line x1="' + f1(cx) + '" y1="' + f1(yP(d.h)) + '" x2="' + f1(cx) + '" y2="' + f1(yP(d.l)) +
               '" stroke="' + col + '" stroke-width=".9"/>' +
               '<rect x="' + f1(cx - bw / 2) + '" y="' + f1(Math.min(yo, yc)) + '" width="' + f1(bw) +
               '" height="' + f1(Math.max(0.8, Math.abs(yc - yo))) + '" fill="' + (up ? col : T.surface) +
               '" stroke="' + col + '" stroke-width=".9"/>';
      }).join('');
    } else {
      s += poly(closes, T.price, 1.9);
    }
    s += poly(ma20, T.ma20, 1.1);
    s += poly(ma5, T.ma5, 1.1);

    var lastC = fin(closes).slice(-1)[0], firstC = fin(closes)[0];
    var col = lastC >= firstC ? T.up : T.down;
    var ly = yP(lastC), lab = Math.round(lastC).toLocaleString('ko-KR');
    var tw = Math.min(padR - 6, Math.max(34, lab.length * 7.2 + 10)), th = 17;
    s += '<line x1="' + x0 + '" y1="' + f1(ly) + '" x2="' + f1(x1) + '" y2="' + f1(ly) + '" stroke="' + col +
         '" stroke-width=".8" stroke-dasharray="3 2" opacity=".45"/>' +
         '<rect x="' + f1(x1 + 3) + '" y="' + f1(ly - th / 2) + '" width="' + tw + '" height="' + th +
         '" rx="3" fill="' + col + '"/>' +
         '<text x="' + f1(x1 + 3 + tw / 2) + '" y="' + f1(ly + 4.2) +
         '" font-size="11" font-weight="800" fill="#fff" text-anchor="middle">' + E(lab) + '</text>';

    // 아래창: 공포·탐욕 0~100. 25/75 기준선이 있어야 숫자가 뜻을 갖는다.
    if (hasFg) {
      var yF = function (v) { return fgTop + (1 - Math.max(0, Math.min(100, v)) / 100) * fgH; };
      s += '<rect x="' + x0 + '" y="' + f1(fgTop) + '" width="' + f1(x1 - x0) + '" height="' + f1(fgH) +
           '" fill="' + T.surface3 + '" opacity=".5" rx="3"/>';
      [[75, T.up], [25, T.down]].forEach(function (g) {
        s += '<line x1="' + x0 + '" y1="' + f1(yF(g[0])) + '" x2="' + f1(x1) + '" y2="' + f1(yF(g[0])) +
             '" stroke="' + g[1] + '" stroke-width=".8" stroke-dasharray="3 3" opacity=".5"/>';
      });
      var pts = fgv.map(function (v, i) { return v == null ? null : f1(X(i)) + ',' + f1(yF(v)); })
                   .filter(Boolean).join(' ');
      s += '<polyline points="' + pts + '" fill="none" stroke="' + T.osc + '" stroke-width="1.6" stroke-linejoin="round"/>';
      var lastF = fin(fgv).slice(-1)[0];
      s += '<text x="' + x0 + '" y="' + f1(fgTop - 4) + '" font-size="11" font-weight="700" fill="' + T.osc +
           '">공포·탐욕<tspan fill="' + T.axis + '" font-weight="600"> · 25 공포 / 75 탐욕</tspan></text>' +
           '<text x="' + f1(x1 - 3) + '" y="' + f1(yF(lastF) - 4) + '" font-size="11.5" font-weight="800" fill="' + T.osc +
           '" text-anchor="end">' + lastF.toFixed(1) + '</text>';
    }

    var ds = h.map(function (p) { return p.date; });
    if (ds.length >= 2) {
      var last = n - 1;
      [[0, 'start'], [Math.round(last / 2), 'middle'], [last, 'end']].forEach(function (t) {
        if (!ds[t[0]]) return;
        s += '<text x="' + f1(X(t[0])) + '" y="' + (H - 3) + '" font-size="11" fill="' + T.axis +
             '" text-anchor="' + t[1] + '">' + E(mdY(ds[t[0]])) + '</text>';
      });
    }
    return svg(W, H, s);
  }

  /* ============================================================
     스파크라인 — 행 안에 들어가는 최소 그림
     ============================================================ */
  function spark(values, W, opts) {
    opts = opts || {};
    var T = tok();
    var H = opts.height || 32;
    var v = fin(values || []);
    if (v.length < 2) return '';
    var min = Math.min.apply(null, v), max = Math.max.apply(null, v), sp = (max - min) || 1;
    var arr = values;
    var pts = arr.map(function (x, i) {
      if (x == null) return null;
      return f1(i / (arr.length - 1) * W) + ',' + f1(H - 2 - ((x - min) / sp) * (H - 4));
    }).filter(Boolean).join(' ');
    var col = opts.color || (v[v.length - 1] >= v[0] ? T.up : T.down);
    var ref = '';
    if (opts.ref != null) {
      var ry = H - 2 - ((opts.ref - min) / sp) * (H - 4);
      ref = '<line x1="0" y1="' + f1(ry) + '" x2="' + W + '" y2="' + f1(ry) + '" stroke="' + T.axis +
            '" stroke-width=".8" stroke-dasharray="2 3" opacity=".6"/>';
    }
    return svg(W, H, ref + '<polyline points="' + pts + '" fill="none" stroke="' + col +
               '" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>');
  }

  /* ============================================================
     쏠림 추이 — 장 난이도를 시간축으로
     ============================================================ */
  function crowding(hist, W, opts) {
    opts = opts || {};
    var T = tok();
    var d = (hist || []).filter(function (x) { return x && x.crowding != null; });
    if (d.length < 5) return emptyBox('쏠림 이력 없음');
    var H = opts.height || Math.round(Math.max(110, Math.min(180, W * 0.34)));
    var padT = 14, padB = 16, x0 = 6, x1 = W - 34;
    var vals = d.map(function (x) { return x.crowding; });
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    var pd = (max - min) * 0.12 || 1; min -= pd; max += pd;
    var X = function (i) { return x0 + i / (d.length - 1) * (x1 - x0); };
    var Y = function (v) { return padT + (1 - (v - min) / (max - min)) * (H - padT - padB); };
    var pts = vals.map(function (v, i) { return f1(X(i)) + ',' + f1(Y(v)); }).join(' ');
    var gid = 'cg' + Math.random().toString(36).slice(2, 7);
    var last = vals[vals.length - 1];

    var s = '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="' + T.osc + '" stop-opacity=".22"/>' +
            '<stop offset="100%" stop-color="' + T.osc + '" stop-opacity="0"/></linearGradient></defs>' +
            '<polygon points="' + f1(X(0)) + ',' + f1(H - padB) + ' ' + pts + ' ' + f1(X(d.length - 1)) + ',' + f1(H - padB) +
            '" fill="url(#' + gid + ')"/>' +
            '<polyline points="' + pts + '" fill="none" stroke="' + T.osc + '" stroke-width="1.8" stroke-linejoin="round"/>' +
            '<circle cx="' + f1(X(d.length - 1)) + '" cy="' + f1(Y(last)) + '" r="3" fill="' + T.osc + '"/>' +
            '<text x="' + f1(x1 + 4) + '" y="' + f1(Y(last) + 4) + '" font-size="12" font-weight="800" fill="' + T.osc + '">' +
            last.toFixed(0) + '</text>';
    [0, Math.round((d.length - 1) / 2), d.length - 1].forEach(function (i, k) {
      var a = k === 0 ? 'start' : k === 1 ? 'middle' : 'end';
      if (!d[i] || !d[i].date) return;
      s += '<text x="' + f1(X(i)) + '" y="' + (H - 3) + '" font-size="11" fill="' + T.axis +
           '" text-anchor="' + a + '">' + E(mdY(d[i].date)) + '</text>';
    });
    return svg(W, H, s);
  }

  /* ============================================================
     전체화면 캔들 (차트 API 응답)
     ============================================================ */
  function candles(list, W, opts) {
    opts = opts || {};
    var T = tok();
    if (!list || !list.length) return emptyBox('데이터 없음');
    var H = opts.height || Math.round(Math.max(280, Math.min(560, W * 0.78)));
    var padL = 6, padR = 62, padT = 12, padB = 20, gap = 8;
    var body = H - padT - padB;
    var priceH = Math.round(body * 0.76);
    var volTop = padT + priceH + gap, volH = padT + body - volTop;
    var x0 = padL, x1 = W - padR, n = list.length;
    var X = function (i) { return x0 + (n <= 1 ? 0 : i / (n - 1) * (x1 - x0)); };

    var closes = list.map(function (c) { return c.c; });
    var lo = Math.min.apply(null, list.map(function (c) { return c.l; }));
    var hi = Math.max.apply(null, list.map(function (c) { return c.h; }));
    if (lo === hi) { lo *= 0.99; hi *= 1.01; }
    var pd = (hi - lo) * 0.06; lo -= pd; hi += pd;
    var yP = function (v) { return padT + (1 - (v - lo) / (hi - lo)) * priceH; };
    var vMax = Math.max.apply(null, list.map(function (c) { return c.v || 0; }).concat([1]));
    var yV = function (v) { return volTop + volH - (v / vMax) * volH; };
    var bw = Math.max(1.4, Math.min(14, (x1 - x0) / n * 0.66));
    var s = '';

    // 가로 격자 + 우측 가격 눈금 — 전체화면에서는 눈금이 있어야 값을 읽는다
    for (var g = 0; g <= 4; g++) {
      var v = lo + (hi - lo) * (g / 4), y = yP(v);
      s += '<line x1="' + x0 + '" y1="' + f1(y) + '" x2="' + f1(x1) + '" y2="' + f1(y) + '" stroke="' + T.grid +
           '" stroke-width="1"/>' +
           '<text x="' + f1(x1 + 5) + '" y="' + f1(y + 4) + '" font-size="11" fill="' + T.axis + '">' +
           Math.round(v).toLocaleString('ko-KR') + '</text>';
    }

    var ma = [[5, T.ma5], [20, T.ma20]];
    ma.forEach(function (m) {
      var arr = sma(closes, m[0]);
      var pts = arr.map(function (v, i) { return v == null ? null : f1(X(i)) + ',' + f1(yP(v)); })
                   .filter(Boolean).join(' ');
      if (pts) s += '<polyline points="' + pts + '" fill="none" stroke="' + m[1] + '" stroke-width="1.3" stroke-linejoin="round"/>';
    });

    s += list.map(function (c, i) {
      var up = c.c >= c.o, col = up ? T.up : T.down, cx = X(i);
      var yo = yP(c.o), yc = yP(c.c);
      return '<line x1="' + f1(cx) + '" y1="' + f1(yP(c.h)) + '" x2="' + f1(cx) + '" y2="' + f1(yP(c.l)) +
             '" stroke="' + col + '" stroke-width="1.1"/>' +
             '<rect x="' + f1(cx - bw / 2) + '" y="' + f1(Math.min(yo, yc)) + '" width="' + f1(bw) +
             '" height="' + f1(Math.max(1, Math.abs(yc - yo))) + '" fill="' + (up ? col : T.surface) +
             '" stroke="' + col + '" stroke-width="1.1"/>' +
             '<rect x="' + f1(cx - bw / 2) + '" y="' + f1(yV(c.v || 0)) + '" width="' + f1(bw) +
             '" height="' + f1(volTop + volH - yV(c.v || 0)) + '" fill="' + col + '" opacity=".3"/>';
    }).join('');

    var lastC = closes[n - 1], col2 = lastC >= closes[0] ? T.up : T.down;
    var ly = yP(lastC), lab = Math.round(lastC).toLocaleString('ko-KR');
    var tw = Math.min(padR - 6, Math.max(38, lab.length * 7.4 + 10));
    s += '<rect x="' + f1(x1 + 2) + '" y="' + f1(ly - 9) + '" width="' + tw + '" height="18" rx="3" fill="' + col2 + '"/>' +
         '<text x="' + f1(x1 + 2 + tw / 2) + '" y="' + f1(ly + 4.5) +
         '" font-size="11.5" font-weight="800" fill="#fff" text-anchor="middle">' + E(lab) + '</text>';

    var lbl = opts.labelAt || function (c) { return c.t || ''; };
    [[0, 'start'], [Math.round((n - 1) / 2), 'middle'], [n - 1, 'end']].forEach(function (t) {
      var txt = lbl(list[t[0]]);
      if (!txt) return;
      s += '<text x="' + f1(X(t[0])) + '" y="' + (H - 4) + '" font-size="11" fill="' + T.axis +
           '" text-anchor="' + t[1] + '">' + E(txt) + '</text>';
    });
    return svg(W, H, s);
  }

  /* ============================================================
     테마 지도 (squarified treemap)
     면적 ≈ 거래대금(감마 보정) · 색 = 등락률
     ============================================================ */
  function squarify(items, x, y, w, h) {
    var out = [], total = items.reduce(function (a, b) { return a + b.value; }, 0);
    if (!(total > 0) || w <= 0 || h <= 0) return out;
    var list = items.map(function (it) {
      var o = {}; for (var k in it) o[k] = it[k];
      o.area = it.value / total * w * h; return o;
    });
    var rx = x, ry = y, rw = w, rh = h;
    var worst = function (row, len) {
      if (!row.length || len <= 0) return Infinity;
      var sum = row.reduce(function (a, b) { return a + b.area; }, 0);
      var mx = Math.max.apply(null, row.map(function (r) { return r.area; }));
      var mn = Math.min.apply(null, row.map(function (r) { return r.area; }));
      var s2 = sum * sum, l2 = len * len;
      return Math.max(l2 * mx / s2, s2 / (l2 * mn));
    };
    while (list.length) {
      var vert = rw >= rh, len = vert ? rh : rw, row = [];
      while (list.length) {
        var next = row.concat([list[0]]);
        if (row.length && worst(next, len) > worst(row, len)) break;
        row.push(list.shift());
      }
      var rowSum = row.reduce(function (a, b) { return a + b.area; }, 0);
      var thick = rowSum / len, off = 0;
      row.forEach(function (it) {
        var side = it.area / thick;
        var o = {}; for (var k in it) o[k] = it[k];
        if (vert) { o.x = rx; o.y = ry + off; o.w = thick; o.h = side; }
        else { o.x = rx + off; o.y = ry; o.w = side; o.h = thick; }
        out.push(o); off += side;
      });
      if (vert) { rx += thick; rw -= thick; } else { ry += thick; rh -= thick; }
      if (rw <= 0.5 || rh <= 0.5) break;
    }
    return out;
  }

  // 면적 정규화 — 원값 그대로면 1등이 화면을 다 먹고 나머지는 이름도 못 넣는다.
  var TM_GAMMA = 0.25, TM_SPREAD = 2.6;
  function tmNorm(items) {
    var sc = items.map(function (it) {
      var o = {}; for (var k in it) o[k] = it[k];
      o.raw = it.value; o.value = Math.pow(Math.max(it.value, 1), TM_GAMMA); return o;
    });
    var mx = Math.max.apply(null, sc.map(function (i) { return i.value; }));
    var fl = mx / TM_SPREAD;
    return sc.map(function (it) { it.value = Math.max(it.value, fl); return it; });
  }

  // 색 스케일은 그날 화면에 실제 오른 값의 분포로 잡는다. 고정 도메인이면
  // 급등주만 모인 날 전부 같은 색으로 뭉갠다.
  var TMS = { pos: 9, neg: 9, posMin: 0, negMin: 0 };
  function tmScale(rates) {
    var pos = rates.filter(function (r) { return r > 0.15; }).sort(function (a, b) { return a - b; });
    var neg = rates.filter(function (r) { return r < -0.15; }).map(Math.abs).sort(function (a, b) { return a - b; });
    var lo = function (a) { return a.length ? a[Math.floor(a.length * 0.05)] : 0; };
    var hi = function (a) { return a.length ? a[Math.floor(a.length * 0.95)] : 1; };
    TMS = { posMin: lo(pos), pos: Math.max(hi(pos), lo(pos) + 0.5), negMin: lo(neg), neg: Math.max(hi(neg), lo(neg) + 0.5) };
  }
  function tmT(rate) {
    var r = Math.abs(rate);
    var mn = rate > 0 ? TMS.posMin : TMS.negMin, mx = rate > 0 ? TMS.pos : TMS.neg;
    if (!(mx > mn)) return 0.6;
    return 0.18 + 0.82 * Math.max(0, Math.min(1, (r - mn) / (mx - mn)));
  }
  function mix(a, b, t) { return Math.round(a + (b - a) * t); }
  function tmColor(rate) {
    var r = (rate == null || !isFinite(rate)) ? 0 : rate;
    var dark = document.documentElement.getAttribute('data-theme') === 'dark' ||
      (!document.documentElement.getAttribute('data-theme') &&
        global.matchMedia && global.matchMedia('(prefers-color-scheme: dark)').matches);
    if (Math.abs(r) < 0.15) return dark ? '#2A3039' : '#EDEFF3';
    var t = tmT(r);
    var c = r > 0
      ? (dark ? [[62, 32, 36], [235, 68, 68]] : [[253, 232, 232], [176, 28, 34]])
      : (dark ? [[26, 40, 66], [70, 130, 235]] : [[232, 240, 252], [18, 76, 158]]);
    return 'rgb(' + mix(c[0][0], c[1][0], t) + ',' + mix(c[0][1], c[1][1], t) + ',' + mix(c[0][2], c[1][2], t) + ')';
  }
  function tmInk(rate) {
    var r = (rate == null || !isFinite(rate)) ? 0 : rate;
    var dark = document.documentElement.getAttribute('data-theme') === 'dark' ||
      (!document.documentElement.getAttribute('data-theme') &&
        global.matchMedia && global.matchMedia('(prefers-color-scheme: dark)').matches);
    if (Math.abs(r) < 0.15) return dark ? '#AAB2BD' : '#5B606B';
    return tmT(r) > 0.5 ? 'rgba(255,255,255,.97)' : (dark ? '#E6EAF0' : '#26292F');
  }

  // 칸에 이름이 들어가는 가장 큰 글자크기를 찾는다.
  // opts.min 을 안 넘기면 cap(칸 높이에서 나온 상한)이 min 보다 작아지는 순간
  // 루프가 한 번도 안 돌고 무조건 잘린 이름이 된다 — 띠 형태의 그룹 헤더가
  // 자리가 남는데도 "진단키트…" 로 나오던 원인이 이것이었다.
  function fitLabel(name, w, h, o) {
    o = o || {};
    var s = String(name || '');
    var min = o.min || 9.5, hi = o.max || 13.5;
    if (!s || h < (o.minH || 13) || w < 22) return null;
    var cap = Math.max(min, Math.min(hi, h * 0.42));
    for (var f = cap; f >= min; f -= 0.5) {
      if (s.length * f * 0.98 <= w - 6) return { text: s, font: f };
    }
    var per = min * 0.98, max = Math.floor((w - 6) / per);
    if (max < 2) return null;
    return { text: s.slice(0, Math.max(1, max - 1)) + '…', font: min };
  }

  function treemap(themes, W, opts) {
    opts = opts || {};
    var list = (themes || []).map(function (t) {
      var seen = {}, stocks = [];
      (t.stocks || []).forEach(function (s) {
        var v = global.Core.parseWon(s.volume);
        var key = String(s.code || s.name || '').trim();
        if (!(v > 0) || !key || seen[key]) return;
        seen[key] = 1;
        stocks.push({ code: s.code, name: s.name, rate: s.changeRate, value: v });
      });
      var value = stocks.reduce(function (a, b) { return a + b.value; }, 0) || global.Core.parseWon(t.totalVolume);
      return { name: t.themeName, value: value, stocks: stocks };
    }).filter(function (t) { return t.value > 0 && t.stocks.length; });
    if (list.length < 2) return '';

    tmScale(list.reduce(function (a, t) {
      return a.concat(t.stocks.map(function (x) { return (x.rate == null || !isFinite(x.rate)) ? 0 : x.rate; }));
    }, []));

    var H = Math.round(W >= 640 ? W * 0.52 : W * 1.16);
    var HEAD = 19;
    var groups = squarify(tmNorm(list), 0, 0, W, H);
    var T = tok(), s = '';

    groups.forEach(function (g) {
      var inner = squarify(tmNorm(g.stocks), g.x + 1, g.y + HEAD, Math.max(0, g.w - 2), Math.max(0, g.h - HEAD - 1));
      s += '<rect x="' + f1(g.x) + '" y="' + f1(g.y) + '" width="' + f1(g.w) + '" height="' + f1(g.h) +
           '" rx="4" fill="' + T.surface2 + '"/>';
      // 그룹 헤더는 띠라서 높이가 늘 작다. 높이로 글자를 정하면 안 되고
      // 11.5px 고정에서 폭에만 맞춘다.
      var gl = fitLabel(g.name, g.w - 6, HEAD, { min: 11.5, max: 11.5, minH: 12 });
      if (gl) {
        s += '<text x="' + f1(g.x + 6) + '" y="' + f1(g.y + 13.5) + '" font-size="11.5" font-weight="800" fill="' +
             T.ink3 + '">' + E(gl.text) + '</text>';
      }
      inner.forEach(function (st) {
        var rate = (st.rate == null || !isFinite(st.rate)) ? 0 : st.rate;
        var lb = fitLabel(st.name, st.w, st.h);
        var showRate = lb && st.h > lb.font * 2.5 && st.w > 42;
        s += '<g class="tmap-tile" data-code="' + E(st.code || '') + '" data-name="' + E(st.name || '') + '">' +
             '<title>' + E(st.name) + ' · ' + (rate >= 0 ? '+' : '') + rate.toFixed(2) + '% · 거래대금 ' + E(global.Core.won(st.raw)) + '</title>' +
             '<rect x="' + f1(st.x + 1) + '" y="' + f1(st.y + 1) + '" width="' + f1(Math.max(0, st.w - 2)) +
             '" height="' + f1(Math.max(0, st.h - 2)) + '" rx="3" fill="' + tmColor(rate) + '"/>' +
             (lb ? '<text x="' + f1(st.x + st.w / 2) + '" y="' + f1(st.y + st.h / 2 + (showRate ? -1 : lb.font * 0.36)) +
               '" text-anchor="middle" font-size="' + f1(lb.font) + '" font-weight="700" fill="' + tmInk(rate) + '">' +
               E(lb.text) + '</text>' : '') +
             (showRate ? '<text x="' + f1(st.x + st.w / 2) + '" y="' + f1(st.y + st.h / 2 + lb.font + 2) +
               '" text-anchor="middle" font-size="' + f1(lb.font * 0.85) + '" font-weight="600" opacity=".9" fill="' +
               tmInk(rate) + '">' + (rate >= 0 ? '+' : '') + rate.toFixed(1) + '%</text>' : '') +
             '</g>';
      });
    });
    return svg(W, H, s, 'tmap-svg');
  }

  /* ── 공통 ─────────────────────────────────────────────── */
  function svg(w, h, inner, cls) {
    return '<svg viewBox="0 0 ' + w + ' ' + h + '" width="' + w + '" height="' + h + '"' +
           (cls ? ' class="' + cls + '"' : '') +
           ' xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet" role="img">' + inner + '</svg>';
  }
  function emptyBox(msg) { return '<div class="chart-empty">' + E(msg) + '</div>'; }

  global.Viz = {
    mount: mount, redrawAll: redrawAll, clearMounts: clearMounts, dropTok: dropTok,
    priceOsc: priceOsc, indexChart: indexChart, spark: spark, crowding: crowding,
    candles: candles, treemap: treemap, sma: sma
  };
})(window);
