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

// 테마 분석이 실패한 회차에는 서버가 기존 themes 를 그대로 두고 updatedAt 만 갱신한다.
// 그 사실을 화면에 드러내지 않으면 "갱신: 방금" 표시 때문에 낡은 테마를 최신으로 오인한다.
function renderThemeStaleNotice(grid, data) {
  const prev = document.getElementById('theme-stale-notice');
  if (prev) prev.remove();
  if (!data || !data.themesError) return;

  const since = data.themesGeneratedAt ? formatDatetime(data.themesGeneratedAt) : null;
  const el = document.createElement('div');
  el.id = 'theme-stale-notice';
  el.className = 'stale-notice';
  el.innerHTML = `
    <strong>테마 갱신이 멈춰 있습니다</strong>
    <span>${escapeHTML(data.themesError)}${since ? ` · 마지막 성공 ${escapeHTML(since)}` : ''}</span>
  `;
  grid.parentNode.insertBefore(el, grid);
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

  // Colored fill bar — 가운데(50%) 기준, 상승→오른쪽 빨강, 하락→왼쪽 파랑
  // 30% = 반쪽 꽉 채움 (50% 폭)
  const MAX_RATE = 30;
  const absRate = Math.abs(changeRate || 0);
  const halfPct = Math.min(50, (absRate / MAX_RATE) * 50);

  const fill = document.createElement('div');
  fill.className = `range-bar-fill ${getChangeClass(changeRate)}`;

  if (changeRate >= 0) {
    // 상승: 가운데에서 오른쪽으로
    fill.style.left = '50%';
    fill.style.width = `${Math.max(0.5, halfPct)}%`;
  } else {
    // 하락: 가운데에서 왼쪽으로
    fill.style.left = `${50 - halfPct}%`;
    fill.style.width = `${Math.max(0.5, halfPct)}%`;
  }
  container.appendChild(fill);

  // 가운데 기준선 틱
  const tick = document.createElement('div');
  tick.className = 'range-bar-tick';
  tick.style.left = '50%';
  container.appendChild(tick);

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
 * 등락률 → "+30.00%" / "-1.20%" (칩·대표종목용, 화살표 없이 부호로)
 */
function formatPct(rate) {
  const r = rate || 0;
  return `${r >= 0 ? '+' : ''}${r.toFixed(2)}%`;
}

/**
 * 아크 게이지 (반원 스피도미터) — 등락률 강도/방향 시각화.
 * 한국장 상·하한 ±30%를 반원 꽉 참으로 매핑 (상한가 = 풀 아크).
 * 정확한 % 는 행 우측에 별도 표기하므로 아크엔 텍스트 없음.
 */
function arcGauge(rate) {
  const r = rate || 0;
  const cx = 27, cy = 30, R = 21;
  const f = Math.max(0.03, Math.min(1, Math.abs(r) / 30));
  const color = r >= 0 ? '#E53935' : '#1565C0';
  const polar = (a) => {
    const rad = (a * Math.PI) / 180;
    return [cx + R * Math.cos(rad), cy - R * Math.sin(rad)];
  };
  const [ax, ay] = polar(180);
  const [bx, by] = polar(0);
  const [ex, ey] = polar(180 - f * 180);
  return `<svg class="bs-arc" width="54" height="40" viewBox="0 0 54 40" aria-hidden="true">
    <path d="M${ax.toFixed(1)} ${ay.toFixed(1)} A${R} ${R} 0 0 1 ${bx.toFixed(1)} ${by.toFixed(1)}" fill="none" stroke="#efe6d5" stroke-width="4.5" stroke-linecap="round"/>
    <path d="M${ax.toFixed(1)} ${ay.toFixed(1)} A${R} ${R} 0 0 1 ${ex.toFixed(1)} ${ey.toFixed(1)}" fill="none" stroke="${color}" stroke-width="4.5" stroke-linecap="round"/>
  </svg>`;
}

/**
 * Theme Card 렌더링 — 매거진 브리핑 (C안)
 * 큰 세리프 타이틀 + 뉴스 한 줄 + 대표종목(레인지 바) + 종목 칩
 */
function createThemeCard(theme, index = 0) {
  const card = document.createElement('div');
  card.className = 'brief';

  const primaryHeadlineLink = theme.headlineUrl
    || theme.headlineLink?.url
    || (Array.isArray(theme.headlineLinks) ? theme.headlineLinks[0]?.url : '')
    || '';

  // 상장·시세 있는 종목만 (미상장 skip)
  const stocks = (theme.stocks || []).filter(s => !(s.price === 0 && s.changeRate === 0));
  const kicker = index === 0 ? 'TODAY · 오늘의 주도 테마' : '급등 테마';

  // 상단(키커 + 타이틀/거래대금 + 뉴스 한 줄)
  const head = document.createElement('div');
  head.innerHTML = `
    <div class="brief-kick">${escapeHTML(kicker)}</div>
    <div class="brief-row1">
      <div class="brief-title">${escapeHTML(theme.themeName)}</div>
      <div class="brief-vol">${escapeHTML(theme.totalVolume || '')}</div>
    </div>
  `;
  card.appendChild(head);

  // 뉴스 한 줄 (근거) — 링크 유지
  const why = document.createElement('div');
  why.className = 'brief-why';
  if (primaryHeadlineLink) {
    const a = document.createElement('a');
    a.href = primaryHeadlineLink;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = theme.headline || '';
    a.title = '관련 뉴스 보기';
    why.appendChild(a);
  } else {
    why.textContent = theme.headline || '';
  }
  if (theme.headline) card.appendChild(why);

  // 종목 행 — 전 종목 리치 표현 (아크 게이지 + 이름 + 가격·거래대금 + 정확한 등락%)
  if (stocks.length) {
    const list = document.createElement('div');
    list.className = 'brief-stocks';
    list.innerHTML = stocks.map(s => {
      const cls = getChangeClass(s.changeRate);
      const sub = [
        `${formatPrice(s.price)}원`,
        s.time ? escapeHTML(s.time) : '',
        s.volume ? `거래대금 ${escapeHTML(s.volume)}` : '',
      ].filter(Boolean).join(' · ');
      return `<div class="brief-stock">
        ${arcGauge(s.changeRate)}
        <div class="bs-info">
          <div class="bs-name">${escapeHTML(s.name)}${s.isTop ? '<span class="bs-top">대표</span>' : ''}</div>
          <div class="bs-sub">${sub}</div>
        </div>
        <div class="bs-chg ${cls}">${formatPct(s.changeRate)}</div>
      </div>`;
    }).join('');
    card.appendChild(list);
  }

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

    // 테마 분석이 실패하면 updatedAt 만 갱신되고 테마는 마지막 성공분이 그대로 남는다.
    // 이걸 표시하지 않으면 "갱신: 방금"으로 보여 몇 달 지난 데이터를 최신으로 오인한다.
    renderThemeStaleNotice(grid, data);

    // Render theme cards
    const themes = data.themes || [];
    themes.forEach((theme, i) => {
      grid.appendChild(createThemeCard(theme, i));
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
