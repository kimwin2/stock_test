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

// ── 면적 정규화 ────────────────────────────────────────────────
// 거래대금 실값을 그대로 면적에 쓰면 1등이 나머지를 다 먹는다. 실측(2026-08-10):
// 테마 그룹은 41억 ~ 1,639억으로 40배, 한 테마 안에서도 17배까지 벌어졌다.
// 그러면 작은 칸은 몇 px 이 되어 종목 이름이 아예 안 들어가고, 지도가
// "1등이 크다" 는 사실 하나만 반복한다 — 그건 이미 목록으로 알 수 있다.
//
// 두 단계로 누른다:
//   1) 제곱근 — 면적 ∝ √거래대금 (한 변 ∝ 거래대금^0.25)
//   2) 스프레드 상한 — 그래도 남는 격차를 최대 5배로 자른다
// 정확한 거래대금은 툴팁과 큰 칸의 보조 라벨에 그대로 남긴다.
// 그래서 캡션도 '= 거래대금' 이 아니라 '≈' 로 적는다. 면적을 손봤으면
// 손봤다고 말해야 한다.
// 면적 지수. 0.5(제곱근)에서 0.25 로 낮춰 큰 칸과 작은 칸의 차이를 절반으로
// 줄인다. 거래대금은 종목 간 수십 배씩 벌어져서 원값 그대로 면적에 쓰면
// 한 종목이 화면을 먹고 나머지는 이름도 못 넣는 실오라기가 된다.
// 순위는 여전히 보이되 읽을 수 있는 크기를 확보하는 쪽을 택했다.
const TM_GAMMA = 0.25;
const TM_MAX_SPREAD = 2.5;   // 한 그룹 안 최대/최소 면적 배율 상한 (5 → 2.5)

function tmNormalize(items) {
  const scaled = items.map(it => ({ ...it, raw: it.value, value: Math.pow(Math.max(it.value, 1), TM_GAMMA) }));
  const max = Math.max(...scaled.map(i => i.value));
  const floor = max / TM_MAX_SPREAD;
  return scaled.map(it => ({ ...it, value: Math.max(it.value, floor) }));
}

// 칸 너비에 맞춰 이름을 자른다. 한글 글자폭 ≈ font-size.
// 2글자도 못 넣을 만큼 좁을 때만 포기한다.
function tmFitLabel(name, w, fontPx) {
  const per = fontPx * 1.0;
  const maxChars = Math.floor((w - 5) / per);
  const s = String(name || '');
  if (maxChars < 2) return '';
  if (s.length <= maxChars) return s;
  return s.slice(0, Math.max(1, maxChars - 1)) + '…';
}

// 칸에 이름을 넣는 가장 큰 글자크기를 찾는다.
// 글자크기를 칸 높이로만 정하면 좁고 높은 칸에서 '와…' 처럼 잘려 이름 구실을
// 못 한다. 이름이 통째로 들어가는 크기를 먼저 찾고, 그래도 안 되면 최소
// 크기에서 자른다 — 잘린 이름이라도 있는 편이 빈 칸보다 낫다.
const TM_FONT_MAX = 12.5;
const TM_FONT_MIN = 8;

function tmLabelFor(name, w, h) {
  const s = String(name || '');
  if (!s || h < 9 || w < 16) return { text: '', font: TM_FONT_MIN };
  const cap = Math.min(TM_FONT_MAX, h * 0.44);
  for (let f = cap; f >= TM_FONT_MIN; f -= 0.5) {
    if (s.length * f <= w - 5) return { text: s, font: f };
  }
  return { text: tmFitLabel(s, w, TM_FONT_MIN), font: TM_FONT_MIN };
}

