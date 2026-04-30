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
// Dual-axis chart: price + Pier&Grid Oscillator (MACD)
// ─────────────────────────────────────────┘
function renderDualAxisChart(history, opts = {}) {
  const w = opts.width || 360;
  const h = opts.height || 130;
  const padL = 4, padR = 4, padT = 6, padB = 6;

  if (!history || history.length < 5) return '<div class="sparkline-empty">데이터 부족</div>';

  const closes = history.map(p => p.close).filter(v => v != null);
  const oscs = history.map(p => p.oscillator).filter(v => v != null);
  if (closes.length < 2 || oscs.length < 2) return '<div class="sparkline-empty">데이터 부족</div>';

  const cMin = Math.min(...closes), cMax = Math.max(...closes);

  // Oscillator centered around 0; symmetric scaling so 0 is at chart center
  const oMaxAbs = Math.max(...oscs.map(v => Math.abs(v))) || 1;
  // Add a touch of headroom
  const oRange = oMaxAbs * 1.1;

  const stepX = (w - padL - padR) / (history.length - 1);

  const closePts = history.map((p, i) => {
    if (p.close == null) return null;
    const x = padL + i * stepX;
    const y = padT + (1 - (p.close - cMin) / (cMax - cMin || 1)) * (h - padT - padB);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).filter(Boolean).join(' ');

  // Map oscillator: 0 at chart vertical center, ±oRange at top/bottom
  const yMid = padT + 0.5 * (h - padT - padB);
  const oscPts = history.map((p, i) => {
    if (p.oscillator == null) return null;
    const x = padL + i * stepX;
    // y up = positive oscillator (above 0 line)
    const y = yMid - (p.oscillator / oRange) * (h - padT - padB) * 0.45;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).filter(Boolean).join(' ');

  // Build positive/negative shaded regions for oscillator
  // Find segments above 0 (red) and below 0 (blue)
  const segs = [];
  let cur = null;
  history.forEach((p, i) => {
    if (p.oscillator == null) {
      if (cur) { segs.push(cur); cur = null; }
      return;
    }
    const sign = p.oscillator >= 0 ? 'pos' : 'neg';
    const x = padL + i * stepX;
    const y = yMid - (p.oscillator / oRange) * (h - padT - padB) * 0.45;
    if (!cur || cur.sign !== sign) {
      if (cur) segs.push(cur);
      cur = { sign, points: [{ x, y }] };
    } else {
      cur.points.push({ x, y });
    }
  });
  if (cur) segs.push(cur);

  const fillSegs = segs.map(s => {
    if (s.points.length < 2) return '';
    const pts = s.points.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
    const firstX = s.points[0].x, lastX = s.points[s.points.length - 1].x;
    const fill = s.sign === 'pos' ? 'rgba(229,57,53,0.15)' : 'rgba(30,136,229,0.15)';
    return `<path d="M${firstX.toFixed(1)},${yMid.toFixed(1)} L${pts} L${lastX.toFixed(1)},${yMid.toFixed(1)} Z" fill="${fill}"/>`;
  }).join('');

  return `
    <svg viewBox="0 0 ${w} ${h}" class="dual-chart" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">
      <line x1="${padL}" y1="${yMid.toFixed(1)}" x2="${w - padR}" y2="${yMid.toFixed(1)}" stroke="#bbb" stroke-dasharray="3,3" stroke-width="1"/>
      ${fillSegs}
      <polyline points="${oscPts}" fill="none" stroke="#6A5ACD" stroke-width="1.6"/>
      <polyline points="${closePts}" fill="none" stroke="#1A1A1A" stroke-width="1.8"/>
    </svg>
  `;
}

// ─────────────────────────────────────────┐
// Mini price chart with MA10/20 + supply bars overlay
//   prices: array of 60 daily closes (left → right = old → recent)
//   dailyFlow10d: array of {instAmount} for the most recent ~10 days
// ─────────────────────────────────────────┘
function renderMiniPriceChart(prices, ma10, ma20, dailyFlow10d, opts = {}) {
  const w = opts.width || 175;
  const h = opts.height || 64;
  if (!prices || prices.length < 2) return '<div class="sparkline-empty"></div>';

  const valid = prices.filter(v => v != null);
  if (valid.length < 2) return '<div class="sparkline-empty"></div>';

  // Layout: top 70% = price, bottom 30% = supply bars
  const priceTop = 1, priceBottom = h * 0.68;
  const flowTop = h * 0.72, flowBottom = h - 1;
  const flowMid = (flowTop + flowBottom) / 2;
  const flowHalf = (flowBottom - flowTop) / 2;

  const min = Math.min(...valid), max = Math.max(...valid);
  const span = max - min || 1;
  const stepX = w / (prices.length - 1);

  const pts = prices.map((v, i) => {
    if (v == null) return null;
    const x = i * stepX;
    const y = priceTop + (1 - (v - min) / span) * (priceBottom - priceTop);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).filter(Boolean).join(' ');

  const lastPrice = prices[prices.length - 1];
  const stroke = lastPrice >= prices[0] ? '#E53935' : '#1E88E5';
  const fillColor = lastPrice >= prices[0] ? 'rgba(229,57,53,0.13)' : 'rgba(30,136,229,0.13)';

  // MA lines (constrained to price area)
  let maLine = '';
  if (ma10 != null && ma10 >= min && ma10 <= max) {
    const yMA = priceTop + (1 - (ma10 - min) / span) * (priceBottom - priceTop);
    maLine += `<line x1="0" y1="${yMA.toFixed(1)}" x2="${w}" y2="${yMA.toFixed(1)}" stroke="#FB8C00" stroke-dasharray="2,2" stroke-width="1"/>`;
  }
  if (ma20 != null && ma20 >= min && ma20 <= max) {
    const yMA = priceTop + (1 - (ma20 - min) / span) * (priceBottom - priceTop);
    maLine += `<line x1="0" y1="${yMA.toFixed(1)}" x2="${w}" y2="${yMA.toFixed(1)}" stroke="#9E9E9E" stroke-dasharray="2,2" stroke-width="1"/>`;
  }

  const firstPt = pts.split(' ')[0];
  const lastPt = pts.split(' ').slice(-1)[0];

  // ── 수급 바 오버레이 (마지막 N일을 차트 우측에 매핑)
  let supplyBars = '';
  let supplyMid = '';
  if (dailyFlow10d && dailyFlow10d.length > 0) {
    const flowCount = Math.min(dailyFlow10d.length, prices.length);
    const flowAmounts = dailyFlow10d.slice(-flowCount).map(d => d.instAmount);
    const maxAbs = Math.max(...flowAmounts.map(v => Math.abs(v))) || 1;
    const startIdx = prices.length - flowCount;

    supplyMid = `<line x1="0" y1="${flowMid.toFixed(1)}" x2="${w}" y2="${flowMid.toFixed(1)}" stroke="#666" stroke-width="0.6" opacity="0.4"/>`;
    supplyMid += `<line x1="${(startIdx * stepX).toFixed(1)}" y1="${priceBottom.toFixed(1)}" x2="${(startIdx * stepX).toFixed(1)}" y2="${flowBottom.toFixed(1)}" stroke="#bbb" stroke-dasharray="1,2" stroke-width="0.6"/>`;

    supplyBars = flowAmounts.map((amt, i) => {
      const x = (startIdx + i) * stepX;
      const ratio = Math.abs(amt) / maxAbs;
      const barHeight = flowHalf * ratio;
      const isBuy = amt > 0;
      const yTop = isBuy ? flowMid - barHeight : flowMid;
      const color = isBuy ? '#E53935' : '#1E88E5';
      return `<rect x="${(x - 1.4).toFixed(1)}" y="${yTop.toFixed(1)}" width="2.8" height="${Math.max(0.5, barHeight).toFixed(1)}" fill="${color}" opacity="0.78"/>`;
    }).join('');
  }

  return `
    <svg viewBox="0 0 ${w} ${h}" class="mini-chart" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">
      <path d="M${firstPt} L${pts.split(' ').slice(1).join(' L')} L${lastPt.split(',')[0]},${priceBottom.toFixed(1)} L${firstPt.split(',')[0]},${priceBottom.toFixed(1)} Z" fill="${fillColor}"/>
      ${maLine}
      <polyline points="${pts}" fill="none" stroke="${stroke}" stroke-width="1.5"/>
      ${supplyMid}
      ${supplyBars}
    </svg>
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
// CARD: Cash recommendation                 │
// ─────────────────────────────────────────┘
function buildCashCard(cash) {
  if (!cash || !cash.available) return '';
  const pct = cash.cashPct || 0;
  const colorFor = pct >= 30 ? '#E53935' : pct >= 15 ? '#FB8C00' : pct >= 5 ? '#FDD835' : '#43A047';
  return `
    <div class="flow-card flow-card-cash">
      <div class="card-header"><span class="card-theme-name">⚖️ 권장 현금 비중</span></div>
      <div class="cash-body">
        <div class="cash-value" style="color:${colorFor}">${pct}%</div>
        <div class="cash-level">${fEscape(cash.level || '')}</div>
        <div class="cash-meta">
          F&G ${cash.fearGreed?.toFixed(1) ?? '-'} · 쏠림: ${fEscape(cash.crowdingSignal || '-')}
        </div>
      </div>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// CARD: Sentiment dual-axis                 │
// ─────────────────────────────────────────┘
function buildSentimentCard(sentiment) {
  const k = sentiment?.kospi || {};
  const q = sentiment?.kosdaq || {};
  return `
    <div class="flow-card flow-card-sentiment">
      <div class="card-header"><span class="card-theme-name">📊 시장 심리 — 가격 vs 공포탐욕</span></div>
      <div class="sentiment-body-v2">
        <div class="sentiment-row">
          <div class="sentiment-row-head">
            <strong>${fEscape(k.label || 'KOSPI')}</strong>
            <span class="sentiment-zone">${fEscape(k.zone || '-')}</span>
            <span class="sentiment-fg">F&G ${k.fearGreed?.toFixed(1) ?? '-'} · Osc ${k.oscillator?.toFixed(2) ?? '-'}</span>
            <span class="sentiment-close">종가 ${fmtNumber(k.close)}</span>
          </div>
          ${renderDualAxisChart(k.history)}
          <div class="dual-legend">
            <span class="legend-price">━ 지수</span>
            <span class="legend-fg">━ Pier&Grid Oscillator (MACD)</span>
            <span class="legend-ref">┄ 0선</span>
            <span class="legend-pos">▒ 그리드(+)</span>
            <span class="legend-neg">▒ 피어(−)</span>
          </div>
        </div>
        <div class="sentiment-row">
          <div class="sentiment-row-head">
            <strong>${fEscape(q.label || 'KOSDAQ')}</strong>
            <span class="sentiment-zone">${fEscape(q.zone || '-')}</span>
            <span class="sentiment-fg">F&G ${q.fearGreed?.toFixed(1) ?? '-'} · Osc ${q.oscillator?.toFixed(2) ?? '-'}</span>
            <span class="sentiment-close">종가 ${fmtNumber(q.close)}</span>
          </div>
          ${renderDualAxisChart(q.history)}
        </div>
      </div>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// CARD: Buy candidates (THE BIG ONE)        │
// ─────────────────────────────────────────┘
function buildBuyCandidatesCard(candidates, leadingLabels) {
  if (!candidates || candidates.length === 0) {
    return `<div class="flow-card flow-card-candidates"><div class="card-header"><span class="card-theme-name">🎯 매수 후보</span></div><div class="empty-msg">현재 후보 없음</div></div>`;
  }
  const title = `🎯 매수 후보 — 주도섹터 ∩ 수급빈집${leadingLabels?.length ? ` (${leadingLabels.join(', ')})` : ''}`;

  const rows = candidates.slice(0, 12).map(c => {
    const bz = c.buyZone || {};
    const sectorTag = c.sector || '-';
    const newHighBadge = c.newHigh250d ? '<span class="badge badge-red">250d 신고가</span>'
                       : c.newHigh50d ? '<span class="badge badge-orange">50d 신고가</span>'
                       : '';
    const trendBadge = c.aboveMA10 ? '<span class="badge badge-green">↑10MA</span>'
                     : c.aboveMA20 ? '<span class="badge badge-yellow">↑20MA</span>'
                     : '<span class="badge badge-gray">추세약함</span>';
    const buyZoneBadge = bz.inBuyZone ? '<span class="badge badge-blue">매수권</span>' : '';

    const todayPullback = bz.todayPullbackPct ?? 0;
    const buyZonePullback = bz.avgHighToClosePct ?? 0;

    return `
      <div class="cand-row">
        <div class="cand-info">
          <div class="cand-name">${fEscape(c.name)} <small>${fEscape(c.code)} · ${fEscape(sectorTag)}</small></div>
          <div class="cand-badges">${trendBadge}${newHighBadge}${buyZoneBadge}</div>
          <div class="cand-prices">
            <span class="cand-close">${fmtNumber(c.close)}</span>
            <span class="cand-ret ${changeClass(c.ret5d)}">5d ${fmtPctSigned(c.ret5d)}</span>
          </div>
          <div class="cand-bz">
            오늘 고가 대비 <strong class="${todayPullback < 0 ? 'down' : 'flat'}">${todayPullback.toFixed(2)}%</strong>
            · 매수권 -${Math.abs(buyZonePullback).toFixed(2)}%
            ${bz.buyZonePrice ? `· 매수가 ${fmtNumber(bz.buyZonePrice)}` : ''}
          </div>
          <div class="cand-flow">
            외인+기관 5d: <strong class="down">${fmtBillion(c.institutionNet5d)}</strong>
            · 거래대금비 ${c.tradingValueRatio != null ? c.tradingValueRatio.toFixed(2) + 'x' : '-'}
          </div>
        </div>
        <div class="cand-chart">
          ${renderMiniPriceChart(c.priceHistory60d, c.ma10, c.ma20, c.dailyFlow10d)}
        </div>
      </div>
    `;
  }).join('');

  return `
    <div class="flow-card flow-card-candidates">
      <div class="card-header"><span class="card-theme-name">${fEscape(title)}</span><span class="card-volume">${candidates.length}개</span></div>
      <div class="cand-body">${rows}</div>
      <div class="cand-legend">
        <span><span class="legend-dot orange"></span>10MA</span>
        <span><span class="legend-dot gray"></span>20MA</span>
        <span><span class="legend-bar red"></span>외인+기관 매수일</span>
        <span><span class="legend-bar blue"></span>매도일</span>
        <span class="legend-tip">차트 우측 막대 = 최근 10일 외인+기관 일별 순매수액</span>
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
            <div class="ti-row">
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
// CARD: Crowding                            │
// ─────────────────────────────────────────┘
function buildCrowdingCard(crowding) {
  if (!crowding || !crowding.available) return '';
  const sigColor = crowding.signal === '극심쏠림' ? '#E53935' : crowding.signal === '쏠림' ? '#FB8C00' : crowding.signal === '주의' ? '#FDD835' : '#43A047';
  const sparkVals = (crowding.history || []).map(h => h.crowding);
  return `
    <div class="flow-card flow-card-crowding">
      <div class="card-header"><span class="card-theme-name">🌀 업종 쏠림 지수</span></div>
      <div class="crowding-body">
        <div class="crowding-headline">
          <span class="crowding-value" style="color:${sigColor}">${(crowding.latest ?? 0).toFixed(1)}</span>
          <span class="crowding-signal" style="background:${sigColor}">${fEscape(crowding.signal)}</span>
        </div>
        <div class="sparkline-wrap">${renderSparkline(sparkVals, { stroke: '#FB8C00', refLine: 35 })}</div>
        <div class="crowding-side">
          <div class="crowd-col">
            <div class="crowd-col-title">📈 주도</div>
            ${(crowding.leaders || []).slice(0, 5).map(l => `<div class="crowd-row"><span>${fEscape(l.name)}</span><span class="up">+${l.ret6m}%</span></div>`).join('')}
          </div>
          <div class="crowd-col">
            <div class="crowd-col-title">📉 소외</div>
            ${(crowding.laggards || []).slice(0, 5).map(l => `<div class="crowd-row"><span>${fEscape(l.name)}</span><span class="${l.ret6m < 0 ? 'down' : 'up'}">${l.ret6m > 0 ? '+' : ''}${l.ret6m}%</span></div>`).join('')}
          </div>
        </div>
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
            <span class="leading-rs"><div class="rs-bar"><div class="rs-bar-fill" style="width:${Math.min(100, e.rsNorm)}%; background:${e.rsNorm >= 70 ? '#E53935' : e.rsNorm >= 50 ? '#FB8C00' : '#1E88E5'}"></div></div><span class="rs-text">${e.rsNorm}</span></span>
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
  const container = document.getElementById('flow-content');
  const loading = document.getElementById('flow-loading');
  try {
    const resp = await fetch(FLOW_DATA_URL + '?t=' + Date.now());
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (loading) loading.remove();

    container.innerHTML = `
      <div class="flow-meta">
        <span>업데이트: ${new Date(data.updatedAt).toLocaleString('ko-KR')}</span>
        <span>분석 ${data.vacancyAnalyzed || 0}/${data.universeSize || 0} · ${data.elapsedSeconds}s</span>
      </div>
      <div class="flow-grid">
        ${buildCashCard(data.cashRecommendation)}
        ${buildSentimentCard(data.marketSentiment)}
        ${buildCrowdingCard(data.crowding)}
        ${buildBuyCandidatesCard(data.buyCandidates, data.leadingSectorLabels)}
        ${buildTICard(data.tradingIntensity)}
        ${buildSectorFlowCard(data.sectorFlows)}
        ${buildExitCard(data.exitSignals)}
        ${buildLeadingCard(data.leadingSectors)}
      </div>
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
  }
}

document.addEventListener('DOMContentLoaded', setupTabs);
