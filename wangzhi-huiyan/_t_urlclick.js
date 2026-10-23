// 网址可点击跳转功能专项测试
// 验证 normalizeLinkUrl（协议补全/注入防护）与 renderClickableUrl（HTML 结构）
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync(__dirname + '/index.html', 'utf8');
// 抽取被测函数源码（escapeHtml 也在同文件里）
const names = ['escapeHtml', 'normalizeLinkUrl', 'renderClickableUrl'];
let code = '';
for (const n of names) {
  const re = n === 'escapeHtml'
    ? /^function escapeHtml\(s\) \{[\s\S]*?\n\}/m
    : new RegExp('^function ' + n + '\\([\\s\\S]*?\\n}', 'm');
  const m = html.match(re);
  if (!m) { console.error('未找到函数: ' + n); process.exit(1); }
  code += m[0] + '\n\n';
}
const ctx = vm.createContext({ console });
vm.runInContext(code, ctx);

let pass = 0, fail = 0;
const eq = (label, got, want) => {
  const ok = got === want;
  if (ok) pass++; else { fail++; console.log('  FAIL', label.padEnd(46), '=>', JSON.stringify(got), '(期望 ' + JSON.stringify(want) + ')'); }
};
const ok = (label, cond) => { if (cond) pass++; else { fail++; console.log('  FAIL', label); } };

// ---------- 1. normalizeLinkUrl 协议补全 ----------
console.log('=== 1. 协议自动补全 ===');
const N = t => vm.runInContext('normalizeLinkUrl(' + JSON.stringify(t) + ')', ctx);
eq('已有 https 原样', N('https://www.baidu.com'), 'https://www.baidu.com');
eq('已有 http 原样', N('http://www.baidu.com'), 'http://www.baidu.com');
eq('大写 HTTPS 原样', N('HTTPS://WWW.BAIDU.COM'), 'HTTPS://WWW.BAIDU.COM');
eq('裸域名补 https', N('www.baidu.com'), 'https://www.baidu.com');
eq('裸域名无www补 https', N('y.qq.com'), 'https://y.qq.com');
eq('带路径裸域名补全', N('example.com/a/b?c=1'), 'https://example.com/a/b?c=1');
eq('去掉前导斜杠', N('//www.baidu.com'), 'https://www.baidu.com');
eq('空串', N(''), '');
eq('null', N(null), '');
eq('undefined', N(undefined), '');
eq('纯空格', N('   '), '');

// ---------- 2. 注入防护 ----------
console.log('=== 2. 注入防护（必须返回空 → 渲染为纯文本）===');
eq('javascript: 拒绝', N('javascript:alert(1)'), '');
eq('data: 拒绝', N('data:text/html,<script>alert(1)</script>'), '');
eq('vbscript: 拒绝', N('vbscript:msgbox(1)'), '');
eq('file: 拒绝', N('file:///etc/passwd'), '');

// ---------- 3. renderClickableUrl 输出结构 ----------
console.log('=== 3. 渲染结构 ===');
const R = (u, allow) => vm.runInContext(
  'renderClickableUrl(' + JSON.stringify(u) + ',' + (allow === undefined ? 'undefined' : String(allow)) + ')', ctx);

// 默认允许跳转
let h = R('https://y.qq.com/');
ok('含 target=_blank', h.includes('target="_blank"'));
ok('含 rel=noopener noreferrer', h.includes('rel="noopener noreferrer"'));
ok('含 class url-link', h.includes('class="url-link"'));
ok('href 与显示文本一致', h.includes('href="https://y.qq.com/"') && h.includes('>https://y.qq.com/</a>'));

// 裸域名自动补协议后再进 href
h = R('y.qq.com');
ok('裸域名 href 已补 https', h.includes('href="https://y.qq.com"'));
ok('裸域名显示原文（不补协议）', h.includes('>y.qq.com</a>'));

// 禁止跳转（高危站）
h = R('https://evil.example', false);
ok('禁止跳转时无 <a>', !h.includes('<a '));
ok('禁止跳转时用 url-text', h.includes('class="url-text"'));
ok('禁止跳转时保留文字', h.includes('https://evil.example'));

// 危险协议 → 纯文本且不产生可点链接
h = R('javascript:alert(1)');
ok('javascript: 渲染为纯文本', h.includes('class="url-text"'));
ok('javascript: 不产生 href', !h.includes('href='));

// ---------- 4. XSS 转义 ----------
console.log('=== 4. XSS 转义 ===');
h = R('https://x.com/"onmouseover="alert(1)');
ok('属性内引号被转义', !h.includes('"onmouseover="alert'));
ok('转义后仍含 &quot;', h.includes('&quot;'));
h = R('https://x.com/<script>alert(1)</script>');
ok('script 标签被转义', !h.includes('<script>'));
ok('含 &lt;script&gt;', h.includes('&lt;script&gt;'));

// ---------- 5. CSS 变量与类名落地检查 ----------
console.log('=== 5. CSS/HTML 落地检查 ===');
const css = html.replace(/\/\*[\s\S]*?\*\//g, '');   // 剥注释后再查
ok('定义了 .url-link 样式', /\.url-link\s*\{/.test(css));
ok('.url-link 用 cursor:pointer', /\.url-link\{[^}]*cursor:pointer/.test(css.replace(/\n/g, '')));
ok('.url-link 有 hover 态', /\.url-link:hover\s*\{/.test(css));
ok('.url-link 有 focus-visible 无障碍态', /\.url-link:focus-visible/.test(css));
ok('定义了 .url-text 样式', /\.url-text\s*\{/.test(css));
ok('深色主题定义了 --blue-hover', /--blue-hover\s*:/.test(css));
ok('浅色主题覆盖了 --blue-hover', (css.match(/--blue-hover\s*:/g) || []).length >= 2);
ok('无残留的无效 var(--accent)', !/var\(--accent\)/.test(css));

// 三处渲染点都已改造
ok('原始提交链接已改造', html.includes('renderClickableUrl(res.input, cls'));
ok('跳转链路已改造', html.includes('renderClickableUrl(s, cls'));
ok('推荐卡片已改造', html.includes('renderClickableUrl(s.url)'));
ok('旧的 onclick=clipboard 写法已移除', !html.includes("onclick=\"navigator.clipboard?.writeText('${res.input}')\""));
// 复制按钮仍在
ok('原始链接保留复制按钮', html.includes('data-url="${escapeHtml(res.input)}" onclick="copyLinkFrom(this)"'));
ok('推荐卡片保留复制按钮', html.includes('data-url="${escapeHtml(s.url)}" onclick="copyLinkFrom(this)"'));
// 高危站不给跳转的安全设计
ok('高危站不提供跳转提示', html.includes('高危站点不提供跳转'));

console.log('\n通过 ' + pass + ' / 失败 ' + fail);
process.exit(fail === 0 ? 0 : 1);