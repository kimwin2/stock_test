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
  const cash = facts.cashRecommendation || {};
  const sectors = (facts.leadingSectors || {}).now || [];

  const chips = [];
  if (fg.kospi != null) {
    const delta = fg.kospiDelta;
    const deltaHtml = (delta != null && Math.abs(delta) >= 0.05)
      ? `<small class="${delta > 0 ? 'up' : 'down'}">${delta > 0 ? '▲' : '▼'}${Math.abs(delta).toFixed(1)}</small>`
      : '';
    chips.push(`<div class="brief-chip"><span class="brief-chip-label">코스피 공포·탐욕</span><span class="brief-chip-value">${fg.kospi}${deltaHtml}</span></div>`);
  }
  if (cash.nowPct != null) {
    chips.push(`<div class="brief-chip"><span class="brief-chip-label">권고 현금</span><span class="brief-chip-value">${cash.nowPct}%</span></div>`);
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

function buildThermometer(name, value, delta) {
  if (value == null) return '';
  const v = Math.max(0, Math.min(100, value));
  const zone = fgZoneLabel(v);
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
        <span class="card-theme-name">💰 오늘 돈이 간 곳</span>
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
  const cash = (flow.cashRecommendation || {});
  if (cash.cashPct != null) items.push(['권고 현금비중', `${cash.cashPct}% · ${bEscape(cash.level || '')}`, '']);
  const crowd = (flow.crowding || {});
  if (crowd.signal) items.push(['업종 쏠림', bEscape(crowd.signal), '']);
  if (!items.length) return '';
  return `
    <div class="flow-card brief-card-nums">
      <div class="card-header"><span class="card-theme-name">📊 오늘의 숫자</span></div>
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
const BRIEF_SECTION_ICONS = {
  '시장 온도': '🌡️',
  '수급 흐름': '💧',
  '빈집 시그널': '🏚️',
  '공시 체크': '📋',
};

function buildBriefingSections(sections) {
  if (!sections || !sections.length) return '';
  return sections.map(s => `
    <div class="brief-section">
      <div class="brief-section-title">${BRIEF_SECTION_ICONS[s.title] || '•'} ${bEscape(s.title)}</div>
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
        <div class="card-header"><span class="card-theme-name">📋 DART 공시 이벤트</span></div>
        <div class="brief-dart-empty">${bEscape(msg)}</div>
      </div>
    `;
  }
  const cand = disclosures.candidateEvents || [];
  const uni = disclosures.universeEvents || [];
  if (!cand.length && !uni.length) {
    return `
      <div class="flow-card brief-card-dart">
        <div class="card-header"><span class="card-theme-name">📋 DART 공시 이벤트</span></div>
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
      <div class="card-header"><span class="card-theme-name">📋 DART 공시 이벤트</span><span class="card-volume">${cand.length + uni.length}건</span></div>
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

function renderBriefing(briefing, flow) {
  const container = document.getElementById('briefing-content');
  const generated = briefing.generatedAt ? new Date(briefing.generatedAt).toLocaleString('ko-KR') : '-';
  const facts = briefing.signalFacts || {};
  const fg = facts.fearGreed || {};
  const sentiment = (flow || {}).marketSentiment || {};

  // 화면 위에서부터 "한 줄 요약 → 온도 → 돈의 흐름 → 숫자 → 서술 → 공시".
  // 공시는 근거 자료라 아래로 내린다 — 시황 파악의 출발점이 아니다.
  container.innerHTML = `
    <div class="flow-meta">
      <span>기준 시각: ${generated}</span>
      ${sourceBadge(briefing.source)}
    </div>
    <div class="brief-wrap">
      <div class="flow-card brief-card-hero">
        <div class="brief-headline">${bEscape(briefing.headline)}</div>
        <div class="tm-wrap">
          ${buildThermometer(sentiment.kospi?.label || '코스피', fg.kospi, fg.kospiDelta)}
          ${buildThermometer(sentiment.kosdaq?.label || '코스닥', fg.kosdaq, null)}
        </div>
      </div>
      ${buildMoneyFlow((flow || {}).sectorFlows)}
      ${buildTodayNumbers(flow, facts)}
      <div class="flow-card brief-card-main">
        ${buildBriefingSections(briefing.sections)}
      </div>
      ${buildDisclosureCard(briefing.disclosures)}
      <p class="brief-disclaimer">${bEscape(briefing.disclaimer || '')}</p>
    </div>
  `;
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
