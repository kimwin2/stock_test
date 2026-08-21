/**
 * 오늘의 시황 탭 — 하루 시장을 한 화면으로 압축
 *
 * 자체 시그널(공포·탐욕, 주도섹터, 수급, 빈집) 변화 + DART 공시 이벤트를
 * 백엔드(briefing/generator.py)가 서술형으로 정리한 것을 표시한다.
 *
 * 데이터: flow_dashboard.json 의 briefing 키 (flow.js 의 loadFlow 재사용).
 * briefing 이 아직 없으면(구버전 Lambda 배포 상태) briefing_sample.json
 * 으로 UI 미리보기를 보여준다 — '샘플' 배지로 구분.
 */

let briefingLoaded = false;
let briefingLoadPromise = null;

const BRIEFING_SAMPLE_URL = './briefing_sample.json';

function bEscape(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[c]);
}

function bFmtDate(yyyymmdd) {
  const s = String(yyyymmdd || '');
  if (s.length !== 8) return s;
  return `${s.slice(4, 6)}/${s.slice(6, 8)}`;
}

// ─────────────────────────────────────────┐
// 요약 스탯 칩                              │
// ─────────────────────────────────────────┘
function buildBriefingStats(facts) {
  if (!facts) return '';
  const fg = facts.fearGreed || {};
  const sectors = (facts.leadingSectors || {}).now || [];

  const chips = [];
  if (fg.kospi != null) {
    const delta = fg.kospiDelta;
    const deltaHtml = (delta != null && Math.abs(delta) >= 0.05)
      ? `<small class="${delta > 0 ? 'up' : 'down'}">${delta > 0 ? '▲' : '▼'}${Math.abs(delta).toFixed(1)}</small>`
      : '';
    chips.push(`<div class="brief-chip"><span class="brief-chip-label">코스피 공포·탐욕</span><span class="brief-chip-value">${fg.kospi}${deltaHtml}</span></div>`);
  }
  if (sectors.length) {
    chips.push(`<div class="brief-chip"><span class="brief-chip-label">주도 1위</span><span class="brief-chip-value">${bEscape(sectors[0])}</span></div>`);
  }
  if (facts.exitSignalCount != null) {
    chips.push(`<div class="brief-chip"><span class="brief-chip-label">매도 시그널</span><span class="brief-chip-value">${facts.exitSignalCount}건</span></div>`);
  }
  if (!chips.length) return '';
  return `<div class="brief-chips">${chips.join('')}</div>`;
}

// ─────────────────────────────────────────┐
// 시장 온도계 — 공포↔탐욕 가로 게이지        │
// ─────────────────────────────────────────┘
// 숫자 하나(38.7)만 보면 그게 높은 건지 낮은 건지 모른다. 구간을 색으로
// 깔고 바늘을 꽂아야 "지금 어디쯤"이 0.5초에 읽힌다.
const FG_ZONES = [
  { to: 25, label: '극단적 공포', color: '#1565C0' },
  { to: 45, label: '공포', color: '#4E8FCB' },
  { to: 55, label: '중립', color: '#C9B896' },
  { to: 75, label: '탐욕', color: '#E58A3C' },
  { to: 100, label: '극단적 탐욕', color: '#D2402F' },
];

function fgZoneLabel(v) {
  for (const z of FG_ZONES) if (v <= z.to) return z;
  return FG_ZONES[FG_ZONES.length - 1];
}

// 백엔드가 내려주는 zone 은 '레벨'이 아니라 '오실레이터(모멘텀)' 기준이다.
// 이 둘은 다른 축이라 섞으면 정반대 결론이 나온다 — 실측(2026-08-07):
//   레벨 38.7 만 보면 '공포', 오실레이터 +0.01 로 보면 '강세'(과열 직전).
//   레퍼런스도 같은 날 "과열권 진입"으로 읽고 현금을 7%→19% 로 올렸다.
// 즉 방향을 말해주는 건 오실레이터다. 백엔드 판정을 그대로 쓴다.
const OSC_ZONE_COLORS = {
  '과열': '#D2402F', '강세': '#E58A3C', '중립': '#C9B896',
  '약세': '#4E8FCB', '공포': '#1565C0',
};

function zoneOf(marketNode, fgValue) {
  const z = (marketNode || {}).zone;
  if (z && OSC_ZONE_COLORS[z]) return { label: z, color: OSC_ZONE_COLORS[z], byOsc: true };
  return { ...fgZoneLabel(fgValue), byOsc: false };   // 구버전 페이로드 폴백
}

// ─────────────────────────────────────────┐
// 용어 한 줄 설명                            │
// ─────────────────────────────────────────┘
// 숫자만 크게 띄워도 뜻을 모르면 안 읽힌다. 지표마다 "이게 무엇인지"를
// 한 줄로 붙인다. 판단을 대신하지 않고 정의만 말한다.
// [정보 설계] 예전에는 이 설명을 항상 펼쳐진 문단으로 깔았다. 카드마다
// 70~150자짜리 문단이 붙으니 화면이 '줄글 나열' 로 읽히고, 정작 숫자가
// 눈에 안 들어왔다 (모바일 기준 탭 전체 높이 2,127px).
// 뜻을 모르는 사람에겐 여전히 필요한 정보라 지우지 않고, 기본은 접어두고
// 필요한 사람만 펼치게 한다. 처음 보는 사람도 한 번 열면 되는 성격의 글이다.
function whatIs(text) {
  return `<details class="what-is"><summary>이게 무슨 뜻인가요?</summary><p>${bEscape(text)}</p></details>`;
}

// 오늘 시장을 한 문장으로. 구간 이름을 가장 크게 보여준다 — '38.7'만으로는
// 높은 건지 낮은 건지 알 수 없기 때문이다.
function buildMarketVerdict(fg, sentiment, flow) {
  const v = fg.kospi;
  if (v == null) return '';
  const zone = zoneOf(sentiment.kospi, v);
  const osc = (sentiment.kospi || {}).oscillator;
  const d = fg.kospiDelta;
  const move = (d == null || Math.abs(d) < 0.05) ? '전일과 비슷'
    : (d > 0 ? `전일 대비 ${d.toFixed(1)} 상승` : `전일 대비 ${Math.abs(d).toFixed(1)} 하락`);
  const crowding = (flow || {}).crowding || {};
  const safety = ((sentiment.buySafety || {}).kospi) || {};
  const nCand = ((flow || {}).buyCandidates || []).length;
  const nExit = ((flow || {}).exitSignals || []).length;
  // 칩 한 칸에 긴 섹터명 3개를 넣으면 3줄로 접혀 히어로가 무너진다. 2개까지.
  const sectorsAll = ((flow || {}).leadingSectorLabels) || [];
  const sectors = sectorsAll.slice(0, 2);

  // 아침에 필요한 사실을 한 줄에 모은다. 칩이 둘뿐이면 화면이 비어 보이고,
  // 정작 "오늘 뭘 봐야 하나" 로 이어지지 않는다.
  const chips = [];
  if (safety.stageLabel) {
    chips.push(['지수 국면', bEscape(safety.stageLabel),
      `${safety.stageIndex ?? '-'}/${safety.totalStages ?? 5}단계`]);
  }
  // 쏠림은 '확산/쏠림' 같은 내부 라벨 대신 사용자가 쓰는 말로 — 이 탭의 결론이다.
  const crowdHist = (crowding.history || []).filter(h => h && h.crowding != null).map(h => h.crowding);
  if (crowdHist.length >= 20) {
    const p = pctRank(crowdHist, crowdHist[crowdHist.length - 1]);
    chips.push(['장 난이도', crowdBandOf(p).label, `쏠림 하위 ${p}%`]);
  } else if (crowding.signal) {
    chips.push(['업종 쏠림', crowding.signal,
      crowding.latest != null ? `지수 ${crowding.latest.toFixed(1)}` : '']);
  }
  if (sectors.length) chips.push(['주도 업종', sectors.join(' · '),
    sectorsAll.length > sectors.length ? `외 ${sectorsAll.length - sectors.length}개` : `상위 ${sectors.length}`]);
  if (nCand) chips.push(['조건 통과', `${nCand}종목`, nExit ? `이탈 신호 ${nExit}` : '']);

  return `
    <div class="verdict">
      <div class="verdict-zone" style="color:${zone.color}">${zone.label}</div>
      <div class="verdict-num">
        <strong style="color:${zone.color}">${v.toFixed(1)}</strong>
        <span>공포·탐욕 레벨 /100 · ${bEscape(move)}${
          zone.byOsc && osc != null ? ` · 방향 ${osc >= 0 ? '+' : ''}${osc.toFixed(3)}` : ''}</span>
        ${(sentiment.kospi || {}).close != null
          ? `<span class="verdict-idx">코스피 ${Number(sentiment.kospi.close).toLocaleString('ko-KR')}` +
            `${(sentiment.kosdaq || {}).close != null ? ` · 코스닥 ${Number(sentiment.kosdaq.close).toLocaleString('ko-KR')}` : ''}</span>`
          : ''}
      </div>
      ${chips.length ? `<div class="verdict-chips">${chips.map(([k, val, sub]) => `
        <div class="vc-item">
          <div class="vc-key">${bEscape(k)}</div>
          <div class="vc-val">${bEscape(val)}</div>
          ${sub ? `<div class="vc-sub">${bEscape(sub)}</div>` : ''}
        </div>`).join('')}</div>` : ''}
    </div>`;
}

