/**
 * Flow Tab — 수급/주도 대시보드 (트레이더 관점 재구성)
 *
 * 우선순위:
 *  1) 리스크 게이지 (오늘 사도 되나, 현금 비중 권고)
 *  2) 시장 심리 — 가격 + 공포탐욕 dual-axis
 *  3) 매수 후보 — 빈집 ∩ 주도섹터 + 차트 + 매수타점 + 추세 + 신고가
 *  4) 거래대금 강도 (TI) — 신고가 후보 종목별 미니 차트
 *  5) 외인/기관 섹터 흐름
 *  6) 매도 시그널 / 신고가 / 주도 ETF / 쏠림지수 (보조 카드)
 */

const FLOW_S3_URL = 'https://stock-dashboard-data.s3.ap-northeast-2.amazonaws.com/flow_dashboard.json';
const FLOW_LOCAL_URL = './flow_dashboard.json';

const flowIsProduction = window.location.hostname.includes('github.io')
                       || window.location.hostname.includes('stock');
const FLOW_DATA_URL = flowIsProduction ? FLOW_S3_URL : FLOW_LOCAL_URL;

let flowLoaded = false;
let flowData = null;          // 로드된 flow_dashboard.json 전체 (검색 인덱스 소스)
let flowLoadPromise = null;   // 동시 호출 방지

// ─────────────────────────────────────────┐
// Tab switching                             │
// ─────────────────────────────────────────┘
function setupTabs() {
  const tabs = document.querySelectorAll('.tab-btn');
  tabs.forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.tab;
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b === btn));
      document.querySelectorAll('.tab-panel').forEach(p => {
        const isActive = p.id === `tab-${target}`;
        p.classList.toggle('active', isActive);
        if (isActive) p.removeAttribute('hidden');
        else p.setAttribute('hidden', '');
      });
      if (target === 'flow' && !flowLoaded) loadFlow();
      if (target === 'briefing' && typeof loadBriefing === 'function') loadBriefing();
    });
  });
}

// ─────────────────────────────────────────┐
// Helpers                                   │
// ─────────────────────────────────────────┘
function fEscape(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[c]);
}

function fmtBillion(won) {
  if (won == null || won === 0) return '0';
  const eok = won / 1e8;
  if (Math.abs(eok) >= 10000) return `${(eok / 10000).toFixed(2)}조`;
  if (Math.abs(eok) >= 1) return `${eok.toFixed(0)}억`;
  return `${eok.toFixed(2)}억`;
}

function fmtNumber(n) { return n == null ? '-' : n.toLocaleString('ko-KR'); }

function changeClass(rate) {
  if (rate == null) return 'flat';
  if (rate > 0) return 'up';
  if (rate < 0) return 'down';
  return 'flat';
}

function fmtPctSigned(v) {
  if (v == null) return '-';
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
}

// RS Norm 막대 시각 폭. rsNorm 50(중립)을 0%, 100을 100%로 매핑해 70~95대 강자들의
// 차이가 눈에 띄도록 한다. (rsNorm 자체가 시그모이드 정규화라 실수치는 그대로 표기)
function rsBarWidth(rsNorm) {
  if (rsNorm == null) return 2;
  return Math.max(2, Math.min(100, (rsNorm - 50) * 2));
}

// ─────────────────────────────────────────┐
// Sentiment Gauge SVG                       │
// ─────────────────────────────────────────┘
function renderGauge(value, label) {
  const v = Math.max(0, Math.min(100, value || 0));
  const angle = -90 + (v / 100) * 180;
  const x = 100 + 80 * Math.cos(angle * Math.PI / 180);
  const y = 100 + 80 * Math.sin(angle * Math.PI / 180);
  const colorFor = v >= 75 ? '#E53935' : v >= 55 ? '#FB8C00' : v >= 45 ? '#FDD835' : v >= 25 ? '#43A047' : '#1E88E5';
  return `
    <svg viewBox="0 0 200 130" class="gauge-svg" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="gauge-grad-${label}" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stop-color="#1E88E5"/><stop offset="25%" stop-color="#43A047"/>
          <stop offset="50%" stop-color="#FDD835"/><stop offset="75%" stop-color="#FB8C00"/>
          <stop offset="100%" stop-color="#E53935"/>
        </linearGradient>
      </defs>
      <path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="url(#gauge-grad-${label})" stroke-width="14"/>
      <line x1="100" y1="100" x2="${x}" y2="${y}" stroke="#222" stroke-width="3"/>
      <circle cx="100" cy="100" r="6" fill="#222"/>
      <text x="100" y="125" text-anchor="middle" font-size="22" font-weight="800" fill="${colorFor}">${v.toFixed(1)}</text>
    </svg>
  `;
}

// ─────────────────────────────────────────┐
// 단순 이동평균 (null safe)                  │
// ─────────────────────────────────────────┘
// ─────────────────────────────────────────┐
// 차트 공통 토큰                             │
// ─────────────────────────────────────────┘
// 이평선 색은 캔들 모달(chart.js MA_LINES)과 반드시 같게 유지한다.
// 같은 5일선이 카드에서는 보라, 모달에서는 다른 색이면 같은 화면으로 안 읽힌다.
// 빨강·파랑은 등락 의미색이라 이평선에 쓰지 않는다 — 캔들과 충돌한다.
const CHART_PRICE = '#1F2933';   // 지수·주가 — 가장 굵고 진하게 (주인공)
const CHART_MA5   = '#8E5BE8';   // 5일선
const CHART_MA20  = '#EE9A1E';   // 20일선
const CHART_OSC   = '#B4791E';   // 오실레이터 (지표창)
const CHART_GRID  = '#E5DFD3';
const CHART_AXIS_TEXT = '#8C8474';
const CHART_PAD_L = 6;
const CHART_PAD_R = 46;          // 우측 가격축 + 현재가 태그 pill 자리

// 차트 논리 크기(viewBox)를 화면 폭에 맞춰 고른다.
// 하나로 고정하면 폭 넓은 화면에서 SVG 가 3배 이상 늘어나고 축·라벨 글자도
// 같이 커져 조잡해진다. 논리 폭을 키우면 확대율이 1.x 로 떨어져 글자가
// 의도한 크기로 보인다. (모바일에서 큰 viewBox 를 쓰면 반대로 글자가 뭉갠다)
function chartBox(mobile, desktop) {
  return (typeof window !== 'undefined' && window.innerWidth >= 760) ? desktop : mobile;
}

function computeMA(values, period) {
  const out = new Array(values.length).fill(null);
  for (let i = period - 1; i < values.length; i++) {
    let sum = 0, valid = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const v = values[j];
      if (v != null && !isNaN(v)) { sum += v; valid++; }
    }
    if (valid === period) out[i] = sum / period;
  }
  return out;
}

// 현재 MDD (%) — 시계열 누적 고점 대비 현재 낙폭 (참고 자료 정의)
//   mdd = (current / running_max) - 1
function computeCurrentMddPct(closes) {
  const arr = closes.filter(v => v != null && !isNaN(v));
  if (arr.length < 2) return null;
  let peak = -Infinity;
  for (const v of arr) if (v > peak) peak = v;
  if (peak <= 0) return null;
  return (arr[arr.length - 1] / peak - 1) * 100;
}

