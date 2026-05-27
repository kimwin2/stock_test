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
// Dual-axis chart: 지수 + Fear & Greed Oscillator
//
// 참고 자료 그래프와 동일한 시각:
//   - 좌측 y축: Fear & Greed Oscillator (보라색, ±0.03 스케일)
//   - 우측 y축: 지수 (검정, 실제 가격 스케일) + MA5/MA10 이동평균선
//   - 0선만 표시, fill 없음, 가이드 없음
//   - matplotlib 스타일의 옅은 그리드
// ─────────────────────────────────────────┘
function renderDualAxisChart(history, opts = {}) {
  const w = opts.width || 360;
  const h = opts.height || 130;
  const padL = 30, padR = 38, padT = 6, padB = 22;

  if (!history || history.length < 5) return '<div class="sparkline-empty">데이터 부족</div>';

  const closes = history.map(p => p.close).filter(v => v != null);
  // Oscillator: backend 가 fearGreed/100 의 MACD line 을 보냄 (±0.03 스케일).
  // 폴백: 구 데이터에 oscillator 가 큰 스케일이면 자동 정규화.
  const oscRaw = history.map(p => p.oscillator).filter(v => v != null);
  if (closes.length < 2 || oscRaw.length < 2) return '<div class="sparkline-empty">데이터 부족</div>';

  const cMin = Math.min(...closes), cMax = Math.max(...closes);
  const cSpan = cMax - cMin || 1;

  // Oscillator y축 — 0선 중심 대칭 스케일, 데이터에서 자동 추출
  const oAbsMax = Math.max(...oscRaw.map(v => Math.abs(v))) || 0.01;
  const oRange = oAbsMax * 1.15;  // 위·아래 약간의 여백

  const innerW = w - padL - padR;
  const innerH = h - padT - padB;
  const stepX = innerW / (history.length - 1);
  const yMid = padT + innerH * 0.5;

  const closeArr = history.map(p => p.close);
  const ma3 = computeMA(closeArr, 3);
  const ma10 = computeMA(closeArr, 10);
  const projectClose = (v, i) => {
    if (v == null) return null;
    const x = padL + i * stepX;
    const y = padT + (1 - (v - cMin) / cSpan) * innerH;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  };
  const closePts = closeArr.map((v, i) => projectClose(v, i)).filter(Boolean).join(' ');
  const ma3Pts = ma3.map((v, i) => projectClose(v, i)).filter(Boolean).join(' ');
  const ma10Pts = ma10.map((v, i) => projectClose(v, i)).filter(Boolean).join(' ');

  const oscPts = history.map((p, i) => {
    if (p.oscillator == null) return null;
    const x = padL + i * stepX;
    const y = yMid - (p.oscillator / oRange) * (innerH * 0.5);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).filter(Boolean).join(' ');

  // y축 ticks — oscillator 는 5개 단계 (위 2 / 0 / 아래 2)
  const niceStep = (() => {
    const candidates = [0.005, 0.01, 0.02, 0.05];
    for (const s of candidates) {
      if (oRange / s <= 3) return s;
    }
    return 0.05;
  })();
  const oTicks = [];
  for (let i = -2; i <= 2; i++) {
    const v = i * niceStep;
    if (Math.abs(v) > oRange) continue;
    const y = yMid - (v / oRange) * (innerH * 0.5);
    oTicks.push({ v, y });
  }

  const cTickCount = 4;
  const cTicks = [];
  for (let i = 0; i <= cTickCount; i++) {
    const v = cMin + (cSpan * i) / cTickCount;
    const y = padT + (1 - (v - cMin) / cSpan) * innerH;
    cTicks.push({ v, y });
  }

  const oAxis = oTicks.map(t => `
    <line x1="${padL}" y1="${t.y.toFixed(1)}" x2="${w - padR}" y2="${t.y.toFixed(1)}" stroke="#eee" stroke-width="0.5"/>
    <text x="${(padL - 3).toFixed(1)}" y="${(t.y + 2.5).toFixed(1)}" text-anchor="end" font-size="7" fill="#6A5ACD">${t.v >= 0 ? ' ' : ''}${t.v.toFixed(2)}</text>
  `).join('');

  const cAxis = cTicks.map(t => `
    <text x="${(w - padR + 3).toFixed(1)}" y="${(t.y + 2.5).toFixed(1)}" text-anchor="start" font-size="7" fill="#444">${t.v.toFixed(0)}</text>
  `).join('');

  // 0선 강조
  const zeroLine = `<line x1="${padL}" y1="${yMid.toFixed(1)}" x2="${w - padR}" y2="${yMid.toFixed(1)}" stroke="#999" stroke-width="0.8"/>`;

  // X축 월별 라벨 — 월이 바뀌는 첫 데이터 포인트마다 "YY.MM" 표기
  const xLabels = [];
  let prevMonth = null;
  history.forEach((p, i) => {
    if (!p.date) return;
    const ym = p.date.slice(0, 7);  // "YYYY-MM"
    if (ym === prevMonth) return;
    prevMonth = ym;
    const yy = p.date.slice(2, 4);
    const mm = p.date.slice(5, 7);
    const x = padL + i * stepX;
    xLabels.push({ x, label: `${yy}.${mm}` });
  });
  // 너무 빽빽하면 격월로 솎아내기 (라벨 사이 최소 픽셀)
  const minLabelGap = 36;
  const xLabelsThin = [];
  let lastX = -Infinity;
  xLabels.forEach(l => {
    if (l.x - lastX >= minLabelGap) { xLabelsThin.push(l); lastX = l.x; }
  });
  const xAxis = xLabelsThin.map(l => `
    <line x1="${l.x.toFixed(1)}" y1="${(padT + innerH).toFixed(1)}" x2="${l.x.toFixed(1)}" y2="${(padT + innerH + 3).toFixed(1)}" stroke="#999" stroke-width="0.5"/>
    <text x="${l.x.toFixed(1)}" y="${(padT + innerH + 11).toFixed(1)}" text-anchor="middle" font-size="7" fill="#666">${l.label}</text>
  `).join('');

  return `
    <svg viewBox="0 0 ${w} ${h}" class="dual-chart" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
      ${oAxis}
      ${zeroLine}
      ${cAxis}
      ${xAxis}
      <polyline points="${oscPts}" fill="none" stroke="#6A5ACD" stroke-width="1.6"/>
      <polyline points="${closePts}" fill="none" stroke="#1A1A1A" stroke-width="1.6"/>
      ${ma3Pts ? `<polyline points="${ma3Pts}" fill="none" stroke="#FB8C00" stroke-width="1.1"/>` : ''}
      ${ma10Pts ? `<polyline points="${ma10Pts}" fill="none" stroke="#1E88E5" stroke-width="1.2"/>` : ''}
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
//   이동평균선(MA10/MA20) 점선은 그리지 않는다.
// ─────────────────────────────────────────┘
function renderMiniPriceChart(c, opts = {}) {
  const w = opts.width || 263;       // 175 × 1.5
  const chartH = opts.height || 96;  // 64 × 1.5
  const axisH = 17;                  // 11 × 1.5 — 하단 X축 날짜 라벨
  const h = chartH + axisH;    // SVG 전체 높이

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

  const capMin = Math.min(...validCap);
  const capMax = Math.max(...validCap);
  const capSpan = capMax - capMin || 1;
  const stepX = w / (cap.length - 1);

  const projectCap = (v, i) => {
    if (v == null) return null;
    const x = i * stepX;
    const y = 1 + (1 - (v - capMin) / capSpan) * (chartH - 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  };
  const capPts = cap.map((v, i) => projectCap(v, i)).filter(Boolean).join(' ');
  const ma10Series = computeMA(cap, 10);
  const ma10MiniPts = ma10Series.map((v, i) => projectCap(v, i)).filter(Boolean).join(' ');

  // 수급 오실레이터 — xlsm '수급오실레이터' 시트의 LineChart 와 동일 형식
  //   선   = osc 자체 (= MACD Histogram = MACD − Signal9), 보라색
  //   점선 = historical osc 의 percentile 4개 (xlsm L7~L11):
  //          상위 10/25%, 하위 25/10%
  //   0선 = 실선 회색
  //   현재값 = 마지막 osc 값 (xlsm L12 = '=H84') — 우측 라벨로 표시
  let oscOverlay = '';
  let lastOscVal = null;
  let oscMaxPct = null;
  if (oscSeries.length >= 2) {
    const oscVals = oscSeries.map(o => o.osc || 0);
    const oscMaxAbs = Math.max(...oscVals.map(v => Math.abs(v))) || 1;
    lastOscVal = oscVals[oscVals.length - 1];
    oscMaxPct = oscMaxAbs * 100;

    const oscMid = chartH / 2;
    const oscHalf = chartH * 0.45;
    const yOf = (v) => oscMid - (v / oscMaxAbs) * oscHalf;

    // 선 = osc — Bloomberg amber, 흰 배경에서 시인성 최대 + 빨강/파랑 percentile 과 영역 분리
    const linePts = oscVals.map((v, i) => `${(i * stepX).toFixed(1)},${yOf(v).toFixed(1)}`).join(' ');
    const oscLine = `<polyline points="${linePts}" fill="none" stroke="#EF6C00" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>`;

    // 0 라인 — 부호 기준선
    const zeroLine = `<line x1="0" y1="${oscMid.toFixed(1)}" x2="${w}" y2="${oscMid.toFixed(1)}" stroke="#999" stroke-width="0.7"/>`;

    // Percentile 점선 — xlsm L7~L11
    const sorted = [...oscVals].sort((a, b) => a - b);
    const p = (q) => {
      const idx = Math.max(0, Math.min(sorted.length - 1, Math.round(q * (sorted.length - 1))));
      return sorted[idx];
    };
    const pcts = [
      { v: p(0.90), color: '#C62828' },
      { v: p(0.75), color: '#E57373' },
      { v: p(0.25), color: '#64B5F6' },
      { v: p(0.10), color: '#1565C0' },
    ];
    const pctLines = pcts.map(pc => {
      const y = yOf(pc.v);
      return `<line x1="0" y1="${y.toFixed(1)}" x2="${w}" y2="${y.toFixed(1)}" stroke="${pc.color}" stroke-width="0.6" stroke-dasharray="3,2" opacity="0.55"/>`;
    }).join('');

    oscOverlay = pctLines + zeroLine + oscLine;
  }

  // X축 날짜 라벨 — 5개 (좌끝 / 25% / 50% / 75% / 우끝). 'M/D' 짧은 형식.
  let axisLabels = '';
  if (dates.length >= 2) {
    const fmt = (s) => {
      // 'YYYY-MM-DD' → 'M/D'
      const m = /^\d{4}-(\d{2})-(\d{2})$/.exec(s);
      if (!m) return s;
      return `${parseInt(m[1], 10)}/${parseInt(m[2], 10)}`;
    };
    const last = dates.length - 1;
    const ticks = [
      { i: 0, anchor: 'start' },
      { i: Math.round(last * 0.25), anchor: 'middle' },
      { i: Math.round(last * 0.5),  anchor: 'middle' },
      { i: Math.round(last * 0.75), anchor: 'middle' },
      { i: last, anchor: 'end' },
    ];
    axisLabels = ticks.map(t => {
      const x = t.i * stepX;
      const y = chartH + axisH - 1;
      return `<text x="${x.toFixed(1)}" y="${y}" font-size="11" fill="#888" text-anchor="${t.anchor}">${fmt(dates[t.i])}</text>`;
    }).join('');
  }

  // 라벨: 좌측 = 시가총액 (조), 우측 = osc 마지막값(%) + 위/아래 = ±max
  const lastCap = cap[cap.length - 1];
  const capLabel = lastCap >= 1e12 ? `${(lastCap / 1e12).toFixed(1)}조`
                  : lastCap >= 1e8 ? `${(lastCap / 1e8).toFixed(0)}억`
                  : `${lastCap}`;
  const oscLastLabel = lastOscVal != null ? `${(lastOscVal * 100).toFixed(3)}%` : '';
  const oscColor = lastOscVal == null ? '#777'
                 : lastOscVal > 0 ? '#C62828' : '#1565C0';
  const oscMaxLabel = oscMaxPct != null ? `±${oscMaxPct.toFixed(3)}%` : '';

  return `
    <div class="mini-chart-wrap">
      <span class="mini-chart-label-left">${capLabel}</span>
      <svg viewBox="0 0 ${w} ${h}" class="mini-chart" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">
        ${oscOverlay}
        <polyline points="${capPts}" fill="none" stroke="#1A1A1A" stroke-width="1.5"/>
        ${ma10MiniPts ? `<polyline points="${ma10MiniPts}" fill="none" stroke="#1E88E5" stroke-width="1.2" vector-effect="non-scaling-stroke"/>` : ''}
        ${axisLabels}
      </svg>
      <span class="mini-chart-label-right">
        <span class="mini-chart-osc-max">${oscMaxLabel}</span>
        <span class="mini-chart-osc-last" style="color:${oscColor}">${oscLastLabel}</span>
      </span>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// Supply Gauge — percentile + zone          │
//   percentile: 0-100 (0=가장 빈집)         │
//   zone: "빈집" | "정상" | "찼음"          │
// ─────────────────────────────────────────┘
function renderSupplyGauge(percentile, zone, amount) {
  if (percentile == null) return '';
  const w = 263, h = 21;  // mini chart 1.5배 스케일과 정렬
  const cx = Math.max(4, Math.min(w - 4, (percentile / 100) * w));

  // 색상은 zone 라벨 우선 (osc 부호 기반). zone 없으면 percentile fallback.
  const zoneColor = zone === '빈집' ? '#1E88E5'
                  : zone === '찼음' ? '#E53935'
                  : zone === '정상' ? '#666'
                  : (percentile < 25 ? '#1E88E5'
                   : percentile > 75 ? '#E53935'
                   : '#666');
  const dotColor = zoneColor;
  const zoneTextColor = zoneColor;

  // 3 zone bands: blue / gray / red
  return `
    <div class="supply-gauge-row">
      <svg viewBox="0 0 ${w} ${h}" class="supply-gauge" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">
        <rect x="0" y="4" width="${w * 0.25}" height="6" fill="rgba(30,136,229,0.40)"/>
        <rect x="${w * 0.25}" y="4" width="${w * 0.5}" height="6" fill="rgba(158,158,158,0.22)"/>
        <rect x="${w * 0.75}" y="4" width="${w * 0.25}" height="6" fill="rgba(229,57,53,0.40)"/>
        <line x1="${w * 0.25}" y1="2" x2="${w * 0.25}" y2="12" stroke="#bbb" stroke-width="0.5"/>
        <line x1="${w * 0.75}" y1="2" x2="${w * 0.75}" y2="12" stroke="#bbb" stroke-width="0.5"/>
        <circle cx="${cx.toFixed(1)}" cy="7" r="4.5" fill="${dotColor}" stroke="#fff" stroke-width="1"/>
      </svg>
      <div class="supply-label">
        <span class="supply-zone" style="color:${zoneTextColor}">${fEscape(zone || '-')}</span>
        <span class="supply-meta">하위 ${percentile.toFixed(0)}% · 외인+기관 5d ${fmtBillion(amount)}</span>
      </div>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// Sparkline                                 │
// ─────────────────────────────────────────┘
function renderSparkline(values, opts = {}) {
  const w = opts.width || 240;
  const h = opts.height || 50;
  const stroke = opts.stroke || '#00897B';
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
  const ma3Arr = computeMA(closes, 3);
  const ma3 = ma3Arr[ma3Arr.length - 1];
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
    <span class="sentiment-ma3" title="3일 이동평균선 — 종가가 위/아래인지로 단기 추세 판단">3일선 ${ma3Str} ${arrow}</span>
  `;
}

function buildStep1Card(sentiment, cash) {
  const k = sentiment?.kospi || {};
  const q = sentiment?.kosdaq || {};
  const buySafety = sentiment?.buySafety || null;
  const stages = buySafety?.stages || null;
  const kSafety = buySafety?.kospi || null;
  const qSafety = buySafety?.kosdaq || null;
  const cashPct = cash?.cashPct ?? null;
  const cashLevel = cash?.level || '';
  const cashColor = cashPct == null ? '#999'
                  : cashPct >= 30 ? '#E53935'
                  : cashPct >= 15 ? '#FB8C00'
                  : cashPct >= 5 ? '#FDD835'
                  : '#43A047';

  return `
    <div class="flow-card flow-card-step1 flow-step1">
      <div class="card-header step-header">
        <span class="step-num">STEP 1</span>
        <span class="card-theme-name">📊 오늘 매수해도 되나?</span>
        ${cashPct != null ? `<span class="cash-pill" style="background:${cashColor}">현금 ${cashPct}%</span>` : ''}
      </div>
      <div class="step1-body">
        ${cashLevel ? `<div class="step1-cash-line">${fEscape(cashLevel)}</div>` : ''}
        <div class="step1-row">
          <div class="step1-row-head">
            <strong>${fEscape(k.label || 'KOSPI')}</strong>
            ${renderBuySafetyPill(kSafety)}
            <span class="sentiment-zone">${fEscape(k.zone || '-')}</span>
            <span class="sentiment-fg">F&G ${k.fearGreed?.toFixed(1) ?? '-'} · Osc ${k.oscillator?.toFixed(2) ?? '-'}</span>
            <span class="sentiment-close">종가 ${fmtNumber(k.close)}</span>
            ${renderMddMa3(k.history, k.close)}
          </div>
          ${renderDualAxisChart(k.history)}
        </div>
        <div class="step1-row">
          <div class="step1-row-head">
            <strong>${fEscape(q.label || 'KOSDAQ')}</strong>
            ${renderBuySafetyPill(qSafety)}
            <span class="sentiment-zone">${fEscape(q.zone || '-')}</span>
            <span class="sentiment-fg">F&G ${q.fearGreed?.toFixed(1) ?? '-'} · Osc ${q.oscillator?.toFixed(2) ?? '-'}</span>
            <span class="sentiment-close">종가 ${fmtNumber(q.close)}</span>
            ${renderMddMa3(q.history, q.close)}
          </div>
          ${renderDualAxisChart(q.history)}
        </div>
        <div class="dual-legend">
          <span class="legend-fg">━ Fear &amp; Greed Oscillator</span>
          <span class="legend-price">━ 지수</span>
          <span class="legend-ma3">━ MA3</span>
          <span class="legend-ma10">━ MA10</span>
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
        <span class="card-theme-name">🔥 어떤 섹터가 강한가?</span>
        ${crowdLatest != null ? `<span class="crowd-pill" style="background:${sigColor}">쏠림 ${crowdLatest.toFixed(0)} ${fEscape(crowdSignal)}</span>` : ''}
      </div>
      <div class="step2-body">
        <div class="step2-rs">
          <div class="step2-label">RS 70+ ETF (시장 대비 강도)</div>
          ${rsBackup.map(e => `
            <div class="step2-rs-row">
              <span class="step2-rs-name">${fEscape(e.name)}</span>
              <div class="rs-bar"><div class="rs-bar-fill" style="width:${rsBarWidth(e.rsNorm)}%; background:${e.rsNorm >= 70 ? '#E53935' : e.rsNorm >= 50 ? '#FB8C00' : '#1E88E5'}"></div></div>
              <span class="step2-rs-num">${e.rsNorm}</span>
              <span class="step2-rs-mom ${changeClass(e.ret3m)}">${e.ret3m != null ? (e.ret3m > 0 ? '+' : '') + e.ret3m + '%' : '-'} <small>3M</small></span>
            </div>
          `).join('')}
        </div>
        ${flowSectors ? `<div class="step2-flow">📈 자금 유입 섹터: <strong>${fEscape(flowSectors)}</strong></div>` : ''}
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
// CARD: Buy candidates (STEP 3 — main hero) │
// ─────────────────────────────────────────┘
function buildBuyCandidatesCard(candidates, leadingLabels) {
  if (!candidates || candidates.length === 0) {
    return `<div class="flow-card flow-card-candidates flow-step3"><div class="card-header step-header"><span class="step-num">STEP 3</span><span class="card-theme-name">🎯 어떤 종목? — 매수 후보</span></div><div class="empty-msg">현재 후보 없음</div></div>`;
  }
  const sectorTag = leadingLabels?.length ? leadingLabels.slice(0, 5).join(', ') : '';
  // 참고 자료: 한 종목 4-5% × 20개. 5개보다 적으면 의미 없음.
  const SHOW_N = 20;
  // 강한 섹터 1·2위 (STEP 2 의 leadingSectorLabels 첫 두 개)
  const TOP_SECTORS = (leadingLabels || []).slice(0, 2);

  const rows = candidates.slice(0, SHOW_N).map((c, idx) => {
    const bz = c.buyZone || {};
    const sectorLabel = c.sector || '-';
    const isTopSector = TOP_SECTORS.includes(c.sector);

    const rankBadge = `<span class="rank-badge">#${idx + 1}</span>`;

    return `
      <div class="cand-row clickable${isTopSector ? ' cand-row-top-sector' : ''}" data-stock-code="${fEscape(c.code)}" data-stock-name="${fEscape(c.name)}" title="${fEscape(c.name)} 차트 보기">
        <div class="cand-top">
          <div class="cand-info">
            <div class="cand-name">${rankBadge} ${fEscape(c.name)} <small>${fEscape(c.code)} · ${fEscape(sectorLabel)}</small></div>
            <div class="cand-prices">
              <span class="cand-close">${fmtNumber(c.close)}</span>
              <span class="cand-ret ${changeClass(c.ret5d)}">5d ${fmtPctSigned(c.ret5d)}</span>
            </div>
          </div>
          <div class="cand-chart">
            ${renderMiniPriceChart(c)}
          </div>
        </div>
        ${renderSupplyGauge(c.vacancyPercentile, c.vacancyZone, c.institutionNet5d)}
      </div>
    `;
  }).join('');

  return `
    <div class="flow-card flow-card-candidates flow-step3">
      <div class="card-header step-header">
        <span class="step-num">STEP 3</span>
        <span class="card-theme-name">🎯 어떤 종목? ${sectorTag ? `<small>(${fEscape(sectorTag)})</small>` : ''}</span>
        <span class="card-volume">${Math.min(SHOW_N, candidates.length)} / ${candidates.length}</span>
      </div>
      <div class="cand-body">${rows}</div>
      <div class="cand-legend">
        <span><span class="legend-line black"></span>시가총액(좌)</span>
        <span><span class="legend-line amber"></span>수급 오실레이터(MACD Histogram)</span>
        <span><span class="legend-line blue"></span>MA10</span>
        <span class="legend-tip">참고 자료(.xlsm) 수급오실레이터 동일 — (외+기 5일누적/시총) 의 EMA12-EMA26 - Signal9 · 점선=상/하 10·25% percentile · ★ 강한섹터 1·2위 · 🔥 현재 매도 연속</span>
      </div>
    </div>
  `;
}
// (위 cand-legend 가 buyCandidatesCard 와 leadingValueCard 양쪽에 동일하게 적용)

