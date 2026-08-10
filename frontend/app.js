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
 * 당일 레인지 바 (Day's Range) — HTS·블룸버그가 쓰는 표준 표현.
 *
 * 반원 아크는 등락률 하나만 인코딩했는데, 그 값은 행 우측에 숫자로 이미
 * 있어서 정보가 중복됐다. 레인지 바는 "오늘 저가~고가 중 현재가가 어디에
 * 있나"를 보여준다 — 고가 부근 마감인지 밀린 마감인지가 한눈에 읽힌다.
 *
 *   저가 ├───[ 시가 ▓▓▓▓ 현재가 ]───┤ 고가
 *                  ╎ 전일종가
 */
function dayRangeBar(stock) {
  const W = 108, H = 34;
  const x0 = 4, x1 = W - 4, trackY = 14, trackH = 8;
  const mid = (x0 + x1) / 2;
  const half = (x1 - x0) / 2;

  const rate = stock.changeRate || 0;
  const up = rate >= 0;
  const color = up ? '#E53935' : '#1565C0';

  // 한국장 상·하한 ±30% 를 반쪽 꽉 참으로 매핑한다.
  //
  // 예전에는 저가~고가 안에서 현재가 위치를 그렸는데, 그러면 변동폭이 좁은 날
  // 2% 종목도 30% 종목도 똑같이 가득 차 보인다 — 막대 길이가 등락률과 무관해
  // 카드끼리 비교가 안 됐다. 0% 를 가운데 두고 |등락률|/30 만큼 채운다.
  const MAX_RATE = 30;
  const f = Math.min(1, Math.abs(rate) / MAX_RATE);
  const segW = Math.max(1.5, f * half);
  const segX = up ? mid : mid - segW;

  const gid = `rb-${Math.random().toString(36).slice(2, 7)}`;
  const tick = (frac) => {
    const x = mid + frac * half;
    return `<line x1="${x.toFixed(1)}" y1="${trackY - 1.5}" x2="${x.toFixed(1)}" y2="${trackY + trackH + 1.5}" stroke="#DDD3C0" stroke-width="0.8"/>`;
  };

  return `<svg class="bs-range" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">
    <defs>
      <linearGradient id="${gid}" x1="${up ? 0 : 1}" y1="0" x2="${up ? 1 : 0}" y2="0">
        <stop offset="0%" stop-color="${color}" stop-opacity="0.5"/>
        <stop offset="100%" stop-color="${color}" stop-opacity="1"/>
      </linearGradient>
    </defs>
    <rect x="${x0}" y="${trackY}" width="${x1 - x0}" height="${trackH}" rx="4" fill="#F2EADB"/>
    ${tick(-0.5)}${tick(0.5)}
    <rect x="${segX.toFixed(1)}" y="${trackY}" width="${segW.toFixed(1)}" height="${trackH}" rx="${Math.min(4, segW / 2).toFixed(1)}" fill="url(#${gid})"/>
    <line x1="${mid}" y1="${trackY - 3}" x2="${mid}" y2="${trackY + trackH + 3}" stroke="#8C8474" stroke-width="1.2"/>
    <text x="${x0}" y="9" font-size="6.8" fill="#bdb4a2">-30%</text>
    <text x="${mid}" y="9" font-size="6.8" fill="#bdb4a2" text-anchor="middle">0</text>
    <text x="${x1}" y="9" font-size="6.8" fill="#bdb4a2" text-anchor="end">+30%</text>
  </svg>`;
}

/**
 * Theme Card 렌더링 — 매거진 브리핑 (C안)
 * 큰 세리프 타이틀 + 뉴스 한 줄 + 대표종목(레인지 바) + 종목 칩
 */
// 테마 종목을 우리 수급 데이터와 대조한다.
//
// 재료·테마 자체는 무료 서비스와 겹치고, 수동 실시간 중계를 하는 업체와도
// 경쟁이 안 된다. 우리가 더할 수 있는 건 "이 테마에서 수급이 빈 종목은
// 어느 것인가" — 뉴스로 뜬 테마 중 아직 외인·기관이 안 들어온 자리다.
function supplyTagFor(name) {
  const flow = (typeof flowData !== 'undefined' && flowData) ? flowData : null;
  if (!flow || !name) return '';
  const n = String(name).replace(/\s+/g, '');
  const hit = (flow.buyCandidates || []).find(c => String(c.name || '').replace(/\s+/g, '') === n);
  if (hit) return `<span class="ts-tag ts-cand" title="수급 빈집 조건 통과 종목">빈집 · 조건통과</span>`;
  const uni = (flow.universeMetadata || []).find(m => String(m.name || '').replace(/\s+/g, '') === n);
  if (uni) return `<span class="ts-tag ts-uni" title="분석 유니버스에 포함">유니버스</span>`;
  return '';
}

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
        <div class="bs-info">
          <div class="bs-name">${escapeHTML(s.name)}${s.isTop ? '<span class="bs-top">대표</span>' : ''}${supplyTagFor(s.name)}</div>
          <div class="bs-sub">${sub}</div>
        </div>
        ${dayRangeBar(s)}
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
// 테마 트리맵 — "AI 가 이렇게 묶었다" 를 한 장으로 증명      │
// ─────────────────────────────────────────┘
// 테마명과 종목명을 글로 나열하면 "왜 이것들이 한 덩어리인지" 가 안 보인다.
// 군집은 원래 그림으로 증명하는 것이다. 면적=거래대금, 색=등락률로 깔면
// 오늘 돈이 어느 테마에 몰렸는지, 그 안에서 누가 주도주인지가 한눈에 잡힌다.
// 그리고 우리만 할 수 있는 한 겹: 이 지도 위에 '수급 빈집' 종목을 표시한다.
// 테마(재료) × 빈집(수급) 교집합은 다른 서비스가 한 화면에서 못 보여준다.

// "1,890억" / "1.2조" / "58억" → 원 단위 숫자
function parseKrAmount(v) {
  if (v == null) return 0;
  if (typeof v === 'number') return v;
  const s = String(v).replace(/[,\s]/g, '');
  const m = /^(-?[\d.]+)(조|억|만)?/.exec(s);
  if (!m) return 0;
  const n = parseFloat(m[1]);
  if (!isFinite(n)) return 0;
  const unit = { '조': 1e12, '억': 1e8, '만': 1e4 }[m[2]] || 1;
  return n * unit;
}

function fmtEok(won) {
  if (!won) return '-';
  if (won >= 1e12) return `${(won / 1e12).toFixed(1)}조`;
  return `${Math.round(won / 1e8).toLocaleString('ko-KR')}억`;
}

// squarified treemap — 타일이 정사각형에 가깝게 나오도록 배치한다.
// 한 방향으로만 자르면 얇고 긴 띠가 되어 종목명이 안 들어간다.
function squarify(items, x, y, w, h) {
  const out = [];
  const total = items.reduce((a, b) => a + b.value, 0);
  if (!(total > 0) || w <= 0 || h <= 0) return out;

  const list = items.map(it => ({ ...it, area: it.value / total * w * h }));
  let rx = x, ry = y, rw = w, rh = h;

  const worst = (row, len) => {
    if (!row.length || len <= 0) return Infinity;
    const sum = row.reduce((a, b) => a + b.area, 0);
    const mx = Math.max(...row.map(r => r.area));
    const mn = Math.min(...row.map(r => r.area));
    const s2 = sum * sum, l2 = len * len;
    return Math.max(l2 * mx / s2, s2 / (l2 * mn));
  };

  while (list.length) {
    const vertical = rw >= rh;          // 짧은 변을 따라 줄을 채운다
    const len = vertical ? rh : rw;
    const row = [];
    while (list.length) {
      const next = row.concat([list[0]]);
      if (row.length && worst(next, len) > worst(row, len)) break;
      row.push(list.shift());
    }
    const rowSum = row.reduce((a, b) => a + b.area, 0);
    const thick = rowSum / len;
    let off = 0;
    row.forEach(it => {
      const side = it.area / thick;
      out.push(vertical
        ? { ...it, x: rx, y: ry + off, w: thick, h: side }
        : { ...it, x: rx + off, y: ry, w: side, h: thick });
      off += side;
    });
    if (vertical) { rx += thick; rw -= thick; } else { ry += thick; rh -= thick; }
    if (rw <= 0.5 || rh <= 0.5) break;
  }
  return out;
}

// 등락률 → 색. 국내 관례대로 상승 빨강 / 하락 파랑.
// 상한가(+30%)와 -10% 를 양 끝으로 두고 제곱근 곡선이라 중간도 구별된다.
function tmColor(rate) {
  const r = (rate == null || !isFinite(rate)) ? 0 : rate;
  if (Math.abs(r) < 0.35) return '#EDE7DA';
  const t = Math.min(1, Math.sqrt(Math.abs(r) / (r > 0 ? 30 : 10)));
  return r > 0
    ? `rgb(${Math.round(250 - 26 * t)},${Math.round(232 - 174 * t)},${Math.round(230 - 175 * t)})`
    : `rgb(${Math.round(230 - 209 * t)},${Math.round(238 - 137 * t)},${Math.round(250 - 58 * t)})`;
}

function tmTextColor(rate) {
  const r = (rate == null || !isFinite(rate)) ? 0 : rate;
  const t = Math.min(1, Math.sqrt(Math.abs(r) / (r > 0 ? 30 : 10)));
  return t > 0.55 ? '#fff' : '#2a2a2a';
}

// 종목명이 수급 빈집 후보인지 — supplyTagFor 와 같은 대조 규칙을 쓴다.
function isVacancyCandidate(name) {
  const flow = (typeof flowData !== 'undefined' && flowData) ? flowData : null;
  if (!flow || !name) return false;
  const n = String(name).replace(/\s+/g, '');
  return (flow.buyCandidates || []).some(c => String(c.name || '').replace(/\s+/g, '') === n);
}

function buildThemeTreemap(themes) {
  const list = (themes || [])
    .map(t => {
      const stocks = (t.stocks || []).map(s => ({
        code: s.code, name: s.name, rate: s.changeRate,
        value: parseKrAmount(s.volume),
      })).filter(s => s.value > 0);
      const value = stocks.reduce((a, b) => a + b.value, 0) || parseKrAmount(t.totalVolume);
      return { name: t.themeName, value, stocks };
    })
    .filter(t => t.value > 0 && t.stocks.length);
  if (list.length < 2) return '';

  const wide = (typeof window !== 'undefined' && window.innerWidth >= 760);
  const W = wide ? 900 : 380;
  const H = wide ? 400 : 430;   // 모바일은 세로를 더 줘야 타일에 이름이 들어간다
  const HEAD = 17;                       // 테마명 띠 높이
  const groups = squarify(list, 0, 0, W, H);

  let tiles = '';
  let candCount = 0;
  groups.forEach(g => {
    const inner = squarify(g.stocks, g.x + 1, g.y + HEAD, Math.max(0, g.w - 2), Math.max(0, g.h - HEAD - 1));
    tiles += `<rect x="${g.x.toFixed(1)}" y="${g.y.toFixed(1)}" width="${g.w.toFixed(1)}" height="${g.h.toFixed(1)}" fill="#F7F2E7" stroke="#fff" stroke-width="2"/>`;
    tiles += `<text x="${(g.x + 5).toFixed(1)}" y="${(g.y + 12).toFixed(1)}" font-size="11" font-weight="900" fill="#4a4336">${escapeHTML(g.name)}</text>`;
    inner.forEach(s => {
      const isCand = isVacancyCandidate(s.name);
      if (isCand) candCount++;
      // 이름 길이를 고려해야 타일 밖으로 안 넘친다 (한글 1자 ≈ 10px @ font-size 10)
      const label = String(s.name || '').slice(0, 7);
      const showName = s.h > 20 && s.w > label.length * 10 + 6;
      const showRate = showName && s.h > 31 && s.w > 40;
      const rate = (s.rate == null || !isFinite(s.rate)) ? 0 : s.rate;
      tiles += `<g class="tm-tile" data-code="${escapeHTML(s.code || '')}" data-name="${escapeHTML(s.name || '')}">
        <title>${escapeHTML(s.name)} · ${rate >= 0 ? '+' : ''}${rate.toFixed(2)}% · 거래대금 ${fmtEok(s.value)}${isCand ? ' · 수급 빈집 조건통과' : ''}</title>
        <rect x="${s.x.toFixed(1)}" y="${s.y.toFixed(1)}" width="${s.w.toFixed(1)}" height="${s.h.toFixed(1)}"
          fill="${tmColor(rate)}" stroke="${isCand ? '#00695C' : '#fff'}" stroke-width="${isCand ? 2 : 1}"/>
        ${showName ? `<text x="${(s.x + s.w / 2).toFixed(1)}" y="${(s.y + s.h / 2 + (showRate ? -2 : 3)).toFixed(1)}" text-anchor="middle" font-size="10" font-weight="800" fill="${tmTextColor(rate)}">${escapeHTML(label)}</text>` : ''}
        ${showRate ? `<text x="${(s.x + s.w / 2).toFixed(1)}" y="${(s.y + s.h / 2 + 10).toFixed(1)}" text-anchor="middle" font-size="9.5" font-weight="900" fill="${tmTextColor(rate)}">${rate >= 0 ? '+' : ''}${rate.toFixed(1)}%</text>` : ''}
        ${isCand ? `<circle cx="${(s.x + s.w - 5).toFixed(1)}" cy="${(s.y + 5).toFixed(1)}" r="2.6" fill="#00695C"/>` : ''}
      </g>`;
    });
  });

  return `
    <div class="tm-card">
      <div class="tm-head">
        <span class="tm-title">오늘의 테마 지도</span>
        <span class="tm-sub">칸 크기 = 거래대금 · 색 = 등락률</span>
      </div>
      <svg viewBox="0 0 ${W} ${H}" class="tm-svg" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">${tiles}</svg>
      <div class="tm-legend">
        <span class="tm-lg"><i style="background:${tmColor(12)}"></i>상승</span>
        <span class="tm-lg"><i style="background:${tmColor(-6)}"></i>하락</span>
        <span class="tm-lg tm-lg-cand"><i></i>수급 빈집 조건통과${candCount ? ` <b>${candCount}종목</b>` : ''}</span>
      </div>
      ${candCount === 0 ? `<p class="tm-note">오늘은 급등 테마주와 수급 빈집이 겹치는 종목이 없습니다.
        급등 중인 종목은 이미 수급이 들어온 상태라 빈집과 잘 겹치지 않습니다 —
        겹치는 날이 재료와 수급이 동시에 비어 있다가 채워지는 자리입니다.</p>` : ''}
    </div>`;
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

    // 수급 대조 태그를 붙이려면 flow 데이터가 먼저 있어야 한다.
    // 실패해도 테마 자체는 그려야 하므로 조용히 넘어간다.
    if (typeof loadFlow === 'function') {
      try { await loadFlow(); } catch (e) { /* 태그만 생략 */ }
    }

    // Render theme cards
    const themes = data.themes || [];

    // 테마 지도 — 카드 목록보다 먼저. "오늘 돈이 어디로 갔나" 가 첫 화면에 와야 한다.
    const tmHtml = buildThemeTreemap(themes);
    if (tmHtml) {
      const tmWrap = document.createElement('div');
      tmWrap.innerHTML = tmHtml;
      const tmEl = tmWrap.firstElementChild;
      if (tmEl) {
        grid.appendChild(tmEl);
        tmEl.querySelectorAll('.tm-tile').forEach(el => {
          el.addEventListener('click', () => {
            const code = el.dataset.code, name = el.dataset.name;
            if (code && typeof openStockChart === 'function') openStockChart(code, name);
          });
        });
      }
    }

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
