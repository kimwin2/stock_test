/* 수급·주도 탭 차트 회귀 검증 — 2026-08-20 실사고.
 *
 *  (1) 마지막날 색: 현재가 pill 이 60일 **구간** 방향으로 칠해져 있었다.
 *      실측 후보 6종목 중 3종목(티에프이·가온칩스·심텍)이 그날 양봉인데
 *      pill 은 파랑이었다. HTS 표준대로 **전일 종가 대비**여야 한다.
 *  (2) 가격 숫자: 가격(FDR 일봉)과 수급 오실레이터(투자자별)는 끝나는 날이
 *      다른데 두 배열의 **꼬리를 맞춰** 겹쳐 그렸다. 60포인트 전부 어긋났다.
 *
 *  실행: node frontend/tests/test_flow_chart.js
 */
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = require('path');

// ── 브라우저 전역 스텁 (flow.js 는 모듈이 아니라 전역 스크립트다) ──
global.window = global;
global.location = { hostname: 'localhost', href: 'http://localhost/', search: '', protocol: 'http:' };
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.document = {
  getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
  addEventListener: () => {}, createElement: () => ({ style: {}, appendChild() {} }),
};
if (!global.navigator) global.navigator = { userAgent: 'node' };

const ctx = vm.createContext(global);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'flow.js'), 'utf8'), ctx);

const RED = '#E53935', BLUE = '#1565C0', GRAY = '#4A5058';
const fails = [];
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${ok ? 'OK ' : 'FAIL'} ${name}: ${JSON.stringify(got)}${ok ? '' : ` (기대 ${JSON.stringify(want)})`}`);
  if (!ok) fails.push(name);
}

// ── 그날의 실제 모양을 최소한으로 재현 ────────────────────────────
// 가격은 8/20 까지, 수급은 8/19 까지. 거래일 집합도 완전히 같지 않다.
function makeCandidate({ closes, dates, oscDates }) {
  const n = closes.length;
  return {
    name: '테스트', code: '000000',
    close: closes[n - 2],
    priceHistory60d: closes,
    dateHistory60d: dates,
    ohlc60d: closes.map((c, i) => ({
      o: i ? closes[i - 1] : c, h: Math.max(c, i ? closes[i - 1] : c),
      l: Math.min(c, i ? closes[i - 1] : c), c, v: 1000,
    })),
    supplyOscHistory: oscDates.map((d, i) => ({ date: d, ratio: 0, osc: (i % 7 - 3) * 1e-4 })),
  };
}

const DATES = [];
for (let d = 1; d <= 30; d++) DATES.push(`2026-07-${String(d).padStart(2, '0')}`);
const OSC_DATES = DATES.slice(0, DATES.length - 1);   // 수급은 하루 늦다

function svgOf(c) { return ctx.renderMiniPriceChart(c, { width: 760, height: 220 }); }
function pillColor(svg) {
  const m = [...svg.matchAll(/<rect x="[\d.]+" y="[\d.]+" width="[\d.]+" height="13" rx="2\.5" fill="(#[0-9A-Fa-f]{6})"/g)].pop();
  return m ? m[1] : null;
}
function pillNumber(svg) {
  const m = svg.match(/font-size="9\.2"[^>]*>([\d,]+)<\/text>/);
  return m ? Number(m[1].replace(/,/g, '')) : null;
}
function oscPointCount(svg) {
  const m = [...svg.matchAll(/<polyline points="([^"]+)" fill="none" stroke="[^"]+" stroke-width="1\.4"/g)];
  return m.length ? m[0][1].trim().split(' ').length : 0;
}

console.log('\n[1] 마지막날 색은 전일 종가 대비다 (60일 구간 방향이 아니다)');
// 60일 내내 내렸지만 마지막날 반등 — 예전 코드는 여기서 파란 pill 을 그렸다.
const down = DATES.map((_, i) => 10000 - i * 100);
down[down.length - 1] = down[down.length - 2] + 500;
check('구간 하락 + 마지막날 상승 → 빨강',
  pillColor(svgOf(makeCandidate({ closes: down, dates: DATES, oscDates: OSC_DATES }))), RED);

const up = DATES.map((_, i) => 10000 + i * 100);
up[up.length - 1] = up[up.length - 2] - 500;
check('구간 상승 + 마지막날 하락 → 파랑',
  pillColor(svgOf(makeCandidate({ closes: up, dates: DATES, oscDates: OSC_DATES }))), BLUE);

const flat = DATES.map((_, i) => 10000 + i * 100);
flat[flat.length - 1] = flat[flat.length - 2];
check('마지막날 보합 → 회색 (빨강도 파랑도 아니다)',
  pillColor(svgOf(makeCandidate({ closes: flat, dates: DATES, oscDates: OSC_DATES }))), GRAY);

console.log('\n[2] 가격 숫자는 가격 시계열의 마지막 종가다');
const c2 = makeCandidate({ closes: up, dates: DATES, oscDates: OSC_DATES });
check('pill 숫자', pillNumber(svgOf(c2)), up[up.length - 1]);

console.log('\n[3] 오실레이터는 날짜로 맞춘다 — 꼬리 맞추기 금지');
// 수급에 없는 날(마지막 하루)은 점을 안 찍는다. 꼬리를 맞추면 30개가 되고
// 전 구간이 하루씩 밀린다.
check('그려진 오실레이터 점 수 = 두 시계열이 공유하는 날 수',
  oscPointCount(svgOf(c2)), OSC_DATES.length);

// 수급이 중간에 빠진 날 — 0 으로 메우면 안 된다 ('중립' 이라는 없는 관측이 생긴다)
const holed = OSC_DATES.filter((_, i) => i !== 5);
check('중간에 빠진 날은 건너뛴다',
  oscPointCount(svgOf(makeCandidate({ closes: up, dates: DATES, oscDates: holed }))), holed.length);

console.log('\n[4] 수급 기준일이 가격과 다르면 화면에 밝힌다');
const svg4 = svgOf(c2);
check('오실레이터 제목에 기준일 표기', /수급 오실레이터 · \d+\/\d+ 기준/.test(svg4), true);
const sameDay = makeCandidate({ closes: up, dates: DATES, oscDates: DATES });
check('같은 날이면 표기 없음', /수급 오실레이터 · /.test(svgOf(sameDay)), false);

console.log('\n' + '='.repeat(52));
console.log(fails.length ? `실패 ${fails.length}건: ${fails.join(', ')}` : '모두 통과');
process.exit(fails.length ? 1 : 0);
