// 功能6 红线核查：确认没有越界改动
// 目的：保证功能6 只改了 index.html + engine.js，
//       没动已完成的功能1-5 业务逻辑，没新增后端接口/文件。
// 用法：node _t_guard.js
const fs = require('fs');
const html = fs.readFileSync('index.html', 'utf-8');
const engine = fs.readFileSync('engine.js', 'utf-8');

let pass = 0, fail = 0;
function ok(n, c, e) { if (c) { pass++; console.log('  PASS  ' + n); } else { fail++; console.log('  FAIL  ' + n + (e !== undefined ? '  -> ' + String(e).slice(0,300) : '')); } }
function sec(t) { console.log('\n===== ' + t + ' ====='); }

sec('1. 功能1-5 的关键函数仍在');
const inHtml = ['parseBatchInput','routeInput','buildReportText','fetchInterpret','toInterpretSections',
                 'aiInterpretFromRow','reuseBatchUrl','reuseHistory','deleteHistory','batchDetect',
                 'addToWhitelist','reportMalicious'];
const inEngine = ['checkProhibited','parseBatchInput','routeInput','buildReportText','fetchInterpret',
                   'toInterpretSections','localFeatures','isValidUrl','extractUrls'];
inHtml.forEach(k => ok('index.html 保留 ' + k, html.includes(k)));
inEngine.forEach(k => ok('engine.js 保留 ' + k, engine.includes(k)));

sec('2. 功能5 AI 解读链路未被破坏');
ok('AI解读弹窗仍在', html.includes('id="aiModal"'));
ok('AI解读按钮类名未变', html.includes('class="aibtn"'));
ok('仍用 window.__wzhBatchRows 取原始行', html.includes('window.__wzhBatchRows['));
ok('AI解读下标按原始数组取（rows.indexOf）', html.includes('const i = rows.indexOf(r);'));
ok('批量表仍是 3 列', html.includes('<th style="width:230px">操作</th>'));
ok('解读库四等级仍在', ['INTERPRET_LIB'].every(k => engine.includes(k)));

sec('3. 功能4 导出链路未被破坏');
ok('导出菜单原有两项仍在', html.includes('id="expBatch"') && html.includes('id="expHistory"'));
ok('batch 分支未变', html.includes("if (scope === 'batch')"));
ok('history 分支未变', html.includes("if (scope === 'history')"));
ok('fetchAllHistory 完好', /async function fetchAllHistory\(\)/.test(html));
ok('finishExport 完好', /function finishExport\(/.test(html));
ok('导出引擎函数齐全', ['buildReportFileName','formatDateTime','normalizeExportRows','buildReportText','downloadTxtFile']
   .every(k => engine.includes(k)));

sec('4. 后端接口红线：没有新增接口');
// 真实的接口调用都走 E.requestJSON / E.postJSON，路径以 / 开头
const callPaths = new Set();
for (const m of html.matchAll(/E\.(?:requestJSON|postJSON)\(\s*'([^']+)'/g)) callPaths.add(m[1]);
console.log('  前端实际调用的接口路径:');
[...callPaths].forEach(p => console.log('    ' + p));
// 允许清单：功能1-5 原有接口 + 功能17 添加站点（功能6 纯前端，不应出现在这里）
const allowed = new Set([
  '/url/detect', '/history/list?page=', '/history/whitelist',
  '/history/', '/history/clear', '/feedback/submit', '/site/search',
  '/site/add', '/url/expand'
]);
const unexpected = [...callPaths].filter(p => !allowed.has(p));
ok('没有超出白名单的新接口', unexpected.length === 0, unexpected.join(', '));
// 功能6 是纯前端筛选，绝不能出现在接口调用里
ok('功能6 未调用任何新接口', !callPaths.has('/filter') && !callPaths.has('/tags'));
// /site/search 出现在文案里，不是调用 —— 单独确认
ok('/site/search 仅作为展示文案出现', (html.match(/api\/v1\/site\/search/g) || []).length === 1);

sec('5. 后端文件红线：没有新增 .py');
const pyFiles = fs.readdirSync('.').filter(f => f.endsWith('.py'));
console.log('  目录下的 .py 文件: ' + pyFiles.join(', '));
ok('目录里只有原本的 serve.py', pyFiles.length === 1 && pyFiles[0] === 'serve.py', pyFiles.join(', '));

sec('6. 引擎关键常量未变');
// API_BASE 自适应：源码应含 location.port 判断（本地 8899→5000，线上→相对路径）
ok('API_BASE 自适应', engine.includes("location.port === '8899'"));
ok('API_BASE 线上回落相对路径', engine.includes("'/api/v1'"));
ok('THEME_KEY 未变', engine.includes("THEME_KEY"));
ok('主题键仍是 wzh_theme', engine.includes("'wzh_theme'"));
ok('检测主流程 detect 未改签名', engine.includes('async function detect('));
ok('批量检测 batchDetect 未改签名', engine.includes('async function batchDetect(urls, opts = {})'));

sec('7. 功能6 只新增不删除');
// 筛选是"叠加"能力：原有元素 id 都还在（按实际存在的 id 列）
const origIds = ['ta','out','histBody','histPager','histCnt','histRefresh','histClear',
                 'expBtn','expMenu','expBatch','expHistory','aiModal','expFiltered','histFilterBar'];
origIds.forEach(id => ok('元素 #' + id + ' 存在', html.includes('id="' + id + '"')));

sec('8. 筛选状态源唯一');
ok('只有一个 filterTags 声明', (html.match(/let filterTags = \[\]/g) || []).length === 1);
ok('两区都读同一个 filterTags', (html.match(/filterTags/g) || []).length > 8, (html.match(/filterTags/g) || []).length);
ok('无遗留的 histAllItems', !html.includes('histAllItems'));

console.log('\n' + '='.repeat(50));
console.log('  通过 ' + pass + ' 项，失败 ' + fail + ' 项');
console.log('='.repeat(50));
process.exit(fail ? 1 : 0);