// ─────────────────────────────────────────┐
// 업종 쏠림 — 오늘 장의 '난이도'             │
// ─────────────────────────────────────────┘
// 이 탭이 수급·종목 탭과 갈라지는 지점. 저쪽은 "무엇을 살까", 여기는
// "지금이 사도 되는 장인가" 다. 종목을 아무리 잘 골라도 장 난이도를 모르면
// 같은 종목이 어떤 날엔 되고 어떤 날엔 안 된다.
//
// 방향 정의 (참고 자료 40일치에서 반복 확인된 것):
//   쏠림 높음/상승  = 일부 업종만 살아남음   → 매매하기 **어려운** 장
//   쏠림 낮음/하락  = 업종 간 수익률 편차 축소 → 매매하기 **편한** 장 (순환매)
// 직관과 반대로 느껴질 수 있어 화면에 방향을 명시한다.
//
// 절대 임계값을 박지 않는 이유: 우리 지수와 참고 자료의 지수는 스케일이
// 다르다(우리 13~80, 저쪽 -0.3~0.3). 같은 숫자를 옮겨 적으면 틀린다.
// 대신 **자기 이력 대비 백분위**로 판정한다 — 참고 자료도 "0.3 넘으면
// 90% 구간" 이라며 백분위로 말한다. 스케일이 달라도 백분위는 옮겨진다.
const CROWD_BANDS = [
  { from: 90, label: '극단 쏠림', tone: 'x', desc: '소수 업종 독주 — 되돌림(순환매)이 나오는 자리' },
  { from: 70, label: '어려운 장', tone: 'hard', desc: '일부 업종만 살아남는 구간' },
  { from: 30, label: '보통', tone: 'mid', desc: '주도업종 위주로 흐르는 구간' },
  { from: 0, label: '편한 장', tone: 'easy', desc: '업종 간 편차가 좁아 종목 고르기가 수월한 구간' },
];

function crowdBandOf(pct) {
  for (const b of CROWD_BANDS) if (pct >= b.from) return b;
  return CROWD_BANDS[CROWD_BANDS.length - 1];
}

function pctRank(values, v) {
  if (!values.length) return null;
  const below = values.filter(x => x <= v).length;
  return Math.round(below / values.length * 100);
}

