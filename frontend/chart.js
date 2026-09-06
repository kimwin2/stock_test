/**
 * Chart Modal — 종목 클릭 시 분/일/주/월봉 캔들 차트.
 *
 * 동작:
 *  1) document-level click 위임으로 [data-stock-code] 요소 감지
 *  2) 모달 열림 → 백엔드 /chart 엔드포인트에서 캔들 데이터 fetch
 *  3) SVG candlestick (KR convention: 빨강=상승, 파랑=하락) 렌더
 *  4) 기간 탭 (1분/일/주/월) 으로 timeframe 전환
 *
 * Phase 2 마이그레이션 시:
 *  - fetchChart() 의 transport 만 REST → WebSocket subscribe 로 교체.
 *  - 응답 shape({code, name, timeframe, candles}) 는 동일 → 렌더링 코드 무수정.
 */

// ─────────────────────────────────────────┐
// Config — 백엔드 URL 환경별 자동 분기      │
// ─────────────────────────────────────────┘
// 프로덕션 URL 은 sam deploy 출력의 ChartApiUrl 을 index.html 에서
//   <script>window.STOCK_CHART_API_URL = 'https://....lambda-url.../chart'</script>
// 형태로 주입. 한 곳만 바꾸면 됨.
const CHART_API_LOCAL_URL = 'http://127.0.0.1:8081/chart';
const CHART_API_PLACEHOLDER_HOST = 'stock-chart-api.example.com';

function getChartApiUrl() {
  if (typeof window !== 'undefined' && window.STOCK_CHART_API_URL) {
    return window.STOCK_CHART_API_URL;
  }
  const host = window.location.hostname;
  const isProd = host.includes('github.io') || host.includes('stock');
  // 프로덕션이지만 URL 주입 안 됐으면 null → fetchChart 가 friendly 메시지
  return isProd ? null : CHART_API_LOCAL_URL;
}

// ─────────────────────────────────────────┐
// 모달 상태                                 │
// ─────────────────────────────────────────┘
// count = 화면에 그릴 캔들 개수. 알파스퀘어처럼 "통통한" 캔들을 위해
//   일/주/월은 60~80개 수준으로 제한 (220개는 너무 빽빽해 줌아웃처럼 보임).
// 네이버 fchart 는 1분봉만 제공 → minute 는 프론트에서 3분봉으로 집계해 표시.
// count 는 1분봉 기준 요청량(하루 정규장 ≈ 390분) → 집계 후 ~130개 3분봉.
const MINUTE_AGG_UNIT = 3;
const TIMEFRAMES = [
  { key: 'minute', label: '3분', count: 400 },
  { key: 'day',    label: '일',  count: 70 },
  { key: 'week',   label: '주',  count: 70 },
  { key: 'month',  label: '월',  count: 60 },
];

const chartState = {
  open: false,
  code: null,
  name: null,
  timeframe: 'day',
  data: null,            // 마지막으로 받은 응답 {code, name, candles, ...}
  loadingToken: 0,       // race 방지용 토큰
  cache: new Map(),      // 모달 열린 동안만 메모리 캐시 (key: code:timeframe)
  topHTML: '',           // 모달 상단에 삽입할 부가 HTML (검색 → 수급 osc 차트)
};

// ─────────────────────────────────────────┐
// 유틸                                      │
// ─────────────────────────────────────────┘
function chartEscape(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[c]);
}

function chartFmtNum(n) {
  if (n == null) return '-';
  return n.toLocaleString('ko-KR');
}

function chartFmtVol(v) {
  if (v == null || v === 0) return '0';
  if (v >= 1e8) return (v / 1e8).toFixed(2) + '억';
  if (v >= 1e4) return (v / 1e4).toFixed(0) + '만';
  return v.toLocaleString('ko-KR');
}

// "YYYYMMDD" or "YYYYMMDDHHMM" → "M/D" or "HH:MM"
function chartFmtTime(t, timeframe) {
  if (!t) return '';
  if (timeframe === 'minute' && t.length >= 12) {
    return `${t.slice(8, 10)}:${t.slice(10, 12)}`;
  }
  if (t.length >= 8) {
    if (timeframe === 'month' || timeframe === 'week') {
      return `${t.slice(2, 4)}/${t.slice(4, 6)}`;
    }
    return `${parseInt(t.slice(4, 6))}/${parseInt(t.slice(6, 8))}`;
  }
  return t;
}

