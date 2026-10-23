// 跨 <script> 全局作用域冲突回归测试
// 背景：所有 <script> 共享同一个全局词法环境，engine.js 与 index.html 内联脚本
// 只要有一处顶层 const/let 重名，就会抛
//   SyntaxError: Identifier 'X' has already been declared
// 这是【编译期】错误：出问题的那个 script 整体不执行，页面功能大面积静默失灵。
// 历史案例：index.html 主脚本重复声明 const THEME_KEY（engine.js 已有），
// 导致除函数声明（重复声明不报错、后者覆盖前者）外的全部主脚本代码失效。
//
// 检查手段：
//   A. 静态：抽取每个 script 的顶层 const/let 声明名，跨 script 查重
//   B. 动态：把 engine.js + 所有内联脚本放进同一个 vm context 依次编译执行，
//      有任何重名都会在编译第二个脚本时抛 SyntaxError
//      （node --check 单文件、new Function 单脚本都查不出这种跨脚本冲突）

const fs = require('fs');
const vm = require('vm');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const engineSrc = fs.readFileSync(path.join(__dirname, 'engine.js'), 'utf8');

// ---- 抽取 index.html 里的内联脚本（无 src 的 <script>...</script>）----
const inlineScripts = [];
const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
let m;
while ((m = re.exec(html)) !== null) inlineScripts.push(m[1]);
console.log('内联脚本数量:', inlineScripts.length, '；外加 engine.js');

let fail = 0;

// ---- A. 静态查重：顶层 const/let 声明名 ----
console.log('\n=== A. 顶层 const/let 跨脚本查重 ===');
const declRe = /^(?:const|let)\s+([A-Za-z_$][\w$]*)/gm;
const owner = {};
for (const [i, src] of [['engine.js', engineSrc], ...inlineScripts.map((s, j) => ['inline#' + (j + 1), s])]) {
  let d;
  while ((d = declRe.exec(src)) !== null) {
    const name = d[1];
    if (owner[name]) {
      console.log('  ❌ 重名:', name, '——', owner[name], '与', i);
      fail++;
    } else {
      owner[name] = i;
    }
  }
}
if (fail === 0) console.log('  ✅ 各脚本顶层 const/let 无重名（共 ' + Object.keys(owner).length + ' 个声明）');

// ---- B. 动态验证：同一 context 依次【执行】 ----
// 注意：必须真的 runInContext，不能只 new vm.Script 编译——跨脚本 const/let 重名
// 是在全局词法实例化阶段（执行前一刻）才抛出的，纯编译查不出来
console.log('\n=== B. 同 context 依次执行 ===');
const ctx = vm.createContext({
  console, URL, setTimeout, clearTimeout,
  document: undefined, window: undefined, localStorage: undefined,
  fetch: undefined, navigator: undefined, alert: () => {}
});
const scripts = [['engine.js', engineSrc], ...inlineScripts.map((s, j) => ['inline#' + (j + 1), s])];
for (const [name, src] of scripts) {
  try {
    vm.runInContext(src, ctx, { filename: name });
    console.log('  ✅', name, '执行通过');
  } catch (e) {
    if (/has already been declared/.test(e.message)) {
      console.log('  ❌', name, '跨脚本声明冲突:', e.message);
      fail++;
    } else {
      // 运行期缺宿主对象（document/localStorage 等）是预期的，不算失败，
      // 但全局词法绑定在报错前已实例化，不影响后续脚本的重名检测
      console.log('  ℹ️', name, '运行期停在缺宿主对象（预期）:', e.message.slice(0, 60));
    }
  }
}
// 保险：再单独编译一次，防漏执行期之外的语法问题
for (const [name, src] of scripts) {
  try {
    new vm.Script(src, { filename: name });
  } catch (e) {
    console.log('  ❌', name, '编译报错:', e.message);
    fail++;
  }
}

// ---- C. 确认 run() 的四个分支关键字都在主脚本里 ----
console.log('\n=== C. run() 分流分支完整性 ===');
const mainSrc = inlineScripts[inlineScripts.length - 1];
for (const kw of ["r.type === 'garbage'", "r.type === 'conflict'", "r.type === 'url'", "r.type === 'intent'", 'fillTaFrom', 'fillTaTextFrom']) {
  const ok = mainSrc.includes(kw);
  console.log(' ', ok ? '✅' : '❌', kw);
  if (!ok) fail++;
}

console.log('\n' + (fail === 0 ? '✅ 跨脚本冲突检查全部通过' : '❌ 有 ' + fail + ' 处问题'));
process.exit(fail === 0 ? 0 : 1);