// ─────────────────────────────────────────┐
// 지수 차트 — 가격창 + 지표창 2단 구성
//   위(66%)  가격창: 지수(굵은 흑) + 5일선 + 20일선, 우측 가격축
//   아래(34%) 지표창: 공포·탐욕 오실레이터, 0선 기준 면적
// ─────────────────────────────────────────┘
// 지수 차트 — 종목 차트(renderMiniPriceChart)와 동일한 2단 구조.
//   가격창: 지수 일봉 캔들 + 거래량 + MA5/MA20 + 현재가 pill
//   지표창: Fear & Greed 오실레이터 백분위 음영 + 분위 점선 + 오실레이터선
// 종목 차트가 캔들 2단으로 바뀐 뒤 지수만 선+격자로 남아 같은 화면에서
// 다른 물건처럼 읽혔다. 백엔드가 history[].ohlc 를 실어주면 캔들로 그리고,
// 없으면(구버전 페이로드) 종가 선으로 폴백한다.
function renderDualAxisChart(history, opts = {}) {
  if (!history || history.length < 5) return '<div class="sparkline-empty">데이터 부족</div>';

  const closeArr = history.map(p => p.close);
  const valid = closeArr.filter(v => v != null);
  const oscArr = history.map(p => (p.oscillator != null ? p.oscillator : null));
  if (valid.length < 2 || oscArr.filter(v => v != null).length < 2) {
    return '<div class="sparkline-empty">데이터 부족</div>';
  }

  const box = chartBox({ w: 360, h: 138 }, { w: 900, h: 260 });
  const W = opts.width || box.w, H = opts.height || box.h;
  const padL = CHART_PAD_L, padR = CHART_PAD_R, padT = 8, padB = 15;
  const gap = 7;
  const bodyH = H - padT - padB;
  const priceH = Math.round(bodyH * 0.62);
  const oscTop = padT + priceH + gap;
  const oscBot = padT + bodyH;
  const oscH = oscBot - oscTop;
  const x0 = padL, x1 = W - padR;
  const n = history.length;
  const X = (i) => x0 + (n <= 1 ? 0 : (i / (n - 1)) * (x1 - x0));

  // 가격창 — 지수 + 5·20일선. 캔들이 있으면 고가·저가까지 스케일에 포함.
  const ma5 = computeMA(closeArr, 5);
  const ma20 = computeMA(closeArr, 20);
  const ohlc = history.map(p => p.ohlc || null);
  const hasCandle = ohlc.filter(Boolean).length >= Math.max(2, Math.floor(n * 0.6));
  let pAll = valid.concat(ma5.filter(v => v != null)).concat(ma20.filter(v => v != null));
  if (hasCandle) {
    ohlc.forEach(d => { if (d) pAll.push(d.h, d.l); });
  }
  let pMin = Math.min(...pAll), pMax = Math.max(...pAll);
  const pPad = (pMax - pMin) * 0.08 || 1; pMin -= pPad; pMax += pPad;
  const yP = (v) => padT + (1 - (v - pMin) / (pMax - pMin)) * priceH;
  const projP = (arr) => arr
    .map((v, i) => v == null ? null : `${X(i).toFixed(1)},${yP(v).toFixed(1)}`)
    .filter(Boolean).join(' ');
  const closePts = projP(closeArr);
  const ma5Pts = projP(ma5);
  const ma20Pts = projP(ma20);

  const firstVal = valid[0], lastVal = valid[valid.length - 1];
  const rising = lastVal >= firstVal;
  const areaColor = rising ? '#E53935' : '#1565C0';

  // 일봉 캔들 + 거래량 — 종목 차트와 동일 규칙(양봉 빨강 채움 / 음봉 파랑 테두리)
  let candleSvg = '', volSvg = '', priceArea = '';
  if (hasCandle) {
    const bw = Math.max(1.2, Math.min(7, (x1 - x0) / n * 0.62));
    candleSvg = ohlc.map((d, i) => {
      if (!d) return '';
      const up = d.c >= d.o;
      const col = up ? '#E53935' : '#1565C0';
      const cx = X(i);
      const yOpen = yP(d.o), yClose = yP(d.c);
      const top = Math.min(yOpen, yClose);
      const bh = Math.max(0.8, Math.abs(yClose - yOpen));
      return `<line x1="${cx.toFixed(1)}" y1="${yP(d.h).toFixed(1)}" x2="${cx.toFixed(1)}" y2="${yP(d.l).toFixed(1)}" stroke="${col}" stroke-width="0.8"/>`
        + `<rect x="${(cx - bw / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" fill="${up ? col : '#fff'}" stroke="${col}" stroke-width="0.8"/>`;
    }).join('');
    const vMax = Math.max(...ohlc.map(d => (d && d.v) || 0), 1);
    const vBase = padT + priceH;
    const vH = priceH * 0.22;
    volSvg = ohlc.map((d, i) => {
      if (!d || !d.v) return '';
      const h = (d.v / vMax) * vH;
      return `<rect x="${(X(i) - bw / 2).toFixed(1)}" y="${(vBase - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" fill="${d.c >= d.o ? '#E53935' : '#1565C0'}" opacity="0.22"/>`;
    }).join('');
  } else {
    const gid = `ia-${Math.random().toString(36).slice(2, 8)}`;
    priceArea = closePts ? `
      <defs>
        <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${areaColor}" stop-opacity="0.20"/>
          <stop offset="100%" stop-color="${areaColor}" stop-opacity="0.02"/>
        </linearGradient>
      </defs>
      <polygon points="${X(0).toFixed(1)},${(padT + priceH).toFixed(1)} ${closePts} ${X(n - 1).toFixed(1)},${(padT + priceH).toFixed(1)}" fill="url(#${gid})"/>
    ` : '';
  }

  // 지표창 — F&G 오실레이터 백분위 음영 + 분위 점선
  const oscVals = oscArr.map(v => v == null ? 0 : v);
  let oMin = Math.min(...oscVals), oMax = Math.max(...oscVals);
  const oPad = (oMax - oMin) * 0.12 || 0.001; oMin -= oPad; oMax += oPad;
  const yO = (v) => oscTop + (1 - (v - oMin) / (oMax - oMin)) * oscH;
  const lastOsc = oscVals[oscVals.length - 1];

  const sorted = [...oscVals].sort((a, b) => a - b);
  const pq = (q) => sorted[Math.max(0, Math.min(sorted.length - 1, Math.round(q * (sorted.length - 1))))];
  const p90 = pq(0.90), p75 = pq(0.75), p50 = pq(0.50), p25 = pq(0.25), p10 = pq(0.10);
  const band = (a, b, fill) => {
    const t = Math.min(a, b), bt = Math.max(a, b), hh = bt - t;
    return hh < 0.5 ? '' : `<rect x="${x0}" y="${t.toFixed(1)}" width="${(x1 - x0).toFixed(1)}" height="${hh.toFixed(1)}" fill="${fill}"/>`;
  };
  const bands =
    band(oscTop, yO(p90), '#fdecee') +
    band(yO(p90), yO(p75), '#faf1f0') +
    band(yO(p75), yO(p25), '#f6f3ee') +
    band(yO(p25), yO(p10), '#eef1f5') +
    band(yO(p10), oscBot, '#e8edf4');
  const dl = (y, color, dash) =>
    `<line x1="${x0}" y1="${y.toFixed(1)}" x2="${x1}" y2="${y.toFixed(1)}" stroke="${color}" stroke-width="0.8"${dash ? ` stroke-dasharray="${dash}"` : ''} opacity="0.55"/>`;
  const dashed = dl(yO(p90), '#E53935', '3 3') + dl(yO(p10), '#1E88E5', '3 3') + dl(yO(p50), '#cbb8bb', '');
  const oscPts = oscVals.map((v, i) => `${X(i).toFixed(1)},${yO(v).toFixed(1)}`).join(' ');
  const oscSvg = bands + dashed
    + `<polyline points="${oscPts}" fill="none" stroke="${CHART_OSC}" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>`;
  const oscFg =
    `<circle cx="${X(n - 1).toFixed(1)}" cy="${yO(lastOsc).toFixed(1)}" r="2.2" fill="${CHART_OSC}"/>` +
    `<text x="${(x1 - 2).toFixed(1)}" y="${(yO(lastOsc) + (lastOsc >= p50 ? 10 : 3)).toFixed(1)}" font-size="9" font-weight="800" fill="#B4560F" text-anchor="end">${lastOsc.toFixed(3)}</text>`;

  // 현재 지수 태그 — 종목 차트의 현재가 pill 과 동일
  const lastY = yP(lastVal);
  const label = Math.round(lastVal).toLocaleString('ko-KR');
  const tagW = Math.max(26, label.length * 5.2 + 8), tagH = 11;
  const closeDot = `<circle cx="${X(n - 1).toFixed(1)}" cy="${lastY.toFixed(1)}" r="2.2" fill="${areaColor}"/>`;
  const closeTag = `
      <line x1="${x0}" y1="${lastY.toFixed(1)}" x2="${x1.toFixed(1)}" y2="${lastY.toFixed(1)}" stroke="${areaColor}" stroke-width="0.7" stroke-dasharray="3 2" opacity="0.5"/>
      <rect x="${(x1 + 2).toFixed(1)}" y="${(lastY - tagH / 2).toFixed(1)}" width="${tagW}" height="${tagH}" rx="2.5" fill="${areaColor}"/>
      <text x="${(x1 + 2 + tagW / 2).toFixed(1)}" y="${(lastY + 3.2).toFixed(1)}" font-size="7.8" font-weight="800" fill="#fff" text-anchor="middle">${fEscape(label)}</text>`;
  const priceTitle = `<text x="${x0}" y="${(padT + 6).toFixed(1)}" font-size="7.5" font-weight="700" fill="${CHART_AXIS_TEXT}">${hasCandle ? '일봉 · 거래량' : '지수'}</text>`;
  const oscTitle = `<text x="${x0}" y="${(oscTop - 1.5).toFixed(1)}" font-size="7.5" font-weight="700" fill="${CHART_OSC}">Fear &amp; Greed 오실레이터</text>`;

  // X축 날짜 — 종목 차트와 동일하게 3틱. 지수는 기간이 길어 연도까지 표기.
  let axisLabels = '';
  const dates = history.map(p => p.date);
  if (dates.length >= 2 && dates[0]) {
    const fmt = (s) => { const m = /^(\d{2})(\d{2})-(\d{2})-\d{2}$/.exec(s); return m ? `${m[2]}.${m[3]}` : s; };
    const last = dates.length - 1;
    const ticks = [[0, 'start'], [Math.round(last * 0.5), 'middle'], [last, 'end']];
    axisLabels = ticks.filter(([i]) => dates[i]).map(([i, a]) =>
      `<text x="${X(i).toFixed(1)}" y="${H - 4}" font-size="8" fill="${CHART_AXIS_TEXT}" text-anchor="${a}">${fmt(dates[i])}</text>`).join('');
  }

  return `
    <div class="mini-chart-wrap">
      <svg viewBox="0 0 ${W} ${H}" class="mini-chart dual-chart" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
        ${oscSvg}
        ${priceArea}
        ${volSvg}
        ${candleSvg}
        ${ma20Pts ? `<polyline points="${ma20Pts}" fill="none" stroke="${CHART_MA20}" stroke-width="1" stroke-linejoin="round"/>` : ''}
        ${ma5Pts ? `<polyline points="${ma5Pts}" fill="none" stroke="${CHART_MA5}" stroke-width="1" stroke-linejoin="round"/>` : ''}
        ${hasCandle ? '' : `<polyline points="${closePts}" fill="none" stroke="${CHART_PRICE}" stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round"/>`}
        ${closeDot}
        ${closeTag}
        ${priceTitle}
        ${oscTitle}
        ${oscFg}
        ${axisLabels}
      </svg>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// Mini price chart — 참고 자료(.xlsm dashboard) 스타일
//   좌축 (검정): 시가총액 시계열 (capHistory60d)
//   우축 (빨강/파랑 막대 + 보라 선): 수급 오실레이터
//     osc = MACD Histogram of (외+기 5일누적 순매수 / 시가총액)  ← xlsm '오실'
//     · 양수 → 빨강 (수급 들어옴)
//     · 음수 → 파랑 (빈집)
//   이동평균선은 5·20일선만 (한국 HTS 표준).
// ─────────────────────────────────────────┘
function renderMiniPriceChart(c, opts = {}) {
  // 형상 상수는 렌더 직전에 정의 (kick_mockup_light 레시피)

  // 가격창은 '주가'를 그린다. 시가총액을 그리면 눈금이 낯설어 (3135억)
  // 사람들이 늘 보던 종목 차트와 다른 물건처럼 읽힌다. 시가총액은
  // 수급 오실레이터 정규화에만 쓰고, 화면에는 주가를 보여준다.
  const capFull = (c.priceHistory60d && c.priceHistory60d.length >= 2)
    ? c.priceHistory60d
    : (c.capHistory60d || []);
  const isPriceSeries = !!(c.priceHistory60d && c.priceHistory60d.length >= 2);
  const oscFull = c.supplyOscHistory || [];
  const dateFull = c.dateHistory60d || [];
  const N = (oscFull.length >= 2 && capFull.length >= 2)
    ? Math.min(capFull.length, oscFull.length)
    : capFull.length;
  const cap = capFull.slice(-N);
  const oscSeries = oscFull.slice(-N);
  // X축 날짜: dateHistory60d (cap 과 동일 길이) 우선, 없으면 osc 의 date
  const dates = (dateFull.length >= N)
    ? dateFull.slice(-N)
    : oscSeries.map(o => o.date);
  if (!cap || cap.length < 2) return '<div class="sparkline-empty"></div>';

  const validCap = cap.filter(v => v != null);
  if (validCap.length < 2) return '<div class="sparkline-empty"></div>';

  // ── 가격창 / 지표창 분리 ─────────────────────────────
  // 기존에는 주가와 수급 오실레이터를 같은 영역에 겹쳐 그리고, 백분위 음영이
  // 차트 전체 높이를 덮었다. 음영은 오실레이터의 백분위인데 가격 뒤에 깔리니
  // "가격 구간"으로 오독됐다. 창을 나누고 음영을 지표창 안에 가둔다.
  const box = chartBox({ w: 360, h: 138 }, { w: 900, h: 260 });
  const W = opts.width || box.w, H = opts.height || box.h;
  const padL = CHART_PAD_L, padR = CHART_PAD_R, padT = 8, padB = 15;
  const gap = 7;
  const bodyH = H - padT - padB;
  const priceH = Math.round(bodyH * 0.62);
  const oscTop = padT + priceH + gap;
  const oscBot = padT + bodyH;
  const oscH = oscBot - oscTop;
  const x0 = padL, x1 = W - padR;
  const n = cap.length;
  const X = (i) => x0 + (n <= 1 ? 0 : (i / (n - 1)) * (x1 - x0));

  // 가격창 — 주가 + 5·20일선
  const ma5Series = computeMA(cap, 5);
  const ma20Series = computeMA(cap, 20);
  const pAll = validCap
    .concat(ma5Series.filter(v => v != null))
    .concat(ma20Series.filter(v => v != null));
  let pMin = Math.min(...pAll), pMax = Math.max(...pAll);
  const pPad = (pMax - pMin) * 0.08 || 1; pMin -= pPad; pMax += pPad;
  const yP = (v) => padT + (1 - (v - pMin) / (pMax - pMin)) * priceH;
  const projP = (arr) => arr
    .map((v, i) => v == null ? null : `${X(i).toFixed(1)},${yP(v).toFixed(1)}`)
    .filter(Boolean).join(' ');
  const capPts = projP(cap);
  const ma5Pts = projP(ma5Series);
  const ma20Pts = projP(ma20Series);

  // 구간 등락 방향 — 현재가 태그 색에 쓴다.
  const firstVal = validCap[0], lastVal = validCap[validCap.length - 1];
  const rising = lastVal >= firstVal;
  const areaColor = rising ? '#E53935' : '#1565C0';

  // ── 일봉 캔들 + 거래량 ──────────────────────────
  // 종가 선 하나로는 그날 무슨 일이 있었는지 안 보인다. 종가베팅 대상을
  // 고르는 사람은 위꼬리·아래꼬리와 거래량을 같이 본다. OHLC 가 있으면
  // 캔들로 그리고, 없으면(구버전 페이로드) 종가 선으로 폴백한다.
  const ohlc = (c.ohlc60d && c.ohlc60d.length >= 2) ? c.ohlc60d.slice(-n) : null;
  let candleSvg = '', volSvg = '', priceArea = '';
  if (ohlc && ohlc.length === n) {
    const bw = Math.max(1.2, Math.min(7, (x1 - x0) / n * 0.62));
    candleSvg = ohlc.map((d, i) => {
      if (!d) return '';
      const up = d.c >= d.o;
      const col = up ? '#E53935' : '#1565C0';
      const cx = X(i);
      const yO = yP(d.o), yC = yP(d.c), yH = yP(d.h), yL = yP(d.l);
      const top = Math.min(yO, yC);
      const bh = Math.max(0.8, Math.abs(yC - yO));
      return `<line x1="${cx.toFixed(1)}" y1="${yH.toFixed(1)}" x2="${cx.toFixed(1)}" y2="${yL.toFixed(1)}" stroke="${col}" stroke-width="0.8"/>`
        + `<rect x="${(cx - bw / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" fill="${up ? col : '#fff'}" stroke="${col}" stroke-width="0.8"/>`;
    }).join('');
    // 거래량 — 가격창 하단 22% 를 빌려 쓴다. 별도 창을 또 만들면 카드가 너무 길어진다.
    const vMax = Math.max(...ohlc.map(d => (d && d.v) || 0), 1);
    const vBase = padT + priceH;
    const vH = priceH * 0.22;
    volSvg = ohlc.map((d, i) => {
      if (!d || !d.v) return '';
      const h = (d.v / vMax) * vH;
      return `<rect x="${(X(i) - bw / 2).toFixed(1)}" y="${(vBase - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" fill="${d.c >= d.o ? '#E53935' : '#1565C0'}" opacity="0.22"/>`;
    }).join('');
  } else {
    const gid = `pa-${Math.random().toString(36).slice(2, 8)}`;
    priceArea = capPts ? `
      <defs>
        <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${areaColor}" stop-opacity="0.20"/>
          <stop offset="100%" stop-color="${areaColor}" stop-opacity="0.02"/>
        </linearGradient>
      </defs>
      <polygon points="${X(0).toFixed(1)},${(padT + priceH).toFixed(1)} ${capPts} ${X(n - 1).toFixed(1)},${(padT + priceH).toFixed(1)}" fill="url(#${gid})"/>
    ` : '';
  }

  // 지표창 — 수급 오실레이터 + 백분위 음영
  let oscSvg = '', oscFg = '';
  let lastOscVal = null;
  if (oscSeries.length >= 2) {
    const oscVals = oscSeries.map(o => (o && o.osc != null) ? o.osc : 0);
    let oMin = Math.min(...oscVals), oMax = Math.max(...oscVals);
    const oPad = (oMax - oMin) * 0.12 || 0.001; oMin -= oPad; oMax += oPad;
    const yO = (v) => oscTop + (1 - (v - oMin) / (oMax - oMin)) * oscH;
    lastOscVal = oscVals[oscVals.length - 1];

    const sorted = [...oscVals].sort((a, b) => a - b);
    const pq = (q) => sorted[Math.max(0, Math.min(sorted.length - 1, Math.round(q * (sorted.length - 1))))];
    const p90 = pq(0.90), p75 = pq(0.75), p50 = pq(0.50), p25 = pq(0.25), p10 = pq(0.10);

    const band = (a, b, fill) => {
      const t = Math.min(a, b), bt = Math.max(a, b), hh = bt - t;
      return hh < 0.5 ? '' : `<rect x="${x0}" y="${t.toFixed(1)}" width="${(x1 - x0).toFixed(1)}" height="${hh.toFixed(1)}" fill="${fill}"/>`;
    };
    const bands =
      band(oscTop, yO(p90), '#fdecee') +
      band(yO(p90), yO(p75), '#faf1f0') +
      band(yO(p75), yO(p25), '#f6f3ee') +
      band(yO(p25), yO(p10), '#eef1f5') +
      band(yO(p10), oscBot, '#e8edf4');
    const dl = (y, color, dash) =>
      `<line x1="${x0}" y1="${y.toFixed(1)}" x2="${x1}" y2="${y.toFixed(1)}" stroke="${color}" stroke-width="0.8"${dash ? ` stroke-dasharray="${dash}"` : ''} opacity="0.55"/>`;
    const dashed = dl(yO(p90), '#E53935', '3 3') + dl(yO(p10), '#1E88E5', '3 3') + dl(yO(p50), '#cbb8bb', '');
    const oscPts = oscVals.map((v, i) => `${X(i).toFixed(1)},${yO(v).toFixed(1)}`).join(' ');

    oscSvg = bands + dashed
      + `<polyline points="${oscPts}" fill="none" stroke="${CHART_OSC}" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>`;
    oscFg =
      `<circle cx="${X(n - 1).toFixed(1)}" cy="${yO(lastOscVal).toFixed(1)}" r="2.2" fill="${CHART_OSC}"/>` +
      `<text x="${(x1 - 2).toFixed(1)}" y="${(yO(lastOscVal) + (lastOscVal >= p50 ? 10 : 3)).toFixed(1)}" font-size="9" font-weight="800" fill="#B4560F" text-anchor="end">${(lastOscVal * 100).toFixed(2)}%</text>`;
  }

  // 현재가 태그 — HTS 처럼 우측 가격축에 색 pill + 점선 가이드
  const lastCapVal = [...cap].reverse().find(v => v != null);
  const capLabel = isPriceSeries
    ? Number(lastCapVal).toLocaleString('ko-KR')
    : (lastCapVal >= 1e12 ? `${(lastCapVal / 1e12).toFixed(1)}조`
      : lastCapVal >= 1e8 ? `${(lastCapVal / 1e8).toFixed(0)}억` : `${lastCapVal}`);
  const lastY = yP(lastCapVal);
  const tagW = Math.max(26, capLabel.length * 5.2 + 8), tagH = 11;
  const capDot = `<circle cx="${X(n - 1).toFixed(1)}" cy="${lastY.toFixed(1)}" r="2.2" fill="${areaColor}"/>`;
  const capLabelSvg = `
      <line x1="${x0}" y1="${lastY.toFixed(1)}" x2="${x1.toFixed(1)}" y2="${lastY.toFixed(1)}" stroke="${areaColor}" stroke-width="0.7" stroke-dasharray="3 2" opacity="0.5"/>
      <rect x="${(x1 + 2).toFixed(1)}" y="${(lastY - tagH / 2).toFixed(1)}" width="${tagW}" height="${tagH}" rx="2.5" fill="${areaColor}"/>
      <text x="${(x1 + 2 + tagW / 2).toFixed(1)}" y="${(lastY + 3.2).toFixed(1)}" font-size="7.8" font-weight="800" fill="#fff" text-anchor="middle">${fEscape(capLabel)}</text>`;
  const priceTitle = `<text x="${x0}" y="${(padT + 6).toFixed(1)}" font-size="7.5" font-weight="700" fill="${CHART_AXIS_TEXT}">${candleSvg ? '일봉 · 거래량' : (isPriceSeries ? '주가' : '시가총액')}</text>`;
  const oscTitle = `<text x="${x0}" y="${(oscTop - 1.5).toFixed(1)}" font-size="7.5" font-weight="700" fill="${CHART_OSC}">수급 오실레이터</text>`;

  // X축 날짜 라벨 — 3개 (좌끝 / 중앙 / 우끝)
  let axisLabels = '';
  if (dates.length >= 2) {
    const fmt = (s) => { const m = /^\d{4}-(\d{2})-(\d{2})$/.exec(s); return m ? `${parseInt(m[1], 10)}/${parseInt(m[2], 10)}` : s; };
    const last = dates.length - 1;
    const ticks = [[0, 'start'], [Math.round(last * 0.5), 'middle'], [last, 'end']];
    axisLabels = ticks.map(([i, a]) =>
      `<text x="${X(i).toFixed(1)}" y="${H - 4}" font-size="8" fill="${CHART_AXIS_TEXT}" text-anchor="${a}">${fmt(dates[i])}</text>`).join('');
  }

  return `
    <div class="mini-chart-wrap">
      <svg viewBox="0 0 ${W} ${H}" class="mini-chart" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
        ${oscSvg}
        ${priceArea}
        ${volSvg}
        ${candleSvg}
        ${ma20Pts ? `<polyline points="${ma20Pts}" fill="none" stroke="${CHART_MA20}" stroke-width="1" stroke-linejoin="round"/>` : ''}
        ${ma5Pts ? `<polyline points="${ma5Pts}" fill="none" stroke="${CHART_MA5}" stroke-width="1" stroke-linejoin="round"/>` : ''}
        ${candleSvg ? '' : `<polyline points="${capPts}" fill="none" stroke="${CHART_PRICE}" stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round"/>`}
        ${capDot}
        ${capLabelSvg}
        ${priceTitle}
        ${oscTitle}
        ${oscFg}
        ${axisLabels}
      </svg>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// Supply Gauge — 5단계 percentile 시각화     │
//   percentile: 0-100 (0=가장 빈집)         │
//   5단계: 강한 빈집 / 빈집 / 중간 /         │
//          찼음 / 강한 찼음                  │
//   직관: 외인·기관이 얼마나 채웠나          │
// ─────────────────────────────────────────┘
const SUPPLY_LEVELS = [
  { key: 'strong-empty', label: '강한 빈집', desc: '외인·기관 거의 없음 — 매수 강한 자리' },
  { key: 'empty',        label: '빈집',      desc: '외인·기관 아직 안 들어옴 — 매수 자리' },
  { key: 'mid',          label: '중간',      desc: '매수세 들어오는 중 — 관망권' },
  { key: 'full',         label: '찼음',      desc: '외인·기관 들어옴 — 추격 주의' },
  { key: 'strong-full',  label: '강한 찼음', desc: '외인·기관 다 채워짐 — 추격 위험' },
];

function supplyLevelIdx(pct) {
  if (pct < 20) return 0;
  if (pct < 40) return 1;
  if (pct < 60) return 2;
  if (pct < 80) return 3;
  return 4;
}

// 게이지 단계는 zone 에서 먼저 결정하고, 백분위로 세부만 나눈다.
//
// 예전에는 백분위만 보고 단계를 정했는데, 카드에 붙는 zone 과 서로 다른
// 지표를 써서 정면으로 어긋났다 (SK이터닉스: 카드 '빈집' ↔ 모달 '강한 찼음').
// zone 은 osc 부호로 정해지는 1차 기준이고 백분위는 보조라, 이 순서를
// 뒤집으면 안 된다. 이렇게 하면 두 표시가 구조적으로 모순될 수 없다.
function supplyLevelIdxFromZone(zone, percentile) {
  const pct = percentile == null ? 50 : Math.max(0, Math.min(100, percentile));
  if (zone === '빈집') return pct < 25 ? 0 : 1;   // 강한 빈집 / 빈집
  if (zone === '찼음') return pct > 90 ? 4 : 3;   // 강한 찼음 / 찼음
  return 2;                                      // 정상 → 중간
}

function renderSupplyGauge(percentile, zone, amount) {
  if (percentile == null && !zone) return '';

  const pct = Math.max(0, Math.min(100, percentile == null ? 50 : percentile));
  const idx = zone ? supplyLevelIdxFromZone(zone, pct) : supplyLevelIdx(pct);
  const lv = SUPPLY_LEVELS[idx];

  const amt = amount != null ? fmtBillion(amount) : '-';
  const amtCls = amount == null ? '' : (amount < 0 ? 'amt-neg' : 'amt-pos');

  const segHtml = SUPPLY_LEVELS.map((s, i) => `
    <div class="supply-seg supply-seg-${s.key} ${i === idx ? 'is-on' : ''}"><span>${s.label}</span></div>
  `).join('');

  // 화살표는 반드시 강조된 칸 안에 꽂혀야 한다.
  //
  // 예전에는 강조 칸을 zone 으로 정하고 화살표는 백분위(pct)로 찍어서,
  // 둘이 서로 다른 칸을 가리키는 종목이 나왔다 (zone=빈집 인데 pct=54 면
  // 화살표는 '찼음' 칸에 가 있다). 같은 근거에서 위치를 뽑는다.
  const segW = 100 / SUPPLY_LEVELS.length;
  const within = Math.max(0.12, Math.min(0.88, pct / 100));  // 칸 안에서의 미세 위치
  const pointerPct = idx * segW + within * segW;

  return `
    <div class="supply-gauge supply-${lv.key}">
      <div class="supply-track">
        ${segHtml}
        <div class="supply-pointer" style="left:${pointerPct.toFixed(1)}%" title="수급 이력 대비 ${pct.toFixed(0)}/100">
          <svg viewBox="0 0 14 10" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
            <path d="M7 10 L0 0 L14 0 Z" fill="currentColor"/>
          </svg>
        </div>
      </div>
      <div class="supply-caption">
        <span class="supply-tag">${fEscape(lv.label)}</span>
        <span class="supply-desc">${lv.desc}</span>
        <span class="supply-amount">5일 외+기 <strong class="${amtCls}">${amt}</strong></span>
      </div>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// 수급 빈집 태그 + 분위 바 (STEP 3 — kick_mockup_light 레이아웃)
//   사실 기반 서술만: 빈집 zone · 하위 percentile · 외인+기관 5일 순매수액 ·
//   빈집 연속일 · 추세(MA10 위) ON. 추천/매수 언어 없음.
// ─────────────────────────────────────────┘
function renderVacancyTags(c) {
  const pct = c.vacancyPercentile;
  const amt = c.institutionNet5d;          // 외인+기관 5일 합산 순매수액
  const zone = c.vacancyZone || '빈집';
  const days = c.currentVacancyDays;
  const alive = c.aboveMA10;
  const pos = pct == null ? 50 : Math.max(2, Math.min(98, pct));  // 0=빈집(좌) → 100=포화(우)
  const amtStr = amt != null ? fmtBillion(amt) : null;
  return `
    <div class="vac-tags">
      <span class="vac-badge">${fEscape(zone)}</span>
      ${pct != null ? `<span class="vac-kv">하위 <b>${Math.round(pct)}%</b></span>` : ''}
      ${amtStr ? `<span class="vac-kv neg">외인+기관 5d <b>${amtStr}</b></span>` : ''}
      ${days ? `<span class="vac-kv">빈집 <b>${days}일</b></span>` : ''}
      ${alive ? `<span class="vac-on">추세 ON</span>` : ''}
    </div>
    <div class="vac-posbar">
      <span class="vac-poslab">수급 백분위</span>
      <div class="vac-postrack"><div class="vac-posknob" style="left:${pos}%"></div></div>
      <span class="vac-poslab">빈집 ◀▶ 포화</span>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// Sparkline                                 │
// ─────────────────────────────────────────┘
function renderSparkline(values, opts = {}) {
  const w = opts.width || 240;
  const h = opts.height || 50;
  const stroke = opts.stroke || '#B07A1C';
  const valid = values.filter(v => v != null && !isNaN(v));
  if (valid.length < 2) return '<div class="sparkline-empty">데이터 부족</div>';
  const min = Math.min(...valid), max = Math.max(...valid);
  const span = max - min || 1;
  const stepX = w / (values.length - 1);
  const pts = values.map((v, i) => {
    if (v == null) return null;
    const x = i * stepX;
    const y = h - ((v - min) / span) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).filter(Boolean).join(' ');
  let refSvg = '';
  if (opts.refLine != null) {
    const yRef = h - ((opts.refLine - min) / span) * h;
    refSvg = `<line x1="0" y1="${yRef}" x2="${w}" y2="${yRef}" stroke="#999" stroke-dasharray="3,3" stroke-width="1"/>`;
  }
  return `<svg viewBox="0 0 ${w} ${h}" class="sparkline" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">${refSvg}<polyline points="${pts}" fill="none" stroke="${stroke}" stroke-width="2"/></svg>`;
}

// ─────────────────────────────────────────┐
// 매수 안전성 — 동그라미 + "매수가능 4/5단계" 만 한 줄에 표시
// ─────────────────────────────────────────┘
function renderBuySafetyPill(safety) {
  if (!safety || safety.error) return '';
  const tip = `${safety.score}점 · 현금 ${fEscape(safety.cashRecommend)} · 신용 ${fEscape(safety.creditRecommend)}`;
  return `<span class="safety-pill safety-pill-${safety.stage}" title="${tip}">${safety.stageEmoji} ${fEscape(safety.stageLabel)} ${safety.stageIndex + 1}/${safety.totalStages}단계</span>`;
}

// ─────────────────────────────────────────┐
// CARD: STEP 1 — 시장 심리 + 현금 비중 통합 │
// ─────────────────────────────────────────┘
function renderMddMa3(history, close) {
  if (!history || history.length < 3) return '';
  const closes = history.map(p => p.close);
  const mdd = computeCurrentMddPct(closes);
  // 차트가 5·20일선을 그리므로 헤더 추세 표기도 5일선으로 맞춘다.
  // 한국 HTS 에 3일선은 없어서, 3일선만 여기 남으면 화면끼리 어긋난다.
  const ma5Arr = computeMA(closes, 5);
  const ma3 = ma5Arr[ma5Arr.length - 1];
  const mddStr = mdd != null ? `${mdd >= 0 ? '+' : ''}${mdd.toFixed(2)}%` : '-';
  const mddColor = mdd == null ? '#999'
                 : mdd <= -15 ? '#E53935'
                 : mdd <= -10 ? '#FB8C00'
                 : mdd <= -5 ? '#FDD835'
                 : '#43A047';
  const ma3Str = ma3 != null ? fmtNumber(Number(ma3.toFixed(2))) : '-';
  const arrow = (ma3 != null && close != null)
              ? (close >= ma3 ? '<span style="color:#E53935">▲</span>' : '<span style="color:#1E88E5">▼</span>')
              : '';
  return `
    <span class="sentiment-mdd" title="현재 누적 고점 대비 낙폭 (최근 120일)">MDD <strong style="color:${mddColor}">${mddStr}</strong></span>
    <span class="sentiment-ma3" title="5일 이동평균선 — 종가가 위/아래인지로 단기 추세 판단">5일선 ${ma3Str} ${arrow}</span>
  `;
}

function buildStep1Card(sentiment, cash) {
  const k = sentiment?.kospi || {};
  const q = sentiment?.kosdaq || {};
  const buySafety = sentiment?.buySafety || null;
  const kSafety = buySafety?.kospi || null;
  const qSafety = buySafety?.kosdaq || null;

  return `
    <div class="flow-card flow-card-step1 flow-step1">
      <div class="card-header step-header">
        <span class="step-num">1</span>
        <span class="card-theme-name">시장 · 지수와 심리</span>
      </div>
      <div class="step1-body">
        <div class="step1-row">
          <div class="step1-row-head">
            <strong>${fEscape(k.label || 'KOSPI')}</strong>
            ${renderBuySafetyPill(kSafety)}
            <span class="sentiment-fg">공포·탐욕 ${k.fearGreed?.toFixed(1) ?? '-'} · 오실 ${k.oscillator?.toFixed(2) ?? '-'}</span>
            <span class="sentiment-close">종가 ${fmtNumber(k.close)}</span>
            ${renderMddMa3(k.history, k.close)}
          </div>
          ${renderDualAxisChart(k.history)}
        </div>
        <div class="step1-row">
          <div class="step1-row-head">
            <strong>${fEscape(q.label || 'KOSDAQ')}</strong>
            ${renderBuySafetyPill(qSafety)}
            <span class="sentiment-fg">공포·탐욕 ${q.fearGreed?.toFixed(1) ?? '-'} · 오실 ${q.oscillator?.toFixed(2) ?? '-'}</span>
            <span class="sentiment-close">종가 ${fmtNumber(q.close)}</span>
            ${renderMddMa3(q.history, q.close)}
          </div>
          ${renderDualAxisChart(q.history)}
        </div>
        <div class="chart-legend">
          <span><i class="ln" style="background:${CHART_PRICE};height:2.5px"></i>일봉</span>
          <span><i class="ln" style="background:${CHART_MA5}"></i>5일선</span>
          <span><i class="ln" style="background:${CHART_MA20}"></i>20일선</span>
          <span><i class="ln" style="background:${CHART_OSC}"></i>공포·탐욕 오실레이터</span>
          <span class="cl-zone">아래창 음영 = 공포·탐욕 백분위 (붉을수록 과열 · 푸를수록 공포)</span>
        </div>
      </div>
    </div>
  `;
}


// ─────────────────────────────────────────┐
// CARD: STEP 2 — 섹터 강도 + 쏠림 통합     │
// ─────────────────────────────────────────┘
function buildStep2Card(leading, crowding, leadingLabels) {
  const top = leading?.top || [];
  const rsLeaders = top.filter(e => e.rsNorm >= 70).slice(0, 6);
  const rsBackup = rsLeaders.length === 0 ? top.slice(0, 6) : rsLeaders;

  const crowdLatest = crowding?.latest;
  const crowdSignal = crowding?.signal || '-';
  const sigColor = crowdSignal === '극심쏠림' ? '#E53935'
                : crowdSignal === '쏠림' ? '#FB8C00'
                : crowdSignal === '주의' ? '#FDD835'
                : '#43A047';

  const flowSectors = leadingLabels?.length ? leadingLabels.slice(0, 6).join(' · ') : '';

  return `
    <div class="flow-card flow-card-step2 flow-step2">
      <div class="card-header step-header">
        <span class="step-num">2</span>
        <span class="card-theme-name">업종 · 어디가 강한가</span>
        ${crowdLatest != null ? `<span class="crowd-pill" style="background:${sigColor}">쏠림 ${crowdLatest.toFixed(0)} ${fEscape(crowdSignal)}</span>` : ''}
      </div>
      <div class="step2-body">
        <div class="step2-rs">
          <div class="step2-label">시장 대비 강도 70+ ETF</div>
          ${rsBackup.map(e => `
            <div class="step2-rs-row">
              <span class="step2-rs-name">${fEscape(e.name)}</span>
              <div class="rs-bar"><div class="rs-bar-fill" style="width:${rsBarWidth(e.rsNorm)}%; background:${e.rsNorm >= 70 ? '#E53935' : e.rsNorm >= 50 ? '#FB8C00' : '#1E88E5'}"></div></div>
              <span class="step2-rs-num">${e.rsNorm}</span>
              <span class="step2-rs-mom ${changeClass(e.ret3m)}">${e.ret3m != null ? (e.ret3m > 0 ? '+' : '') + e.ret3m + '%' : '-'} <small>3M</small></span>
            </div>
          `).join('')}
        </div>
        ${flowSectors ? `<div class="step2-flow">자금 유입 섹터: <strong>${fEscape(flowSectors)}</strong></div>` : ''}
      </div>
    </div>
  `;
}


// ─────────────────────────────────────────┐
// 매수 후보 핵심 키워드 chip — 기존 badge/vacancy-now 와 중복되지 않는 것만.
// (★주도1·2위, ↑MA, 신고가, 매수권 = 이미 badge / 5d%, 매도연속 = 이미 다른 위치)
// ─────────────────────────────────────────┘
// flowScore 를 5단계 등급으로 매핑.
// 종합 점수가 곧 매수 강도이므로 모든 개별 시그널을 한 라벨로 압축.
function buyGradeBadge(score) {
  if (score == null) return '';
  let grade, cls;
  if (score >= 95)      { grade = 5; cls = 'badge-red'; }
  else if (score >= 75) { grade = 4; cls = 'badge-orange'; }
  else if (score >= 55) { grade = 3; cls = 'badge-yellow'; }
  else if (score >= 30) { grade = 2; cls = 'badge-gray'; }
  else                  { grade = 1; cls = 'badge-blue'; }
  return `<span class="badge ${cls}">매수 ${grade}/5</span>`;
}

// ─────────────────────────────────────────┐
// 차트 선 범례 (각 차트 하단 — 선/음영 설명)  │
// ─────────────────────────────────────────┘
// 범례가 없으면 어느 선이 주가고 어느 선이 이평선인지 알 수 없다.
// 선 견본 색은 실제 stroke 값과 반드시 같아야 한다 (CHART_* 토큰 사용).
// ─────────────────────────────────────────┐
// 일별 수급 히트맵 (10거래일)                │
// ─────────────────────────────────────────┘
// 종가베팅·1~2일 보유 관점에서 가장 중요한 건 "수급이 언제 돌아섰나"다.
// HTS 에서는 종목마다 투자자별 매매동향을 일일이 열어봐야 보이는데,
// 우리는 후보 전 종목에 대해 이미 계산해 두고 화면에 안 쓰고 있었다.
// 외인/기관 각각 일별 순매수 부호와 크기를 칸 색으로 보여준다.
// 수급 상태 — '지금 비어 있나 / 채워지기 시작했나'를 한 곳에서 판정한다.
// 빈집 타임라인(수급 탭)과 오늘 탭의 종목 리스트가 같은 문구를 쓰도록 공유한다.
// 두 화면이 다른 규칙으로 같은 말을 하면 사용자는 어느 쪽을 믿을지 알 수 없다.
function supplyStateOf(c) {
  const days = (c && c.dailyFlow10d) || [];
  let turnIdx = -1;
  for (let i = 1; i < days.length; i++) {
    const prev = days[i - 1].instAmount || 0, cur = days[i].instAmount || 0;
    if (prev <= 0 && cur > 0) turnIdx = i;          // 순매도 → 순매수 전환
  }
  const sinceTurn = turnIdx >= 0 ? (days.length - 1 - turnIdx) : -1;
  const streak = (c && c.currentVacancyDays) || 0;

  if (sinceTurn === 0) return { label: '오늘 채우기 시작', cls: 'fh-state-turn', turnIdx, sinceTurn, streak };
  if (sinceTurn > 0 && sinceTurn <= 2) return { label: `${sinceTurn}일 전 채우기 시작`, cls: 'fh-state-turn', turnIdx, sinceTurn, streak };
  if (streak > 0) return { label: `${streak}일째 비어있음`, cls: 'fh-state-empty', turnIdx, sinceTurn, streak };
  return { label: '수급 관망', cls: 'fh-state-flat', turnIdx, sinceTurn, streak };
}

function renderFlowHeatmap(c) {
  const days = c.dailyFlow10d || [];
  if (days.length < 3) return '';

  // ── 이 그림이 답해야 하는 것 ────────────────────────────
  // 후보 목록은 "지금 비어 있다"는 정적 사실만 말한다. 매매에 필요한 건
  // "언제 채워지기 시작하나"다 — 빈집 논리의 값은 기관·외인이 돌아서는
  // 순간에 나오기 때문이다. 그래서 이 표의 일은 셋이다:
  //   ① 지금 비어 있나  ② 얼마나 깊게·오래  ③ 채워지기 시작했나(전환)
  //
  // [이전 결함] 일별 순매수 '원' 을 그 종목 자체의 10일 최대값으로 정규화했다.
  //   - 같은 진한 칸이 A 종목 8.8, B 종목 227(만분율) 을 뜻해 종목 간 비교 불가
  //     (실측 26배 차이)
  //   - 빈집 판정 지표(시총 정규화 오실레이터)와 단위·기간이 달라
  //     "빈집이라는데 칸은 빨갛다" 는 불일치가 생겼다
  // → (1) 오실레이터 행을 맨 위에 둬 빈집 판정과 같은 지표·같은 분포를 쓴다.
  //   (2) 외인·기관은 시총 대비 만분율 + 고정 스케일로 바꿔 종목 간 채도가
  //       같은 뜻을 갖게 한다.
  const mc = c.marketCap || 0;

  // 고정 스케일 — 후보 종목 일별 |순매수/시총| 의 p90 실측치(만분율).
  // 제곱근 곡선이라 중간값(p50≈8)도 또렷하고 극단만 포화된다.
  const FLOW_FULL = 53;
  const localMax = Math.max(...days.flatMap(d => [Math.abs(d.foreigner || 0), Math.abs(d.organ || 0)]), 1);

  const flowCell = (v) => {
    const amt = v || 0;
    const t = mc
      ? Math.min(1, Math.sqrt(Math.abs(amt) / mc * 1e4 / FLOW_FULL))
      : Math.min(1, Math.sqrt(Math.abs(amt) / localMax));
    const bg = t < 0.12 ? '#F1EADC'
      : `${amt >= 0 ? 'rgba(229,57,53,' : 'rgba(21,101,192,'}${(0.14 + t * 0.74).toFixed(2)})`;
    const per = mc ? ` (시총 대비 ${(amt / mc * 1e4).toFixed(1)}bp)` : '';
    return `<i style="background:${bg}" title="${amt >= 0 ? '+' : ''}${fmtBillion(amt)}${per}"></i>`;
  };

  // 빈집도 행 — 빈집 판정과 동일한 시계열·동일한 자기분포 백분위.
  // 뱃지가 '빈집' 이면 이 행의 마지막 칸은 반드시 파랗다 (같은 지표라서).
  const oscHist = c.supplyOscHistory || [];
  const oscAll = oscHist.map(o => o.osc).filter(v => v != null).sort((a, b) => a - b);
  const oscByDate = new Map(oscHist.filter(o => o.osc != null).map(o => [o.date, o.osc]));
  const pctOf = (v) => oscAll.length ? oscAll.filter(x => x < v).length / oscAll.length * 100 : 50;
  const oscCell = (d) => {
    const v = oscByDate.get(d.date);
    if (v == null) return '<i style="background:#F1EADC" title="데이터 없음"></i>';
    const pct = pctOf(v);
    const t = Math.min(1, Math.abs(pct - 50) / 50);
    const bg = t < 0.12 ? '#F1EADC'
      : `${pct >= 50 ? 'rgba(229,57,53,' : 'rgba(21,101,192,'}${(0.14 + t * 0.74).toFixed(2)})`;
    return `<i style="background:${bg}" title="빈집도 하위 ${pct.toFixed(0)}%"></i>`;
  };

  const md = (s) => { const m = /^\d{4}-(\d{2})-(\d{2})$/.exec(s || ''); return m ? `${+m[1]}/${+m[2]}` : ''; };
  const ss = supplyStateOf(c);
  const { turnIdx, label: state, cls: stateCls } = ss;
  const pctLast = c.oscPercentile;
  const depth = (pctLast != null) ? `빈집도 하위 ${Math.round(pctLast)}%` : '';

  return `
    <div class="fh">
      <div class="fh-head">
        <span class="fh-title">빈집 타임라인 <em>10거래일</em></span>
        <span class="fh-badges">
          ${depth ? `<span class="fh-depth">${depth}</span>` : ''}
          <span class="fh-state ${stateCls}">${state}</span>
        </span>
      </div>
      <div class="fh-grid">
        <span class="fh-lab fh-lab-key">빈집도</span>
        <span class="fh-cells">${days.map(d => oscCell(d)).join('')}</span>
        <span class="fh-lab">외국인</span>
        <span class="fh-cells">${days.map(d => flowCell(d.foreigner)).join('')}</span>
        <span class="fh-lab">기관</span>
        <span class="fh-cells">${days.map(d => flowCell(d.organ)).join('')}</span>
        <span class="fh-lab"></span>
        <span class="fh-cells fh-marks">${days.map((_, i) => `<em>${i === turnIdx ? '▲' : ''}</em>`).join('')}</span>
        <span class="fh-lab"></span>
        <span class="fh-cells fh-dates">${days.map((d, i) =>
          `<em>${(i === 0 || i === days.length - 1) ? md(d.date) : ''}</em>`).join('')}</span>
      </div>
      <div class="fh-legend">푸를수록 비어있음 · 붉을수록 채워짐${turnIdx >= 0 ? ' · ▲ 순매수 전환일' : ''}</div>
    </div>`;
}

// 종가베팅 관점 한 줄 — 오늘 고가에서 얼마나 눌렸나, 거래대금은 평소 대비 어떤가.
// 눌림에서 담는 전략이라 이 둘이 진입 판단의 재료다.
function renderCloseBetLine(c) {
  const bz = c.buyZone || {};
  const parts = [];
  if (bz.todayPullbackPct != null)
    parts.push(`<span>고가 대비 <b class="${bz.todayPullbackPct < 0 ? 'down' : 'up'}">${bz.todayPullbackPct.toFixed(1)}%</b></span>`);
  if (c.tradingValueRatio != null)
    parts.push(`<span>거래대금 <b>${c.tradingValueRatio.toFixed(2)}배</b><em>(20일 평균 대비)</em></span>`);
  if (c.foreignerHoldRatio) parts.push(`<span>외인 지분 <b>${fEscape(c.foreignerHoldRatio)}</b></span>`);
  if (!parts.length) return '';
  return `<div class="cb-line">${parts.join('')}</div>`;
}

function renderChartLegend() {
  return `
    <div class="chart-legend">
      <span><i class="ln" style="background:${CHART_PRICE};height:2.5px"></i>일봉</span>
      <span><i class="ln" style="background:${CHART_MA5}"></i>5일선</span>
      <span><i class="ln" style="background:${CHART_MA20}"></i>20일선</span>
      <span><i class="ln" style="background:${CHART_OSC}"></i>수급 오실레이터</span>
      <span class="cl-zone">아래창 음영 = 수급 백분위 (붉을수록 채움 · 푸를수록 빈집)</span>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// 공용 종목 카드 (STEP3 = 빈집, 주도섹터 거래대금 공용) │
//   헤더行 + 풀폭 차트박스 + 선 범례 + 빈집 태그行 + 분위 바
// ─────────────────────────────────────────┘
// 포착 경로 — "왜 이 종목이 나왔나" 를 한 줄로 되짚는다.
//   주도 ETF(RS) → 섹터 → 종목 → 빈집·추세 상태
// 리스트만 던지면 "AI 가 뽑았다" 는 주장이 되고, 경로를 보여주면 근거가 된다.
// 출처(leadingSectorSources)는 백엔드가 ETF 매칭 시점에 기록해 내려준다.
function renderPickPath(c) {
  const flow = (typeof flowData !== 'undefined' && flowData) ? flowData : null;
  const sector = c.sector;
  if (!flow || !sector) return '';
  const labels = flow.leadingSectorLabels || [];
  const rank = labels.indexOf(sector);
  if (rank < 0) return '';
  const src = (flow.leadingSectorSources || {})[sector] || {};

  const steps = [];
  if (src.via === 'etf' && src.etf) {
    steps.push(`<em class="pp-src">${fEscape(src.etf)}</em><small>RS ${src.rsNorm}</small>`);
  } else if (src.via === 'flow' && src.strength != null) {
    steps.push(`<em class="pp-src">외인·기관 자금 유입</em><small>강도 ${src.strength}</small>`);
  }
  steps.push(`<em>${fEscape(sector)}</em><small>주도 ${rank + 1}위</small>`);

  // 실제 편입비중 — 여기가 예전엔 비어 있었다. "그 ETF 가 강하고 같은 섹터니까
  // 이 종목에도 자금이 온다" 는 추정이었는데, ETF 가 이 종목을 담고 있어야
  // 성립하는 주장이다. 담은 비중을 그대로 보여 추정을 근거로 바꾼다.
  //
  // 집계 대상은 '테마 ETF' 다. 시장 전체 지수(KODEX 200 등)는 백엔드에서
  // 제외했다 — 거기 담겼다는 건 테마 근거가 아니라 시가총액을 다시 말하는 것이다.
  // 미편입은 숨기지 않는다. 어떤 테마 ETF 도 안 담았다는 사실이 정보다.
  const eh = c.etfHoldings;
  if (eh && eh.top && eh.top.length) {
    const lead = eh.top[0];
    const more = eh.count > 1 ? ` 외 ${eh.count - 1}` : '';
    const star = eh.leadingCount ? '<b class="pp-lead-mark" title="주도 ETF(RS 70+)가 담고 있음">★</b>' : '';
    steps.push(`<em class="pp-hold">${fEscape(lead.etf)}${more}${star}</em>` +
               `<small>편입 ${eh.totalWeight}%</small>`);
  } else if (eh === null) {
    steps.push(`<em class="pp-hold-none">테마 ETF 미편입</em><small>수급 근거만</small>`);
  }

  const state = (typeof supplyStateOf === 'function') ? supplyStateOf(c) : null;
  const last = [];
  if (c.oscPercentile != null) last.push(`빈집 하위 ${Math.round(c.oscPercentile)}%`);
  if (c.aboveMA10) last.push('10일선 위');
  if (state) last.push(state.label);
  steps.push(`<em>${fEscape(c.name)}</em><small>${fEscape(last.join(' · '))}</small>`);

  return `
    <div class="pp">
      <span class="pp-title">포착 경로</span>
      <div class="pp-chain">${steps.map((h, i) =>
        `${i ? '<span class="pp-arrow">›</span>' : ''}<span class="pp-step">${h}</span>`).join('')}</div>
    </div>`;
}

function renderCandCardV2(c, idx) {
  const sectorLabel = c.sector || '-';
  const rankBadge = `<span class="rank-badge${idx === 0 ? ' rank-badge-top' : ''}">#${idx + 1}</span>`;
  return `
    <div class="cand-row cand-v2 clickable" data-stock-code="${fEscape(c.code)}" data-stock-name="${fEscape(c.name)}" title="${fEscape(c.name)} 차트 보기">
      <div class="cand-v2-head">
        ${rankBadge}
        <div class="cand-v2-name">
          <div class="cand-name">${fEscape(c.name)}</div>
          <div class="cand-v2-sub">${fEscape(c.code)} · ${fEscape(sectorLabel)}</div>
        </div>
        <div class="cand-v2-px">
          <span class="cand-close">${fmtNumber(c.close)}</span>
          ${c.ret5d != null ? `<span class="cand-ret ${changeClass(c.ret5d)}">5d ${fmtPctSigned(c.ret5d)}</span>` : ''}
        </div>
      </div>
      <div class="cand-v2-chartbox">${renderMiniPriceChart(c)}</div>
      ${renderChartLegend()}
      ${renderCloseBetLine(c)}
      ${renderPickPath(c)}
      ${renderFlowHeatmap(c)}
      ${renderVacancyTags(c)}
    </div>
  `;
}

// ─────────────────────────────────────────┐
// CARD: Buy candidates (STEP 3 — main hero) │
// ─────────────────────────────────────────┘
// 필터 통과 현황 — "무엇을 왜 걸러냈는지" 를 보여준다.
// 후보 목록에 미달 종목이 섞이지 않았음을 사용자가 직접 확인할 수 있는 근거.
function renderFilterStats(stats) {
  if (!stats || !stats.beforeFilter) return '';
  const parts = [];
  if (stats.droppedByTrend)         parts.push(`10일선 이탈 ${stats.droppedByTrend}`);
  if (stats.droppedByVacancy)       parts.push(`빈집 아님 ${stats.droppedByVacancy}`);
  if (stats.droppedByScore)         parts.push(`점수 미달 ${stats.droppedByScore}`);
  if (stats.droppedByConcentration) parts.push(`섹터 편중 ${stats.droppedByConcentration}`);
  const detail = parts.length ? ` — 제외: ${parts.join(' · ')}` : '';
  return `<div class="cand-filter-stats">검토 ${stats.beforeFilter}종목 → 조건 통과 ${stats.afterFilter}종목${fEscape(detail)}</div>`;
}

function buildBuyCandidatesCard(candidates, leadingLabels, filterStats) {
  if (!candidates || candidates.length === 0) {
    return `<div class="flow-card flow-card-candidates flow-step3"><div class="card-header step-header"><span class="step-num">3</span><span class="card-theme-name">종목 · 수급 <b class="ct-accent">빈집</b></span></div><div class="empty-msg">현재 조건을 통과한 종목 없음</div></div>`;
  }
  const sectorTag = leadingLabels?.length ? leadingLabels.slice(0, 5).join(', ') : '';
  // 참고 자료: 한 종목 4-5% × 20개. 5개보다 적으면 의미 없음.
  const SHOW_N = 20;
  const shown = Math.min(SHOW_N, candidates.length);

  const rows = candidates.slice(0, SHOW_N).map((c, idx) => renderCandCardV2(c, idx)).join('');

  return `
    <div class="flow-card flow-card-candidates flow-step3">
      <div class="card-header step-header">
        <span class="step-num">3</span>
        <span class="card-theme-name">종목 · 수급 <b class="ct-accent">빈집</b> ${sectorTag ? `<small>(${fEscape(sectorTag)})</small>` : ''}</span>
      </div>
      <div class="cand-submeta">${shown}종목 · 외인·기관 순매수 빠진 자리(빈집) ∩ 추세 생존(10일선 위)</div>
      ${renderFilterStats(filterStats)}
      <div class="cand-body">${rows}</div>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// CARD: 주도섹터 거래대금 톱10               │
// 매수 후보(빈집 전략) 와 별개로, 외인+기관   │
// 동행 매수 중인 거래대금 1위급 주도주        │
// ─────────────────────────────────────────┘
function buildLeadingValueCard(items, leadingLabels) {
  if (!items || items.length === 0) return '';

  const rows = items.map((c, idx) => renderCandCardV2(c, idx)).join('');

  return `
    <div class="flow-card flow-card-candidates flow-card-leading-value">
      <div class="card-header step-header">
        <span class="step-num">TOP 10</span>
        <span class="card-theme-name">주도 섹터 <b class="ct-accent">거래대금</b> 상위</span>
      </div>
      <div class="cand-submeta">외인+기관이 가장 큰 돈을 베팅 중인 주도주 · 거래대금순 (수급 채워진 종목 포함)</div>
      <div class="cand-body">${rows}</div>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// CARD: Trading intensity                   │
// ─────────────────────────────────────────┘
function buildTICard(items) {
  if (!items || items.length === 0) return '';
  return `
    <div class="flow-card flow-card-ti">
      <div class="card-header"><span class="card-theme-name">거래대금 강도</span><span class="card-volume">${items.length}개</span></div>
      <div class="ti-body">
        ${items.map(t => {
          const tiColor = t.ti >= 80 ? '#E53935' : t.ti >= 60 ? '#FB8C00' : t.ti >= 40 ? '#FDD835' : t.ti >= 20 ? '#43A047' : '#1E88E5';
          return `
            <div class="ti-row clickable" data-stock-code="${fEscape(t.code)}" data-stock-name="${fEscape(t.name)}" title="${fEscape(t.name)} 차트 보기">
              <div class="ti-info">
                <div class="ti-name">${fEscape(t.name)} <small>${fEscape(t.sector || '-')}</small></div>
                <div class="ti-meta">거래대금 강도 <strong style="color:${tiColor}">${t.ti}</strong> <small>${fEscape(t.zone)}</small> · ${fmtNumber(t.close)}원</div>
              </div>
              <div class="ti-chart">
                ${renderSparkline(t.priceHistory, { stroke: '#1A1A1A', height: 30, width: 110 })}
                ${renderSparkline(t.tiHistory, { stroke: tiColor, height: 30, width: 110, refLine: 50 })}
              </div>
            </div>
          `;
        }).join('')}
      </div>
      <div class="ti-tip">바닥(<20)→강세 진입 + 신고가 = 매수, 과열(>80) = 식음</div>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// CARD: Investor sector flow                │
// ─────────────────────────────────────────┘
function buildSectorFlowCard(flows) {
  if (!flows) return '';
  const renderFlowList = (arr, title, colorFn) => {
    if (!arr || arr.length === 0) return `<div class="flow-col-empty">${fEscape(title)} 데이터 없음</div>`;
    const max = Math.max(...arr.map(x => Math.abs(x.amount)));
    return `
      <div class="flow-col">
        <div class="flow-col-title">${fEscape(title)}</div>
        ${arr.slice(0, 8).map(x => {
          const pct = max > 0 ? Math.abs(x.amount) / max * 100 : 0;
          const color = colorFn(x.amount);
          return `
            <div class="flow-bar-row">
              <span class="flow-bar-name">${fEscape(x.sector)}</span>
              <div class="flow-bar-track"><div class="flow-bar-fill" style="width:${pct}%; background:${color}"></div></div>
              <span class="flow-bar-amount ${x.amount >= 0 ? 'up' : 'down'}">${fmtBillion(x.amount)}</span>
            </div>
          `;
        }).join('')}
      </div>
    `;
  };

  const colorByAmount = (a) => a >= 0 ? '#E53935' : '#1E88E5';
  return `
    <div class="flow-card flow-card-sectorflow">
      <div class="card-header"><span class="card-theme-name">🌊 외인 / 기관 섹터별 매수 (5일)</span></div>
      <div class="sectorflow-body">
        ${renderFlowList(flows.foreigner, '🌍 외국인', colorByAmount)}
        ${renderFlowList(flows.organ, '🏛 기관', colorByAmount)}
      </div>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// CARD: Leading ETF                         │
// ─────────────────────────────────────────┘
function buildLeadingCard(leading) {
  if (!leading || !leading.top || leading.top.length === 0) return '';
  return `
    <div class="flow-card flow-card-leading">
      <div class="card-header"><span class="card-theme-name">주도 업종 ETF</span><span class="card-volume">강도 70+ ${leading.leadingCount || 0}개</span></div>
      <div class="leading-body">
        <div class="leading-table-head"><span>ETF</span><span>시장대비강도</span><span>3개월</span><span>1개월</span></div>
        ${leading.top.slice(0, 12).map(e => `
          <div class="leading-row ${e.rsNorm >= 70 ? 'is-leading' : ''}">
            <span class="leading-name">${fEscape(e.name)}</span>
            <span class="leading-rs"><div class="rs-bar"><div class="rs-bar-fill" style="width:${rsBarWidth(e.rsNorm)}%; background:${e.rsNorm >= 70 ? '#E53935' : e.rsNorm >= 50 ? '#FB8C00' : '#1E88E5'}"></div></div><span class="rs-text">${e.rsNorm}</span></span>
            <span class="${changeClass(e.ret3m)}">${e.ret3m != null ? (e.ret3m > 0 ? '+' : '') + e.ret3m + '%' : '-'}</span>
            <span class="${changeClass(e.ret1m)}">${e.ret1m != null ? (e.ret1m > 0 ? '+' : '') + e.ret1m + '%' : '-'}</span>
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// CARD: Exit signals                        │
// ─────────────────────────────────────────┘
function buildExitCard(exits) {
  if (!exits || exits.length === 0) return '';
  return `
    <div class="flow-card flow-card-exit">
      <div class="card-header"><span class="card-theme-name">매도 시그널</span><span class="card-volume">${exits.length}개</span></div>
      <div class="exit-body">
        ${exits.slice(0, 10).map(e => `
          <div class="exit-row">
            <span class="exit-name">${fEscape(e.name)} <small>${fEscape(e.sector || '-')}</small></span>
            <span class="exit-pull down">${e.drawdownFromHighPct}%</span>
            <span class="exit-meta">${fmtNumber(e.lastClose)} / 10일선 ${fmtNumber(e.ma10)}</span>
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// Main loader                               │
// ─────────────────────────────────────────┘
async function loadFlow() {
  if (flowLoadPromise) return flowLoadPromise;
  flowLoadPromise = (async () => {
  const container = document.getElementById('flow-content');
  const loading = document.getElementById('flow-loading');
  try {
    const resp = await fetch(FLOW_DATA_URL + '?t=' + Date.now());
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    flowData = data;
    if (loading) loading.remove();

    container.innerHTML = `
      <div class="flow-meta">
        <span>업데이트: ${new Date(data.updatedAt).toLocaleString('ko-KR')}</span>
        <span>분석 ${data.vacancyAnalyzed || 0}/${data.universeSize || 0} · ${data.elapsedSeconds}s</span>
      </div>
      <div class="flow-grid flow-grid-v2">
        ${buildStep1Card(data.marketSentiment, data.cashRecommendation)}
        ${buildStep2Card(data.leadingSectors, data.crowding, data.leadingSectorLabels)}
        ${buildBuyCandidatesCard(data.buyCandidates, data.leadingSectorLabels, data.candidateFilterStats)}
        ${buildLeadingValueCard(data.leadingValueTop, data.leadingSectorLabels)}
      </div>
      <details class="flow-collapsible">
        <summary>▼ 정밀 분석 펼치기 (주트레이더용)</summary>
        <div class="flow-grid flow-grid-collapsed">
          ${buildSectorFlowCard(data.sectorFlows)}
          ${buildTICard(data.tradingIntensity)}
          ${buildExitCard(data.exitSignals)}
          ${buildLeadingCard(data.leadingSectors)}
        </div>
      </details>
    `;
    flowLoaded = true;
  } catch (err) {
    console.error('flow load error:', err);
    container.innerHTML = `
      <div class="error-state">
        <p>수급 데이터를 불러올 수 없습니다.</p>
        <p style="font-size:0.8rem;color:#999">${fEscape(err.message)}</p>
        <button class="retry-btn" onclick="loadFlow()">다시 시도</button>
      </div>
    `;
    flowLoadPromise = null;  // 실패 시 재시도 허용
  }
  })();
  return flowLoadPromise;
}

// ─────────────────────────────────────────┐
// Stock search — 종목 검색 → 모달에서 STEP3 차트 표시
//   소스: buyCandidates + leadingValueTop (capHistory60d + supplyOscHistory 보유분)
// ─────────────────────────────────────────┘
function buildSearchIndex() {
  if (!flowData) return [];
  const seen = new Set();
  const items = [];
  // 1차 — chart history 가 있는 종목 (모달 상단에 수급 osc 차트 표시 가능)
  const pushWithChart = (arr) => {
    if (!arr) return;
    arr.forEach(c => {
      if (!c || !c.code || seen.has(c.code)) return;
      const hasChart = (c.capHistory60d && c.capHistory60d.length >= 2)
                    || (c.priceHistory60d && c.priceHistory60d.length >= 2);
      if (!hasChart) return;
      seen.add(c.code);
      items.push(c);
    });
  };
  // 2차 — metadata only (chart history 는 없지만 검색은 됨, 모달은 캔들차트만)
  const pushMetaOnly = (arr) => {
    if (!arr) return;
    arr.forEach(c => {
      if (!c || !c.code || seen.has(c.code)) return;
      if (!c.name) return;
      seen.add(c.code);
      items.push(c);
    });
  };
  pushWithChart(flowData.buyCandidates);
  pushWithChart(flowData.leadingValueTop);
  pushMetaOnly(flowData.universeMetadata);
  return items;
}

function searchStocks(query, max = 8) {
  const q = (query || '').trim().toLowerCase();
  if (!q) return [];
  const idx = buildSearchIndex();
  const out = [];
  for (const c of idx) {
    const name = (c.name || '').toLowerCase();
    const code = (c.code || '').toLowerCase();
    if (name.includes(q) || code.includes(q)) {
      out.push(c);
      if (out.length >= max) break;
    }
  }
  return out;
}

function renderSuggestions(matches) {
  const box = document.getElementById('search-suggestions');
  if (!box) return;
  if (!matches || matches.length === 0) {
    box.innerHTML = '<div class="search-suggestion-empty">일치하는 종목이 없습니다 (KOSPI/KOSDAQ 시총 상위 600 유니버스 검색)</div>';
    box.hidden = false;
    return;
  }
  box.innerHTML = matches.map((c, i) => `
    <div class="search-suggestion-item${i === 0 ? ' active' : ''}" data-code="${fEscape(c.code)}">
      <span class="search-suggestion-name">${fEscape(c.name)}</span>
      <span class="search-suggestion-code">${fEscape(c.code)}</span>
      <span class="search-suggestion-sector">${fEscape(c.sector || '')}</span>
    </div>
  `).join('');
  box.hidden = false;
}

function hideSuggestions() {
  const box = document.getElementById('search-suggestions');
  if (box) box.hidden = true;
}

function findStockByCode(code) {
  return buildSearchIndex().find(c => c.code === code) || null;
}

// 검색 결과 → 통합 모달 (수급 osc 차트 + 캔들 차트).
//   - 위: renderMiniPriceChart (시가총액 + 5·20일선 + 수급 오실레이터) + supply gauge
//        (capHistory60d 가 있는 종목 — buyCandidates / leadingValueTop 만)
//   - 아래: chart.js 의 캔들차트 (모든 종목)
function buildTopOscHTML(c) {
  const hasChart = (c.capHistory60d && c.capHistory60d.length >= 2)
                || (c.priceHistory60d && c.priceHistory60d.length >= 2);
  if (!hasChart) return '';

  const sectorLabel = c.sector || '-';
  const priceLine = `
    <div class="search-modal-top-meta">
      <span class="search-modal-top-name">${fEscape(c.name)}</span>
      <small>${fEscape(c.code)} · ${fEscape(sectorLabel)}</small>
      <span class="cand-close">${fmtNumber(c.close)}</span>
      ${c.ret5d != null ? `<span class="cand-ret ${changeClass(c.ret5d)}">5d ${fmtPctSigned(c.ret5d)}</span>` : ''}
    </div>
  `;
  const gauge = (c.vacancyPercentile != null)
    // oscPercentile 을 쓴다. vacancyPercentile 은 유니버스 점수 백분위라
    // zone 과 다른 지표이고, 백엔드도 osc 가 있으면 그쪽을 무시한다.
    ? renderSupplyGauge(c.oscPercentile, c.vacancyZone, c.institutionNet5d)
    : '';
  return `
    <div class="search-modal-osc-wrap">
      ${priceLine}
      <div class="search-modal-osc-chart">${renderMiniPriceChart(c)}</div>
      ${gauge}
      <div class="search-modal-legend">
        <span><span class="legend-line" style="background:${CHART_PRICE}"></span>일봉</span>
        <span><span class="legend-line" style="background:${CHART_MA5}"></span>5일선</span>
        <span><span class="legend-line" style="background:${CHART_MA20}"></span>20일선</span>
        <span><span class="legend-line" style="background:${CHART_OSC}"></span>수급 오실레이터</span>
        <span class="legend-zones">
          아래창 음영:
          <span class="zone-chip zone-empty">빈집</span>
          <span class="zone-chip zone-cool">비어감</span>
          <span class="zone-chip zone-neutral">중립</span>
          <span class="zone-chip zone-warm">채움</span>
          <span class="zone-chip zone-hot">과열</span>
        </span>
      </div>
    </div>
  `;
}

function openStockModal(c) {
  if (!c || !c.code) return;
  if (typeof window.openStockChart !== 'function') return;
  const topHTML = buildTopOscHTML(c);
  window.openStockChart(c.code, c.name, { topHTML });
}

// chart.js 의 [data-stock-code] 클릭 위임에서 호출 — 카드/리스트 어디서 클릭해도
// 검색 모달과 동일하게 수급 osc 차트 + supply gauge 가 상단에 함께 뜨도록.
window.getStockTopHTML = function (code) {
  const c = findStockByCode(code);
  if (!c) return '';
  return buildTopOscHTML(c);
};

async function ensureFlowLoaded() {
  if (flowData) return;
  await loadFlow();
}

function setupStockSearch() {
  const input = document.getElementById('search-input');
  const btn = document.getElementById('search-btn');
  const box = document.getElementById('search-suggestions');
  const modal = document.getElementById('stock-modal');
  if (!input || !box) return;

  let currentMatches = [];
  let activeIdx = 0;

  const refresh = async () => {
    if (!input.value.trim()) {
      hideSuggestions();
      currentMatches = [];
      return;
    }
    await ensureFlowLoaded();
    currentMatches = searchStocks(input.value);
    activeIdx = 0;
    renderSuggestions(currentMatches);
  };

  input.addEventListener('focus', refresh);
  input.addEventListener('input', refresh);

  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (currentMatches.length === 0) return;
      activeIdx = (activeIdx + 1) % currentMatches.length;
      updateActive();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (currentMatches.length === 0) return;
      activeIdx = (activeIdx - 1 + currentMatches.length) % currentMatches.length;
      updateActive();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const pick = currentMatches[activeIdx];
      if (pick) {
        openStockModal(pick);
        hideSuggestions();
        input.blur();
      }
    } else if (e.key === 'Escape') {
      hideSuggestions();
      input.blur();
    }
  });

  function updateActive() {
    const items = box.querySelectorAll('.search-suggestion-item');
    items.forEach((el, i) => el.classList.toggle('active', i === activeIdx));
  }

  box.addEventListener('mousedown', (e) => {
    const item = e.target.closest('.search-suggestion-item');
    if (!item) return;
    e.preventDefault();
    const code = item.dataset.code;
    const c = findStockByCode(code);
    if (c) {
      openStockModal(c);
      hideSuggestions();
      input.blur();
    }
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.search-bar')) hideSuggestions();
  });

  if (btn) {
    btn.addEventListener('click', async () => {
      await ensureFlowLoaded();
      currentMatches = searchStocks(input.value);
      if (currentMatches.length > 0) {
        openStockModal(currentMatches[0]);
        hideSuggestions();
      } else {
        renderSuggestions([]);
      }
    });
  }

  // 모달 닫기 / ESC / backdrop 처리는 chart.js 가 위임 핸들러로 모두 담당.
}

document.addEventListener('DOMContentLoaded', () => {
  setupTabs();
  setupStockSearch();
  // '오늘' 이 기본 탭이다. 탭 클릭 때만 로드하면 첫 화면이 계속 스피너로 남는다.
  if (document.querySelector('.tab-btn.active')?.dataset.tab === 'briefing'
      && typeof loadBriefing === 'function') {
    loadBriefing();
  }
});