// 1분봉 → N분봉 집계. 시계 기준 버킷(09:00,09:03,…)으로 묶어 OHLCV 합성.
//   o=버킷 첫봉 시가, h=최고, l=최저, c=버킷 막봉 종가, v=합, t=첫봉 시각.
function aggregateMinuteCandles(candles, unit = MINUTE_AGG_UNIT) {
  if (!Array.isArray(candles) || candles.length === 0 || unit <= 1) return candles;
  const out = [];
  let bucket = null, key = null;
  for (const c of candles) {
    const t = c.t || '';
    let k;
    if (t.length >= 12) {
      const mm = parseInt(t.slice(10, 12), 10);
      k = t.slice(0, 10) + String(Math.floor(mm / unit) * unit).padStart(2, '0');
    } else {
      k = t;
    }
    if (k !== key) {
      if (bucket) out.push(bucket);
      key = k;
      bucket = { t: c.t, o: c.o, h: c.h, l: c.l, c: c.c, v: c.v || 0 };
    } else {
      if (c.h != null && (bucket.h == null || c.h > bucket.h)) bucket.h = c.h;
      if (c.l != null && (bucket.l == null || c.l < bucket.l)) bucket.l = c.l;
      bucket.c = c.c;
      bucket.v += c.v || 0;
    }
  }
  if (bucket) out.push(bucket);
  return out;
}

// ─────────────────────────────────────────┐
// API fetch                                 │
// ─────────────────────────────────────────┘
async function fetchChart(code, timeframe, count) {
  const url = getChartApiUrl();
  if (!url) {
    // 프로덕션인데 window.STOCK_CHART_API_URL 가 주입 안 됨 → backend 배포 전.
    throw new Error('차트 API 미배포. sam deploy 후 index.html 에 window.STOCK_CHART_API_URL 설정 필요.');
  }
  const qs = new URLSearchParams({ code, timeframe, count: String(count) });
  const resp = await fetch(`${url}?${qs}`, { method: 'GET', mode: 'cors' });
  if (!resp.ok) {
    let detail = '';
    try {
      const err = await resp.json();
      detail = err.error || JSON.stringify(err);
    } catch (e) {
      detail = await resp.text().catch(() => '');
    }
    throw new Error(`HTTP ${resp.status}: ${detail || resp.statusText}`);
  }
  const json = await resp.json();
  // 분봉은 1분봉으로 받아 3분봉으로 집계 (네이버가 1분봉만 주므로).
  if (timeframe === 'minute' && json && Array.isArray(json.candles)) {
    json.candles = aggregateMinuteCandles(json.candles, MINUTE_AGG_UNIT);
  }
  return json;
}

// ─────────────────────────────────────────┐
// SVG 캔들스틱 렌더                          │
// ─────────────────────────────────────────┘
// viewBox 기하 — 알파스퀘어처럼 세로가 넉넉한 ~2:1 비율.
//   가격 패널이 화면 대부분을, 거래량은 하단 좁은 띠. 가격축은 우측(업계 표준).
const GEO = {
  W: 720, H: 400,
  padL: 8, padR: 58, padT: 14,
  priceBottom: 312,
  volTop: 330, volBottom: 386,
  xAxisY: 398,
};

// 색상: 한국 관행 (빨강=상승, 파랑=하락)
const C_UP = '#E12B2B';
const C_DOWN = '#1763D4';
const C_FLAT = '#9AA0A6';

// 이동평균선 (알파스퀘어 단순이동평균과 동일 컨셉)
const MA_LINES = [
  { p: 5,  color: '#8E5BE8' },
  { p: 20, color: '#EE9A1E' },
  { p: 60, color: '#16B39A' },
];

