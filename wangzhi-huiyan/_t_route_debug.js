// 逐层追踪 routeInput 的判定过程（调试用）
// 从 engine.js 抽出相关函数源码，放进 vm 沙箱执行（engine.js 未导出这些内部函数）
const fs = require('fs');
const vm = require('vm');

const src = fs.readFileSync(__dirname + '/engine.js', 'utf8');
const names = ['extractUrls', 'isValidUrl', 'saneHost', 'matchWholeUrl', 'urlDominance', 'looksLikeDemand', 'routeInput'];
let code = '';
for (const n of names) {
  const m = src.match(new RegExp('^function ' + n + '\\([\\s\\S]*?\\n}', 'm'));
  if (!m) { console.error('未找到函数: ' + n); process.exit(1); }
  code += m[0] + '\n\n';
}
// 注意：vm 上下文默认没有 URL 这类宿主内置对象，必须显式传进去，
// 否则 isValidUrl 里 new URL 会抛 ReferenceError 被 catch 吞掉、恒返回 false，
// 追踪结果完全失真（曾因此误判过 matchWholeUrl 的行为）
const ctx = vm.createContext({ console, URL });
vm.runInContext(code, ctx);

const inputs = [
  '123456',
  '帮我检测 https://www.baidu.com 这个网站安全吗',
  'https://baidu.com 帮我看看',
  '!!!abc.def!!!',
  'qwe123.cn',
  'zzz.zzz',
  '测试abc.def乱码',
  '画画',
  '乱码乱码.cn',
  '啊啊啊啊？？？',
];

for (const t of inputs) {
  console.log('━━━ ' + JSON.stringify(t));
  const whole = vm.runInContext('matchWholeUrl(' + JSON.stringify(t) + ')', ctx);
  console.log('  matchWholeUrl:', JSON.stringify(whole));
  const urls = vm.runInContext('extractUrls(' + JSON.stringify(t) + ')', ctx);
  console.log('  extractUrls:', JSON.stringify(urls));
  if (urls.length) {
    console.log('  urlDominance:', vm.runInContext('urlDominance(' + JSON.stringify(t) + ',' + JSON.stringify(urls) + ')', ctx));
  }
  console.log('  looksLikeDemand:', vm.runInContext('looksLikeDemand(' + JSON.stringify(t) + ')', ctx));
  const r = vm.runInContext('routeInput(' + JSON.stringify(t) + ')', ctx);
  console.log('  routeInput:', JSON.stringify(r));
}

// 附带验证 Node 的 URL 对裸数字的处理
console.log('\n━━━ Node URL 行为验证');
for (const u of ['https://123456', 'https://1.2.3.4', 'https://a.b']) {
  try {
    const p = new URL(u);
    console.log(' ', u, '=> hostname=' + JSON.stringify(p.hostname));
  } catch (e) {
    console.log(' ', u, '=> 抛错:', e.message);
  }
}