// 등락률 → 색. 국내 관례대로 상승 빨강 / 하락 파랑.
// 상한가(+30%)와 -10% 를 양 끝으로 두고 제곱근 곡선이라 중간도 구별된다.
// [이전 결함] 도메인을 상한가(+30%)까지 잡았더니 오늘처럼 테마주가 전부
// +5~20% 인 날에는 모든 타일이 비슷한 중간 분홍으로 뭉개져 색이 정보를 주지
// 못했다. Finviz 는 일간 heatmap 을 ±3% 로 자른다 — 대부분의 날 색이 끝까지
// 벌어지게 하려는 것이다. 급등 테마를 다루므로 ±9% 로 잡고 넘으면 포화시킨다.
// 정확한 수치는 라벨이 주므로 색은 순위만 보여주면 된다.
// [두 번 고친 곳] 처음엔 도메인을 상한가(+30%)까지 잡아 모든 타일이 비슷한
// 중간 분홍으로 뭉갰다. 그래서 ±9% 로 좁혔더니 이번엔 24칸 중 색이 4종류만
// 나왔다 — 테마 지도에 오르는 종목은 애초에 급등주만 모여 있어(오늘 +5~30%)
// 고정 도메인이면 대부분 포화된다. Finviz 가 ±3% 를 쓸 수 있는 건 시장 전체를
// 담아 오르내림이 섞이기 때문이다.
// → 그날 화면에 실제로 오른 값의 분포에 맞춰 스케일을 잡는다. 어떤 날이든
//   색이 끝까지 벌어지고, 이웃 타일이 구별된다. 정확한 수치는 라벨이 준다.
let TM_SCALE = { pos: 9, neg: 9, posMin: 0, negMin: 0 };
function tmSetScale(rates) {
  const pos = rates.filter(r => r > 0.15).sort((a, b) => a - b);
  const neg = rates.filter(r => r < -0.15).map(Math.abs).sort((a, b) => a - b);
  const lo = (arr) => arr.length ? arr[Math.floor(arr.length * 0.05)] : 0;
  const hi = (arr) => arr.length ? arr[Math.floor(arr.length * 0.95)] : 1;
  TM_SCALE = {
    posMin: lo(pos), pos: Math.max(hi(pos), lo(pos) + 0.5),
    negMin: lo(neg), neg: Math.max(hi(neg), lo(neg) + 0.5),
  };
}
function tmLerp(a, b, t) { return Math.round(a + (b - a) * t); }
function tmNorm(rate) {
  const r = Math.abs(rate);
  const [mn, mx] = rate > 0 ? [TM_SCALE.posMin, TM_SCALE.pos] : [TM_SCALE.negMin, TM_SCALE.neg];
  if (!(mx > mn)) return 0.6;
  // 0.16~1 로 눌러 가장 옅은 칸도 색으로 읽히게 한다
  return 0.16 + 0.84 * Math.max(0, Math.min(1, (r - mn) / (mx - mn)));
}
function tmColor(rate) {
  const r = (rate == null || !isFinite(rate)) ? 0 : rate;
  if (Math.abs(r) < 0.15) return '#EEEFF2';          // 보합은 무채색
  const t = tmNorm(r);
  const [c0, c1] = r > 0
    ? [[252, 231, 231], [168, 28, 34]]
    : [[231, 238, 248], [16, 72, 150]];
  return `rgb(${tmLerp(c0[0], c1[0], t)},${tmLerp(c0[1], c1[1], t)},${tmLerp(c0[2], c1[2], t)})`;
}

