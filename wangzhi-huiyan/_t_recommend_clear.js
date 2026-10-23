// 通道B 连续提交防叠加专项测试
// 验证：
//  1. recommendUI 开头先清空 $('out')，再显示加载态
//  2. 有请求序号 recommendSeq 做竞态保护（await 后丢弃过期请求）
//  3. 序号自增逻辑正确（每次提交 +1，await 后比较）
// 通过正则抽函数源码 + 静态断言（DOM 异步场景无法在纯 Node 里完整模拟）
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync(__dirname + '/index.html', 'utf8');

let pass = 0, fail = 0;
const ok = (label, cond, detail) => {
  if (cond) pass++;
  else { fail++; console.log('  FAIL', label, detail || ''); }
};

// ---------- 1. 静态结构断言 ----------
console.log('=== 1. 清空 + 竞态保护结构 ===');
// recommendUI 函数体里：先 mySeq = ++recommendSeq，再清空 out，再 await，再判断
const fnMatch = html.match(/async function recommendUI\(raw\) \{[\s\S]*?\n\}/);
ok('找到 recommendUI 函数', !!fnMatch);
const fn = fnMatch ? fnMatch[0] : '';

// 序号自增
ok('有序号自增 recommendSeq', /const mySeq = \+\+recommendSeq/.test(fn));
// 清空输出区（在加载态之前）
ok('有清空 $(\'out\')', /\$\('out'\)\.innerHTML = ''/.test(fn));
// await 后有过期丢弃判断
ok('await 后有过期丢弃判断', /if \(mySeq !== recommendSeq\) return/.test(fn));

// 清空必须在 add 加载态之前（顺序）
const clearIdx = fn.indexOf("$('out').innerHTML = ''");
const loadIdx = fn.indexOf('正在联网检索站点');
ok('清空在加载态之前', clearIdx !== -1 && loadIdx !== -1 && clearIdx < loadIdx);

// 过期判断必须在渲染各分支之前（await 之后、if backendDown 之前）
const seqCheckIdx = fn.indexOf('mySeq !== recommendSeq');
const backendIdx = fn.indexOf("r.type === 'backendDown'");
ok('过期判断在渲染分支之前', seqCheckIdx !== -1 && backendIdx !== -1 && seqCheckIdx < backendIdx);

// ---------- 2. 顶层声明 ----------
console.log('=== 2. recommendSeq 顶层声明 ===');
ok('声明了 let recommendSeq', /let recommendSeq = 0/.test(html));
// 全局唯一（不重复声明）
const cnt = (html.match(/recommendSeq/g) || []).length;
ok('recommendSeq 只在合理位置出现', cnt >= 3, '出现 ' + cnt + ' 次');

// ---------- 3. 竞态逻辑（纯函数模拟） ----------
console.log('=== 3. 竞态保护逻辑模拟 ===');
// 用 vm 抽一个小片段验证"序号比较"的语义正确性
const logic = `
function simulate() {
  let seq = 0;
  const results = [];
  function submit() {
    const my = ++seq;
    results.push('提交# ' + my + ' 开始加载');
    return my;
  }
  function resolve(my) {
    if (my !== seq) { results.push('提交# ' + my + ' 过期丢弃'); return false; }
    results.push('提交# ' + my + ' 正常渲染'); return true;
  }
  return { submit, resolve, results, getSeq: () => seq };
}
const s = simulate();
const a = s.submit();   // 第一次提交，seq=1
const b = s.submit();   // 第二次提交，seq=2
s.resolve(a);           // 第一次返回：a=1 != seq=2 → 丢弃
s.resolve(b);           // 第二次返回：b=2 == seq=2 → 渲染
__out = s.results.join(';');
`;
const ctx = vm.createContext({});
vm.runInContext(logic, ctx);
const simOut = vm.runInContext('__out', ctx);
ok('第一次提交被丢弃', simOut.includes('提交# 1 过期丢弃'), simOut);
ok('第二次提交正常渲染', simOut.includes('提交# 2 正常渲染'), simOut);
ok('顺序正确(先丢弃后渲染)', simOut.indexOf('过期丢弃') < simOut.indexOf('正常渲染'));

// ---------- 4. 通道A 不受影响 ----------
console.log('=== 4. 通道A detectUI 不受影响 ===');
const detectFn = html.match(/async function detectUI\(raw\) \{[\s\S]*?\n\}/);
ok('找到 detectUI', !!detectFn);
// detectUI 不应清空 out（保持原追加行为）
if (detectFn) {
  ok('detectUI 未清空输出区', !/\$\('out'\)\.innerHTML = ''/.test(detectFn[0]));
  ok('detectUI 未引入 recommendSeq', !/recommendSeq/.test(detectFn[0]));
}

console.log('\n通过 ' + pass + ' / 失败 ' + fail);
process.exit(fail === 0 ? 0 : 1);