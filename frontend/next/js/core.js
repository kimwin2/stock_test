/* ============================================================
   core.js — 데이터 · 포맷 · 저장소 · 용어사전
   ------------------------------------------------------------
   이 파일은 화면을 그리지 않는다. 그리는 쪽(home/stocks/…)이
   숫자를 각자 다르게 포맷하기 시작하면 같은 값이 화면마다 달라
   보이므로, 포맷은 전부 여기 한 곳에 둔다.
   ============================================================ */
(function (global) {
  'use strict';

  /* ── 데이터 위치 ────────────────────────────────────────────
     기존 화면과 같은 규칙: github.io(프로덕션)면 S3, 아니면 로컬 파일.
     로컬 파일은 이 디렉터리 기준 한 단계 위(frontend/)에 있다. */
  var S3 = 'https://stock-dashboard-data.s3.ap-northeast-2.amazonaws.com';
  var host = global.location ? global.location.hostname : '';
  var IS_PROD = host.indexOf('github.io') >= 0 || host.indexOf('stock') >= 0;

  var URLS = {
    theme: IS_PROD ? S3 + '/dashboard_data.json' : '../dashboard_data.json',
    flow:  IS_PROD ? S3 + '/flow_dashboard.json' : '../flow_dashboard.json'
  };

  /* ── 안전한 문자열 ──────────────────────────────────────── */
  var ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
  function esc(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, function (c) { return ESC[c]; });
  }

  /* ── 숫자 포맷 ──────────────────────────────────────────── */
  function num(n) {
    if (n == null || isNaN(n)) return '-';
    return Number(n).toLocaleString('ko-KR');
  }

  // 원 단위 → 억/조. 트레이더는 '억' 단위로 말한다.
  function won(v) {
    if (v == null || isNaN(v)) return '-';
    var a = Math.abs(v), sign = v < 0 ? '-' : '';
    if (a >= 1e12) return sign + (a / 1e12).toFixed(a / 1e12 >= 10 ? 0 : 1) + '조';
    if (a >= 1e8)  return sign + Math.round(a / 1e8).toLocaleString('ko-KR') + '억';
    if (a >= 1e4)  return sign + Math.round(a / 1e4).toLocaleString('ko-KR') + '만';
    return sign + Math.round(a).toLocaleString('ko-KR');
  }

  // "1,890억" / "1.2조" 같은 문자열 → 원 단위 숫자 (테마 데이터가 문자열로 온다)
  function parseWon(v) {
    if (v == null) return 0;
    if (typeof v === 'number') return v;
    var s = String(v).replace(/[,\s]/g, '');
    var m = /^(-?[\d.]+)(조|억|만)?/.exec(s);
    if (!m) return 0;
    var n = parseFloat(m[1]);
    if (!isFinite(n)) return 0;
    return n * ({ '조': 1e12, '억': 1e8, '만': 1e4 }[m[2]] || 1);
  }

  function pct(v, digits) {
    if (v == null || isNaN(v)) return '-';
    var d = digits == null ? 2 : digits;
    return (v > 0 ? '+' : '') + Number(v).toFixed(d) + '%';
  }

  function dirClass(v) {
    if (v == null || isNaN(v) || v === 0) return 'flat';
    return v > 0 ? 'up' : 'down';
  }

  /* ── 시각 ──────────────────────────────────────────────── */
  var WD = ['일', '월', '화', '수', '목', '금', '토'];

  function stamp(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (isNaN(d)) return '—';
    return pad(d.getMonth() + 1) + '.' + pad(d.getDate()) + ' ' +
           pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  function dateLine(iso) {
    var d = iso ? new Date(iso) : new Date();
    if (isNaN(d)) d = new Date();
    return (d.getMonth() + 1) + '월 ' + d.getDate() + '일 ' + WD[d.getDay()] + '요일';
  }

  // "몇 분 전" — 데이터가 살아 있는지를 절대시각보다 빠르게 알려준다.
  function ago(iso) {
    if (!iso) return '';
    var t = new Date(iso).getTime();
    if (isNaN(t)) return '';
    var m = Math.floor((Date.now() - t) / 60000);
    if (m < 1) return '방금';
    if (m < 60) return m + '분 전';
    var h = Math.floor(m / 60);
    if (h < 24) return h + '시간 전';
    return Math.floor(h / 24) + '일 전';
  }

  function pad(n) { return String(n).padStart(2, '0'); }

  /* ── 데이터 로딩 ────────────────────────────────────────────
     flow 는 1.9MB 다. 탭을 옮길 때마다 다시 받으면 안 되므로
     프라미스를 캐시한다. 실패한 프라미스는 버려서 재시도가 되게 한다. */
  var cache = {};

  function getJSON(key) {
    if (cache[key]) return cache[key];
    var url = URLS[key];
    var p = fetch(url + (url.indexOf('?') >= 0 ? '&' : '?') + 't=' + Date.now())
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .catch(function (e) {
        delete cache[key];        // 재시도 허용
        throw e;
      });
    cache[key] = p;
    return p;
  }

  function flow()  { return getJSON('flow'); }
  function theme() { return getJSON('theme'); }
  function reload() { cache = {}; }

  /* ── 종목 인덱스 ────────────────────────────────────────────
     후보/거래대금상위/유니버스를 한 목록으로 합친다. 차트 히스토리를
     가진 쪽을 먼저 넣어, 같은 종목이면 정보가 많은 레코드가 이긴다. */
  // 인덱스는 550종목을 훑는다. 관심 목록 30개를 그릴 때마다 다시 만들면
  // 30번 훑는다. flow 객체 하나당 한 번만 만들어 재사용한다.
  var idxCache = (typeof WeakMap === 'function') ? new WeakMap() : null;

  function stockIndex(f) {
    if (!f) return [];
    if (idxCache && idxCache.has(f)) return idxCache.get(f);
    var built = buildIndex(f);
    if (idxCache) idxCache.set(f, built);
    return built;
  }

  function buildIndex(f) {
    var byCode = {}, out = [];
    // 같은 종목이 여러 목록에 있으면 **합친다.** 먼저 온 쪽이 이기게만 하면
    // 이탈 신호 종목의 상세에 시가총액이 안 나오고(그건 universeMetadata 에만
    // 있다), 유니버스 종목의 상세에 이탈 정보가 안 나온다. 어느 쪽이든
    // 화면이 반쯤 빈 채로 뜬다. 종류(kind)는 처음 잡힌 것을 쓴다 — 목록의
    // 성격은 가장 구체적인 것부터 넣기 때문이다.
    function push(arr, kind) {
      (arr || []).forEach(function (c) {
        var code = String(c.code || '').trim();
        if (!code) return;
        var hit = byCode[code];
        if (!hit) {
          var merged = {};
          for (var k in c) merged[k] = c[k];
          hit = byCode[code] = {
            code: code, name: c.name || '', sector: c.sector || '', kind: kind, rec: merged, kinds: [kind]
          };
          out.push(hit);
          return;
        }
        hit.kinds.push(kind);
        for (var k2 in c) if (hit.rec[k2] == null) hit.rec[k2] = c[k2];
        if (!hit.sector && c.sector) hit.sector = c.sector;
        if (!hit.name && c.name) hit.name = c.name;
      });
    }
    push(f.buyCandidates, 'cand');
    push(f.leadingValueTop, 'value');
    push(f.overflowCandidates, 'overflow');
    push(f.exitSignals, 'exit');
    push(f.tradingIntensity, 'ti');
    push(f.universeMetadata, 'universe');
    return out;
  }

  function findStock(f, code) {
    var idx = stockIndex(f);
    for (var i = 0; i < idx.length; i++) if (idx[i].code === String(code)) return idx[i];
    return null;
  }

  /* ── 수급 상태 ──────────────────────────────────────────────
     '지금 비어 있나 / 채워지기 시작했나'를 한 곳에서 판정한다.
     여러 화면이 같은 말을 하려면 규칙이 하나여야 한다. (기존 화면의
     supplyStateOf 와 같은 규칙 — 두 시안이 다른 말을 하면 안 된다.) */
  function supplyState(c) {
    var days = (c && c.dailyFlow10d) || [];
    var turnIdx = -1;
    for (var i = 1; i < days.length; i++) {
      var prev = days[i - 1].instAmount || 0, cur = days[i].instAmount || 0;
      if (prev <= 0 && cur > 0) turnIdx = i;
    }
    var sinceTurn = turnIdx >= 0 ? (days.length - 1 - turnIdx) : -1;
    var streak = (c && c.currentVacancyDays) || 0;

    if (sinceTurn === 0) return { label: '오늘 채우기 시작', tone: 'warn', turnIdx: turnIdx };
    if (sinceTurn > 0 && sinceTurn <= 2) return { label: sinceTurn + '일 전 채우기 시작', tone: 'warn', turnIdx: turnIdx };
    if (streak > 0) return { label: streak + '일째 비어있음', tone: 'down', turnIdx: turnIdx };
    return { label: '수급 관망', tone: 'flat', turnIdx: turnIdx };
  }

  /* 수급 게이지 5단계 — zone 이 1차, 백분위는 세부.
     순서를 뒤집으면 카드와 시트가 서로 다른 단계를 가리킨다(기존 실사고). */
  // 화면 라벨에서 '빈집' 을 뺐다. 그 한 단어가 서로 다른 네 상황을 덮고
  // 있어서(담다가 쉬는 중 / 담다가 돌아섬 / 계속 파는 중 / 팔다가 돌아옴)
  // 어떤 설명을 붙여도 학습이 안 됐다 (2026-08-22 실측 9종목).
  // 백엔드 zone 값('빈집'/'찼음')은 그대로 두고 표시만 바꾼다.
  var SUPPLY_LEVELS = [
    { label: '많이 빠짐', desc: '외인·기관 매수 거의 없음' },
    { label: '빠지는 중', desc: '외인·기관 매수 둔화' },
    { label: '보통',      desc: '외인·기관 소폭 매수' },
    { label: '들어옴',    desc: '외인·기관 매수 진행' },
    { label: '많이 들어옴', desc: '외인·기관 매수 집중' }
  ];

  function supplyLevel(zone, percentile) {
    var p = percentile == null ? 50 : Math.max(0, Math.min(100, percentile));
    if (zone === '빈집') return p < 25 ? 0 : 1;
    if (zone === '찼음') return p > 90 ? 4 : 3;
    return 2;
  }

  /* ── 관심종목 (localStorage) ────────────────────────────── */
  var WKEY = 'next.watch.v1';

  function watchAll() {
    try {
      var raw = global.localStorage.getItem(WKEY);
      var v = raw ? JSON.parse(raw) : [];
      return Array.isArray(v) ? v : [];
    } catch (e) { return []; }
  }
  function watchSave(list) {
    try { global.localStorage.setItem(WKEY, JSON.stringify(list)); } catch (e) {}
  }
  function watchHas(code) {
    return watchAll().some(function (x) { return x.code === String(code); });
  }
  function watchToggle(code, name) {
    var list = watchAll(), c = String(code);
    var i = list.findIndex(function (x) { return x.code === c; });
    if (i >= 0) { list.splice(i, 1); watchSave(list); return false; }
    list.unshift({ code: c, name: name || '', at: Date.now() });
    watchSave(list);
    return true;
  }

  /* ── 용어 사전 ──────────────────────────────────────────────
     화면에 전문어가 나오면 반드시 여기 항목이 있어야 한다.
     '무엇인가 / 왜 보는가 / 어떻게 읽는가' 셋을 채운다. 뜻만 적으면
     읽어도 행동이 안 바뀐다. */
  var GLOSSARY = {
    vacancy: {
      t: '큰손 움직임',
      d: '외인·기관 최근 5일 순매수 합계의 증감. 순매도 전환이 아니라 <b>매수 속도 둔화</b>를 봄.',
      how: '5일 합계가 줄면 매수세 약화. 매일 사고 있어도 합계가 줄면 힘이 빠지는 중.'
    },
    depth: {
      t: '들어온 정도',
      d: '해당 종목 <b>과거 60거래일 수급 이력</b> 내 현재 위치(백분위).',
      how: '하위 8% = 최근 60거래일 중 지금보다 수급이 약했던 날이 8%. 낮을수록 빠진 상태. 종목 간 비교값 아님.'
    },
    osc: {
      t: '수급 오실레이터',
      d: '외인+기관 5일 누적 순매수 ÷ 시가총액의 <b>MACD 히스토그램</b>.',
      how: '0 아래 = 5일 합계 증가 속도가 20일 흐름보다 둔화. 시총 대비 값이라 대형주·소형주 동일 기준.'
    },
    fg: {
      t: '공포·탐욕 지수',
      d: '지수 이평 이격·변동성·추세를 합산한 0~100 시장 심리 지표.',
      how: '25 이하 공포(과매도), 75 이상 탐욕(과열). 매수 신호 아님, 장세 배경 지표. 과열권에서는 비중 축소 참고.'
    },
    crowding: {
      t: '쏠림 (장 난이도)',
      d: '주도 ETF 상위·하위 수익률 격차. 소수 업종 독주 정도.',
      how: '높음 = 소수 업종만 상승, 종목 선별 어려움. 낮음 = 업종 간 편차 축소, 순환매 장.'
    },
    leading: {
      t: '주도 업종',
      d: '세 축으로 선정: 업종 ETF 시장 대비 강도(RS) · 외인·기관 자금 유입 강도 · 거래대금 쏠림.',
      how: '세 축에서 번갈아 최대 7개. 후보 종목은 이 업종 안에서만 선정.'
    },
    rs: {
      t: '시장 대비 강도 (RS)',
      d: 'Mansfield 상대강도의 0~100 정규화값. 50 = 지수 동행, 70 이상 = 지수 대비 뚜렷한 강세.',
      how: '절대 수익률 아님, <b>지수 대비</b>. 하락장에서 덜 빠진 업종도 높게 나옴.'
    },
    trend: {
      t: '추세 생존',
      d: '종가 > 10일 이동평균선.',
      how: '수급이 빠져 있어도 추세가 꺾인 종목은 후보 제외.'
    },
    cash: {
      t: '현금 비중',
      d: '공포·탐욕 지수와 쏠림 신호로 산출한 <b>참고용</b> 현금 비중.',
      how: '과열+쏠림 → 상향, 공포+분산 → 하향. 매매 지시 아님.'
    },
    ti: {
      t: '거래대금 강도 (TI)',
      d: '해당 종목 거래대금의 과거 대비 수준(0~100).',
      how: '20 이하 바닥권에서 상승 + 신고가 = 관심. 80 이상 과열권 = 이미 자금 유입 완료.'
    },
    exit: {
      t: '이탈 신호',
      d: '후보 종목 중 최근 고점 대비 하락 후 10일선 이탈.',
      how: '추세 이탈 <b>사실</b> 표시. 매도 신호 아님. 보유 중이면 진입 근거 재점검.'
    },
    overflow: {
      t: '섹터 상한',
      d: '조건 통과했으나 업종별 상한 초과로 제외된 종목.',
      how: '한 업종 편중 방지 규칙. 제외 종목이 많은 업종 = 그만큼 강한 업종.'
    },
    etfhold: {
      t: 'ETF 편입비중',
      d: '테마 ETF의 실제 편입 비중(%).',
      how: 'ETF 강세 → 종목 자금 유입 추정을 실제 편입 여부로 확인. 미편입도 표시.'
    },
    theme: {
      t: '급등 테마',
      d: '뉴스·실시간 시그널·급등 클러스터 기반 오늘의 테마.',
      how: '오늘 상승 종목과 재료만 표시. 수급 판정은 종목 탭 기준.'
    }
  };

  /* ── 공개 ─────────────────────────────────────────────── */
  global.Core = {
    IS_PROD: IS_PROD, URLS: URLS,
    esc: esc, num: num, won: won, parseWon: parseWon, pct: pct, dirClass: dirClass,
    stamp: stamp, dateLine: dateLine, ago: ago,
    flow: flow, theme: theme, reload: reload,
    stockIndex: stockIndex, findStock: findStock,
    supplyState: supplyState, supplyLevel: supplyLevel, SUPPLY_LEVELS: SUPPLY_LEVELS,
    watchAll: watchAll, watchHas: watchHas, watchToggle: watchToggle,
    GLOSSARY: GLOSSARY
  };
})(window);
