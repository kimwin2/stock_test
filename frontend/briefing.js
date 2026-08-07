/**
 * Briefing Tab — AI 데이터 브리핑
 *
 * 자체 시그널(F&G, 주도섹터, 수급, 빈집) 변화 + DART 공시 이벤트를
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
    chips.push(`<div class="brief-chip"><span class="brief-chip-label">KOSPI F&amp;G</span><span class="brief-chip-value">${fg.kospi}${deltaHtml}</span></div>`);
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
    return `
      <div class="flow-card brief-card-dart">
        <div class="card-header"><span class="card-theme-name">📋 DART 공시 이벤트</span></div>
        <div class="brief-dart-empty">공시 데이터 미연결 — DART API 키 설정 후 표시됩니다.</div>
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
      <span class="brief-dart-name">${bEscape(e.name)}${e.isCandidate ? ' <span class="brief-cand-chip">매수후보</span>' : ''}</span>
      <span class="brief-dart-cat">${bEscape(e.category)}</span>
      ${toneBadge(e.tone)}
    </a>
  `;
  return `
    <div class="flow-card brief-card-dart">
      <div class="card-header"><span class="card-theme-name">📋 DART 공시 이벤트</span><span class="card-volume">${cand.length + uni.length}건</span></div>
      <div class="brief-dart-body">
        ${cand.length ? `<div class="brief-dart-group">매수 후보 종목</div>${cand.map(row).join('')}` : ''}
        ${uni.length ? `<div class="brief-dart-group">유니버스 (시총 상위 600)</div>${uni.slice(0, 12).map(row).join('')}` : ''}
      </div>
    </div>
  `;
}

// ─────────────────────────────────────────┐
// 렌더링                                    │
// ─────────────────────────────────────────┘
function sourceBadge(source) {
  if (source === 'llm') return '<span class="brief-badge brief-badge-ai">AI 생성</span>';
  if (source === 'sample') return '<span class="brief-badge brief-badge-sample">샘플 미리보기 — Lambda 배포 후 실데이터로 교체</span>';
  return '<span class="brief-badge brief-badge-rule">데이터 요약</span>';
}

function renderBriefing(briefing) {
  const container = document.getElementById('briefing-content');
  const generated = briefing.generatedAt ? new Date(briefing.generatedAt).toLocaleString('ko-KR') : '-';
  container.innerHTML = `
    <div class="flow-meta">
      <span>브리핑 생성: ${generated}</span>
      ${sourceBadge(briefing.source)}
    </div>
    <div class="brief-wrap">
      <div class="flow-card brief-card-main">
        <div class="brief-headline">${bEscape(briefing.headline)}</div>
        ${buildBriefingStats(briefing.signalFacts)}
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
      renderBriefing(briefing);
      briefingLoaded = true;
    } catch (err) {
      console.error('briefing load error:', err);
      container.innerHTML = `
        <div class="error-state">
          <p>브리핑을 불러올 수 없습니다.</p>
          <p style="font-size:0.8rem;color:#999">${bEscape(err.message)}</p>
          <button class="retry-btn" onclick="loadBriefing()">다시 시도</button>
        </div>
      `;
      briefingLoadPromise = null;  // 실패 시 재시도 허용
    }
  })();
  return briefingLoadPromise;
}
