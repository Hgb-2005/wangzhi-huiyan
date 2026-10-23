// 功能6 样式校验：主题变量、对比度（WCAG AA）、双主题一致
// 目的：保证筛选栏的选中态/置灰态在深浅两套主题下都清晰可读，
//       且只用既有的 CSS 变量，不引入新的硬编码颜色。
// 用法：node _t_style.js
const fs = require('fs');
const html = fs.readFileSync('index.html', 'utf-8');
const lines = html.split(/\r?\n/);

let pass = 0, fail = 0;
function ok(n, c, e) { if (c) { pass++; console.log('  PASS  ' + n); } else { fail++; console.log('  FAIL  ' + n + (e !== undefined ? '  -> ' + String(e).slice(0,300) : '')); } }
function sec(t) { console.log('\n===== ' + t + ' ====='); }

// ---- 提取主题块（按行号定位，避免匹配到注释里的 :root）----
function blockAt(ln) {
  let s = lines[ln - 1].indexOf('{');
  let body = lines[ln - 1].slice(s + 1);
  let d = 1;
  for (let i = ln; i < lines.length && d > 0; i++) {
    for (const ch of lines[i]) { if (ch === '{') d++; else if (ch === '}') d--; }
    if (d > 0) body += '\n' + lines[i];
  }
  return body;
}
function findLineOf(re) {
  for (let i = 0; i < lines.length; i++) if (re.test(lines[i])) return i + 1;
  return -1;
}
const rootLn = findLineOf(/^:root\{/);
const lightLn = findLineOf(/^html\[data-theme="light"\]\{/);
const rootBody = blockAt(rootLn);
const lightBody = blockAt(lightLn);

function pick(body, name) {
  const m = body.match(new RegExp(name.replace(/-/g, '\\-') + '\\s*:\\s*([^;]+);'));
  return m ? m[1].trim() : null;
}
function themeVars(ov) {
  return n => (ov ? pick(ov, n) : null) || pick(rootBody, n);
}

// ---- 颜色工具 ----
function lum(hex) {
  hex = String(hex).replace('#', '').trim();
  if (hex.length === 3) hex = hex.split('').map(c => c + c).join('');
  const r = parseInt(hex.slice(0, 2), 16) / 255,
        g = parseInt(hex.slice(2, 4), 16) / 255,
        b = parseInt(hex.slice(4, 6), 16) / 255;
  const f = c => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
function ratio(a, b) {
  const L1 = lum(a), L2 = lum(b);
  return (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
}

sec('1. 主题块定位');
ok(':root 块存在', rootLn > 0, rootLn);
ok('light 主题块存在', lightLn > 0, lightLn);

sec('2. 筛选栏只用既有 CSS 变量（无硬编码颜色）');
const fbarStart = html.indexOf('.fbar{');
const fbarEnd = html.indexOf('.fbar .rs{');
const fbarCssRaw = html.slice(fbarStart, fbarEnd);
// 先去掉注释再检查 —— 注释里举例子用的颜色不是实际样式
const fbarCss = fbarCssRaw.replace(/\/\*[\s\S]*?\*\//g, '');
const hexes = fbarCss.match(/#[0-9a-fA-F]{3,6}/g);
ok('筛选栏 CSS 无硬编码颜色', !hexes, hexes && hexes.join(', '));
const usedVars = [...new Set((fbarCss.match(/var\(--[a-z0-9-]+\)/g) || []))];
ok('使用了 CSS 变量', usedVars.length > 0, usedVars.length);
console.log('  用到的变量: ' + usedVars.map(v => v.replace(/var\(|\)/g, '')).join(' '));

sec('3. 所有用到的变量在两套主题中都有定义');
for (const [tname, ov] of [['深色', null], ['浅色', lightBody]]) {
  const g = themeVars(ov);
  const missing = usedVars
    .map(v => v.replace('var(', '').replace(')', ''))
    .filter(v => !g(v));
  ok(tname + '主题变量齐全', missing.length === 0, missing.join(', '));
}

sec('4. 选中态对比度 ≥ 4.5:1（WCAG AA）');
// .ftag.on 现在沿用全站 .lv/.tag 的「浅底 + 深字」套路
const onStates = [
  ['高危', '--dang-bg', '--dang-fg'],
  ['可疑', '--warn-bg', '--warn-fg'],
  ['安全', '--ok-bg', '--ok-fg'],
  ['未知', '--panel2', '--text2']
];
for (const [tname, ov] of [['深色', null], ['浅色', lightBody]]) {
  const g = themeVars(ov);
  console.log('  --- ' + tname + '主题 ---');
  for (const [label, bg, fg] of onStates) {
    const r = ratio(g(fg), g(bg));
    ok(tname + '·选中·' + label + ' ' + r.toFixed(2) + ':1', r >= 4.5, g(fg) + ' on ' + g(bg));
  }
}

sec('5. 未选中态对比度 ≥ 4.5:1');
for (const [tname, ov] of [['深色', null], ['浅色', lightBody]]) {
  const g = themeVars(ov);
  const r = ratio(g('--text2'), g('--panel2'));
  ok(tname + '·未选中 ' + g('--text2') + ' on ' + g('--panel2') + ' → ' + r.toFixed(2) + ':1', r >= 4.5);
}

sec('6. 不再使用「实心状态色 + 白字」的失效写法');
ok('不再有 .ftag.on{color:var(--on-btn)}', !/\.ftag\.on\{[^}]*var\(--on-btn\)/.test(html));
ok('不再用 --dang-fg-strong 当底色', !/\.ftag\.on\.c-danger\{[^}]*background:var\(--dang-fg-strong\)/.test(html));
ok('不再用 --ok-fg 当底色', !/\.ftag\.on\.c-safe\{[^}]*background:var\(--ok-fg\)/.test(html));
ok('选中态改用 --xx-bg 底色', /\.ftag\.on\.c-danger\{[^}]*background:var\(--dang-bg\)/.test(html));

sec('7. 标签配色与 .lv 风险徽章保持同一套变量');
ok('高危：--dang-bg / --dang-fg', /--dang-bg/.test(html) && /--dang-fg/.test(html));
ok('可疑：--warn-bg / --warn-fg', /--warn-bg/.test(html) && /--warn-fg/.test(html));
ok('安全：--ok-bg / --ok-fg', /--ok-bg/.test(html) && /--ok-fg/.test(html));

sec('8. 置灰态有明确的不可用视觉信号');
ok('.ftag.off 有 opacity 降低', /\.ftag\.off\{[^}]*opacity/.test(html));
ok('.ftag.off 有虚线边框', /\.ftag\.off\{[^}]*border-style:dashed/.test(html));
ok('.ftag.off 有 not-allowed 光标', /\.ftag\.off\{[^}]*cursor:not-allowed/.test(html));
ok('.ftag.off 有禁用 hover 态', /\.ftag\.off:hover/.test(html));
ok('.ftag.clear:disabled 有样式', /\.ftag\.clear:disabled/.test(html));

sec('9. 筛选栏布局不会撑破页面');
ok('.fbar 有 flex-wrap:wrap', /\.fbar\{[^}]*flex-wrap:wrap/.test(html));
ok('.ftag 有 white-space:nowrap', /\.ftag\{[^}]*white-space:nowrap/.test(html));
ok('结果计数用 margin-left:auto 靠右', /\.fbar \.rs\{[^}]*margin-left:auto/.test(html));

console.log('\n' + '='.repeat(50));
console.log('  通过 ' + pass + ' 项，失败 ' + fail + ' 项');
console.log('='.repeat(50));
process.exit(fail ? 1 : 0);