// viewW: 이 차트가 실제로 차지할 CSS 픽셀 폭.
//
// 고정 viewBox 를 width:100% 로 늘리면 SVG 안 글자가 폭에 정비례해 커지거나
// 줄어든다. 폰에서는 폭 범위가 좁아 눈에 안 띄지만 폴드는 280~884px 를
// 오간다 — 실측으로 축 글자가 접힘 3.4px / 펼침 13.2px 이었다(4배).
// viewBox 폭을 실제 폭에 맞추면 배율이 1 근처로 고정돼 어디서나 같은
// 크기로 읽힌다.
function buildCrowdingChart(flow, viewW) {
  const cr = (flow || {}).crowding || {};
  const hist = (cr.history || []).filter(h => h && h.crowding != null);
  if (hist.length < 20) return '';

  const vals = hist.map(h => h.crowding);
  const last = vals[vals.length - 1];
  const pct = pctRank(vals, last);
  const band = crowdBandOf(pct);

  // 방향이 수준보다 중요하다. 참고 자료는 "꺾이면 편해짐 / 하단에서 올라오면
  // 어려워짐" 처럼 언제나 방향으로 말한다. 3거래일 창 — 5일로 잡으면 되돌아선
  // 것을 놓친다(실측 2026-08-10: 이틀 만에 13.5→16.2). 백엔드 _crowding_state 와 동일.
  const back = vals[Math.max(0, vals.length - 4)];
  const diff = last - back;
  const span = Math.max(...vals) - Math.min(...vals) || 1;
  const rising = diff > span * 0.02;
  const falling = diff < -span * 0.02;
  const dirLabel = rising ? '올라가는 중' : (falling ? '내려가는 중' : '옆걸음');
  const dirMark = rising ? '▲' : (falling ? '▼' : '▬');
  let verdict;
  if (rising && pct < 30) verdict = '바닥에서 올라오는 중 — 지금부터 어려워질 수 있는 자리';
  else if (rising) verdict = '쏠림이 강해지는 중 — 살아남는 업종이 줄어드는 흐름';
  else if (falling && pct >= 70) verdict = '고점에서 꺾이는 중 — 순환매로 풀리는 자리';
  else if (falling) verdict = '쏠림이 풀리는 중 — 업종 간 편차가 좁아지는 흐름';
  else verdict = '큰 변화 없이 이어지는 중';

  // ── SVG ───────────────────────────────────────────────────────────
  // [2026-08-19 재작업] 예전에는 백분위 4구간을 **베이지·살구색 가로 띠**로
  // 깔고 그 위에 얇은 검정 선을 그렸다. 문제:
  //   · 띠 4개가 화면 면적의 대부분을 차지하는데 어느 띠가 무슨 뜻인지 라벨이
  //     없다. 색이 정보를 주지 않으면서 시선만 먹는다.
  //   · 베이지 계열이 겹치면 채도가 낮은 진흙색으로 읽힌다(구형 엑셀 차트 인상).
  //   · 정작 읽어야 할 선이 배경 대비가 낮아 가장 안 보인다.
  // → 띠를 걷어내고 **경계선 두 개(30%·70%)만 점선**으로 남긴다. 선은 굵게,
  //   아래는 그라데이션 면적. 마지막 값은 우측 pill 로 — HTS 관례다.
  const W = Math.round(Math.max(200, Math.min(760, viewW || 640)));
  const H = Math.round(Math.max(132, Math.min(196, W * 0.46)));
  const PL = 6, PR = W >= 400 ? 52 : 42, PT = 12, PB = 22;
  const iw = W - PL - PR, ih = H - PT - PB;
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.08 || 1;
  const yLo = lo - pad, yHi = hi + pad;
  const X = i => PL + (i / (vals.length - 1)) * iw;
  const Y = v => PT + (1 - (v - yLo) / (yHi - yLo)) * ih;

  const sorted = [...vals].sort((a, b) => a - b);
  const qv = p2 => sorted[Math.min(sorted.length - 1, Math.floor(p2 / 100 * sorted.length))];

  // 색은 현재 구간 하나만 쓴다. 구간이 바뀌면 차트 전체 톤이 바뀌어,
  // 숫자를 읽기 전에 "지금 어느 쪽인지"가 먼저 전달된다.
  const TONE = { easy: '#1B8A5A', mid: '#5B6472', hard: '#C2610C', x: '#B01F26' };
  const tone = TONE[band.tone] || TONE.mid;
  const gid = 'cg' + Math.random().toString(36).slice(2, 7);

  const line = vals.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join('');
  const area = `${line}L${X(vals.length - 1).toFixed(1)},${(PT + ih).toFixed(1)}L${X(0).toFixed(1)},${(PT + ih).toFixed(1)}Z`;
  const cx = X(vals.length - 1), cy = Y(last);

  // 기준선 — 상위 30%(어려움 시작) / 하위 30%(편함 시작) 두 개만.
  const guides = [[70, '어려움'], [30, '편함']].map(([q, lab]) => {
    const y = Y(qv(q));
    return `<line x1="${PL}" y1="${y.toFixed(1)}" x2="${(PL + iw).toFixed(1)}" y2="${y.toFixed(1)}" class="cc-guide"/>` +
           `<text x="${(PL + 3)}" y="${(y - 4).toFixed(1)}" class="cc-guide-lab">${lab}</text>`;
  }).join('');

  // x 축 — 월이 바뀌는 지점만
  let ticks = '', prevMonth = '';
  hist.forEach((h, i) => {
    const m = String(h.date || '').slice(5, 7);
    if (m && m !== prevMonth) {
      prevMonth = m;
      if (i > 2) ticks += `<text x="${X(i).toFixed(1)}" y="${H - 7}" class="cc-tick">${parseInt(m, 10)}월</text>`;
    }
  });

  // 현재값 pill — 우측 축에 붙인다
  const label = last.toFixed(1);
  const pw = Math.max(30, label.length * 7.4 + 12), ph = 19;
  const px = Math.min(W - pw - 2, cx + 8);
  const pill =
    `<line x1="${cx.toFixed(1)}" y1="${cy.toFixed(1)}" x2="${px.toFixed(1)}" y2="${cy.toFixed(1)}" class="cc-lead" stroke="${tone}"/>` +
    `<rect x="${px.toFixed(1)}" y="${(cy - ph / 2).toFixed(1)}" width="${pw}" height="${ph}" rx="4" fill="${tone}"/>` +
    `<text x="${(px + pw / 2).toFixed(1)}" y="${(cy + 4.6).toFixed(1)}" class="cc-now">${label}</text>`;

  return `
    <div class="cc-card">
      <div class="cc-verdict">
        <span class="cc-dir cc-dir-${rising ? 'up' : (falling ? 'down' : 'flat')}">${dirMark} ${dirLabel}</span>
        <span class="cc-verdict-text">${bEscape(verdict)}</span>
      </div>
      <svg viewBox="0 0 ${W} ${H}" class="cc-svg" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg"
           role="img" aria-label="업종 쏠림 추이, 현재 ${label}, 6개월 분포 하위 ${pct}%">
        <defs>
          <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${tone}" stop-opacity="0.20"/>
            <stop offset="100%" stop-color="${tone}" stop-opacity="0"/>
          </linearGradient>
        </defs>
        ${guides}
        <path d="${area}" fill="url(#${gid})"/>
        <path d="${line}" class="cc-line" stroke="${tone}"/>
        <circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="6" fill="${tone}" opacity="0.18"/>
        <circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="3" fill="${tone}"/>
        ${pill}
        ${ticks}
      </svg>
      <div class="cc-scale">
        <span class="cc-axis">↑ 어려운 장</span>
        <span class="cc-pct">6개월 분포 하위 <b>${pct}%</b></span>
        <span class="cc-axis">↓ 편한 장</span>
      </div>
      ${whatIs('업종 쏠림은 업종별 6개월 수익률이 얼마나 벌어져 있는지를 하나의 수로 만든 값입니다. ' +
        '높을수록 소수 업종만 오르고 나머지는 눌려 종목 고르기가 어려워지고, 낮을수록 업종 간 편차가 좁아 순환매가 돕니다. ' +
        '지수의 절대값은 산출 방식마다 달라, 여기서는 최근 6개월 자기 이력 안에서의 위치(백분위)로 구간을 나눕니다.')}
    </div>`;
}


// ─────────────────────────────────────────┐
// 오늘의 재료 — 테마 탭과 이어지는 다리        │
// ─────────────────────────────────────────┘
// '오늘' 탭이 장 상태만 말하면 반쪽이다. 장이 왜 그렇게 움직였는지는 재료에
// 있다. 다만 테마 탭을 여기 복제하지는 않는다 — 상위 3개만, 수급 빈집과
// 겹치는지만 붙여 "재료와 수급이 만나는 자리"가 있는지 한 줄로 답한다.
let themeDataCache = null;

async function loadThemeSummary() {
  if (themeDataCache !== null) return themeDataCache;
  try {
    const url = (typeof DATA_URL !== 'undefined') ? DATA_URL : './dashboard_data.json';
    const resp = await fetch(url + '?t=' + Date.now());
    themeDataCache = resp.ok ? await resp.json() : false;
  } catch (e) {
    themeDataCache = false;
  }
  return themeDataCache;
}

function buildThemeBridge(themeData, flow) {
  const themes = (themeData || {}).themes || [];
  if (!themes.length) return '';
  const candNames = new Set(((flow || {}).buyCandidates || [])
    .map(c => String(c.name || '').replace(/\s+/g, '')));

  const rows = themes.slice(0, 4).map(t => {
    const stocks = (t.stocks || []).slice(0, 4);
    const hits = stocks.filter(s => candNames.has(String(s.name || '').replace(/\s+/g, '')));
    const lead = stocks.map(s => {
      const on = candNames.has(String(s.name || '').replace(/\s+/g, ''));
      return `<span class="tb-stock${on ? ' tb-hit' : ''}">${bEscape(s.name)}` +
             `${s.changeRate != null ? `<em>${s.changeRate >= 0 ? '+' : ''}${Number(s.changeRate).toFixed(1)}%</em>` : ''}</span>`;
    }).join('');
    return `
      <div class="tb-row">
        <div class="tb-name">${bEscape(t.themeName)}${hits.length ? `<b class="tb-badge">수급 겹침 ${hits.length}</b>` : ''}</div>
        <div class="tb-stocks">${lead}</div>
      </div>`;
  }).join('');

  const total = themes.slice(0, 4).reduce((n, t) => n + (t.stocks || []).slice(0, 4)
    .filter(s => candNames.has(String(s.name || '').replace(/\s+/g, ''))).length, 0);

  return `
    <div class="flow-card tb-card">
      <div class="card-header">
        <span class="card-theme-name">오늘 움직인 재료</span>
        <span class="card-volume">${total ? `수급 겹침 ${total}종목` : '수급 겹침 없음'}</span>
      </div>
      <div class="tb-body">${rows}</div>
      <p class="tb-note">${total
        ? '굵게 표시된 종목은 재료가 돌면서 수급도 아직 비어 있는 자리입니다.'
        : '오늘은 급등 재료와 수급 빈집이 겹치는 종목이 없습니다. 급등 중인 종목은 이미 수급이 들어와 있어 잘 겹치지 않습니다.'}</p>
      <button class="tb-more" type="button">재료·테마 탭에서 전체 보기</button>
    </div>`;
}