// 종가 배열에 대한 단순이동평균 (null = 데이터 부족 구간)
function smaSeries(values, period) {
  const out = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

function renderCandlestickSVG(candles, timeframe) {
  if (!candles || candles.length === 0) {
    return '<div class="chart-empty">데이터 없음</div>';
  }

  const { W, H, padL, padR, padT, priceBottom, volTop, volBottom, xAxisY } = GEO;
  const innerW = W - padL - padR;
  const innerPriceH = priceBottom - padT;
  const innerVolH = volBottom - volTop;

  // 가격 범위
  let minP = Infinity, maxP = -Infinity, maxV = 0;
  for (const c of candles) {
    if (c.l != null && c.l < minP) minP = c.l;
    if (c.h != null && c.h > maxP) maxP = c.h;
    if (c.v != null && c.v > maxV) maxV = c.v;
  }
  if (!isFinite(minP) || !isFinite(maxP)) {
    return '<div class="chart-empty">가격 데이터 부정확</div>';
  }
  if (minP === maxP) {  // 모든 가격 같을 때 (분봉 장외 시간)
    minP = minP * 0.99;
    maxP = maxP * 1.01;
  }
  // 위/아래 약간의 여백만 (꽉 채워 캔들을 크게 — 알파스퀘어 느낌)
  const pad = (maxP - minP) * 0.06;
  minP = minP - pad;
  maxP = maxP + pad;

  const N = candles.length;
  const slot = innerW / N;
  const bodyW = Math.max(1.5, slot * 0.68);

  function xCenter(i) { return padL + slot * (i + 0.5); }
  function yPrice(p) { return padT + (1 - (p - minP) / (maxP - minP)) * innerPriceH; }
  function yVol(v) { return volTop + (1 - (maxV > 0 ? v / maxV : 0)) * innerVolH; }

  // 캔들 + 거래량
  // 분봉(minute) 은 Naver 가 close 만 보내서 o==h==l==c 인 경우가 많음 →
  // 이전 캔들 close 와 비교해 색 결정 (라인 차트 느낌).
  const candleSvg = [];
  const volSvg = [];
  const isMinute = timeframe === 'minute';
  for (let i = 0; i < N; i++) {
    const c = candles[i];
    let up, down;
    if (isMinute && c.o === c.c && i > 0) {
      const prev = candles[i - 1];
      up = c.c > prev.c;
      down = c.c < prev.c;
    } else {
      up = c.c > c.o;
      down = c.c < c.o;
    }
    const color = up ? C_UP : down ? C_DOWN : C_FLAT;
    const cx = xCenter(i);
    const yH = yPrice(c.h);
    const yL = yPrice(c.l);
    const yO = yPrice(c.o);
    const yC = yPrice(c.c);
    const bodyTop = Math.min(yO, yC);
    const bodyH = Math.max(0.6, Math.abs(yC - yO));

    // 심지
    candleSvg.push(
      `<line x1="${cx.toFixed(1)}" y1="${yH.toFixed(1)}" x2="${cx.toFixed(1)}" y2="${yL.toFixed(1)}" stroke="${color}" stroke-width="1"/>`
    );
    // 몸통
    candleSvg.push(
      `<rect x="${(cx - bodyW / 2).toFixed(1)}" y="${bodyTop.toFixed(1)}" width="${bodyW.toFixed(1)}" height="${bodyH.toFixed(1)}" fill="${color}" stroke="${color}"/>`
    );
    // 거래량 바
    const vy = yVol(c.v || 0);
    volSvg.push(
      `<rect x="${(cx - bodyW / 2).toFixed(1)}" y="${vy.toFixed(1)}" width="${bodyW.toFixed(1)}" height="${(volBottom - vy).toFixed(1)}" fill="${color}" opacity="0.55"/>`
    );
  }

  // 이동평균선 — 부드러운 곡선 (알파스퀘어 핵심 미감)
  const closes = candles.map(c => c.c);
  const maSvg = [];
  const maLegend = [];
  let legendX = padL + 2;
  for (const ma of MA_LINES) {
    if (N <= ma.p) continue;
    const series = smaSeries(closes, ma.p);
    const pts = [];
    for (let i = 0; i < N; i++) {
      if (series[i] == null) continue;
      pts.push(`${xCenter(i).toFixed(1)},${yPrice(series[i]).toFixed(1)}`);
    }
    if (pts.length < 2) continue;
    maSvg.push(
      `<polyline points="${pts.join(' ')}" fill="none" stroke="${ma.color}" stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round" opacity="0.95"/>`
    );
    maLegend.push(
      `<text x="${legendX.toFixed(1)}" y="${(padT - 3).toFixed(1)}" font-size="9.5" font-weight="600" fill="${ma.color}">${ma.p}일선</text>`
    );
    legendX += 34;
  }

  // Y축 가격 눈금 (우측, 5단계) + 옅은 가로 그리드
  const yTicks = [];
  for (let k = 0; k <= 4; k++) {
    const p = minP + (maxP - minP) * (k / 4);
    const y = yPrice(p);
    yTicks.push(
      `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="#EDEFF2" stroke-width="1"/>`,
      `<text x="${(W - padR + 5).toFixed(1)}" y="${(y + 3).toFixed(1)}" font-size="10" text-anchor="start" fill="#8A9099">${chartFmtNum(Math.round(p))}</text>`
    );
  }

  // 현재가(마지막 종가) 강조 — 우측 가격축에 컬러 태그 + 점선
  const lastC = candles[N - 1].c;
  const prevC = N > 1 ? candles[N - 2].c : lastC;
  const lastColor = lastC > prevC ? C_UP : lastC < prevC ? C_DOWN : C_FLAT;
  const ly = yPrice(lastC);
  const priceTag =
    `<line x1="${padL}" y1="${ly.toFixed(1)}" x2="${W - padR}" y2="${ly.toFixed(1)}" stroke="${lastColor}" stroke-width="0.8" stroke-dasharray="3 2" opacity="0.55"/>` +
    `<rect x="${(W - padR).toFixed(1)}" y="${(ly - 8).toFixed(1)}" width="${(padR - 3).toFixed(1)}" height="16" rx="2.5" fill="${lastColor}"/>` +
    `<text x="${(W - padR + (padR - 3) / 2).toFixed(1)}" y="${(ly + 3.5).toFixed(1)}" font-size="10" font-weight="700" text-anchor="middle" fill="#fff">${chartFmtNum(Math.round(lastC))}</text>`;

  // X축 시간 눈금 (최대 6개)
  const xTicks = [];
  const tickStep = Math.max(1, Math.floor(N / 6));
  for (let i = 0; i < N; i += tickStep) {
    const cx = xCenter(i);
    xTicks.push(
      `<text x="${cx.toFixed(1)}" y="${xAxisY}" font-size="10" text-anchor="middle" fill="#8A9099">${chartEscape(chartFmtTime(candles[i].t, timeframe))}</text>`
    );
  }

  // 거래량 panel 구분선
  const separator = `<line x1="${padL}" y1="${priceBottom}" x2="${W - padR}" y2="${priceBottom}" stroke="#E3E6EA" stroke-width="1"/>`;

  // hover hit areas (투명 rect, 각 캔들별로 — JS 가 이벤트로 처리)
  const hitAreas = [];
  for (let i = 0; i < N; i++) {
    const cx = xCenter(i);
    hitAreas.push(
      `<rect class="chart-hit" data-i="${i}" x="${(cx - slot / 2).toFixed(1)}" y="${padT}" width="${slot.toFixed(2)}" height="${(volBottom - padT).toFixed(1)}" fill="transparent"/>`
    );
  }

  // crosshair (JS 가 위치 업데이트)
  const crosshair = `
    <line class="chart-crosshair-x" x1="0" y1="${padT}" x2="0" y2="${volBottom}" stroke="#A8AEB6" stroke-width="0.7" stroke-dasharray="3 3" visibility="hidden"/>
    <line class="chart-crosshair-y" x1="${padL}" y1="0" x2="${W - padR}" y2="0" stroke="#A8AEB6" stroke-width="0.7" stroke-dasharray="3 3" visibility="hidden"/>
  `;

  return `
    <svg class="chart-svg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
      ${yTicks.join('')}
      ${separator}
      ${maSvg.join('')}
      ${candleSvg.join('')}
      ${volSvg.join('')}
      ${priceTag}
      ${maLegend.join('')}
      ${xTicks.join('')}
      ${crosshair}
      ${hitAreas.join('')}
    </svg>
  `;
}

// ─────────────────────────────────────────┐
// 모달 본문 렌더링                            │
// ─────────────────────────────────────────┘
function chartHeaderHTML() {
  const tabs = TIMEFRAMES.map(tf => {
    const active = tf.key === chartState.timeframe ? ' active' : '';
    return `<button class="chart-tab${active}" data-tf="${tf.key}">${tf.label}</button>`;
  }).join('');
  return `
    <div class="chart-header">
      <div class="chart-title">
        <span class="chart-name">${chartEscape(chartState.name || chartState.code)}</span>
        <span class="chart-code">${chartEscape(chartState.code)}</span>
      </div>
      <div class="chart-tabs">${tabs}</div>
    </div>
  `;
}

function chartMetaHTML(data) {
  if (!data) return '';
  const last = data.candles[data.candles.length - 1];
  const first = data.candles[0];
  const chg = last && first ? ((last.c - first.c) / first.c) * 100 : null;
  const chgCls = chg == null ? '' : chg > 0 ? 'up' : chg < 0 ? 'down' : '';
  const chgTxt = chg == null ? '-' : `${chg > 0 ? '+' : ''}${chg.toFixed(2)}%`;
  const cacheTag = data.fromCache ? '<span class="chart-cache-tag">캐시</span>' : '';
  const updated = data.updatedAt ? new Date(data.updatedAt).toLocaleString('ko-KR') : '';

  return `
    <div class="chart-meta">
      <div class="chart-meta-left">
        <span class="chart-last">${chartFmtNum(last?.c)}</span>
        <span class="chart-period-chg ${chgCls}">${chgTxt} <small>(${data.candles.length}개 기준)</small></span>
      </div>
      <div class="chart-meta-right">
        ${cacheTag}
        <span class="chart-updated">${chartEscape(updated)}</span>
      </div>
    </div>
  `;
}

function renderModalBody(state, body) {
  const topSection = chartState.topHTML
    ? `<div class="modal-top-section">${chartState.topHTML}</div>`
    : '';
  if (state.error) {
    body.innerHTML = `
      ${topSection}
      ${chartHeaderHTML()}
      <div class="chart-error">
        <p>차트 데이터 로드 실패</p>
        <p class="chart-error-detail">${chartEscape(state.error)}</p>
        <button class="chart-retry-btn" data-action="retry">다시 시도</button>
      </div>
    `;
    return;
  }
  if (state.loading) {
    body.innerHTML = `
      ${topSection}
      ${chartHeaderHTML()}
      <div class="chart-loading">
        <div class="chart-spinner"></div>
        <p>차트 불러오는 중...</p>
      </div>
    `;
    return;
  }
  body.innerHTML = `
    ${topSection}
    ${chartHeaderHTML()}
    <div class="chart-canvas">
      ${renderCandlestickSVG(state.data?.candles || [], chartState.timeframe)}
      <div class="chart-tooltip" hidden></div>
    </div>
    ${chartMetaHTML(state.data)}
  `;
  attachCrosshair(body);
}

// ─────────────────────────────────────────┐
// Crosshair + Tooltip                       │
// ─────────────────────────────────────────┘
function attachCrosshair(body) {
  const svg = body.querySelector('.chart-svg');
  const tooltip = body.querySelector('.chart-tooltip');
  if (!svg || !tooltip || !chartState.data) return;

  const candles = chartState.data.candles;
  const hitAreas = svg.querySelectorAll('.chart-hit');
  const xLine = svg.querySelector('.chart-crosshair-x');
  const yLine = svg.querySelector('.chart-crosshair-y');

  function showAt(i, clientX, clientY) {
    if (!candles[i]) return;
    const rect = svg.getBoundingClientRect();
    const vbH = GEO.H;
    // 클릭/호버 위치 → viewBox 좌표 (cx)
    const hit = hitAreas[i];
    const cx = parseFloat(hit.getAttribute('x')) + parseFloat(hit.getAttribute('width')) / 2;
    xLine.setAttribute('x1', cx);
    xLine.setAttribute('x2', cx);
    xLine.setAttribute('visibility', 'visible');
    // y crosshair: 마우스 Y 위치를 viewBox 좌표로
    const localY = ((clientY - rect.top) / rect.height) * vbH;
    yLine.setAttribute('y1', localY);
    yLine.setAttribute('y2', localY);
    yLine.setAttribute('visibility', 'visible');

    const c = candles[i];
    const t = chartFmtTime(c.t, chartState.timeframe);
    const chg = c.o > 0 ? ((c.c - c.o) / c.o) * 100 : 0;
    const chgCls = chg > 0 ? 'up' : chg < 0 ? 'down' : '';
    tooltip.innerHTML = `
      <div class="chart-tooltip-time">${chartEscape(t)}</div>
      <div class="chart-tooltip-row"><span>시</span><b>${chartFmtNum(c.o)}</b></div>
      <div class="chart-tooltip-row"><span>고</span><b>${chartFmtNum(c.h)}</b></div>
      <div class="chart-tooltip-row"><span>저</span><b>${chartFmtNum(c.l)}</b></div>
      <div class="chart-tooltip-row"><span>종</span><b class="${chgCls}">${chartFmtNum(c.c)}</b></div>
      <div class="chart-tooltip-row"><span>변동</span><b class="${chgCls}">${chg > 0 ? '+' : ''}${chg.toFixed(2)}%</b></div>
      <div class="chart-tooltip-row"><span>거래량</span><b>${chartFmtVol(c.v)}</b></div>
    `;
    tooltip.hidden = false;
    // 툴팁 위치 — 캔버스 내에서 좌/우 가장자리 회피
    const tipRect = tooltip.getBoundingClientRect();
    const wrap = tooltip.parentElement.getBoundingClientRect();
    let left = clientX - wrap.left + 12;
    let top = clientY - wrap.top + 12;
    if (left + tipRect.width > wrap.width) left = clientX - wrap.left - tipRect.width - 12;
    if (top + tipRect.height > wrap.height) top = wrap.height - tipRect.height - 8;
    tooltip.style.left = `${Math.max(4, left)}px`;
    tooltip.style.top = `${Math.max(4, top)}px`;
  }

  function hide() {
    xLine.setAttribute('visibility', 'hidden');
    yLine.setAttribute('visibility', 'hidden');
    tooltip.hidden = true;
  }

  hitAreas.forEach((hit, i) => {
    hit.addEventListener('mouseenter', e => showAt(i, e.clientX, e.clientY));
    hit.addEventListener('mousemove', e => showAt(i, e.clientX, e.clientY));
  });
  svg.addEventListener('mouseleave', hide);
}

// ─────────────────────────────────────────┐
// Load & state machine                      │
// ─────────────────────────────────────────┘
async function loadChart(reason = 'init') {
  const body = document.getElementById('stock-modal-body');
  if (!body) return;

  // 메모리 캐시 — 같은 모달 세션에서 timeframe 토글 시 즉시 표시
  const cacheKey = `${chartState.code}:${chartState.timeframe}`;
  const cached = chartState.cache.get(cacheKey);
  if (cached && reason !== 'force') {
    chartState.data = cached;
    renderModalBody({ loading: false, data: cached }, body);
    return;
  }

  const myToken = ++chartState.loadingToken;
  renderModalBody({ loading: true }, body);

  try {
    const tf = TIMEFRAMES.find(t => t.key === chartState.timeframe);
    const count = tf ? tf.count : 200;
    const data = await fetchChart(chartState.code, chartState.timeframe, count);
    if (myToken !== chartState.loadingToken) return;  // 더 새 요청이 있음
    // 응답이 알려진 이름을 포함하면 모달 헤더 갱신
    if (data.name && data.name !== chartState.name) {
      chartState.name = data.name;
    }
    chartState.data = data;
    chartState.cache.set(cacheKey, data);
    renderModalBody({ loading: false, data }, body);
  } catch (err) {
    if (myToken !== chartState.loadingToken) return;
    console.error('[chart] fetch failed', err);
    renderModalBody({ loading: false, error: err.message || String(err) }, body);
  }
}

// ─────────────────────────────────────────┐
// 모달 open/close                            │
// ─────────────────────────────────────────┘
function openChartModal(code, name, opts = {}) {
  if (!code) return;
  const modal = document.getElementById('stock-modal');
  if (!modal) return;
  chartState.open = true;
  chartState.code = String(code).trim();
  chartState.name = name ? String(name).trim() : null;
  chartState.timeframe = opts.timeframe || 'day';
  chartState.data = null;
  chartState.cache = new Map();   // 모달 새로 열 때마다 캐시 reset
  chartState.topHTML = opts.topHTML || '';
  modal.removeAttribute('hidden');
  document.body.style.overflow = 'hidden';
  loadChart('init');
}

function closeChartModal() {
  const modal = document.getElementById('stock-modal');
  if (!modal) return;
  modal.setAttribute('hidden', '');
  document.body.style.overflow = '';
  chartState.open = false;
  chartState.data = null;
  chartState.cache.clear();
  chartState.topHTML = '';
  // body 비우기 — 다음 열림 때 깨끗하게
  const body = document.getElementById('stock-modal-body');
  if (body) body.innerHTML = '';
}

// ─────────────────────────────────────────┐
// 이벤트 위임                                │
// ─────────────────────────────────────────┘
function setupChartHandlers() {
  // 클릭 위임 — 어떤 요소든 data-stock-code 가 있으면 모달 오픈.
  // 단, 모달 내부의 클릭은 제외 (모달 자체에서 핸들).
  document.addEventListener('click', e => {
    // 모달 내부 닫기/탭 처리는 별도 핸들러에서.
    const modal = document.getElementById('stock-modal');
    if (modal && !modal.hasAttribute('hidden') && modal.contains(e.target)) {
      handleModalInternalClick(e);
      return;
    }
    // 외부: data-stock-code 가진 요소 (또는 ancestor) 찾기
    const trigger = e.target.closest('[data-stock-code]');
    if (!trigger) return;
    // 트리거 안의 a/button 은 자체 동작 우선 (뉴스 링크 등)
    if (e.target.closest('a, button')) return;
    // <summary> 클릭은 접기/펼치기 전용. 여기서 모달까지 열면 펼치자마자 모달이
    // 덮어 정작 펼친 내용을 못 본다.
    if (e.target.closest('summary')) return;
    const code = trigger.dataset.stockCode;
    const name = trigger.dataset.stockName || '';
    e.preventDefault();
    // flow.js 가 종목별 상단 위젯(수급 osc 차트 + supply gauge)을 제공하면 함께 표시
    const opts = {};
    if (typeof window.getStockTopHTML === 'function') {
      const html = window.getStockTopHTML(code);
      if (html) opts.topHTML = html;
    }
    openChartModal(code, name, opts);
  });

  // ESC 로 닫기
  document.addEventListener('keydown', e => {
    if (!chartState.open) return;
    if (e.key === 'Escape') closeChartModal();
  });
}

function handleModalInternalClick(e) {
  // 닫기 (× 버튼, backdrop)
  if (e.target.matches('[data-modal-close]')) {
    closeChartModal();
    return;
  }
  // 기간 탭
  const tab = e.target.closest('.chart-tab');
  if (tab) {
    const tf = tab.dataset.tf;
    if (tf && tf !== chartState.timeframe) {
      chartState.timeframe = tf;
      loadChart('tab-change');
    }
    return;
  }
  // 재시도
  if (e.target.matches('[data-action="retry"]')) {
    loadChart('force');
  }
}

// ─────────────────────────────────────────┐
// Init                                      │
// ─────────────────────────────────────────┘
document.addEventListener('DOMContentLoaded', setupChartHandlers);

// 외부에서 프로그래매틱 호출 가능 (e.g. 검색창에서 직접 오픈)
window.openStockChart = openChartModal;
window.closeStockChart = closeChartModal;