function tmTextColor(rate) {
  const r = (rate == null || !isFinite(rate)) ? 0 : rate;
  if (Math.abs(r) < 0.15) return '#5B606B';
  return tmNorm(r) > 0.52 ? 'rgba(255,255,255,0.97)' : '#2A2E36';
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
      // 같은 종목이 두 번 들어오면 면적이 두 배로 잡혀 테마 크기까지 틀어진다.
      // 백엔드에서도 막지만, 이미 S3 에 올라간 데이터에는 남아 있으므로 여기서도 막는다.
      const seenCode = new Set();
      const stocks = (t.stocks || []).map(s => ({
        code: s.code, name: s.name, rate: s.changeRate,
        value: parseKrAmount(s.volume),
      })).filter(s => {
        if (!(s.value > 0)) return false;
        const key = String(s.code || s.name || '').trim();
        if (!key || seenCode.has(key)) return false;
        seenCode.add(key);
        return true;
      });
      const value = stocks.reduce((a, b) => a + b.value, 0) || parseKrAmount(t.totalVolume);
      return { name: t.themeName, value, stocks };
    })
    .filter(t => t.value > 0 && t.stocks.length);
  if (list.length < 2) return '';

  // 색 스케일은 오늘 화면에 실제로 오르는 값들로 정한다.
  tmSetScale(list.flatMap(t => t.stocks.map(x => (x.rate == null || !isFinite(x.rate)) ? 0 : x.rate)));

  const wide = (typeof window !== 'undefined' && window.innerWidth >= 760);
  const W = wide ? 900 : 380;
  const H = wide ? 400 : 470;   // 모바일은 세로를 더 줘야 타일에 이름이 들어간다
  const HEAD = 17;                       // 테마명 띠 높이
  const groups = squarify(tmNormalize(list), 0, 0, W, H);

  let tiles = '';
  let candCount = 0;
  groups.forEach(g => {
    const inner = squarify(tmNormalize(g.stocks), g.x + 1, g.y + HEAD, Math.max(0, g.w - 2), Math.max(0, g.h - HEAD - 1));
    tiles += `<rect x="${g.x.toFixed(1)}" y="${g.y.toFixed(1)}" width="${g.w.toFixed(1)}" height="${g.h.toFixed(1)}" rx="3" fill="#F6F7F9"/>`;
    const gLabel = tmFitLabel(g.name, g.w - 4, 9.5);
    tiles += `<title>${escapeHTML(g.name)} · 거래대금 ${fmtEok(g.raw)}</title>`;
    tiles += `<text x="${(g.x + 5).toFixed(1)}" y="${(g.y + 12).toFixed(1)}" font-size="9.5" font-weight="700" letter-spacing="0.02em" fill="#6B7280">${escapeHTML(gLabel)}</text>`;
    inner.forEach(s => {
      const isCand = isVacancyCandidate(s.name);
      if (isCand) candCount++;
      const rate = (s.rate == null || !isFinite(s.rate)) ? 0 : s.rate;
      // 칸에 맞춰 글자를 줄인다. 예전엔 고정 10px 라 조금만 좁아도 이름이
      // 통째로 사라졌다 — 이름 없는 칸은 지도에서 아무 의미가 없다.
      const { text: label, font } = tmLabelFor(s.name, s.w, s.h);
      const showRate = label && s.h > font * 2.6 && s.w > 34;
      tiles += `<g class="tm-tile" data-code="${escapeHTML(s.code || '')}" data-name="${escapeHTML(s.name || '')}">
        <title>${escapeHTML(s.name)} · ${rate >= 0 ? '+' : ''}${rate.toFixed(2)}% · 거래대금 ${fmtEok(s.raw)}${isCand ? ' · 수급 빈집 조건통과' : ''}</title>
        <rect x="${(s.x + 1).toFixed(1)}" y="${(s.y + 1).toFixed(1)}"
          width="${Math.max(0, s.w - 2).toFixed(1)}" height="${Math.max(0, s.h - 2).toFixed(1)}" rx="2"
          fill="${tmColor(rate)}"${isCand ? ' stroke="#0F766E" stroke-width="1.5"' : ''}/>
        ${label ? `<text x="${(s.x + s.w / 2).toFixed(1)}" y="${(s.y + s.h / 2 + (showRate ? -1.5 : font * 0.36)).toFixed(1)}" text-anchor="middle" font-size="${font.toFixed(1)}" font-weight="600" letter-spacing="-0.02em" fill="${tmTextColor(rate)}">${escapeHTML(label)}</text>` : ''}
        ${showRate ? `<text x="${(s.x + s.w / 2).toFixed(1)}" y="${(s.y + s.h / 2 + font + 1).toFixed(1)}" text-anchor="middle" font-size="${(font * 0.86).toFixed(1)}" font-weight="500" opacity="0.88" fill="${tmTextColor(rate)}">${rate >= 0 ? '+' : ''}${rate.toFixed(1)}%</text>` : ''}
        ${isCand ? `<circle cx="${(s.x + s.w - 5).toFixed(1)}" cy="${(s.y + 5).toFixed(1)}" r="2.6" fill="#0F766E"/>` : ''}
      </g>`;
    });
  });

  return `
    <div class="tm-card">
      <div class="tm-head">
        <span class="tm-title">오늘의 테마 지도</span>
        <span class="tm-sub">칸 크기 ≈ 거래대금(제곱근 보정) · 색 = 등락률</span>
      </div>
      <svg viewBox="0 0 ${W} ${H}" class="tm-svg" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">${tiles}</svg>
      <div class="tm-legend">
        <span class="tm-lg"><i style="background:${tmColor(6)}"></i>상승</span>
        <span class="tm-lg"><i style="background:${tmColor(-4)}"></i>하락</span>
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
