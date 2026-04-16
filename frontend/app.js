const S3_DATA_URL = 'https://stock-dashboard-data.s3.ap-northeast-2.amazonaws.com/dashboard_data.json';
const LOCAL_DATA_URL = './dashboard_data.json';

const isProduction = window.location.hostname.includes('github.io')
  || window.location.hostname.includes('stock');
const DATA_URL = isProduction ? S3_DATA_URL : LOCAL_DATA_URL;

const FILTERS = [
  { key: 'all', label: '전체' },
  { key: 'strong', label: '강한 테마' },
  { key: 'broad', label: '확산' },
  { key: 'recurring', label: '연속' },
];

const state = {
  themes: [],
  search: '',
  sort: 'volume',
  filter: 'all',
};

function escapeHTML(value) {
  if (value === undefined || value === null) return '';
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
  return String(value).replace(/[&<>"']/g, char => map[char]);
}

function formatDatetime(isoStr) {
  if (!isoStr) return '--:--';
  const date = new Date(isoStr);
  const days = ['일', '월', '화', '수', '목', '금', '토'];
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  const hh = String(date.getHours()).padStart(2, '0');
  const mi = String(date.getMinutes()).padStart(2, '0');
  return `${mm}.${dd}(${days[date.getDay()]}) ${hh}:${mi}`;
}

function parseAmount(value) {
  if (!value) return 0;
  const text = String(value).replace(/,/g, '');
  const number = parseFloat(text);
  if (Number.isNaN(number)) return 0;
  if (text.includes('조')) return number * 10000;
  if (text.includes('억')) return number;
  if (text.includes('만')) return number / 10000;
  return number;
}

function getChangeClass(rate) {
  if (rate > 0) return 'up';
  if (rate < 0) return 'down';
  return 'flat';
}

function getChangeText(rate) {
  const sign = rate > 0 ? '+' : '';
  return `${sign}${Number(rate || 0).toFixed(2)}%`;
}

function createPriceBar(stock) {
  const rate = Number(stock.changeRate || 0);
  const maxRate = 30;
  const width = Math.max(3, Math.min(50, Math.abs(rate) / maxRate * 50));
  const isUp = rate >= 0;
  const left = isUp ? 50 : 50 - width;

  return `
    <div class="price-bar">
      <div class="price-bar-track"></div>
      <div class="price-bar-fill ${getChangeClass(rate)}" style="left:${left}%;width:${width}%"></div>
      <div class="price-bar-mid"></div>
    </div>
  `;
}

function getSessionLabel(updatedAt) {
  const hour = new Date(updatedAt).getHours();
  if (hour < 9) return `프리마켓 · ${formatDatetime(updatedAt)}`;
  if (hour < 15) return `장중 · ${formatDatetime(updatedAt)}`;
  return `마감 · ${formatDatetime(updatedAt)}`;
}

function getRecurringCount(name, rawData) {
  const textPool = [
    ...(rawData.antwinnerSignals || []).map(item => item.thema || ''),
    ...(rawData.youtubeSignals || []).flatMap(item => item.sectors || []),
    ...(rawData.priceSignalCandidates || []).map(item => item.themeName || ''),
  ].join(' ');

  if (name.includes('보안') && textPool.includes('보안')) return 3;
  if (name.includes('양자') && textPool.includes('양자')) return 3;
  if (name.includes('유리기판') && textPool.includes('유리기판')) return 2;
  if (name.includes('지역화폐') && textPool.includes('지역화폐')) return 2;
  return 1;
}

function buildThemes(rawData) {
  return (rawData.themes || []).map((theme, index) => {
    const stocks = (theme.stocks || [])
      .filter(stock => !(stock.price === 0 && stock.changeRate === 0))
      .sort((a, b) => b.changeRate - a.changeRate)
      .slice(0, 4);

    const avgMove = stocks.reduce((sum, stock) => sum + Number(stock.changeRate || 0), 0) / Math.max(stocks.length, 1);
    const positiveCount = stocks.filter(stock => stock.changeRate > 0).length;
    const breadth = stocks.length ? Math.round((positiveCount / stocks.length) * 100) : 0;
    const recurringDays = getRecurringCount(theme.themeName, rawData);

    return {
      id: `${theme.themeName}-${index}`,
      name: theme.themeName,
      headline: theme.headline,
      totalVolume: theme.totalVolume,
      volumeValue: parseAmount(theme.totalVolume),
      avgMove,
      breadth,
      recurringDays,
      stocks,
    };
  });
}

function createFilterChips() {
  const row = document.getElementById('filter-row');
  row.innerHTML = FILTERS.map(filter => `
    <button type="button" class="filter-chip${state.filter === filter.key ? ' is-active' : ''}" data-filter="${filter.key}">
      ${filter.label}
    </button>
  `).join('');
}

function applyFilter(theme) {
  const query = state.search.trim().toLowerCase();
  const searchable = `${theme.name} ${theme.headline || ''} ${theme.stocks.map(stock => stock.name).join(' ')}`.toLowerCase();
  if (query && !searchable.includes(query)) return false;

  if (state.filter === 'strong') return theme.avgMove >= 12;
  if (state.filter === 'broad') return theme.breadth >= 75;
  if (state.filter === 'recurring') return theme.recurringDays >= 2;
  return true;
}

function sortThemes(themes) {
  const sorted = [...themes];

  sorted.sort((a, b) => {
    if (state.sort === 'move') return b.avgMove - a.avgMove;
    if (state.sort === 'name') return a.name.localeCompare(b.name, 'ko');
    return b.volumeValue - a.volumeValue;
  });

  return sorted;
}

function createThemeCard(theme) {
  return `
    <article class="theme-card">
      <div class="theme-card-header">
        <div class="theme-title-group">
          <h2>${escapeHTML(theme.name)}</h2>
          ${theme.recurringDays >= 2 ? `<span class="streak-badge">${theme.recurringDays}일</span>` : ''}
        </div>
        <div class="theme-volume">${escapeHTML(theme.totalVolume)}</div>
      </div>

      <p class="theme-headline">${escapeHTML(theme.headline || '관련 수급 체크')}</p>

      <div class="theme-meta">
        <span>평균등락 <strong class="${getChangeClass(theme.avgMove)}">${getChangeText(theme.avgMove)}</strong></span>
        <span>확산 <strong>${theme.breadth}%</strong></span>
      </div>

      <div class="stock-list">
        ${theme.stocks.map((stock, index) => `
          <div class="stock-row${index === 0 ? ' is-top' : ''}">
            <div class="stock-main-row">
              <div class="stock-name">${escapeHTML(stock.name)}</div>
              <div class="stock-change ${getChangeClass(stock.changeRate)}">${getChangeText(stock.changeRate)}</div>
              <div class="stock-volume">${escapeHTML(stock.volume || '-')}</div>
            </div>
            ${createPriceBar(stock)}
          </div>
        `).join('')}
      </div>
    </article>
  `;
}

function renderThemes() {
  const list = document.getElementById('theme-list');
  const visibleThemes = sortThemes(state.themes.filter(applyFilter));

  if (!visibleThemes.length) {
    list.innerHTML = `
      <div class="empty-state">
        <p>조건에 맞는 테마가 없습니다.</p>
      </div>
    `;
    return;
  }

  list.innerHTML = visibleThemes.map(createThemeCard).join('');
}

function bindEvents() {
  document.getElementById('search-input').addEventListener('input', event => {
    state.search = event.target.value;
    renderThemes();
  });

  document.getElementById('sort-select').addEventListener('change', event => {
    state.sort = event.target.value;
    renderThemes();
  });

  document.getElementById('filter-row').addEventListener('click', event => {
    const button = event.target.closest('[data-filter]');
    if (!button) return;
    state.filter = button.dataset.filter;
    createFilterChips();
    renderThemes();
  });
}

async function loadAndRender() {
  const loading = document.getElementById('loading-state');
  try {
    const response = await fetch(`${DATA_URL}?t=${Date.now()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const rawData = await response.json();
    state.themes = buildThemes(rawData);
    document.getElementById('session-chip').textContent = getSessionLabel(rawData.updatedAt);
    if (loading) loading.remove();
    renderThemes();
  } catch (error) {
    console.error('Failed to load dashboard data:', error);
    if (loading) loading.remove();
    document.getElementById('theme-list').innerHTML = `
      <div class="empty-state">
        <p>데이터를 불러오지 못했습니다.</p>
      </div>
    `;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  createFilterChips();
  bindEvents();
  loadAndRender();
});