// 점수 근거 문자열은 디버그용 raw 값이 섞여 있다 ("빈집 osc=-0.00188 pct=30.3").
// 사용자에게는 뜻이 통하는 것만, 가점이 큰 순으로 두 개까지 보여준다.
// 숫자 꼬리(+40)와 osc/pct 같은 내부 표기는 떼어낸다.
function pickReasons(reasons) {
  const clean = (reasons || [])
    .filter(r => r && !/osc=|pct=|ratio=/.test(r))
    .map(r => ({
      text: r.replace(/\s*[+-]\d+(\.\d+)?$/, '').replace(/^★\s*/, '').trim(),
      pts: Math.abs(parseFloat((r.match(/([+-]\d+(?:\.\d+)?)\s*$/) || [])[1] || 0)),
      up: !/-\d/.test(r.slice(-4)),
    }))
    .filter(x => x.text && x.up)
    .sort((a, b) => b.pts - a.pts);
  return clean.slice(0, 2).map(x => x.text).join(' · ');
}

// 직전 실행 대비 변화. 매일 같은 목록을 다시 읽게 하면 며칠 만에 안 열게 된다.
// "어제와 뭐가 달라졌나"가 매일 여는 이유다. 변화가 없으면 카드를 그리지 않는다.
function buildChanges(flow) {
  const ch = (flow || {}).changes;
  if (!ch || !ch.available) return '';
  const items = [];
  const names = (a) => (a || []).map(x => bEscape(x.name || x)).join(', ');
  if ((ch.candidatesEntered || []).length)
    items.push(['새로 진입', names(ch.candidatesEntered), 'in']);
  if ((ch.candidatesLeft || []).length)
    items.push(['목록에서 빠짐', names(ch.candidatesLeft), 'out']);
  if ((ch.sectorsEntered || []).length)
    items.push(['주도 업종 진입', names(ch.sectorsEntered), 'in']);
  if ((ch.sectorsLeft || []).length)
    items.push(['주도 업종 이탈', names(ch.sectorsLeft), 'out']);
  if ((ch.newExitSignals || []).length)
    items.push(['새 이탈 신호', names(ch.newExitSignals), 'out']);
  if (!items.length) return '';

  return `
    <div class="flow-card brief-card-changes">
      <div class="card-header">
        <span class="card-theme-name">직전 대비 변화</span>
        <span class="card-volume">${items.length}건</span>
      </div>
      <div class="chg-body">${items.map(([k, v, dir]) => `
        <div class="chg-row">
          <span class="chg-key chg-${dir}">${bEscape(k)}</span>
          <span class="chg-val">${v}</span>
        </div>`).join('')}</div>
    </div>`;
}

// 조건은 다 통과했는데 '섹터당 4개' 상한에만 걸린 종목.
//
// 숨기면 "우리가 그 종목을 못 봤다"로 오해된다. 실제로 주도섹터가 반도체장비인
// 날에는 여기서 6~7개가 잘려나가는데, 그게 곧 그 섹터가 강하다는 뜻이기도 하다.
// 목록을 좁게 유지하는 규율은 지키되, 잘린 사실은 드러낸다.
function buildOverflow(flow) {
  const ov = (flow || {}).overflowCandidates || [];
  if (!ov.length) return '';
  const bySector = {};
  ov.forEach(o => { (bySector[o.sector || '기타'] ||= []).push(o.name); });
  const parts = Object.entries(bySector)
    .map(([sec, names]) => `<span><b>${bEscape(sec)}</b> ${names.map(bEscape).join(', ')}</span>`);
  // [밀도] 48종목 이름을 펼쳐 두면 '오늘' 화면 마지막이 글자벽으로 끝난다.
  // 결론(최종 후보) 다음에 와야 할 것은 더 긴 목록이 아니라 닫힌 각주다.
  // 잘린 사실은 요약 줄에 남기고, 이름은 열어야 나오게 한다.
  return `
    <details class="sr-overflow">
      <summary class="sr-ov-key">섹터 상한에 걸려 제외된 ${ov.length}종목 보기</summary>
      <div class="sr-ov-body">${parts.join('')}</div>
    </details>`;
}

// 조건을 통과한 종목 — 이 제품의 본체. '오늘' 화면에서 결론까지 보여주고
// 상세는 종목 탭으로 넘긴다. 몇 개에서 몇 개로 좁혔는지는 바로 위 깔때기가
// 그림으로 말한다 (근거 없는 목록은 신뢰를 못 얻는다).
function buildScreenResult(flow) {
  const cands = (flow || {}).buyCandidates || [];
  if (!cands.length) return '';

  // 11개가 전부 '빈집' 이면 그 칸은 정보가 0 이다. 얼마나 깊은 빈집인지를
  // 자기 종목 osc 히스토리 백분위로 보여줘야 종목끼리 구별이 된다.
  const depth = (c) => {
    const p = c.oscPercentile;
    if (p == null) return { label: c.vacancyZone || '-', w: 0 };
    return { label: `하위 ${Math.round(p)}%`, w: Math.max(4, 100 - p) };
  };
  // [레이아웃] 5열 한 줄로 깔았더니 모바일 430px 에서 행이 2줄로 흐트러지고
  // 상태 칩이 잘렸다. 정보량을 줄이는 대신 2줄 구조로 바꾼다.
  //   1줄: 순위 · 종목명 ................ 5일 등락
  //   2줄: 업종 · 수급상태 · 빈집 깊이
  // 이름과 등락률이 같은 줄에서 좌우로 대비되어 훑기 쉽고, 부가 정보는
  // 아래줄에 모여 시선이 두 번만 움직인다.
  const row = (c, i) => {
    const ret = c.ret5d;
    const cls = ret == null ? '' : (ret >= 0 ? 'up' : 'down');
    const d = depth(c);
    const ss = (typeof supplyStateOf === 'function') ? supplyStateOf(c) : null;
    return `
      <div class="sr-row" data-stock-code="${bEscape(c.code)}" data-stock-name="${bEscape(c.name)}">
        <span class="sr-rank">${i + 1}</span>
        <span class="sr-name">${bEscape(c.name)}</span>
        <span class="sr-ret ${cls}">${ret == null ? '-' : `${ret >= 0 ? '+' : ''}${ret.toFixed(1)}%`}</span>
        <span class="sr-meta">
          <em class="sr-sector-in">${bEscape(c.sector || '-')}</em>
          ${ss ? `<em class="sr-state ${ss.cls}">${bEscape(ss.label)}</em>` : ''}
          <em class="sr-depth" title="자기 종목 수급 이력 대비 위치 — 낮을수록 깊은 빈집">
            <i class="sr-depth-bar"><b style="width:${d.w}%"></b></i>
            ${bEscape(d.label)}
          </em>
        </span>
      </div>`;
  };

  // 한 화면에 들어오는 만큼만 펼친다. 10행을 통째로 깔면 순위가 안 읽힌다.
  const TOP_N = 5;

  // [중복 제거] 예전엔 "검토 600 → 조건 120 → 최종 30" 을 글로 한 줄 더 적었다.
  // 바로 위 buildFunnel 이 같은 숫자를 막대로 이미 보여준다. 그림 옆에 같은 말을
  // 글로 또 쓰면 그림이 장식이 된다.

  return `
    <div class="flow-card brief-card-screen">
      <div class="card-header">
        <span class="card-theme-name">조건 통과 종목</span>
        <span class="card-volume">${cands.length}개</span>
      </div>
      ${whatIs('외국인·기관이 최근 5일 순매수를 줄인(수급이 빠진) 자리 중, 10일선 위에서 추세가 살아있는 종목만 남겼습니다. 빈집 깊이는 그 종목의 과거 수급 이력에서 지금이 얼마나 아래인지를 뜻합니다 — 낮을수록 매물이 비어 있습니다. 매수 권유가 아니라 관찰 대상입니다.')}
      <div class="sr-body">${cands.slice(0, TOP_N).map(row).join('')}</div>
      ${cands.length > TOP_N ? `<details class="sr-rest">
        <summary>나머지 ${cands.length - TOP_N}종목 보기</summary>
        <div class="sr-body">${cands.slice(TOP_N).map((c, i) => row(c, i + TOP_N)).join('')}</div>
      </details>` : ''}
      ${buildOverflow(flow)}
      <button class="sr-more" data-goto-tab="flow">종목별 차트·근거 보기 →</button>
    </div>`;
}

