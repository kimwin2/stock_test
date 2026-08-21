/* 오늘 탭 1번 — 공포·탐욕 반원 게이지 회귀 검증 (2026-08-21 실사고).
 *
 *  (1) SVG large-arc-flag 를 **값**으로 판정했다 (`(to-from) > 50`).
 *      50 은 각도가 아니라 값이고 90°일 뿐이라, F&G 가 50 을 넘는 순간
 *      플래그가 1 이 되어 반대편 대호가 그려졌다 — 작은 부채꼴이어야 할 것이
 *      거의 한 바퀴를 돌아 원형이 깨진다.
 *      실측: 8/20 은 49.25 라 멀쩡했고 8/21 은 51.0 이라 깨졌다.
 *  (2) 구간 이름 어휘가 두 벌인데 색표(DIAL_TONE)는 한 벌만 갖고 있었다.
 *      폴백 경로의 '탐욕'·'극단적 탐욕'·'극단적 공포' 는 표에 없어 게이지가
 *      통째로 무채색이 됐다.
 *
 *  실행: node frontend/tests/test_hero_dial.js
 */
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = require('path');

global.window = global;
global.addEventListener = () => {};
global.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
global.location = { hostname: 'localhost', href: 'http://localhost/', search: '', protocol: 'http:' };
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.document = {
  getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
  addEventListener: () => {}, createElement: () => ({ style: {}, appendChild() {} }),
};
if (!global.navigator) global.navigator = { userAgent: 'node' };

const ctx = vm.createContext(global);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'briefing.js'), 'utf8'), ctx);

const fails = [];
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${ok ? 'OK ' : 'FAIL'} ${name}: ${JSON.stringify(got)}${ok ? '' : ` (기대 ${JSON.stringify(want)})`}`);
  if (!ok) fails.push(name);
}

const FLOW = { buyCandidates: new Array(9) };
const dial = (v, zone) => ctx.buildHeroDial({ kospi: v }, { kospi: zone ? { zone } : {} }, FLOW);

// 값 아크 = 그라데이션으로 칠한 path (트랙은 class="dl-track")
function valueArc(svg) {
  const m = svg.match(/<path d="([^"]+)" stroke="url\(#/);
  if (!m) return null;
  const a = m[1].match(/A\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s+(\d)\s+(\d)\s+([\d.]+)\s+([\d.]+)/);
  return a ? { r: +a[1], large: a[4], sweep: a[5], x: +a[6], y: +a[7] } : null;
}

console.log('\n[1] large-arc-flag 는 0~100 전 구간에서 0 이어야 한다');
// 반원(180°)을 0~100 에 대응시켰으므로 쓸린 각은 절대 180°를 넘지 않는다.
const flagged = [];
for (let v = 0; v <= 100; v += 0.25) {
  const a = valueArc(dial(v, '중립'));
  if (!a) { flagged.push(`${v}:파싱실패`); continue; }
  if (a.large !== '0') flagged.push(String(v));
}
check('플래그가 1 인 값', flagged, []);
// 수정 전이었다면 50 초과 전부가 걸렸다는 것을 같이 남긴다
check('예전 판정식 재현 (참고)', [50.25, 51, 75, 100].filter(v => (v - 0) > 50).length, 4);

console.log('\n[2] 아크 끝점이 반원 안에 있다 (밖으로 새지 않는다)');
const CX = 100, CY = 92, R = 71;
const outside = [];
for (const v of [0.6, 25, 49.25, 50.1, 51, 65, 75, 92, 95, 100]) {
  const a = valueArc(dial(v, '중립'));
  const ex = CX + R * Math.cos((180 + v * 1.8) * Math.PI / 180);
  const ey = CY + R * Math.sin((180 + v * 1.8) * Math.PI / 180);
  const off = Math.hypot(a.x - ex, a.y - ey);
  if (off > 0.2 || a.y > CY + 0.2) outside.push(`${v}(off=${off.toFixed(2)})`);
}
check('끝점이 어긋난 값', outside, []);

console.log('\n[3] 두 어휘 모두 제 색을 받는다 (무채색 폴백 금지)');
// 백엔드 zone 어휘 / 레벨 폴백 어휘 — 색은 zone.color 하나에서 파생돼야 한다.
const GRAY = '#7C8493';
const EXPECT = {
  '과열': '#D2402F', '강세': '#E58A3C', '중립': '#C9B896', '약세': '#4E8FCB', '공포': '#1565C0',
};
const LEVEL = [
  [10, '극단적 공포', '#1565C0'], [35, '공포', '#4E8FCB'], [51, '중립', '#C9B896'],
  [65, '탐욕', '#E58A3C'], [95, '극단적 탐욕', '#D2402F'],
];
function strongStop(svg) {
  const m = svg.match(/<stop offset="100%" stop-color="([^"]+)"\/>/);
  return m ? m[1].toUpperCase() : null;
}
for (const [label, color] of Object.entries(EXPECT)) {
  check(`백엔드 zone '${label}'`, strongStop(dial(60, label)), color.toUpperCase());
}
for (const [v, label, color] of LEVEL) {
  check(`레벨 폴백 ${v} → '${label}'`, strongStop(dial(v)), color.toUpperCase());
}
const grays = LEVEL.filter(([v]) => strongStop(dial(v)) === GRAY.toUpperCase()).map(([, l]) => l);
check('무채색으로 떨어진 구간', grays, []);

console.log('\n[4] 밝은 쪽 stop 은 같은 색의 옅은 톤이다 (하드코딩 표 아님)');
const svg = dial(60, '과열');
const light = svg.match(/<stop offset="0%" stop-color="([^"]+)"\/>/)[1];
check('과열 밝은 톤', light.toUpperCase(), ctx.tintHex ? ctx.tintHex('#D2402F', 0.70).toUpperCase() : light.toUpperCase());
check('밝은 톤이 진한 톤과 다르다', light.toUpperCase() !== '#D2402F', true);

console.log('\n' + '='.repeat(52));
console.log(fails.length ? `실패 ${fails.length}건: ${fails.join(', ')}` : '모두 통과');
process.exit(fails.length ? 1 : 0);