// ─────────────────────────────────────────┐
// CARD: 주도섹터 거래대금 톱10               │
// 매수 후보(빈집 전략) 와 별개로, 외인+기관   │
// 동행 매수 중인 거래대금 1위급 주도주        │
// ─────────────────────────────────────────┘
function buildLeadingValueCard(items, leadingLabels) {
  if (!items || items.length === 0) return '';
  const TOP_SECTORS = (leadingLabels || []).slice(0, 2);

  const rows = items.map(c => {
    const bz = c.buyZone || {};
    const isTopSector = TOP_SECTORS.includes(c.sector);
    const inst5d = c.institutionNet5d ?? 0;
    const flowClass = inst5d > 0 ? 'up' : inst5d < 0 ? 'down' : 'flat';
    const flowLabel = inst5d > 0 ? '동행 매수' : inst5d < 0 ? '동행 매도' : '중립';

    const topSectorBadge = isTopSector
      ? `<span class="badge badge-gold">★ 강한섹터 ${TOP_SECTORS.indexOf(c.sector) + 1}위</span>`
      : '';

    return `
      <div class="cand-row clickable${isTopSector ? ' cand-row-top-sector' : ''}" data-stock-code="${fEscape(c.code)}" data-stock-name="${fEscape(c.name)}" title="${fEscape(c.name)} 차트 보기">
        <div class="cand-top">
          <div class="cand-info">
            <div class="cand-name">${fEscape(c.name)} <small>${fEscape(c.code)} · ${fEscape(c.sector || '-')}</small></div>
            <div class="cand-prices">
              <span class="cand-close">${fmtNumber(c.close)}</span>
              ${c.ret5d != null ? `<span class="cand-ret ${changeClass(c.ret5d)}">5d ${fmtPctSigned(c.ret5d)}</span>` : ''}
              ${topSectorBadge}
            </div>
          </div>
          <div class="cand-chart">
            ${renderMiniPriceChart(c)}
          </div>
        </div>
        ${renderSupplyGauge(c.vacancyPercentile, c.vacancyZone, c.institutionNet5d)}
      </div>
    `;
  }).join('');

  return `
    <div class="flow-card flow-card-candidates flow-card-leading-value">
      <div class="card-header">
        <span class="card-theme-name">💰 주도섹터 거래대금 톱 10 — 외인·기관 동행 매수 주도주</span>
        <span class="card-volume">${items.length}개</span>
      </div>
      <div class="cand-body">${rows}</div>
      <div class="cand-legend">
        <span><span class="legend-line black"></span>시가총액(좌)</span>
        <span><span class="legend-line amber"></span>수급 오실레이터(MACD Histogram)</span>
        <span><span class="legend-line blue"></span>MA10</span>
        <span class="legend-tip">매수 후보(빈집 전략)에 안 잡히지만 외인+기관이 가장 큰 돈을 베팅 중인 주도주. 게이지 "찼음"=수급 채워짐.</span>
      </div>
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
                <div class="ti-meta">TI <strong style="color:${tiColor}">${t.ti}</strong> <small>${fEscape(t.zone)}</small> · ${fmtNumber(t.close)}원</div>
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
      <div class="card-header"><span class="card-theme-name">🚀 주도 업종 ETF</span><span class="card-volume">${leading.leadingCount || 0}/RS70+</span></div>
      <div class="leading-body">
        <div class="leading-table-head"><span>ETF</span><span>RS</span><span>3M</span><span>1M</span></div>
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
      <div class="card-header"><span class="card-theme-name">⚠️ 매도 시그널 — 신고가 후 음전 + 10MA 이탈</span><span class="card-volume">${exits.length}개</span></div>
      <div class="exit-body">
        ${exits.slice(0, 10).map(e => `
          <div class="exit-row">
            <span class="exit-name">${fEscape(e.name)} <small>${fEscape(e.sector || '-')}</small></span>
            <span class="exit-pull down">${e.drawdownFromHighPct}%</span>
            <span class="exit-meta">${fmtNumber(e.lastClose)} / 10MA ${fmtNumber(e.ma10)}</span>
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
        ${buildBuyCandidatesCard(data.buyCandidates, data.leadingSectorLabels)}
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
//   - 위: renderMiniPriceChart (시가총액 + MA10 + 수급 오실레이터) + supply gauge
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
        <span><span class="legend-line black"></span>시가총액(좌)</span>
        <span><span class="legend-line amber"></span>수급 오실레이터(MACD Histogram)</span>
        <span><span class="legend-line blue"></span>MA10</span>
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