// 보유 종목 점검용. 기회보다 리스크를 먼저 보는 사람이 오래 살아남는다.
function buildExitList(flow) {
  const ex = (flow || {}).exitSignals || [];
  if (!ex.length) return '';
  return `
    <div class="flow-card brief-card-exit">
      <div class="card-header">
        <span class="card-theme-name">이탈 신호</span>
        <span class="card-volume">${ex.length}건</span>
      </div>
      ${whatIs('수급 빈집 화면에 올랐던 종목 중, 최근 고점에서 밀리면서 10일 이동평균선까지 내준 종목입니다. 추세가 꺾였다는 사실만 알립니다.')}
      <div class="ex-head"><span>종목</span><span>업종</span><span>고점 대비</span><span>종가</span></div>
      <div class="ex-body">${ex
        .slice()
        .sort((a, b) => (a.drawdownFromHighPct ?? 0) - (b.drawdownFromHighPct ?? 0))
        .slice(0, 12).map(e => `
        <div class="ex-row">
          <span class="ex-name">${bEscape(e.name)}</span>
          <span class="ex-sector">${bEscape(e.sector || '-')}</span>
          <span class="ex-dd">${e.drawdownFromHighPct != null ? `${e.drawdownFromHighPct.toFixed(1)}%` : '-'}</span>
          <span class="ex-px">${e.lastClose != null ? Number(e.lastClose).toLocaleString('ko-KR') : ''}</span>
        </div>`).join('')}</div>
    </div>`;
}

function buildThermometer(name, value, delta, node) {
  if (value == null) return '';
  const v = Math.max(0, Math.min(100, value));
  const zone = zoneOf(node, v);
  const stops = FG_ZONES.map((z, i) => {
    const from = i === 0 ? 0 : FG_ZONES[i - 1].to;
    return `${z.color} ${from}%, ${z.color} ${z.to}%`;
  }).join(', ');
  const deltaHtml = (delta != null && Math.abs(delta) >= 0.05)
    ? `<span class="tm-delta ${delta > 0 ? 'up' : 'down'}">${delta > 0 ? '▲' : '▼'}${Math.abs(delta).toFixed(1)}</span>` : '';
  return `
    <div class="tm-row">
      <div class="tm-head">
        <span class="tm-name">${bEscape(name)}</span>
        <span class="tm-value" style="color:${zone.color}">${v.toFixed(1)}</span>
        <span class="tm-zone" style="color:${zone.color}">${zone.label}</span>
        ${deltaHtml}
      </div>
      <div class="tm-track" style="background:linear-gradient(90deg, ${stops})">
        <span class="tm-needle" style="left:${v}%"></span>
      </div>
    </div>`;
}

