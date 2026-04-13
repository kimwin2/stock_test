/**
 * Stock Premium - 주식 테마 대시보드 App
 * dashboard_data.json을 로드하여 테마 카드를 렌더링합니다.
 */

// ─────────────────────────────────────────┐
// Config                                    │
// ─────────────────────────────────────────┘
// ── 환경별 데이터 URL 자동 전환 ──
// GitHub Pages(프로덕션): S3에서 fetch
// 로컬 개발: 같은 디렉터리의 JSON 파일
const S3_DATA_URL = 'https://stock-dashboard-data.s3.ap-northeast-2.amazonaws.com/dashboard_data.json';
const LOCAL_DATA_URL = './dashboard_data.json';

const isProduction = window.location.hostname.includes('github.io') 
                  || window.location.hostname.includes('stock');
const DATA_URL = isProduction ? S3_DATA_URL : LOCAL_DATA_URL;

// ─────────────────────────────────────────┐
// Utils                                     │
// ─────────────────────────────────────────┘
function formatPrice(price) {
  if (!price || price === 0) return '-';
  return price.toLocaleString('ko-KR');
}

function formatDatetime(isoStr) {
  if (!isoStr) return '--:--';
  const d = new Date(isoStr);
  const days = ['일', '월', '화', '수', '목', '금', '토'];
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const day = days[d.getDay()];
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${mm}-${dd}(${day}) ${hh}:${mi}`;
}

function getChangeClass(rate) {
  if (rate > 0) return 'up';
  if (rate < 0) return 'down';
  return 'flat';
}

function getChangeText(rate) {
  if (rate > 0) return `↑${rate.toFixed(2)}%`;
  if (rate < 0) return `↓${Math.abs(rate).toFixed(2)}%`;
  return `${rate.toFixed(2)}%`;
}

function getSignalLabel(state) {
  switch (state) {
    case 'THEME_CONFIRMED':
      return 'CONFIRMED';
    case 'THEME_FORMING':
      return 'FORMING';
    case 'LEADER_DETECTED':
      return 'LEADER';
    case 'THEME_WEAKENING':
      return 'WEAKENING';
    case 'THEME_ENDED':
      return 'ENDED';
    default:
      return '';
  }
}

function getSignalClass(state) {
  switch (state) {
    case 'THEME_CONFIRMED':
      return 'confirmed';
    case 'THEME_FORMING':
      return 'forming';
    case 'LEADER_DETECTED':
      return 'leader';
    case 'THEME_WEAKENING':
      return 'weakening';
    case 'THEME_ENDED':
      return 'ended';
    default:
      return '';
  }
}

function getSourceBadge(source) {
  if (source === 'FALLBACK') {
    return '<span class="signal-badge fallback">FALLBACK</span>';
  }
  return '';
}

// ─────────────────────────────────────────┐
// Components                                │
// ─────────────────────────────────────────┘

/**
 * Range Bar 렌더링
 * barData: { minMaxRange: [0,100], currentRange: [start, end], baseline: number }
 */
function createRangeBar(barData, changeRate) {
  const container = document.createElement('div');
  container.className = 'range-bar-container';

  // Gray background bar
  const bg = document.createElement('div');
  bg.className = 'range-bar-bg';
  container.appendChild(bg);

  // Colored fill bar
  const [start, end] = barData.currentRange || [40, 60];
  const fill = document.createElement('div');
  fill.className = `range-bar-fill ${getChangeClass(changeRate)}`;
  fill.style.left = `${Math.max(0, Math.min(100, start))}%`;
  fill.style.width = `${Math.max(1, Math.min(100, end - start))}%`;
  container.appendChild(fill);

  // Baseline tick
  const baseline = barData.baseline;
  if (baseline !== undefined && baseline !== null) {
    const tick = document.createElement('div');
    tick.className = 'range-bar-tick';
    tick.style.left = `${Math.max(0, Math.min(100, baseline))}%`;
    container.appendChild(tick);
  }

  return container;
}

/**
 * Stock Item 렌더링
 */
function createStockItem(stock) {
  const item = document.createElement('div');
  item.className = `stock-item${stock.isTop ? ' is-top' : ''}`;

  // Skip items with no price (unlisted)
  if (stock.price === 0 && stock.changeRate === 0) {
    return null;
  }

  const changeClass = getChangeClass(stock.changeRate);

  item.innerHTML = `
    <div class="stock-row-1">
      <div class="stock-name">
        ${stock.isTop ? '<span class="top-marker"></span>' : '<span style="width:10px;display:inline-block"></span>'}
        ${escapeHTML(stock.name)}
      </div>
      <div class="stock-change ${changeClass}">${getChangeText(stock.changeRate)}</div>
    </div>
    <div class="stock-row-2">
      <div class="stock-price">
        <span class="price-value ${changeClass}">${formatPrice(stock.price)}</span>
        ${stock.time ? `<span class="price-time">${escapeHTML(stock.time)}</span>` : ''}
      </div>
      <div class="stock-volume">${escapeHTML(stock.volume || '')}</div>
    </div>
  `;

  // Range bar
  if (stock.barData) {
    item.appendChild(createRangeBar(stock.barData, stock.changeRate));
  }

  return item;
}

/**
 * Theme Card 렌더링
 */
function createThemeCard(theme) {
  const card = document.createElement('div');
  card.className = 'theme-card';

  // Header
  const header = document.createElement('div');
  header.className = 'card-header';
  const signalState = theme.signal && theme.signal.state ? theme.signal.state : '';
  const signalLabel = getSignalLabel(signalState);
  const signalClass = getSignalClass(signalState);
  const sourceBadge = theme.signal ? getSourceBadge(theme.signal.source) : '';
  const signalBadge = signalLabel
    ? `<span class="signal-badge ${signalClass}">${escapeHTML(signalLabel)}</span>`
    : '';
  header.innerHTML = `
    <span class="card-theme-name">${escapeHTML(theme.themeName)}${signalBadge}${sourceBadge}</span>
    <span class="card-volume">${escapeHTML(theme.totalVolume)}</span>
  `;
  card.appendChild(header);

  // Headline
  const headlineDiv = document.createElement('div');
  headlineDiv.className = 'card-headline';
  if (theme.headlineUrl) {
    const a = document.createElement('a');
    a.href = theme.headlineUrl;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = theme.headline || '';
    a.title = '관련 뉴스 보기';
    headlineDiv.appendChild(a);
  } else {
    headlineDiv.textContent = theme.headline || '';
  }
  card.appendChild(headlineDiv);

  // Stock list
  const stocks = theme.stocks || [];
  stocks.forEach(stock => {
    const stockEl = createStockItem(stock);
    if (stockEl) {
      card.appendChild(stockEl);
    }
  });

  return card;
}

// ─────────────────────────────────────────┐
// Ticker                                    │
// ─────────────────────────────────────────┘
function renderTicker(themes) {
  const tickerContent = document.getElementById('ticker-content');
  if (!tickerContent) return;

  let items = [];
  themes.forEach(theme => {
    const topStock = (theme.stocks || []).find(s => s.isTop);
    if (topStock) {
      const arrow = topStock.changeRate >= 0 ? '▲' : '▼';
      items.push(
        `<span><strong>[${theme.themeName}]</strong> ${theme.headline} | ${topStock.name} ${arrow}${Math.abs(topStock.changeRate).toFixed(2)}%</span>`
      );
    } else {
      items.push(
        `<span><strong>[${theme.themeName}]</strong> ${theme.headline}</span>`
      );
    }
  });

  // Duplicate for seamless scrolling
  tickerContent.innerHTML = items.join('') + items.join('');
}

// ─────────────────────────────────────────┐
// Main Render                               │
// ─────────────────────────────────────────┘
async function loadAndRender() {
  const grid = document.getElementById('theme-grid');
  const loading = document.getElementById('loading-state');

  try {
    const resp = await fetch(DATA_URL + '?t=' + new Date().getTime());
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    // Update header datetime
    const dtEl = document.getElementById('header-datetime');
    if (dtEl && data.updatedAt) {
      dtEl.textContent = formatDatetime(data.updatedAt);
    }

    // Clear loading
    if (loading) loading.remove();

    // Render theme cards
    const themes = data.themes || [];
    themes.forEach(theme => {
      grid.appendChild(createThemeCard(theme));
    });

    // Render ticker
    renderTicker(themes);

  } catch (err) {
    console.error('Failed to load dashboard data:', err);
    if (loading) loading.remove();
    grid.innerHTML = `
      <div class="error-state">
        <p>데이터를 불러올 수 없습니다.</p>
        <p style="font-size:0.8rem;color:#999">${escapeHTML(err.message)}</p>
        <button class="retry-btn" onclick="location.reload()">다시 시도</button>
      </div>
    `;
  }
}

// ─────────────────────────────────────────┐
// Helpers                                   │
// ─────────────────────────────────────────┘
function escapeHTML(str) {
  if (!str) return '';
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
  return String(str).replace(/[&<>"']/g, c => map[c]);
}


// ─────────────────────────────────────────┐
// Init                                      │
// ─────────────────────────────────────────┘
document.addEventListener('DOMContentLoaded', loadAndRender);
