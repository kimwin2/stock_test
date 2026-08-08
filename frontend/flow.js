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
const CHART_PAD_R = 36;          // 우측 가격축 (한국 HTS 관례)

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
function renderDualAxisChart(history, opts = {}) {
  const w = opts.width || 360;
  const h = opts.height || 150;

  if (!history || history.length < 5) return '<div class="sparkline-empty">데이터 부족</div>';

  const closeArr = history.map(p => p.close);
  const closes = closeArr.filter(v => v != null);
  const oscRaw = history.map(p => p.oscillator).filter(v => v != null);
  if (closes.length < 2 || oscRaw.length < 2) return '<div class="sparkline-empty">데이터 부족</div>';

  // ── 가격창 / 지표창 분리 ─────────────────────────────
  // 한 영역에 지수와 오실레이터를 겹쳐 그리면 스케일이 달라 어느 선이
  // 무엇인지 읽을 수 없다. 실제 트레이딩 앱처럼 위를 가격창, 아래를
  // 지표창으로 나누고 각자 축을 갖게 한다.
  const padL = CHART_PAD_L, padR = CHART_PAD_R, padT = 14, padB = 18;
  const gap = 8;
  const bodyH = h - padT - padB;
  const priceH = Math.round(bodyH * 0.66);
  const oscTop = padT + priceH + gap;
  const oscH = bodyH - priceH - gap;

  const innerW = w - padL - padR;
  const stepX = innerW / (history.length - 1);
  const X = (i) => padL + i * stepX;

  // ── 가격창: 지수 + 5·20일선 ─────────────────────────
  const ma5 = computeMA(closeArr, 5);
  const ma20 = computeMA(closeArr, 20);
  const priceAll = closes
    .concat(ma5.filter(v => v != null))
    .concat(ma20.filter(v => v != null));
  let pMin = Math.min(...priceAll), pMax = Math.max(...priceAll);
  const pPad = (pMax - pMin) * 0.08 || 1;
  pMin -= pPad; pMax += pPad;
  const yP = (v) => padT + (1 - (v - pMin) / (pMax - pMin)) * priceH;
  const line = (arr) => arr
    .map((v, i) => v == null ? null : `${X(i).toFixed(1)},${yP(v).toFixed(1)}`)
    .filter(Boolean).join(' ');

  const closePts = line(closeArr);
  const ma5Pts = line(ma5);
  const ma20Pts = line(ma20);

  // 가격 눈금 — 한국 HTS 관례대로 우측
  const priceTicks = [0, 0.5, 1].map(f => {
    const v = pMin + (pMax - pMin) * (1 - f);
    const y = padT + f * priceH;
    return `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${w - padR}" y2="${y.toFixed(1)}" stroke="${CHART_GRID}" stroke-width="0.6"/>`
      + `<text x="${(w - padR + 3).toFixed(1)}" y="${(y + 2.8).toFixed(1)}" font-size="7.5" fill="${CHART_AXIS_TEXT}">${Math.round(v).toLocaleString('ko-KR')}</text>`;
  }).join('');

  // ── 지표창: 오실레이터 (0선 기준 면적) ────────────────
  const oAbsMax = Math.max(...oscRaw.map(v => Math.abs(v))) || 0.01;
  const oRange = oAbsMax * 1.15;
  const oMid = oscTop + oscH / 2;
  const yO = (v) => oMid - (v / oRange) * (oscH / 2);
  const oscVals = history.map(p => p.oscillator);
  const oscPts = oscVals
    .map((v, i) => v == null ? null : `${X(i).toFixed(1)},${yO(v).toFixed(1)}`)
    .filter(Boolean).join(' ');
  // 0선 위는 붉게(과열), 아래는 푸르게(위축) 채운다. 등락 의미색과 방향이
  // 같아서 색만 보고도 어느 쪽인지 즉시 읽힌다. clip 으로 반쪽씩 잘라낸다.
  const clipId = `oscclip-${Math.random().toString(36).slice(2, 8)}`;
  const oscArea = oscPts ? `
      <defs>
        <clipPath id="${clipId}-up"><rect x="${padL}" y="${oscTop.toFixed(1)}" width="${innerW.toFixed(1)}" height="${(oMid - oscTop).toFixed(1)}"/></clipPath>
        <clipPath id="${clipId}-dn"><rect x="${padL}" y="${oMid.toFixed(1)}" width="${innerW.toFixed(1)}" height="${(oscTop + oscH - oMid).toFixed(1)}"/></clipPath>
      </defs>
      <polygon points="${X(0).toFixed(1)},${oMid.toFixed(1)} ${oscPts} ${X(history.length - 1).toFixed(1)},${oMid.toFixed(1)}" fill="#E53935" opacity="0.16" clip-path="url(#${clipId}-up)"/>
      <polygon points="${X(0).toFixed(1)},${oMid.toFixed(1)} ${oscPts} ${X(history.length - 1).toFixed(1)},${oMid.toFixed(1)}" fill="#1E88E5" opacity="0.16" clip-path="url(#${clipId}-dn)"/>
  ` : '';
  const lastOsc = [...oscVals].reverse().find(v => v != null);

  // X축 월 라벨
  const xLabels = [];
  let prevMonth = null;
  history.forEach((p, i) => {
    if (!p.date) return;
    const ym = p.date.slice(0, 7);
    if (ym === prevMonth) return;
    prevMonth = ym;
    xLabels.push({ x: X(i), label: `${p.date.slice(2, 4)}.${p.date.slice(5, 7)}` });
  });
  const thinned = [];
  let lastX = -Infinity;
  xLabels.forEach(l => { if (l.x - lastX >= 40) { thinned.push(l); lastX = l.x; } });
  // 양 끝 라벨은 anchor 를 안쪽으로 돌린다. 가운데 정렬로 두면 첫 라벨이
  // 차트 왼쪽 밖으로 삐져나가 "26.02" 가 "6.02" 로 잘려 보인다.
  const xAxis = thinned.map(l => {
    const anchor = l.x - padL < 14 ? 'start' : (w - padR - l.x < 14 ? 'end' : 'middle');
    return `<text x="${l.x.toFixed(1)}" y="${(h - 5).toFixed(1)}" text-anchor="${anchor}" font-size="7.5" fill="${CHART_AXIS_TEXT}">${l.label}</text>`;
  }).join('');

  return `
    <svg viewBox="0 0 ${w} ${h}" class="dual-chart" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
      ${priceTicks}
      ${ma20Pts ? `<polyline points="${ma20Pts}" fill="none" stroke="${CHART_MA20}" stroke-width="1" stroke-linejoin="round"/>` : ''}
      ${ma5Pts ? `<polyline points="${ma5Pts}" fill="none" stroke="${CHART_MA5}" stroke-width="1" stroke-linejoin="round"/>` : ''}
      <polyline points="${closePts}" fill="none" stroke="${CHART_PRICE}" stroke-width="1.9" stroke-linejoin="round" stroke-linecap="round"/>
      <line x1="${padL}" y1="${oscTop.toFixed(1)}" x2="${w - padR}" y2="${oscTop.toFixed(1)}" stroke="${CHART_GRID}" stroke-width="0.6"/>
      ${oscArea}
      <line x1="${padL}" y1="${oMid.toFixed(1)}" x2="${w - padR}" y2="${oMid.toFixed(1)}" stroke="${CHART_AXIS_TEXT}" stroke-width="0.7" stroke-dasharray="2 2"/>
      <polyline points="${oscPts}" fill="none" stroke="${CHART_OSC}" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/>
      <text x="${padL}" y="${(oscTop + 8).toFixed(1)}" font-size="7.5" font-weight="700" fill="${CHART_OSC}">오실레이터 ${lastOsc != null ? lastOsc.toFixed(3) : '-'}</text>
      ${xAxis}
    </svg>
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

  // cap (시가총액, 60일) 과 osc (수급, ~76일) 길이가 다르면 둘 중 짧은 쪽
  // 길이로 우측 끝부터 trim. 둘 다 마지막 거래일이 같으므로 우측 정렬됨.
  const capFull = (c.capHistory60d && c.capHistory60d.length >= 2)
    ? c.capHistory60d
    : (c.priceHistory60d || []);
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

  // 가격창 — 시가총액 + 5·20일선
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

  // 가격 끝점 + 우측 시총 라벨
  const lastCapVal = [...cap].reverse().find(v => v != null);
  const capLabel = lastCapVal >= 1e12 ? `${(lastCapVal / 1e12).toFixed(1)}조`
                  : lastCapVal >= 1e8 ? `${(lastCapVal / 1e8).toFixed(0)}억`
                  : `${lastCapVal}`;
  const capDot = `<circle cx="${X(n - 1).toFixed(1)}" cy="${yP(lastCapVal).toFixed(1)}" r="2" fill="${CHART_PRICE}"/>`;
  const capLabelSvg = `<text x="${(x1 + 3).toFixed(1)}" y="${(yP(lastCapVal) + 3).toFixed(1)}" font-size="8.5" font-weight="800" fill="${CHART_PRICE}">${fEscape(capLabel)}</text>`;
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
        ${ma20Pts ? `<polyline points="${ma20Pts}" fill="none" stroke="${CHART_MA20}" stroke-width="1" stroke-linejoin="round"/>` : ''}
        ${ma5Pts ? `<polyline points="${ma5Pts}" fill="none" stroke="${CHART_MA5}" stroke-width="1" stroke-linejoin="round"/>` : ''}
        <polyline points="${capPts}" fill="none" stroke="${CHART_PRICE}" stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round"/>
        ${capDot}
        ${capLabelSvg}
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

function renderSupplyGauge(percentile, _zone, amount) {
  if (percentile == null) return '';

  const pct = Math.max(0, Math.min(100, percentile));
  const idx = supplyLevelIdx(pct);
  const lv = SUPPLY_LEVELS[idx];

  const amt = amount != null ? fmtBillion(amount) : '-';
  const amtCls = amount == null ? '' : (amount < 0 ? 'amt-neg' : 'amt-pos');

  const segHtml = SUPPLY_LEVELS.map((s, i) => `
    <div class="supply-seg supply-seg-${s.key} ${i === idx ? 'is-on' : ''}"><span>${s.label}</span></div>
  `).join('');

  return `
    <div class="supply-gauge supply-${lv.key}">
      <div class="supply-track">
        ${segHtml}
        <div class="supply-pointer" style="left:${pct.toFixed(1)}%" title="채워진 정도 ${pct.toFixed(0)}/100">
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
        <span class="step-num">STEP 1</span>
        <span class="card-theme-name">시장 상태</span>
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
          <span><i class="ln" style="background:${CHART_PRICE};height:2.5px"></i>지수</span>
          <span><i class="ln" style="background:${CHART_MA5}"></i>5일선</span>
          <span><i class="ln" style="background:${CHART_MA20}"></i>20일선</span>
          <span><i class="ln" style="background:${CHART_OSC}"></i>공포·탐욕 오실레이터</span>
          <span class="cl-zone">아래창 = 오실레이터 (0선 위 과열 · 아래 위축)</span>
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
        <span class="step-num">STEP 2</span>
        <span class="card-theme-name">주도 업종</span>
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
// taerinScore 를 5단계 매수 등급으로 매핑.
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
function renderChartLegend() {
  return `
    <div class="chart-legend">
      <span><i class="ln" style="background:${CHART_PRICE};height:2.5px"></i>시가총액</span>
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
    return `<div class="flow-card flow-card-candidates flow-step3"><div class="card-header step-header"><span class="step-num">STEP 3</span><span class="card-theme-name">수급 <b class="ct-accent">빈집</b> · 추세 생존</span></div><div class="empty-msg">현재 조건을 통과한 종목 없음</div></div>`;
  }
  const sectorTag = leadingLabels?.length ? leadingLabels.slice(0, 5).join(', ') : '';
  // 참고 자료: 한 종목 4-5% × 20개. 5개보다 적으면 의미 없음.
  const SHOW_N = 20;
  const shown = Math.min(SHOW_N, candidates.length);

  const rows = candidates.slice(0, SHOW_N).map((c, idx) => renderCandCardV2(c, idx)).join('');

  return `
    <div class="flow-card flow-card-candidates flow-step3">
      <div class="card-header step-header">
        <span class="step-num">STEP 3</span>
        <span class="card-theme-name">수급 <b class="ct-accent">빈집</b> · 추세 생존 ${sectorTag ? `<small>(${fEscape(sectorTag)})</small>` : ''}</span>
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
      <div class="card-header"><span class="card-theme-name">📈 거래대금 강도 — 후보 종목별 (60d)</span><span class="card-volume">${items.length}개</span></div>
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
      <div class="card-header"><span class="card-theme-name">🚀 주도 업종 ETF</span><span class="card-volume">강도 70+ ${leading.leadingCount || 0}개</span></div>
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
      <div class="card-header"><span class="card-theme-name">⚠️ 매도 시그널 — 신고가 후 음전 + 10일선 이탈</span><span class="card-volume">${exits.length}개</span></div>
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
    ? renderSupplyGauge(c.vacancyPercentile, c.vacancyZone, c.institutionNet5d)
    : '';
  return `
    <div class="search-modal-osc-wrap">
      ${priceLine}
      <div class="search-modal-osc-chart">${renderMiniPriceChart(c)}</div>
      ${gauge}
      <div class="search-modal-legend">
        <span><span class="legend-line" style="background:${CHART_PRICE}"></span>시가총액</span>
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
});