// ─────────────────────────────────────────┐
// 돈의 흐름 — 업종별 외인·기관 순매수 대칭 바 │
// ─────────────────────────────────────────┘
// 이 페이지의 핵심 그림. "오늘 돈이 어디로 갔나"를 한 장으로 보여준다.
// 공시 리스트보다 시황 파악에 훨씬 직접적이다.
function buildMoneyFlow(flows) {
  if (!flows) return '';
  // 한쪽 목록에만 있는 섹터를 0 으로 채우면 "순매수 0원"으로 읽혀 오해를 부른다.
  // 상위 목록에 안 들어왔을 뿐이므로 null 로 두고 '—' 로 표기한다.
  const map = {};
  const merge = (arr, key) => (arr || []).forEach(e => {
    if (!e || !e.sector) return;
    map[e.sector] = map[e.sector] || { sector: e.sector, foreigner: null, organ: null };
    map[e.sector][key] = e.amount == null ? null : e.amount;
  });
  merge(flows.foreigner, 'foreigner');
  merge(flows.organ, 'organ');

  const rows = Object.values(map)
    .map(r => ({ ...r, total: (r.foreigner || 0) + (r.organ || 0) }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 8);
  if (!rows.length) return '';

  // 0선을 가운데 두면 순매도가 작은 날 왼쪽 절반이 통째로 비어 허전하다.
  // 0선을 왼쪽 ZERO% 로 옮기고 양·음을 각자 최댓값으로 스케일해 폭을 다 쓴다.
  const ZERO = 18;
  const vals = rows.flatMap(r => [r.foreigner, r.organ]).filter(v => v != null);
  const maxPos = Math.max(...vals.filter(v => v > 0), 1);
  const maxNeg = Math.max(...vals.filter(v => v < 0).map(Math.abs), 1);
  const eok = (v) => {
    if (v == null) return '—';
    const e = v / 1e8;
    return Math.abs(e) >= 10000 ? `${(e / 10000).toFixed(1)}조` : `${Math.round(e).toLocaleString('ko-KR')}억`;
  };
  const bar = (v, cls) => {
    if (v == null) return '';
    if (v >= 0) {
      const w = Math.max(1.2, (v / maxPos) * (100 - ZERO));
      return `<span class="mf-bar mf-${cls} mf-pos" style="left:${ZERO}%;width:${w.toFixed(1)}%"></span>`;
    }
    const w = Math.max(1.2, (Math.abs(v) / maxNeg) * ZERO);
    return `<span class="mf-bar mf-${cls} mf-neg" style="left:${(ZERO - w).toFixed(1)}%;width:${w.toFixed(1)}%"></span>`;
  };

  return `
    <div class="flow-card brief-card-money">
      <div class="card-header">
        <span class="card-theme-name">업종별 자금 유입</span>
        <span class="card-volume">외국인 · 기관 5일 순매수</span>
      </div>
      <div class="mf-legend">
        <span><i class="mf-swatch mf-foreigner"></i>외국인</span>
        <span><i class="mf-swatch mf-organ"></i>기관</span>
      </div>
      <div class="mf-body">
        ${rows.map(r => `
          <div class="mf-row">
            <span class="mf-sector">${bEscape(r.sector)}</span>
            <span class="mf-track">${bar(r.foreigner, 'foreigner')}</span>
            <span class="mf-amt ${r.foreigner == null ? 'na' : (r.foreigner >= 0 ? 'up' : 'down')}">${eok(r.foreigner)}</span>
            <span class="mf-track">${bar(r.organ, 'organ')}</span>
            <span class="mf-amt ${r.organ == null ? 'na' : (r.organ >= 0 ? 'up' : 'down')}">${eok(r.organ)}</span>
          </div>`).join('')}
      </div>
    </div>`;
}

// ─────────────────────────────────────────┐
// 오늘의 숫자 — 시장 폭 요약                 │
// ─────────────────────────────────────────┘
function buildTodayNumbers(flow, facts) {
  if (!flow) return '';
  const items = [];
  const sectors = ((facts || {}).leadingSectors || {}).now || [];
  if (sectors.length) items.push(['주도 업종', sectors.slice(0, 3).join(' · '), '']);
  if (Array.isArray(flow.newHighs)) items.push(['신고가', `${flow.newHighs.length}종목`, 'up']);
  if (Array.isArray(flow.exitSignals)) items.push(['이탈 신호', `${flow.exitSignals.length}종목`, 'down']);
  if (Array.isArray(flow.buyCandidates)) items.push(['빈집 시그널', `${flow.buyCandidates.length}종목`, '']);
  const crowd = (flow.crowding || {});
  if (crowd.signal) items.push(['업종 쏠림', bEscape(crowd.signal), '']);
  if (!items.length) return '';
  return `
    <div class="flow-card brief-card-nums">
      <div class="card-header"><span class="card-theme-name">오늘의 숫자</span></div>
      <div class="tn-grid">
        ${items.map(([k, v, cls]) => `
          <div class="tn-item">
            <div class="tn-key">${bEscape(k)}</div>
            <div class="tn-val ${cls}">${v}</div>
          </div>`).join('')}
      </div>
    </div>`;
}

// ─────────────────────────────────────────┐
// 브리핑 본문 섹션                          │
// ─────────────────────────────────────────┘


function buildBriefingSections(sections) {
  if (!sections || !sections.length) return '';
  return sections.map(s => `
    <div class="brief-section">
      <div class="brief-section-title">${bEscape(s.title)}</div>
      <p class="brief-section-body">${bEscape(s.body)}</p>
    </div>
  `).join('');
}

// ─────────────────────────────────────────┐
// 공시 이벤트 리스트                        │
// ─────────────────────────────────────────┘
function toneBadge(tone) {
  if (tone === 'positive') return '<span class="brief-tone brief-tone-pos">호전 요인</span>';
  if (tone === 'negative') return '<span class="brief-tone brief-tone-neg">주의 요인</span>';
  return '<span class="brief-tone brief-tone-watch">주목</span>';
}

function buildDisclosureCard(disclosures) {
  if (!disclosures) return '';
  if (!disclosures.available) {
    // 수집 실패와 키 미설정을 구분 — 키가 이미 설정된 사용자에게 잘못된 안내를 하지 않는다.
    const msg = disclosures.reason === 'fetch_failed'
      ? '이번 회차에는 공시 데이터를 가져오지 못했습니다. 다음 갱신 때 다시 시도합니다.'
      : '공시 데이터 미연결 — DART API 키 설정 후 표시됩니다.';
    return `
      <div class="flow-card brief-card-dart">
        <div class="card-header"><span class="card-theme-name">DART 공시</span></div>
        <div class="brief-dart-empty">${bEscape(msg)}</div>
      </div>
    `;
  }
  const cand = disclosures.candidateEvents || [];
  const uni = disclosures.universeEvents || [];
  if (!cand.length && !uni.length) {
    return `
      <div class="flow-card brief-card-dart">
        <div class="card-header"><span class="card-theme-name">DART 공시</span></div>
        <div class="brief-dart-empty">최근 3일 내 후보·유니버스 종목의 특이 공시가 없습니다.</div>
      </div>
    `;
  }
  const row = (e) => `
    <a class="brief-dart-row" href="${bEscape(e.url)}" target="_blank" rel="noopener">
      <span class="brief-dart-date">${bFmtDate(e.date)}</span>
      <span class="brief-dart-name">${bEscape(e.name)}${e.isCandidate ? ' <span class="brief-cand-chip">시그널</span>' : ''}</span>
      <span class="brief-dart-cat">${bEscape(e.category)}</span>
      ${toneBadge(e.tone)}
    </a>
  `;
  return `
    <div class="flow-card brief-card-dart">
      <div class="card-header"><span class="card-theme-name">DART 공시</span><span class="card-volume">${cand.length + uni.length}건</span></div>
      <div class="brief-dart-body">
        ${cand.length ? `<div class="brief-dart-group">수급 시그널 종목</div>${cand.map(row).join('')}` : ''}
        ${uni.length ? `<div class="brief-dart-group">유니버스 (시총 상위 600)</div>${uni.slice(0, 12).map(row).join('')}` : ''}
      </div>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// 렌더링                                    │
// ─────────────────────────────────────────┘
function sourceBadge(source) {
  if (source === 'llm') return '<span class="brief-badge brief-badge-rule">데이터 요약</span>';
  if (source === 'sample') return '<span class="brief-badge brief-badge-sample">샘플 미리보기 — Lambda 배포 후 실데이터로 교체</span>';
  return '<span class="brief-badge brief-badge-rule">데이터 요약</span>';
}


// ─────────────────────────────────────────┐
// 4단계 흐름 — 사용자 루틴을 화면 구조로     │
// ─────────────────────────────────────────┘
// 피드백: "중구난방이라 어떻게 쓰는지 감이 안 온다. 그래서 뭘 사야 되는데?"
// 사용자가 실제로 밟는 순서는 정해져 있다.
//   ① 오늘 시장이 어떤지 → ② 매매하기 좋은 장인지 → ③ 어디가 강한지 → ④ 뭘 볼지
// 카드를 늘어놓지 말고 이 순서를 화면 구조로 만든다. 번호와 연결선이 있으면
// 처음 보는 사람도 "위에서 아래로 읽으면 답이 나온다"를 배우지 않고 안다.

// 오늘의 그림 — 탭이 문장으로 시작하면 아무도 안 읽는다.
// 반원 게이지 하나로 "지금 시장이 어디쯤"을 0.5초에 보여주고,
// 그 옆에 오늘의 답(종목 수)을 큰 숫자로 놓는다. 설명은 붙이지 않는다.
// 지금 시장이 어디쯤인지를 0.5초에 보여주는 자리.
//
// [2026-08-19 재작업] 예전 다이얼은 5색 무지개 아크 + 굵은 바늘 + 중심의 큰
// 검은 점이었다. 문제는 셋이었다:
//   · 무지개 아크는 색이 다섯 개나 의미를 갖는데, 정작 "지금 값"은 색이 아니라
//     바늘 위치로만 읽힌다. 색 다섯 개가 전부 장식으로 돌아간다.
//   · 숫자(51.1)가 화면에 아예 없었다. 구간 이름만 있으니 어제와 비교가 안 된다.
//   · 굵은 바늘 + 중심 점은 2010년대 대시보드 위젯 인상을 준다.
//
// 바꾼 규칙:
//   · 트랙은 무채색 하나. **색은 현재 구간 하나에만** 쓴다.
//   · 값은 아크 길이로 인코딩한다 — 길이는 각도보다 정확하게 읽힌다.
//   · 숫자를 중앙에 가장 크게. 구간 이름은 그 아래 작게.
//   · 바늘 대신 아크 끝의 작은 노브. 중심 장식 제거.
// 색은 **zone.color 하나에서 파생**한다. 색표를 따로 두면 안 된다 —
// 이 화면에는 구간 이름 어휘가 두 벌 있다:
//   OSC_ZONE_COLORS  과열/강세/중립/약세/공포        (백엔드 zone, 정상 경로)
//   FG_ZONES         극단적 공포/공포/중립/탐욕/극단적 탐욕 (레벨 폴백 경로)
// 처음엔 앞 어휘만 담은 표(DIAL_TONE)를 따로 뒀는데, 폴백 경로로 흐르는 날엔
// '탐욕'·'극단적 탐욕'·'극단적 공포' 가 표에 없어 게이지가 통째로 무채색이 됐다.
// zone.color 는 두 어휘 모두가 이미 들고 있으므로 그것만 쓰면 갈라질 일이 없다.
function tintHex(hex, ratio) {
  const m = /^#([0-9a-f]{6})$/i.exec(String(hex || ''));
  if (!m) return '#D3D8DF';
  const n = parseInt(m[1], 16);
  const up = (c) => Math.round(c + (255 - c) * ratio);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(up);
  return '#' + ((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1);
}

function buildHeroDial(fg, sentiment, flow) {
  const v = Math.max(0, Math.min(100, fg.kospi ?? 50));
  const zone = zoneOf(sentiment.kospi, fg.kospi);
  const nCand = ((flow || {}).buyCandidates || []).length;
  const tone = [zone.color || '#7C8493', tintHex(zone.color || '#7C8493', 0.70)];

  // 반원 기하 — 180°(좌) → 360°(우)
  // 아크의 지름선(y=CY) 아래는 비어 있다. 숫자는 그릇 안, 구간 이름은
  // 지름선 **아래**로 내려 서로 붙지 않게 한다.
  const CX = 100, CY = 92, R = 71, SW = 12;
  const DEG = 1.8;                     // 0~100 → 180° (반원)
  const pt = (deg, r) => [
    CX + r * Math.cos(deg * Math.PI / 180),
    CY + r * Math.sin(deg * Math.PI / 180),
  ];
  const arc = (from, to, r) => {
    const [x0, y0] = pt(180 + from * DEG, r);
    const [x1, y1] = pt(180 + to * DEG, r);
    // SVG large-arc-flag 는 **쓸린 각이 180°를 넘을 때만** 1 이다.
    // 예전엔 `(to - from) > 50` 이었는데 그건 각도가 아니라 값이다 —
    // 값 50 은 90°일 뿐이라, F&G 가 50 을 넘는 순간 플래그가 1 이 되어
    // SVG 가 반대편 대호(major arc)를 그렸다. 작은 부채꼴이어야 할 것이
    // 거의 한 바퀴를 돌아 원형이 깨져 보인다.
    // 실측: 2026-08-20 은 49.25 라 멀쩡했고 2026-08-21 은 51.0 이라 깨졌다.
    // 여기 정의역(0~100)에서 쓸린 각은 최대 180° 라 실은 항상 0 이지만,
    // 기하가 바뀌어도 맞도록 **각도로** 판정한다.
    const large = (to - from) * DEG > 180 ? 1 : 0;
    return `M ${x0.toFixed(1)} ${y0.toFixed(1)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
  };

  const gid = 'dg' + Math.random().toString(36).slice(2, 7);
  const [kx, ky] = pt(180 + v * DEG, R);

  // 25/50/75 눈금 — 값을 대략 가늠하는 기준. 숫자는 쓰지 않는다(밀도).
  const ticks = [25, 50, 75].map(t => {
    const [x0, y0] = pt(180 + t * DEG, R - SW / 2 - 2.5);
    const [x1, y1] = pt(180 + t * DEG, R + SW / 2 + 2.5);
    return `<line x1="${x0.toFixed(1)}" y1="${y0.toFixed(1)}" x2="${x1.toFixed(1)}" y2="${y1.toFixed(1)}" class="dl-tick"/>`;
  }).join('');

  return `
    <div class="dial">
      <div class="dial-gauge">
        <svg viewBox="0 0 200 118" class="dial-svg" xmlns="http://www.w3.org/2000/svg" role="img"
             aria-label="공포·탐욕 ${v.toFixed(1)}, ${bEscape(zone.label)}">
          <defs>
            <linearGradient id="${gid}" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stop-color="${tone[1]}"/>
              <stop offset="100%" stop-color="${tone[0]}"/>
            </linearGradient>
          </defs>
          <path d="${arc(0, 100, R)}" class="dl-track" stroke-width="${SW}"/>
          ${ticks}
          <path d="${arc(0, Math.max(v, 0.6), R)}" stroke="url(#${gid})" stroke-width="${SW}"
                fill="none" stroke-linecap="round"/>
          <circle cx="${kx.toFixed(1)}" cy="${ky.toFixed(1)}" r="6.4" class="dl-knob-halo"/>
          <circle cx="${kx.toFixed(1)}" cy="${ky.toFixed(1)}" r="3.4" fill="${tone[0]}"/>
          <text x="${CX}" y="${CY - 12}" class="dl-val">${v.toFixed(1)}</text>
          <text x="${CX}" y="${CY + 15}" class="dl-zone" fill="${tone[0]}">${bEscape(zone.label)}</text>
        </svg>
        <div class="dial-cap">공포·탐욕 지수 <em>KOSPI</em></div>
      </div>
      <div class="dial-answer">
        <span>오늘 볼 종목</span>
        <b>${nCand}<i>개</i></b>
        <a class="dial-jump" href="#s4">바로 보기 <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4"/></svg></a>
      </div>
    </div>`;
}

// 왼쪽에 ①②③ 번호 구슬과 세로 레일을 세웠던 적이 있는데 뺐다.
// 카드가 위에서 아래로 놓여 있으면 순서는 이미 위치가 말한다. 번호를 또
// 붙이면 설명이 하나 늘 뿐이고, 레일이 가져간 30px 만큼 본문이 좁아진다.
function stepCard(title, answer, body, opts = {}) {
  return `
    <section class="st ${opts.cls || ''}"${opts.id ? ` id="${opts.id}"` : ''}>
      <div class="st-body">
        <div class="st-head">
          <h3 class="st-title">${bEscape(title)}</h3>
          ${answer ? `<div class="st-answer">${answer}</div>` : ''}
        </div>
        ${body || ''}
      </div>
    </section>`;
}

// ③ 주도 섹터 — 어디가 강한가. 근거(어느 ETF·수급)를 칩에 같이 단다.
function buildSectorStep(flow) {
  const labels = ((flow || {}).leadingSectorLabels) || [];
  if (!labels.length) return '';
  const src = (flow || {}).leadingSectorSources || {};
  const chips = labels.slice(0, 6).map((s, i) => {
    const v = src[s] || {};
    const why = v.via === 'etf' && v.etf ? `${v.etf} RS ${v.rsNorm}`
              : v.via === 'flow' && v.strength != null ? `외인·기관 자금 유입`
              : '';
    return `<div class="sec-chip${i === 0 ? ' sec-chip-top' : ''}">
      <b>${bEscape(s)}</b>${why ? `<em>${bEscape(why)}</em>` : ''}
    </div>`;
  }).join('');
  return `<div class="sec-chips">${chips}</div>`;
}

// ④ 앞에 놓는 깔때기 — "이런 근거로 이 종목이 남았다" 를 한 장으로.
// 결과 목록만 주면 근거가 안 보이고, 글로 설명하면 아무도 안 읽는다.
function buildFunnel(flow) {
  const st = (flow || {}).candidateFilterStats || {};
  const uni = ((flow || {}).universeMetadata || []).length;
  const fin = ((flow || {}).buyCandidates || []).length;
  if (!fin) return '';
  const before = st.beforeFilter || fin;
  const steps = [
    { label: '전체 분석 종목', n: uni || before, desc: '코스피·코스닥 시총 상위' },
    { label: '주도 업종 소속', n: before, desc: '돈이 들어오는 업종만' },
    { label: '수급이 빈 자리', n: Math.max(fin, before - (st.droppedByVacancy || 0)), desc: '외인·기관이 빠져나간 종목' },
    { label: '추세 생존', n: fin, desc: '10일선 위 · 흐름이 살아있는 종목' },
  ];
  const max = Math.max(...steps.map(x => x.n), 1);
  return `
    <div class="fn">
      <div class="fn-title">이렇게 좁혔습니다</div>
      ${steps.map((x, i) => `
        <div class="fn-row${i === steps.length - 1 ? ' fn-row-last' : ''}">
          <div class="fn-track">
            <div class="fn-bar" style="width:${Math.max(22, x.n / max * 100).toFixed(1)}%">
              <span class="fn-n">${x.n.toLocaleString('ko-KR')}</span>
            </div>
          </div>
          <div class="fn-lab"><b>${bEscape(x.label)}</b></div>
        </div>`).join('')}
    </div>`;
}

// ② 장 난이도 — 한 줄 결론 + 뜻
function crowdAnswer(flow) {
  const crowding = (flow || {}).crowding || {};
  const hist = (crowding.history || []).filter(h => h && h.crowding != null).map(h => h.crowding);
  if (hist.length < 20) return { pill: crowding.signal || '-', desc: '' };
  const p = pctRank(hist, hist[hist.length - 1]);
  const band = crowdBandOf(p);
  return { pill: band.label, tone: band.tone, desc: band.desc, pct: p };
}

// 그려 넣은 뒤 실제 폭을 재서 쏠림 차트의 viewBox 를 다시 잡는다.
// 문자열을 만드는 시점에는 폭을 알 수 없으므로 삽입 후 한 번 더 맞춘다.
function fitCrowdingChart(container, flow) {
  const card = container && container.querySelector('.cc-card');
  if (!card) return;
  const w = Math.round(card.getBoundingClientRect().width);
  if (!(w > 0)) return;
  const target = Math.max(200, Math.min(760, w));
  const svg = card.querySelector('.cc-svg');
  const cur = (svg && svg.viewBox && svg.viewBox.baseVal) ? svg.viewBox.baseVal.width : 0;
  if (Math.abs(cur - target) < 12) return;   // 미세한 차이로 다시 그리지 않는다
  const html = buildCrowdingChart(flow, target);
  if (html) card.outerHTML = html;
}

// 폴드는 화면을 접었다 펴면 폭이 두 배 이상 바뀌는데, 렌더는 그때 한 번뿐이라
// 그림이 옛 폭에 맞춰진 채 늘어난다. 폭이 의미 있게 바뀔 때만 다시 맞춘다.
let briefLastFlow = null;
let briefResizeTimer = null;
let briefLastWidth = 0;
if (typeof window !== 'undefined') {
  window.addEventListener('resize', () => {
    clearTimeout(briefResizeTimer);
    briefResizeTimer = setTimeout(() => {
      const w = window.innerWidth;
      if (Math.abs(w - briefLastWidth) < 24) return;
      briefLastWidth = w;
      if (briefLastFlow) fitCrowdingChart(document.getElementById('briefing-content'), briefLastFlow);
    }, 180);
  });
}

function renderBriefing(briefing, flow) {
  const container = document.getElementById('briefing-content');
  const generated = briefing.generatedAt ? new Date(briefing.generatedAt).toLocaleString('ko-KR') : '-';
  const facts = briefing.signalFacts || {};
  const fg = facts.fearGreed || {};
  const sentiment = (flow || {}).marketSentiment || {};

  const zone = zoneOf(sentiment.kospi, fg.kospi);
  const crowd = crowdAnswer(flow);
  const nCand = ((flow || {}).buyCandidates || []).length;
  const nExit = ((flow || {}).exitSignals || []).length;

  // 위에서 아래로 ① 시장 → ② 난이도 → ③ 주도 업종 → ④ 종목.
  // 사용자가 실제로 밟는 순서 그대로다. 마지막 칸이 '답' 이어야 한다.
  container.innerHTML = `
    <div class="brief-wrap">
      <div class="flow-card hero2">
        ${buildHeroDial(fg, sentiment, flow)}
        <div class="hero2-line">${bEscape(briefing.headline)}</div>
      </div>

      ${stepCard('장 난이도', `<span class="st-pill st-pill-${crowd.tone || 'mid'}">${bEscape(crowd.pill)}</span>`,
        buildCrowdingChart(flow))}

      ${stepCard('주도 업종', '', buildSectorStep(flow))}

      ${stepCard('오늘 볼 종목', `<span class="st-pill st-pill-main">${nCand}개</span>`,
        `${buildFunnel(flow)}${buildScreenResult(flow)}`, { cls: 'st-last', id: 's4' })}

      ${buildChanges(flow)}
      <div id="brief-theme-bridge"></div>

      <details class="brief-more">
        <summary>서술 요약 · 섹터 수급 · 이탈 신호 · 공시 자세히 보기</summary>
        <div class="brief-more-body">
          <div class="flow-card brief-card-main">
            <div class="tm-wrap">
              ${buildThermometer(sentiment.kospi?.label || '코스피', fg.kospi, fg.kospiDelta, sentiment.kospi)}
              ${buildThermometer(sentiment.kosdaq?.label || '코스닥', fg.kosdaq, null, sentiment.kosdaq)}
            </div>
            ${buildBriefingSections(briefing.sections)}
          </div>
          ${buildExitList(flow)}
          ${buildMoneyFlow((flow || {}).sectorFlows)}
          ${buildTodayNumbers(flow, facts)}
          ${buildDisclosureCard(briefing.disclosures)}
        </div>
      </details>

      <div class="brief-foot">
        <span>기준 시각: ${generated}</span>
        ${sourceBadge(briefing.source)}
      </div>
      <p class="brief-disclaimer">${bEscape(briefing.disclaimer || '')}</p>
    </div>
  `;

  // 실제 폭을 재서 쏠림 차트 배율을 1 근처로 맞춘다 (폴드 대응).
  briefLastFlow = flow;
  briefLastWidth = (typeof window !== 'undefined') ? window.innerWidth : 0;
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => fitCrowdingChart(container, flow));
  else fitCrowdingChart(container, flow);

  container.querySelector('.dial-jump')?.addEventListener('click', (e) => {
    e.preventDefault();
    container.querySelector('.st-last')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  // 종목 행 클릭 → 차트 모달, 버튼 → 종목 탭
  container.querySelectorAll('.sr-row').forEach(el => {
    el.addEventListener('click', () => {
      const c = ((flow || {}).buyCandidates || []).find(x => x.code === el.dataset.stockCode);
      if (c && typeof openStockModal === 'function') openStockModal(c);
    });
  });
  container.querySelector('.sr-more')?.addEventListener('click', () => {
    document.querySelector('.tab-btn[data-tab="flow"]')?.click();
  });

  // 재료 요약은 테마 데이터(dashboard_data.json)가 따로 필요하다. 이것 때문에
  // 탭 전체가 늦어지면 안 되므로, 먼저 그리고 도착하면 끼워 넣는다.
  loadThemeSummary().then(td => {
    if (!td) return;
    const slot = container.querySelector('#brief-theme-bridge');
    if (!slot) return;
    slot.innerHTML = buildThemeBridge(td, flow);
    slot.querySelector('.tb-more')?.addEventListener('click', () => {
      document.querySelector('.tab-btn[data-tab="themes"]')?.click();
    });
    slot.querySelectorAll('.tb-stock').forEach(el => {
      el.addEventListener('click', () => {
        const nm = el.textContent.replace(/[+-][\d.]+%$/, '').trim();
        const c = ((flow || {}).buyCandidates || []).find(x => x.name === nm);
        if (c && typeof openStockModal === 'function') openStockModal(c);
      });
    });
  });
}

// ─────────────────────────────────────────┐
// Loader — flow 데이터 재사용 + 샘플 폴백    │
// ─────────────────────────────────────────┘
async function loadBriefing() {
  if (briefingLoaded) return;
  if (briefingLoadPromise) return briefingLoadPromise;
  briefingLoadPromise = (async () => {
    const container = document.getElementById('briefing-content');
    try {
      // flow_dashboard.json 은 flow.js 의 로더를 재사용 (동시 fetch 방지 + 수급탭 프리로드)
      await loadFlow();
      let briefing = flowData && flowData.briefing;
      if (!briefing) {
        // 구버전 데이터(브리핑 미탑재) — 샘플로 UI 미리보기
        const resp = await fetch(BRIEFING_SAMPLE_URL);
        if (resp.ok) briefing = await resp.json();
      }
      if (!briefing) throw new Error('briefing 데이터 없음');
      // flow 원본도 넘긴다 — 온도계·돈의 흐름·오늘의 숫자는 briefing 요약이
      // 아니라 flow payload 의 원자료(sectorFlows/newHighs/…)로 그린다.
      renderBriefing(briefing, flowData);
      briefingLoaded = true;
    } catch (err) {
      console.error('briefing load error:', err);
      container.innerHTML = `
        <div class="error-state">
          <p>시황을 불러올 수 없습니다.</p>
          <p style="font-size:0.8rem;color:#999">${bEscape(err.message)}</p>
          <button class="retry-btn" onclick="loadBriefing()">다시 시도</button>
        </div>
      `;
      briefingLoadPromise = null;  // 실패 시 재시도 허용
    }
  })();
  return briefingLoadPromise;
}
