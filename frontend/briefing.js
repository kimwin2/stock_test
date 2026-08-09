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

// ─────────────────────────────────────────┐
// 용어 한 줄 설명                            │
// ─────────────────────────────────────────┘
// 숫자만 크게 띄워도 뜻을 모르면 안 읽힌다. 지표마다 "이게 무엇인지"를
// 한 줄로 붙인다. 판단을 대신하지 않고 정의만 말한다.
function whatIs(text) {
  return `<p class="what-is">${bEscape(text)}</p>`;
}

// 오늘 시장을 한 문장으로. 구간 이름을 가장 크게 보여준다 — '38.7'만으로는
// 높은 건지 낮은 건지 알 수 없기 때문이다.
function buildMarketVerdict(fg, sentiment, flow) {
  const v = fg.kospi;
  if (v == null) return '';
  const zone = fgZoneLabel(v);
  const d = fg.kospiDelta;
  const move = (d == null || Math.abs(d) < 0.05) ? '전일과 비슷'
    : (d > 0 ? `전일 대비 ${d.toFixed(1)} 상승` : `전일 대비 ${Math.abs(d).toFixed(1)} 하락`);
  const cash = (flow || {}).cashRecommendation || {};
  const crowd = ((flow || {}).crowding || {}).signal;
  const mdd = (sentiment.kospi || {}).mddPct;

  const chips = [];
  if (cash.cashPct != null) chips.push(['권고 현금비중', `${cash.cashPct}%`, cash.level || '']);
  if (crowd) chips.push(['업종 쏠림', crowd, '']);
  if (mdd != null) chips.push(['코스피 낙폭(MDD)', `${mdd.toFixed(1)}%`, '고점 대비']);

  return `
    <div class="verdict">
      <div class="verdict-zone" style="color:${zone.color}">${zone.label}</div>
      <div class="verdict-num">
        <strong style="color:${zone.color}">${v.toFixed(1)}</strong>
        <span>/100 · ${bEscape(move)}</span>
      </div>
      ${chips.length ? `<div class="verdict-chips">${chips.map(([k, val, sub]) => `
        <div class="vc-item">
          <div class="vc-key">${bEscape(k)}</div>
          <div class="vc-val">${bEscape(val)}</div>
          ${sub ? `<div class="vc-sub">${bEscape(sub)}</div>` : ''}
        </div>`).join('')}</div>` : ''}
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

// 조건을 통과한 종목 — 이 제품의 본체. '오늘' 화면에서 결론까지 보여주고
// 상세는 종목 탭으로 넘긴다. 몇 개에서 몇 개로 좁혔는지를 함께 적어
// 걸러진 과정이 보이게 한다 (근거 없는 목록은 신뢰를 못 얻는다).
function buildScreenResult(flow) {
  const cands = (flow || {}).buyCandidates || [];
  if (!cands.length) return '';
  const st = (flow || {}).candidateFilterStats || {};
  const uni = ((flow || {}).universeMetadata || []).length;

  // 11개가 전부 '빈집' 이면 그 칸은 정보가 0 이다. 얼마나 깊은 빈집인지를
  // 자기 종목 osc 히스토리 백분위로 보여줘야 종목끼리 구별이 된다.
  const depth = (c) => {
    const p = c.oscPercentile;
    if (p == null) return { label: c.vacancyZone || '-', w: 0 };
    return { label: `하위 ${Math.round(p)}%`, w: Math.max(4, 100 - p) };
  };
  const row = (c) => {
    const ret = c.ret5d;
    const cls = ret == null ? '' : (ret >= 0 ? 'up' : 'down');
    const d = depth(c);
    const why = pickReasons(c.flowReasons);
    return `
      <div class="sr-row" data-stock-code="${bEscape(c.code)}" data-stock-name="${bEscape(c.name)}">
        <span class="sr-name">${bEscape(c.name)}
          ${why ? `<em class="sr-why">${bEscape(why)}</em>` : ''}
        </span>
        <span class="sr-sector">${bEscape(c.sector || '-')}</span>
        <span class="sr-depth" title="자기 종목 수급 이력 대비 위치 — 낮을수록 깊은 빈집">
          <span class="sr-depth-bar"><i style="width:${d.w}%"></i></span>
          <em>${bEscape(d.label)}</em>
        </span>
        <span class="sr-ret ${cls}">${ret == null ? '-' : `${ret >= 0 ? '+' : ''}${ret.toFixed(1)}%`}</span>
      </div>`;
  };

  const funnel = (uni && st.beforeFilter)
    ? `검토 ${uni}종목 → 추세·수급 조건 ${st.beforeFilter} → 최종 ${cands.length}`
    : `최종 ${cands.length}종목`;

  return `
    <div class="flow-card brief-card-screen">
      <div class="card-header">
        <span class="card-theme-name">🎯 오늘 조건을 통과한 종목</span>
        <span class="card-volume">${cands.length}개</span>
      </div>
      ${whatIs('외국인·기관이 최근 5일 순매수를 줄인(수급이 빠진) 자리 중, 10일선 위에서 추세가 살아있는 종목만 남겼습니다. 빈집 깊이는 그 종목의 과거 수급 이력에서 지금이 얼마나 아래인지를 뜻합니다 — 낮을수록 매물이 비어 있습니다. 매수 권유가 아니라 관찰 대상입니다.')}
      <div class="sr-funnel">${bEscape(funnel)}</div>
      <div class="sr-head"><span>종목 · 뽑힌 이유</span><span>업종</span><span>빈집 깊이</span><span>5일</span></div>
      <div class="sr-body">${cands.map(row).join('')}</div>
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
        <span class="card-theme-name">⚠️ 이탈 신호</span>
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
        <div class="brief-eyebrow">오늘 시장</div>
        <div class="brief-headline">${bEscape(briefing.headline)}</div>
        ${buildMarketVerdict(fg, sentiment, flow)}
        <div class="tm-wrap">
          ${buildThermometer(sentiment.kospi?.label || '코스피', fg.kospi, fg.kospiDelta)}
          ${buildThermometer(sentiment.kosdaq?.label || '코스닥', fg.kosdaq, null)}
        </div>
        ${whatIs('공포·탐욕 지수는 주가 흐름·거래량·변동성·안전자산 선호를 하나로 합친 0~100 값입니다. 낮을수록 시장이 위축된 상태입니다.')}
      </div>

      ${buildScreenResult(flow)}
      ${buildExitList(flow)}
      ${buildMoneyFlow((flow || {}).sectorFlows)}

      <details class="brief-more">
        <summary>서술 요약 · 오늘의 숫자 · 공시 자세히 보기</summary>
        <div class="brief-more-body">
          <div class="flow-card brief-card-main">
            ${buildBriefingSections(briefing.sections)}
          </div>
          ${buildTodayNumbers(flow, facts)}
          ${buildDisclosureCard(briefing.disclosures)}
        </div>
      </details>

      <p class="brief-disclaimer">${bEscape(briefing.disclaimer || '')}</p>
    </div>
  `;

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
